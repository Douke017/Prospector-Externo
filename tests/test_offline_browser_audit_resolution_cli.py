"""Offline preparation CLIs write traceable reports without acquiring portals."""
import json
import sys
from types import SimpleNamespace

import yaml

from apps.browser_runtime_audit import main as audit
from apps.custom_resolution import main as resolution


def test_runtime_audit_reports_actual_reference_paths_without_launch(tmp_path, monkeypatch):
    import playwright.sync_api
    binary = tmp_path / "chromium.exe"
    binary.write_bytes(b"fixture path only")
    browser = SimpleNamespace(executable_path=str(binary))
    pw = SimpleNamespace(chromium=browser, firefox=browser, webkit=browser)
    class Context:
        def __enter__(self): return pw
        def __exit__(self, *args): pass
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: Context())
    (tmp_path / "src").mkdir()
    (tmp_path / "src/browser.py").write_text("# playwright route.abort\n")
    config = tmp_path / "candidates.yaml"
    config.write_text(yaml.safe_dump({"sources": [{"source_id": "fixture"}]}))
    output = tmp_path / "report"
    monkeypatch.setattr(sys, "argv", ["audit", "--candidates", str(config), "--repo-root", str(tmp_path), "--output-dir", str(output)])
    assert audit.main() == 0
    report = json.loads((output / "latest.json").read_text())
    assert report["recommended_strategy"] == "USE_EXISTING_PLAYWRIGHT"
    assert report["candidate_source_ids"] == ["fixture"]
    assert report["code_reference_files"] == 1
    assert report["playwright"]["driver_start"] is True
    assert report["playwright"]["browsers"]["chromium"]["executable_exists"] is True


def test_historical_custom_resolution_cli_preserves_other_identities(tmp_path, monkeypatch):
    candidates = [{"source_id": f"c{i}", "logical_code": f"C{i}"} for i in range(8)]
    probes = [{"source_id": f"c{i}", "status": "CUSTOM_DATA_ENDPOINT_CONFIRMED" if i < 3 else "CUSTOM_NO_DATA_EVIDENCE",
        "http_results": [{"json_data": True, "url": "https://fixture.test/data.json"}]} for i in range(8)]
    plan = {"sentinel": "preserve", "sources": [
        {"source_id": f"op{i}", "next_phase": "OPERATIONAL_CONFIG", "workflow_strategy": "html"} for i in range(38)] + [
        {"source_id": f"status{i}", "next_phase": "B10_STATUS"} for i in range(6)] + [
        {**r, "next_phase": "B8_CUSTOM"} for r in candidates]}
    inputs = {"--plan": plan, "--custom-candidates": {"sources": candidates}, "--custom-probe": {"results": probes},
        "--policy-probe": {"results": []}, "--identity-resolution": {"sources": [
            {"source_id": "c3", "status": "RESOLVED_SAME_INSTITUTION", "candidate_entrypoint": "https://fixture.test/new"}]},
        "--transtats-probe": {}, "--transtats-policy": {}}
    args = ["resolution"]
    for index, (flag, content) in enumerate(inputs.items()):
        path = tmp_path / f"input{index}.json"
        path.write_text(json.dumps(content))
        args += [flag, str(path)]
    output = tmp_path / "report"
    result_path = tmp_path / "resolution.yaml"
    args += ["--output-resolution", str(result_path), "--report-dir", str(output)]
    monkeypatch.setattr(sys, "argv", args)
    assert resolution.main() == 0
    result = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    assert result["sources_resolved"] == 8
    updated = json.loads((output / "latest.json").read_text(encoding="utf-8"))
    assert updated["sentinel"] == "preserve"
    assert updated["summary_by_next_phase"] == {"B10_STATUS": 11, "OPERATIONAL_CONFIG": 41}
    assert next(r for r in updated["sources"] if r["source_id"] == "c3")["effective_entrypoint"] == "https://fixture.test/new"
    assert len(updated["sources"]) == 52
