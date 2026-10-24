import hashlib
import json
from pathlib import Path

import pytest
import yaml
import zipfile

from apps.b13_pipeline.main import build_package, build_zip, inspect_and_project
from src.execution.checkpointed_batch import run_checkpointed_sources
from test_physical_acquisition_reuse import fixture
from test_b13_pipeline import _record


@pytest.fixture
def offline_package_inputs(tmp_path):
    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    from prospector_externo.application.projection_export_pipeline import DataxProjectionExportPipeline
    from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate

    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_operational_plan.yaml").write_text(yaml.safe_dump({"sources": [
        {"source_id": "doc", "next_phase": "OPERATIONAL_CONFIG"},
    ]}), encoding="utf-8")
    history = tmp_path / "output/checkpointed/offline/doc"
    repo = LocalJsonRepositoryAdapter(history)
    repo.save_source(Source(source_id="doc", name="Doc", entrypoint="https://example.test/", workflow="html"))
    repo.save_snapshot(Snapshot(source_id="doc", run_id="crawl", resources_hash="raw-hash",
        total_resources=1, execution_status="PARTIAL_SUCCESS", resources=[
            ResourceCandidate(source_id="doc", resource_key="key", url="https://example.test/a b.pdf",
                              raw_url="/a b.pdf", file_extension=".pdf", discovered_from_url="https://example.test/")]))
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"ESTADISTICAS": {"Files": {"Doc": _record()}}}), encoding="utf-8")
    original = build_package(repo_root=tmp_path, run_id="offline", expected_source_ids=["doc"], rows=[{
        "logical_source_id": "doc", "physical_source_id": "doc", "classification": "DATAX_READY",
        "crawl_run_id": "crawl", "raw_resource_count": 1, "artifact": str(old),
        "execution_status": "PARTIAL_SUCCESS", "coverage_complete": False, "urls_pending": 3,
    }])
    exports = tmp_path / "corrected"
    DataxProjectionExportPipeline(repo).export("doc", exports)
    return tmp_path, repo, history, exports, original


