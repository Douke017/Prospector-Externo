"""Entradas de preparación históricas sobre copias/fixtures: sin red ni cambios reales."""
import json
import shutil
import sys
from pathlib import Path

import yaml

from apps.source_resolution import main as resolution
from apps.source_operational_plan import main as operational
from apps.sources_config_audit import main as config_audit
from apps.custom_candidate_audit import main as custom_audit
from apps.source_evidence_audit import main as evidence_audit
from apps.source_diagnostics import main as diagnostics


ROOT = Path(__file__).resolve().parents[1]


def test_resolution_and_operational_cli_preserve_52_identities(tmp_path, monkeypatch):
    audit = tmp_path / "evidence.json"
    audit.write_text(json.dumps({"ready_results": [], "results": []}), encoding="utf-8")
    output = tmp_path / "resolution.yaml"
    monkeypatch.setattr(sys, "argv", ["resolution", "--capabilities", str(ROOT / "config/source_capabilities.yaml"),
        "--audit-report", str(audit), "--reconciliation", str(ROOT / "config/source_endpoint_reconciliation.yaml"),
        "--reconciliation-run", str(audit), "--output-yaml", str(output), "--report-dir", str(tmp_path / "resolution")])
    assert resolution.main() == 0
    rows = yaml.safe_load(output.read_text(encoding="utf-8"))["sources"]
    assert len(rows) == len({row["source_id"] for row in rows}) == 52
    # El merge B7 se prueba con resoluciones versionadas, sin reemplazarlas.
    monkeypatch.setattr(sys, "argv", ["operational", "--source-resolution", str(ROOT / "config/source_resolution.yaml"),
        "--browser-resolution", str(ROOT / "config/browser_resolution.yaml"), "--output-plan", str(tmp_path / "plan.yaml"),
        "--output-custom", str(tmp_path / "custom.yaml"), "--report-dir", str(tmp_path / "operational")])
    assert operational.main() == 0
    plan = yaml.safe_load((tmp_path / "plan.yaml").read_text(encoding="utf-8"))
    assert len(plan["sources"]) == 52
    assert sum(plan["summary_by_next_phase"].values()) == 52
    assert plan["browser_sources_resolved"] == 11


def test_config_registry_audit_reports_ambiguous_and_missing_without_editing(tmp_path, monkeypatch):
    config = tmp_path / "config"; config.mkdir()
    for name in ("sources.yaml", "source_operational_plan.yaml", "source_execution_map.yaml"):
        shutil.copy2(ROOT / "config" / name, config / name)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/consumer.py").write_text('config.get("sources")\n', encoding="utf-8")
    (config / "empty.yaml").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    before = (config / "sources.yaml").read_bytes()
    assert config_audit.main() == 0
    result = json.loads((tmp_path / ".runtime/sources_config_audit/latest.json").read_text(encoding="utf-8"))
    assert result["sources_yaml"]["entry_count"] == 39
    assert result["plan_operational_count"] == 45
    assert result["matched_count"] + result["unmatched_count"] + result["ambiguous_count"] == 45
    assert result["config_consumers"][0]["path"].endswith("consumer.py")
    assert (config / "sources.yaml").read_bytes() == before


def test_custom_candidate_cli_keeps_unproven_forms_and_apis_under_review(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["custom", "--custom-candidates", str(ROOT / "config/custom_candidates.yaml"),
        "--browser-resolution", str(ROOT / "config/browser_resolution.yaml"),
        "--browser-artifacts", str(tmp_path / "no-browser-evidence"),
        "--source-evidence-audit", str(tmp_path / "absent.json"),
        "--output-yaml", str(tmp_path / "custom.yaml"), "--report-dir", str(tmp_path / "reports")])
    assert custom_audit.main() == 0
    result = yaml.safe_load((tmp_path / "custom.yaml").read_text(encoding="utf-8"))
    expected = yaml.safe_load((ROOT / "config/custom_candidates.yaml").read_text(encoding="utf-8"))
    assert result["candidate_count"] == len(expected["sources"])
    assert {r["source_id"] for r in result["sources"]} == {r["source_id"] for r in expected["sources"]}
    assert all(not row["browser_evidence"]["browser_artifacts_available"] for row in result["sources"])


def test_evidence_and_diagnostic_cli_preserve_failure_causes_and_raw_references(tmp_path, monkeypatch):
    raw = tmp_path / "crawl"; raw.mkdir()
    (raw / "mapa.json").write_text(json.dumps({"resources": [
        {"url": "https://example.test/monthly.pdf", "resource_type": "file", "discovery_method": "commented_html_link"},
        {"url": "https://example.test/api/datasets", "resource_type": "api", "discovery_method": "api_endpoint"}]}), encoding="utf-8")
    log = tmp_path / "error.log"; log.write_text("SSL: CERTIFICATE_VERIFY_FAILED\nHTTP 503\n", encoding="utf-8")
    rows = [{"source_id": "ready", "logical_code": "READY", "entrypoint": "https://example.test",
             "characterization_status": "HTTP_HTML_CANDIDATE", "winning_stage": "expanded_html",
             "stages": [{"stage": "expanded_html", "crawl_output_dir": str(raw), "status": "SUCCESS_RESOURCES"}]},
            {"source_id": "broken", "logical_code": "BROKEN", "entrypoint": "https://broken.test",
             "physical_probe_source_id": "broken", "characterization_status": "EXECUTION_ERROR",
             "stages": [{"stage": "recovery_http", "status": "EXECUTION_ERROR", "log_path": str(log),
                         "site_http_codes": [], "robots_http_codes": [503]}]}]
    report = tmp_path / "characterization.json"
    report.write_text(json.dumps({"logical_results": rows}), encoding="utf-8")
    capabilities = tmp_path / "caps.yaml"
    capabilities.write_text(yaml.safe_dump({"sources": [
        {"source_id": "ready", "operational_readiness": "READY_CANDIDATE", "workflow_hint": "html"},
        {"source_id": "broken", "operational_readiness": "UNRESOLVED", "physical_probe_source_id": "broken"}]}), encoding="utf-8")
    for module, name in ((evidence_audit, "evidence"), (diagnostics, "diagnostics")):
        monkeypatch.setattr(sys, "argv", [name, "--capabilities", str(capabilities),
            "--characterization-report", str(report), "--output-dir", str(tmp_path / name)])
        assert module.main() == 0
        result = json.loads((tmp_path / name / "latest.json").read_text(encoding="utf-8"))
        assert result
    audited = json.loads((tmp_path / "evidence/latest.json").read_text(encoding="utf-8"))
    assert len(audited["ready_results"]) == 1
    assert len(audited["unresolved_results"]) == 1
    assert audited["ready_results"][0]["resource_count"] == 2
    assert audited["ready_results"][0]["api_quality"] == "DATA_API_EVIDENCE"
    assert evidence_audit.audit_ready_candidate({"winning_stage": "missing", "stages": [
        {"stage": "missing"}]})["resource_count"] == 0
