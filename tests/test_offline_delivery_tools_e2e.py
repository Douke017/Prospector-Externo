"""Regresión del formato histórico B11/B12 y su integridad; datos sintéticos.

El fixture no representa una corrida pública ni certifica fuentes actuales.
Todos los directorios y reconstrucciones de paquetes viven en tmp_path.
"""
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from apps.b11_output_audit import main as output_audit
from apps.b12_datax_package import main as old_package
from apps.b12_enterprise_delivery import main as enterprise
from apps.b13_closure_audit import main as closure_audit
from apps.b13_pipeline import main as pipeline
from test_b13_pipeline import _record


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def historical_fixture(tmp_path):
    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ids = list(output_audit.OPERATIONAL_SOURCE_IDS)
    for index, sid in enumerate(ids):
        evidence = tmp_path / output_audit.BASELINE_ROOT_REL / sid
        fail = sid in {"mhe", "sigma"}
        rid = f"run_{sid}"
        url = f"https://{sid}.example.test/{'DL_SelectFields' if sid=='transtats' else 'doc.csv'}"
        method = "custom_form_acquisition_job" if sid == "transtats" else "html_link"
        resource = {"source_id": sid, "resource_key": sid, "url": url, "file_extension": ".csv",
                    "discovery_method": method, "resource_type": "file"}
        dump(evidence / f"reports/{rid}.json", {"run_id": rid, "started_at": (at+timedelta(seconds=index*2)).isoformat(),
            "finished_at": (at+timedelta(seconds=index*2+1)).isoformat(), "source_results": [{
                "source_id": sid, "run_id": rid, "workflow": "custom" if sid=="transtats" else "html",
                "execution_status": "FAILED" if fail else "SUCCESS", "content_status": "NOT_EVALUATED" if fail else "CHANGED",
                "failure_code": "ROBOTS_UNREACHABLE" if fail else None,
                "coverage": {"stop_reason": "QUEUE_EXHAUSTED", "resources_found": 0 if fail else 1,
                             "requests_total": 2, "pages_visited": 1}}]})
        if fail:
            continue
        dump(evidence / f"state/snapshots/{sid}_{rid}.json", {"source_id": sid, "run_id": rid,
            "captured_at": (at+timedelta(seconds=index*2+1)).isoformat(), "resources": [resource], "total_resources": 1})
        dump(evidence / f"{sid}/mapa_{sid}.json", {"resources": [resource]})
        if sid == "transtats":
            dump(evidence / sid / "downstream/acquisition_job.json", {"decision": "ACQUISITION_JOB_READY", "url": url})
        else:
            record = _record(); record.update(descripcion=sid, url_descarga=url)
            dump(evidence / sid / "downstream/legacy_estadisticas.json", {"ESTADISTICAS": {"Files": {sid: record}}})
        config = tmp_path / ".runtime/checkpointed_batch/historical/source_configs" / f"{sid}.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(yaml.safe_dump({"sources": [{"source_id": sid, "max_urls": 250, "max_depth": 2,
            "max_runtime_seconds": 900}]}), encoding="utf-8")
    plan = {"sources": [{"source_id": sid, "next_phase": "OPERATIONAL_CONFIG"} for sid in ids] + [
        {"source_id": f"status{i}", "next_phase": "B10_STATUS"} for i in range(11)]}
    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_operational_plan.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
    return tmp_path


def test_b11_audit_to_b12_package_and_enterprise_contract(historical_fixture, monkeypatch):
    root = historical_fixture
    audit = output_audit.build_audit(root)
    assert audit["summary"]["classification_counts"] == {"DATAX_READY": 38, "EXTERNAL_BLOCKER": 2, "ACQUISITION_JOB_READY": 1}
    assert audit["summary"]["total_raw_resources"] == 39
    assert audit["summary"]["total_legacy_projection_records"] == 38
    assert output_audit.render_markdown(audit)
    output_audit.print_console(audit)
    path = root / "audit.json"; dump(path, audit)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["package", "--audit", str(path), "--output-dir", str(root / "package"), "--zip", str(root / "package.zip")])
    assert old_package.main() == 0
    assert old_package.verify_package(root / "package")["legacy_records"] == 38
    monkeypatch.setattr(sys, "argv", ["package", "--output-dir", str(root / "package"), "--zip", str(root / "package.zip"), "--verify-only"])
    assert old_package.main() == 0
    monkeypatch.setattr(sys, "argv", ["enterprise", "--audit", str(path), "--output-dir", str(root / "enterprise"),
        "--zip", str(root / "enterprise.zip"), "--report", str(root / "enterprise-report.json")])
    assert enterprise.main() == 0
    report = json.loads((root / "enterprise-report.json").read_text(encoding="utf-8"))
    assert report["duplicates"]["cross_physical_groups"] == 0
    assert report["index"]["manifest_sources"] == 41
    # Un byte alterado debe invalidar el paquete: ensayo solo en tmp_path.
    source = next((root / "enterprise/sources").glob("*.json"))
    source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError):
        enterprise.validate_checksums(root / "enterprise")


def test_b13_closure_checks_actual_checksums_and_report_times(historical_fixture, monkeypatch):
    root = historical_fixture
    audit = output_audit.build_audit(root)
    rows = []
    for row in audit["sources"]:
        artifact = (row["legacy_projection_files"][0]["path"] if row["classification"] == "DATAX_READY"
                    else row.get("special_downstream_manifest"))
        rows.append({**row, "artifact": artifact, "legacy_records": row["legacy_projection_records"]})
    package = pipeline.build_package(repo_root=root, run_id="historical", rows=rows)
    state = pipeline.save_state(repo_root=root, run_id="historical", checkpoint_returncode=2, rows=rows, package=package)
    report = closure_audit.evaluate(repo_root=root, run_id="historical", state=state)
    assert report["closure_status"] == "CLOSED"
    assert report["timing_and_limits"]["timed_reports"] == 41
    assert report["timing_and_limits"]["active_source_crawl_seconds"] == 41
    assert report["package"]["legacy_records"] == 38
    assert closure_audit.render_markdown(report)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["closure", "--run-id", "historical", "--state", str(root / ".runtime/b13_pipeline/historical/state.json"),
        "--output", str(root / "closure.json"), "--markdown", str(root / "closure.md")])
    assert closure_audit.main() == 0
    source = next((Path(package["package_dir"]) / "sources").glob("*.json"))
    source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="Checksums"):
        closure_audit.validate_package(repo_root=root, state=state)