def file_bytes(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_offline_cli_uses_existing_packager_without_http_and_keeps_all_history(offline_package_inputs, monkeypatch, capsys):
    import socket
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prospector_externo.adapters.api.routes import create_catalog_router
    from apps.b13_pipeline import main as b13

    root, repo, history, exports, original = offline_package_inputs
    protected = {**file_bytes(history), **file_bytes(exports), **file_bytes(Path(original["package_dir"])),
                 Path(original["zip_path"]): Path(original["zip_path"]).read_bytes()}
    def prohibited(*args, **kwargs):
        pytest.fail("offline packaging attempted network or acquisition")
    original_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", prohibited)
    for name in ("run_command", "command_run", "inspect_and_project", "save_state"):
        monkeypatch.setattr(b13, name, prohibited)
    monkeypatch.setattr("sys.argv", ["b13", "--project-dir", str(root), "package-offline",
        "--run-id", "offline", "--projection-dir", str(exports), "--catalog-dir", str(history)])
    assert b13.main() == 0
    result = json.loads(capsys.readouterr().out)
    package_dir = Path(result["package_dir"])
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert result["legacy_records"] == 1 and result["source_counts"] == {"doc": 1}
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["revision"]["http_acquisitions"] == 0
    assert manifest["revision"]["remote_availability_verified"] is False
    assert manifest["revision"]["origin_zip"]["sha256"] == original["zip_sha256"]
    assert manifest["coverage_partial_sources"] == ["doc"]
    source = manifest["sources"][0]
    assert source["crawl_run_id"] == "crawl" and source["raw_resource_count"] == 1
    assert source["input_resources_hash"] == "raw-hash" and source["urls_pending"] == 3
    assert source["snapshot_reference"]["run_id"] == "crawl"
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    # Windows' ASGI event loop creates a local socketpair internally.
    monkeypatch.setattr(socket.socket, "connect", original_connect)
    api_projection = TestClient(app).get("/catalog/sources/doc/projection").json()
    assert json.loads((package_dir / source["projection_artifact"]).read_text(encoding="utf-8")) == api_projection
    checksum_rows = {}
    for line in (package_dir / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        checksum_rows[relative] = digest
        assert hashlib.sha256((package_dir / relative).read_bytes()).hexdigest() == digest
    files = {p.relative_to(package_dir).as_posix() for p in package_dir.rglob("*") if p.is_file()}
    assert set(checksum_rows) == files - {"checksums.sha256"}
    with zipfile.ZipFile(result["zip_path"]) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == files
        for name in files:
            assert archive.read(name) == (package_dir / name).read_bytes()
    assert {p: p.read_bytes() for p in protected} == protected
    first_revision = {**file_bytes(package_dir), Path(result["zip_path"]): Path(result["zip_path"]).read_bytes()}
    monkeypatch.setattr(socket.socket, "connect", prohibited)
    second = b13.package_offline(root, run_id="offline", projection_dir=exports, catalog_dir=history)
    assert result["zip_path"] != second["zip_path"]
    assert {p: p.read_bytes() for p in first_revision} == first_revision
    assert {p: p.read_bytes() for p in protected} == protected


@pytest.mark.parametrize("corruption", ["export_hash", "legacy_count", "legacy_content", "projection_identity", "origin_snapshot", "catalog_snapshot"])
def test_offline_preflight_refuses_inconsistent_inputs_before_creating_a_revision(offline_package_inputs, corruption):
    from apps.b13_pipeline.main import package_offline
    root, repo, history, exports, original = offline_package_inputs
    package_root = Path(original["package_dir"]).parent
    protected = file_bytes(package_root)
    downstream = exports / "doc/downstream"
    export_path = downstream / "export_manifest.json"
    export = json.loads(export_path.read_text(encoding="utf-8"))
    catalog_dir = history
    if corruption == "export_hash":
        export["projection_sha256"] = "0" * 64
    elif corruption == "legacy_count":
        export["legacy_records"] = 999
    elif corruption == "legacy_content":
        legacy_path = downstream / "legacy_estadisticas.json"
        legacy_path.write_text(legacy_path.read_text(encoding="utf-8").replace("https://example.test/a%20b.pdf", "https://example.test/wrong.pdf"), encoding="utf-8")
        export["legacy_sha256"] = hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    elif corruption == "projection_identity":
        projection_path = downstream / "datax_projection.json"
        data = json.loads(projection_path.read_text(encoding="utf-8"))
        data["run_id"] = "unrelated-crawl"
        projection_path.write_text(json.dumps(data), encoding="utf-8")
        export["projection_sha256"] = hashlib.sha256(projection_path.read_bytes()).hexdigest()
    elif corruption == "origin_snapshot":
        snapshot = repo.get_latest_snapshot("doc")
        snapshot.resources[0].title = "Changed after offline projection"
        repo.save_snapshot(snapshot)
    else:
        from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
        catalog_dir = root / "live"
        live_repo = LocalJsonRepositoryAdapter(catalog_dir)
        live_repo.save_source(repo.get_source("doc"))
        snapshot = repo.get_latest_snapshot("doc")
        snapshot.resources[0].title = "Changed in live catalog"
        live_repo.save_snapshot(snapshot)
    export_path.write_text(json.dumps(export), encoding="utf-8")
    with pytest.raises(ValueError, match="incoherentes|snapshot|catálogo"):
        package_offline(root, run_id="offline", projection_dir=exports, catalog_dir=catalog_dir)
    assert file_bytes(package_root) == protected


def test_resume_packaging_preserves_old_directory_zip_and_failed_revision(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "source_operational_plan.yaml").write_text(yaml.safe_dump({"sources": [
        {"source_id": "doc", "next_phase": "OPERATIONAL_CONFIG"},
        {"source_id": "transtats", "next_phase": "OPERATIONAL_CONFIG"},
        {"source_id": "fdta_valles", "next_phase": "OPERATIONAL_CONFIG"},
    ]}), encoding="utf-8")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"ESTADISTICAS": {"Files": {"Doc": _record()}}}), encoding="utf-8")
    job = tmp_path / "job.json"
    job.write_text('{"submission_policy":"metadata_only_no_post"}', encoding="utf-8")
    rows = [
        {"logical_source_id": "doc", "physical_source_id": "doc", "classification": "DATAX_READY", "artifact": str(legacy)},
        {"logical_source_id": "transtats", "classification": "ACQUISITION_JOB_READY", "artifact": str(job)},
        {"logical_source_id": "fdta_valles", "classification": "EXTERNAL_BLOCKER", "failure_code": "IDENTITY_UNVERIFIED_FDTA_ICCO"},
    ]
    first = build_package(repo_root=tmp_path, run_id="isolated", rows=rows)
    original = {p: p.read_bytes() for p in Path(first["package_dir"]).rglob("*") if p.is_file()}
    zipped = Path(first["zip_path"]).read_bytes()
    second = build_package(repo_root=tmp_path, run_id="isolated", rows=rows)
    assert first["package_dir"] != second["package_dir"]
    assert first["zip_path"] != second["zip_path"]
    assert {p: p.read_bytes() for p in original} == original
    assert Path(first["zip_path"]).read_bytes() == zipped
    assert second["legacy_records"] == 1
    manifest = json.loads((Path(second["package_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert next(row for row in manifest["sources"] if row["logical_source_id"] == "fdta_valles")["artifact"] is None
    assert not (Path(second["package_dir"]) / "sources/fdta_valles.json").exists()
    with pytest.raises(FileExistsError):
        build_zip(Path(first["package_dir"]), Path(first["zip_path"]))
    assert Path(first["zip_path"]).read_bytes() == zipped


def test_identity_hold_never_executes_or_projects_even_with_old_artifacts(tmp_path, monkeypatch):
    checkpoint, options, executions, _ = fixture(tmp_path)
    options["execution_map"]["sources"][1]["acquisition_hold"] = "IDENTITY_REVIEW"
    updated, report = run_checkpointed_sources(checkpoint=checkpoint, **options)
    assert executions == ["primary", "unverified_author"]
    assert updated["sources"]["alias"]["state"] == "SKIPPED_STATUS"
    assert updated["sources"]["alias"]["attempts"] == 0
    assert report["acquisition_holds"] == {"alias": "IDENTITY_REVIEW"}
    assert not (tmp_path / "out/alias").exists()
    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_execution_map.yaml").write_text(yaml.safe_dump(options["execution_map"]), encoding="utf-8")
    def prohibited(**kwargs):
        pytest.fail("La identidad retenida no debe proyectarse")
    monkeypatch.setattr("apps.b13_pipeline.main.project_success", prohibited)
    rows = inspect_and_project(repo_root=tmp_path, run_id="isolated", roster=["alias"])
    assert rows[0]["classification"] == "EXTERNAL_BLOCKER"
    assert rows[0]["execution_status"] == "SKIPPED"
    assert rows[0]["artifact"] is None


def test_latest_report_uses_time_and_refuses_stale_catalog_snapshot(tmp_path):
    from apps.b13_pipeline.main import latest_report
    root = tmp_path / "output/checkpointed/isolated/source"
    (root / "reports").mkdir(parents=True)
    for rid, date, status in [("run_z", "2026-10-22T00:00:00Z", "SUCCESS"),
                               ("run_a", "2026-10-22T00:00:01Z", "FAILED")]:
        (root / f"reports/{rid}.json").write_text(json.dumps({"run_id": rid, "started_at": date,
            "source_results": [{"source_id": "source", "execution_status": status}]}), encoding="utf-8")
    assert latest_report(root)[0].name == "run_a.json"
    rows = inspect_and_project(repo_root=tmp_path, run_id="isolated", roster=["source"])
    assert rows[0]["classification"] == "EXECUTION_FAILED"
    (root / "state").mkdir()
    source_path = root / "state/sources.json"
    source_path.write_text(json.dumps({"source": {"last_run_id": "run_inflight"}}), encoding="utf-8")
    assert latest_report(root) == (None, {})
    source_path.write_text(json.dumps({"source": {"last_run_id": "run_a"}}), encoding="utf-8")
    report = json.loads((root / "reports/run_a.json").read_text(encoding="utf-8"))
    report["source_results"][0]["execution_status"] = "SUCCESS"
    (root / "reports/run_a.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "state/snapshots").mkdir()
    (root / "state/snapshots/source_run_z.json").write_text(json.dumps({"run_id": "run_z", "resources": []}), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="isolated", roster=["source"])
    assert rows[0]["classification"] == "SNAPSHOT_MISSING_OR_STALE"
