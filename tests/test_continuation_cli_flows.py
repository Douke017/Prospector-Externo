"""Flujos CLI con YAML, checkpoints y JSON reales; red de fuentes simulada."""
import json
import sys

import httpx
import pytest
import yaml

from apps.checkpointed_batch import main as checkpoint_cli
from apps.crawler_batch import main as crawler_cli
from apps.datax_projection import main as projection_cli
from apps.catalog_sync.main import main as sync_cli
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from src.execution.checkpointed_batch import run_checkpointed_sources
from test_discovery_checkpoint_resume import handler_for, source_config, attempt
from test_physical_acquisition_reuse import fixture


def test_crawler_cli_partial_resume_completion_and_cadence_gate(tmp_path, monkeypatch):
    calls = []
    path = tmp_path / "sources.yaml"
    config = source_config()
    def save(cfg):
        path.write_text(yaml.safe_dump({"sources": [cfg.model_dump(mode="json")]}), encoding="utf-8")
    save(config)
    monkeypatch.setattr(crawler_cli, "RunOrchestrator", lambda **kwargs: RunOrchestrator(
        **kwargs, http_runtime_factory=lambda: AsyncHttpRuntime(transport=httpx.MockTransport(handler_for(calls)))))
    arguments = ["crawler", "--config", str(path), "--source", "resume", "--output-dir", str(tmp_path / "out"),
                 "--resume-discovery", "--pipeline-run-id", "cli_test"]
    monkeypatch.setattr(sys, "argv", arguments + ["--force"])
    with pytest.raises(SystemExit) as error:
        crawler_cli.main()
    assert error.value.code == 3
    save(config.model_copy(update={"max_requests": 10}))
    monkeypatch.setattr(sys, "argv", arguments + ["--force"])
    crawler_cli.main()
    assert calls.count("/root") == 1
    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    assert len(repo.get_latest_snapshot("resume").resources) == 4
    before = list(calls)
    monkeypatch.setattr(sys, "argv", arguments)
    crawler_cli.main()  # Periódico: respeta next_eligible_at sin --force.
    assert calls == before
    reports = list(repo.reports_dir.glob("run_*.json"))
    assert len(reports) == 3
    assert any(json.loads(p.read_text(encoding="utf-8"))["sources_skipped"] == 1 for p in reports)
    monkeypatch.setattr(sys, "argv", arguments + ["--recover-abandoned"])
    crawler_cli.main()
    assert calls == before
    assert len(list(repo.reports_dir.glob("run_*.json"))) == 3


def test_crawler_cli_invalid_configuration_exits_without_success(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crawler", "--config", str(tmp_path / "absent.yaml"),
                                     "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as error:
        crawler_cli.main()
    assert error.value.code == 1


@pytest.mark.parametrize("partial", [False, True])
def test_checkpoint_cli_real_store_resume_reuse_and_exit_status(tmp_path, monkeypatch, partial):
    _, options, executions, _ = fixture(tmp_path, partial=partial)
    def write(name, data):
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return str(path)
    args = ["checkpointed", "--plan", write("plan.yaml", options["plan"]),
            "--sources-config", write("sources.yaml", options["source_config"]),
            "--execution-map", write("map.yaml", options["execution_map"]),
            "--checkpoint-config", write("checkpointing.yaml", {"json": {"path": str(tmp_path / "cli-checkpoint.json")}}),
            "--backend", "json"]
    def execute(**kwargs):
        kwargs["executor"] = options["executor"]
        return run_checkpointed_sources(**kwargs)
    monkeypatch.setattr(checkpoint_cli, "run_checkpointed_sources", execute)
    report_path = tmp_path / "report.json"
    command = ["run", "--run-id", "acquisition_test", "--work-dir", str(tmp_path / "cli-work"),
               "--output-dir", str(tmp_path / "cli-out"), "--catalog-dir", str(tmp_path / "catalog"),
               "--report", str(report_path)]
    monkeypatch.setattr(sys, "argv", args + command + ["--max-sources", "1"])
    assert checkpoint_cli.main() == (2 if partial else 0)
    first = json.loads(report_path.read_text(encoding="utf-8"))
    assert first["executed_source_ids"] == ["primary"]
    monkeypatch.setattr(sys, "argv", args + command + ["--resume"])
    assert checkpoint_cli.main() == (2 if partial else 0)
    last = json.loads(report_path.read_text(encoding="utf-8"))
    assert last["acquisitions_reused"][0]["logical_source_id"] == "alias"
    assert executions == (["primary", "primary", "unverified_author"] if partial else ["primary", "unverified_author"])
    saved = json.loads((tmp_path / "cli-checkpoint.json").read_text(encoding="utf-8"))
    assert saved["sources"]["alias"]["state"] == ("PARTIAL" if partial else "SUCCEEDED")
    monkeypatch.setattr(sys, "argv", args + ["validate", "--report", str(report_path)])
    assert checkpoint_cli.main() == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["matched_sources"] == 3


def test_projection_cli_replay_is_offline_and_byte_identical(tmp_path, monkeypatch):
    repo, _ = attempt(tmp_path, source_config().model_copy(update={"max_requests": 10}), [])
    monkeypatch.setattr(sys, "argv", ["projection", "--output-dir", str(repo.base_dir), "--source", "resume"])
    projection_cli.main()
    downstream = repo.base_dir / "resume/downstream"
    first = {p.name: p.read_bytes() for p in downstream.glob("*.json")}
    snapshot_before = repo.get_latest_snapshot("resume")
    projection_cli.main()
    assert first == {p.name: p.read_bytes() for p in downstream.glob("*.json")}
    assert repo.get_latest_snapshot("resume") == snapshot_before
    manifest = json.loads(first["export_manifest.json"])
    assert manifest["raw_resources"] == manifest["selected_resources"] == manifest["legacy_records"] == 4
    assert sync_cli(["--evidence-dir", str(repo.base_dir), "--check"]) == 0


def test_projection_cli_missing_snapshot_and_sync_bad_evidence_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["projection", "--output-dir", str(tmp_path), "--source", "absent"])
    with pytest.raises(SystemExit) as error:
        projection_cli.main()
    assert error.value.code == 1
    assert not (tmp_path / "absent/downstream").exists()
    assert sync_cli(["--evidence-dir", str(tmp_path), "--check"]) == 2
