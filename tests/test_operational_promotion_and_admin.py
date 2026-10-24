"""Regresión de herramientas de preparación; nunca escribe config/ o runtime reales."""
import json
import sys
from pathlib import Path

import pytest
import yaml

from apps.operational_config_promotion import main as promotion
from apps.checkpoint_admin import main as admin
from apps.persistence_closure import main as closure
from src.persistence.model import new_checkpoint, transition


ROOT = Path(__file__).resolve().parents[1]


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(path)


def test_promotion_cli_handles_current_45_and_preserves_source_and_policy(tmp_path, monkeypatch):
    before = {name: (ROOT / "config" / name).read_bytes() for name in (
        "sources.yaml", "source_operational_plan.yaml", "source_execution_map.yaml", "source_inventory.yaml")}
    monkeypatch.setattr(sys, "argv", ["promotion", "--current-sources", str(ROOT / "config/sources.yaml"),
        "--plan", str(ROOT / "config/source_operational_plan.yaml"), "--inventory", str(ROOT / "config/source_inventory.yaml"),
        "--existing-map", str(ROOT / "config/source_execution_map.yaml"),
        "--output-sources", str(tmp_path / "sources.yaml"), "--output-map", str(tmp_path / "map.yaml"),
        "--report", str(tmp_path / "report.json")])
    assert promotion.main() == 0
    result = yaml.safe_load((tmp_path / "map.yaml").read_text(encoding="utf-8"))
    assert result["logical_operational_sources"] == 45
    assert result["physical_config_sources"] == 39
    assert next(r for r in result["sources"] if r["logical_source_id"] == "fdta_valles")["acquisition_hold"] == "IDENTITY_UNVERIFIED_FDTA_ICCO"
    assert len([r for r in result["sources"] if r.get("reuse_acquisition")]) == 5
    assert all((ROOT / "config" / name).read_bytes() == content for name, content in before.items())


@pytest.mark.parametrize("operational", [41, 45])
def test_generate_missing_physical_configs_from_plan_without_stale_fixed_count(operational):
    plan = {"sources": [{"source_id": f"s{i}", "next_phase": "OPERATIONAL_CONFIG" if i < operational else "B10_STATUS",
                         "effective_entrypoint": f"https://s{i}.example.test", "workflow_strategy": "html",
                         "operational_config": {"seed_urls": [f"https://s{i}.example.test/data"]}}
                        for i in range(52)]}
    template = {"sources": [{"source_id": "finrural", "entrypoint": "https://template.test", "workflow": "html", "max_requests": 5}]}
    candidate, mapping, report = promotion.build_candidate(template, plan, {})
    assert len(mapping["sources"]) == operational
    assert len(candidate["sources"]) == operational + 1
    assert candidate["sources"][1]["seeds"] == ["https://s0.example.test/data"]
    assert report["runtime_model_validation"]["validated"] == operational + 1


def test_runtime_validator_rejects_invalid_limits_instead_of_accepting_source_state():
    result = promotion.validate_runtime_source_model([{"source_id": "bad", "name": "Bad",
        "entrypoint": "https://example.test", "workflow": "html", "max_requests": 0}])
    assert result["attempted"] and result["errors"]
    assert result["validated"] == 0


def test_checkpoint_admin_cli_preserves_confirmed_sources_and_recovers_running(tmp_path, monkeypatch):
    plan = {"sources": [{"source_id": f"s{i}", "next_phase": "OPERATIONAL_CONFIG" if i < 45 else "B10_STATUS"} for i in range(52)]}
    plan_path = write_yaml(tmp_path / "plan.yaml", plan)
    config_path = write_yaml(tmp_path / "checkpointing.yaml", {"json": {"path": str(tmp_path / "checkpoint.json")}})
    def cli(*args):
        monkeypatch.setattr(sys, "argv", ["admin", "--config", config_path, "--backend", "json", *args])
        return admin.main()
    assert cli("init", "--plan", plan_path, "--run-id", "admin_test") == 0
    assert cli("mark", "--run-id", "admin_test", "--source-id", "s0", "--state", "RUNNING") == 0
    assert cli("mark", "--run-id", "admin_test", "--source-id", "s0", "--state", "SUCCEEDED", "--resource-count", "2") == 0
    assert cli("mark", "--run-id", "admin_test", "--source-id", "s1", "--state", "RUNNING") == 0
    assert cli("summary", "--run-id", "admin_test") == 0
    pending = tmp_path / "pending.json"
    assert cli("resume", "--run-id", "admin_test", "--plan", plan_path, "--pending-output", str(pending)) == 0
    report = json.loads(pending.read_text(encoding="utf-8"))
    assert report["recovered_interrupted"] == ["s1"]
    assert "s0" not in report["source_ids"] and "s1" in report["source_ids"]
    with pytest.raises(ValueError, match="inv"):
        cli("mark", "--run-id", "admin_test", "--source-id", "s0", "--state", "RUNNING")


@pytest.mark.parametrize("operational", [41, 45])
def test_persistence_closure_counts_derive_from_checkpoint(operational, tmp_path, monkeypatch):
    ids = ["anapo", "aps"] + [f"s{i}" for i in range(50)]
    plan = {"sources": [{"source_id": sid, "next_phase": "OPERATIONAL_CONFIG" if i < operational else "B10_STATUS"}
                        for i, sid in enumerate(ids)]}
    checkpoint = new_checkpoint(plan, run_id="closure_test")
    for sid in ids[:2]:
        checkpoint = transition(checkpoint, sid, "RUNNING")
        checkpoint = transition(checkpoint, sid, "SUCCEEDED")
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint_file.write_text(json.dumps(checkpoint), encoding="utf-8")
    map_file = write_yaml(tmp_path / "map.yaml", {"logical_operational_sources": operational,
        "physical_config_sources": 1, "sources": [{"logical_source_id": sid} for sid in ids[:operational]]})
    sources_file = write_yaml(tmp_path / "sources.yaml", {"sources": [{"source_id": "physical"}]})
    monkeypatch.setattr(sys, "argv", ["closure", "--checkpoint", str(checkpoint_file), "--execution-map", map_file,
        "--sources-config", sources_file, "--run-id", "closure_test", "--output-dir", str(tmp_path / "report")])
    assert closure.main() == 0
    result = json.loads((tmp_path / "report/latest.json").read_text(encoding="utf-8"))
    assert result["live_resume_smoke"]["checkpoint_counts"]["PENDING"] == operational - 2
