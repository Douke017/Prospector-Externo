"""One production CLI per operation, real subprocesses and local HTTP consumers."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

from apps.b13_pipeline.main import command_run, selected_roster, selection_plan
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.domain.models import Source

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def operational_site(tmp_path):
    calls = Counter()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            calls[self.path] += 1
            bodies = {"/robots.txt": "User-agent: *\nAllow: /", "/alpha": '<a href="/a.csv">First monthly table</a><a href="/next">Next</a>',
                "/next": '<a href="/b.pdf">Second annual report</a>',
                "/bravo": '<!-- <a href="/bravo.xlsx">Commented monthly series</a> -->', "/empty": '<p>No resources</p>'}
            self.send_response(200 if self.path in bodies else 404)
            self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "text/html")
            self.end_headers(); self.wfile.write(bodies.get(self.path, "missing").encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    config = tmp_path / "config"; config.mkdir()
    ids = ["alpha", "alpha_alias", "bravo", "fdta_valles", "paused"]
    def write(name, content): (config / name).write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    write("source_operational_plan.yaml", {"sources": [
        {"source_id": sid, "next_phase": "OPERATIONAL_CONFIG"} for sid in ids] + [
        {"source_id": f"status{i}", "next_phase": "B10_STATUS"} for i in range(47)]})
    write("source_execution_map.yaml", {"sources": [
        {"logical_source_id": "alpha", "config_source_id": "alpha"},
        {"logical_source_id": "alpha_alias", "config_source_id": "alpha", "reuse_acquisition": True},
        {"logical_source_id": "bravo", "config_source_id": "bravo"},
        {"logical_source_id": "paused", "config_source_id": "paused"},
        {"logical_source_id": "fdta_valles", "config_source_id": "alpha", "acquisition_hold": "IDENTITY_UNVERIFIED_FDTA_ICCO"}]})
    write("sources.yaml", {"sources": [
        {"source_id": sid, "entrypoint": base, "seeds": [base + seed], "workflow": workflow,
         "rate_limit_seconds": 0, "max_requests": 2, "discover_sitemaps": False,
         "discover_apis": False, "inspect_archives": False}
        for sid, seed, workflow in [("alpha", "/alpha", "html"), ("bravo", "/bravo", "commented_html"), ("paused", "/paused", "html")]]})
    catalog = tmp_path / "catalog"
    repo = LocalJsonRepositoryAdapter(catalog)
    repo.save_source(Source(source_id="paused", name="Suspended fixture", entrypoint=base, workflow="html", health_status="SUSPENDED"))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    try: yield tmp_path, catalog, calls, env
    finally: server.shutdown(); server.server_close(); thread.join(timeout=3)


def cli(project, catalog, env, *args, expected=0):
    command = [sys.executable, "-B", "-m", "apps.b13_pipeline.main", "--project-dir", str(project),
               "run", "--catalog-dir", str(catalog), *args]
    cp = subprocess.run(command, cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True, timeout=45)
    assert cp.returncode == expected, cp.stdout + cp.stderr
    rid = args[args.index("--run-id") + 1]
    runtime = project / ".runtime/b13_pipeline" / rid
    (runtime / f"console_{len(list(runtime.glob('console_*.log')))}.log").write_text(cp.stdout + cp.stderr, encoding="utf-8")
    return json.loads((runtime / "state.json").read_text(encoding="utf-8"))


def check_package(state):
    package = state["package"]
    folder = Path(package["package_dir"])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["sources"]:
        if row["artifact"]:
            path = folder / row["artifact"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    for line in (folder / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    with zipfile.ZipFile(package["zip_path"]) as archive:
        assert archive.testzip() is None
        assert "manifest.json" in archive.namelist()
    assert hashlib.sha256(Path(package["zip_path"]).read_bytes()).hexdigest() == package["zip_sha256"]
    return manifest


def test_production_cli_initial_resume_incremental_package_and_http_api(operational_site):
    project, catalog, calls, env = operational_site
    selected = "alpha,alpha_alias,bravo,fdta_valles"
    first = cli(project, catalog, env, "--run-id", "initial", "--mode", "initial", "--source-ids", selected, "--max-sources", "1", expected=3)
    assert first["coverage_partial_sources"] == ["alpha"]
    resumed = cli(project, catalog, env, "--run-id", "initial", "--resume")
    manifest = check_package(resumed)
    assert manifest["legacy_records"] == 5
    assert calls["/alpha"] == calls["/next"] == calls["/bravo"] == 1
    assert not calls["/a.csv"] and not calls["/b.pdf"] and not calls["/bravo.xlsx"]
    assert resumed["batch_report"]["acquisitions_executed"] == 2
    assert resumed["batch_report"]["acquisitions_reused"][0]["logical_source_id"] == "alpha_alias"
    assert all(row["telemetry"]["rss_peak_sampled_bytes"] > 0 for row in resumed["sources"]
               if row["logical_source_id"] in {"alpha", "bravo"})
    initial_bytes = Path(resumed["package"]["zip_path"]).read_bytes()
    before = dict(calls)
    sources_before = (catalog / "state/sources.json").read_bytes()
    snapshots_before = {p.name: p.read_bytes() for p in (catalog / "state/snapshots").glob("*.json")}
    diagnose = subprocess.run([sys.executable, "-B", "-m", "apps.b13_pipeline.main", "--project-dir", str(project),
        "plan", "--catalog-dir", str(catalog), "--mode", "incremental", "--json"], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert diagnose.returncode == 0, diagnose.stderr
    diagnostic = json.loads(diagnose.stdout)
    assert diagnostic["eligible_physical"] == 0 and diagnostic["counts"]["HELD"] == 1
    assert dict(calls) == before and (catalog / "state/sources.json").read_bytes() == sources_before
    incremental = cli(project, catalog, env, "--run-id", "incremental", "--mode", "incremental")
    incremental_manifest = check_package(incremental)
    assert incremental_manifest["legacy_records"] == 5
    assert incremental["batch_report"]["acquisitions_executed"] == 0
    assert len(incremental["batch_report"]["skipped_eligibility"]) == 4
    assert all(row["requests_this_attempt"] == 0 for row in incremental["sources"])
    assert all(row["execution_status"] == "SKIPPED" for row in incremental["sources"])
    assert all(row["artifact_reused"] for row in incremental["sources"] if row["logical_source_id"] in {"alpha", "alpha_alias", "bravo"})
    assert dict(calls) == before
    assert (catalog / "state/sources.json").read_bytes() == sources_before
    assert {p.name: p.read_bytes() for p in (catalog / "state/snapshots").glob("*.json")} == snapshots_before
    assert Path(resumed["package"]["zip_path"]).read_bytes() == initial_bytes
    repeated = cli(project, catalog, env, "--run-id", "incremental", "--resume")
    check_package(repeated)
    assert repeated["package"]["zip_path"] != incremental["package"]["zip_path"]
    assert dict(calls) == before
    assert "--force" not in (project / ".runtime/b13_pipeline/incremental/checkpointed_batch.log").read_text(encoding="utf-8")

    with socket.socket() as free:
        free.bind(("127.0.0.1", 0)); port = free.getsockname()[1]
    api_log = project / "api.log"
    with api_log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "-B", "-m", "apps.catalog_api.main", "--backend", "json",
            "--catalog-dir", str(catalog), "--port", str(port)], cwd=ROOT,
            env={**env, "PROSPECTOR_BACKEND": "mongo", "PROSPECTOR_MONGO_URI": "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=50"},
            stdout=output, stderr=subprocess.STDOUT)
        clock = time.perf_counter()
        responses = {}
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
                while True:
                    try:
                        if client.get("/health").status_code == 200: break
                    except httpx.TransportError: pass
                    assert process.poll() is None, api_log.read_text(encoding="utf-8")
                    assert time.perf_counter() - clock < 20
                    time.sleep(.1)
                startup = time.perf_counter() - clock
                for path in ["/health", "/docs", "/openapi.json", "/catalog/sources", "/catalog/sources/alpha",
                             "/catalog/resources?source_id=alpha&limit=1&offset=0", "/runs", "/sources/alpha/observations"]:
                    response = client.get(path); assert response.status_code == 200
                    responses[path] = response.json() if "json" in response.headers.get("content-type", "") else {"status_code": response.status_code}
                reports = responses["/runs"]; assert len(reports) == 4
                assert reports[0]["sources_skipped"] == 3
                assert len(client.get("/catalog/resources?source_id=alpha").json()) == 2
                assert len(client.get("/catalog/resources?source_id=bravo").json()) == 1
                assert client.get("/catalog/sources/fdta_valles").status_code == 404
                assert client.get("/catalog/sources/paused").json()["health_status"] == "SUSPENDED"
                rid = reports[0]["run_id"]
                for path in [f"/runs/{rid}", f"/runs/{rid}/report"]:
                    response = client.get(path); assert response.status_code == 200; responses[path] = response.json()
                page1 = client.get("/catalog/resources?source_id=alpha&limit=1&offset=0").json()
                page2 = client.get("/catalog/resources?source_id=alpha&limit=1&offset=1").json()
                assert page1[0]["resource_key"] != page2[0]["resource_key"]
                assert all(r["source_id"] == "alpha" for r in page1 + page2)
                assert client.get("/catalog/sources/missing").status_code == 404
                assert client.get("/catalog/resources?limit=0").status_code == 422
                assert client.post("/catalog/resources", json={}).status_code == 405
                assert client.get("/search/export-index").status_code == 404
                inspect = client.post("/mapping/inspect", json={"url": "https://example.org/a.csv?utm_source=x#section"})
                assert inspect.status_code == 200
                assert inspect.json()["normalized_url"] == "https://example.org/a.csv"
                responses["POST /mapping/inspect"] = inspect.json()
                assert client.post("/mapping/inspect", json={"url": "invalid"}).status_code == 422
                state_path = catalog / "state/sources.json"; original = state_path.read_bytes()
                try:
                    state_path.write_text('{"broken":', encoding="utf-8")
                    broken = client.get("/health"); assert broken.status_code == 503
                    assert str(catalog) not in broken.text
                finally: state_path.write_bytes(original)
        finally:
            process.terminate(); process.wait(timeout=10)
    evidence = {"fixture_only": True, "physical_crawled": 2, "logical_selected_initial": 4,
        "initial": first, "resumed": resumed, "incremental": incremental, "resume_incremental": repeated,
        "diagnostic": diagnostic, "server_calls": dict(calls), "api_responses": responses,
        "native_api_startup_seconds": startup, "api_log": str(api_log), "api_process_closed": process.poll() is not None}
    path = project / "operational_evidence.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print("OPERATIONAL_EVIDENCE=" + str(path))


@pytest.mark.parametrize("changes", [{"mode": "incremental"}, {"catalog_dir": "other"}, {"source_ids": ["bravo"]}])
def test_resume_rejects_changed_request_before_network(operational_site, changes):
    project, catalog, calls, env = operational_site
    first = cli(project, catalog, env, "--run-id", "safe", "--source-ids", "alpha", expected=3)
    before = dict(calls)
    options = {"catalog_dir": catalog, "source_ids": ["alpha"], **changes}
    with pytest.raises(ValueError, match="PIPELINE_REQUEST_CHANGED"):
        command_run(project, run_id="safe", resume=True, max_sources=None, **options)
    assert dict(calls) == before


def test_plan_missing_catalog_is_read_only_and_selection_is_validated(operational_site):
    project, _, calls, _ = operational_site
    missing = project / "does-not-exist"
    plan = selection_plan(project, missing, ["alpha", "fdta_valles"], "incremental")
    assert plan["selected_logical"] == 2 and plan["eligible_physical"] == 1
    assert not missing.exists() and not calls
    with pytest.raises(ValueError): selected_roster(project, ["typo"])


def test_failed_source_preserves_other_data_and_packages_explicit_failure(operational_site):
    project, catalog, calls, env = operational_site
    state = cli(project, catalog, env, "--run-id", "failed", "--source-ids", "bravo,paused,fdta_valles", expected=2)
    assert state["status"] == "FAILED"
    manifest = check_package(state)
    assert manifest["legacy_records"] == 1 and manifest["failed_sources"] == ["paused"]
    assert next(r for r in manifest["sources"] if r["logical_source_id"] == "paused")["artifact"] is None
    repo = LocalJsonRepositoryAdapter(catalog)
    assert len(repo.list_resources("bravo")) == 1
    assert any(r.sources_failed for r in repo.list_reports())


def test_incremental_rejects_tampered_delivery_then_refreshes_due_source(operational_site, monkeypatch):
    project, catalog, calls, env = operational_site
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    assert command_run(project, run_id="seed", resume=False, max_sources=None, catalog_dir=catalog, source_ids=["bravo"]) == 0
    saved = json.loads((catalog / "state/deliveries.json").read_text(encoding="utf-8"))["bravo"]
    artifact = Path(saved["row"]["artifact"]); original = artifact.read_bytes()
    before = dict(calls)
    artifact.write_text("tampered", encoding="utf-8")
    assert command_run(project, run_id="tampered", resume=False, max_sources=None, catalog_dir=catalog,
                       source_ids=["bravo"], mode="incremental") == 0
    state = json.loads((project / ".runtime/b13_pipeline/tampered/state.json").read_text(encoding="utf-8"))
    assert state["sources"][0]["classification"] == "SKIPPED"
    assert state["sources"][0]["artifact_status"] == "NO_VERIFIED_PREVIOUS_DELIVERY"
    assert check_package(state)["legacy_records"] == 0
    assert dict(calls) == before
    artifact.write_bytes(original)
    repo = LocalJsonRepositoryAdapter(catalog)
    source = repo.get_source("bravo")
    source.next_eligible_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    repo.save_source(source)
    assert command_run(project, run_id="due", resume=False, max_sources=None, catalog_dir=catalog,
                       source_ids=["bravo"], mode="incremental") == 0
    assert calls["/bravo"] == 2
    state = json.loads((project / ".runtime/b13_pipeline/due/state.json").read_text(encoding="utf-8"))
    assert state["batch_report"]["acquisitions_executed"] == 1
    assert check_package(state)["legacy_records"] == 1
    assert all(r.change_status.value != "REMOVED" for r in repo.list_resources("bravo"))


def test_empty_success_produces_manifest_without_inventing_legacy(operational_site, monkeypatch):
    project, catalog, calls, env = operational_site
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    path = project / "config/sources.yaml"; config = yaml.safe_load(path.read_text(encoding="utf-8"))
    bravo = next(r for r in config["sources"] if r["source_id"] == "bravo")
    bravo["seeds"] = [bravo["entrypoint"] + "/empty"]
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert command_run(project, run_id="empty", resume=False, max_sources=None, catalog_dir=catalog, source_ids=["bravo"]) == 0
    state = json.loads((project / ".runtime/b13_pipeline/empty/state.json").read_text(encoding="utf-8"))
    manifest = check_package(state)
    assert manifest["sources"][0]["classification"] == "NO_RESOURCES"
    assert manifest["legacy_records"] == 0 and manifest["sources"][0]["artifact"] is None


def test_optional_mongo_mirror_uses_selected_database_and_copies_skip_reports(operational_site, monkeypatch):
    from prospector_externo.adapters.persistence import mongo_adapter
    from types import SimpleNamespace
    project, catalog, calls, env = operational_site
    monkeypatch.setenv("PYTHONPATH", env["PYTHONPATH"])
    target = LocalJsonRepositoryAdapter(project / "fake_mongo_port")
    target.client = SimpleNamespace(close=lambda: None)
    databases = []
    def factory(connection_uri, database_name):
        databases.append(database_name)
        return target
    monkeypatch.setattr(mongo_adapter, "MongoPersistenceAdapter", factory)
    options = {"catalog_dir": catalog, "source_ids": ["bravo"], "sync_mongo": True, "mongo_database": "isolated_test"}
    assert command_run(project, run_id="mongo_seed", resume=False, max_sources=None, **options) == 0
    assert command_run(project, run_id="mongo_skip", resume=False, max_sources=None, mode="incremental", **options) == 0
    assert set(databases) == {"isolated_test"}
    assert len(target.list_resources("bravo")) == 1
    assert target.list_reports()[0].sources_skipped == 1
    assert target.list_reports()[0].source_results[0].execution_status.value == "SKIPPED"
