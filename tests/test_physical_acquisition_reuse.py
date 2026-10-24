"""B9B/B13: reutilizar hechos físicos sin fabricar otra adquisición o autoría."""
import json
from pathlib import Path

import httpx
import pytest
import yaml

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.catalog_sync import read_evidence
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from src.execution.checkpointed_batch import run_checkpointed_sources
from src.persistence.model import new_checkpoint
from src.persistence.store import JsonCheckpointStore
from test_discovery_checkpoint_resume import source_config, handler_for


def fixture(tmp_path, *, partial=False, reuse=True):
    ids = ["primary", "alias", "unverified_author"]
    plan = {"sources": [
        {"source_id": sid, "next_phase": "OPERATIONAL_CONFIG" if i < 3 else "B10_STATUS"}
        for i, sid in enumerate(ids + [f"status_{i}" for i in range(49)])
    ]}
    cfg = source_config().model_copy(update={"max_requests": 2 if partial else 10})
    config = {"sources": [cfg.model_dump(mode="json")]}
    execution_map = {"sources": [
        {"logical_source_id": sid, "config_source_id": "resume", "reuse_acquisition": reuse and sid == "alias"}
        for sid in ids
    ]}
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    checkpoint = new_checkpoint(plan, run_id="acquisition_test")
    store.save(checkpoint)
    calls, executions = [], []

    def executor(**kwargs):
        executions.append(kwargs["output_dir"].name)
        repo = LocalJsonRepositoryAdapter(kwargs["output_dir"], catalog_dir=kwargs["catalog_dir"])
        runner = RunOrchestrator(repo, repo, kwargs["single_config_path"],
            http_runtime_factory=lambda: AsyncHttpRuntime(transport=httpx.MockTransport(handler_for(calls))))
        report = runner.run_batch(force=True, resume_discovery=True, pipeline_run_id="acquisition_test")
        return 3 if any(o.execution_status.value == "PARTIAL_SUCCESS" for o in report.source_results) else 0

    options = dict(plan=plan, source_config=config, store=store, work_dir=tmp_path / "work",
                   output_root=tmp_path / "out", catalog_dir=tmp_path / "catalog",
                   execution_map=execution_map, executor=executor)
    return checkpoint, options, executions, calls


def test_reuse_preserves_physical_provenance_and_real_report_identity(tmp_path):
    checkpoint, options, executions, calls = fixture(tmp_path)
    updated, report = run_checkpointed_sources(checkpoint=checkpoint, **options)
    assert executions == ["primary", "unverified_author"]
    assert calls.count("/root") == 2
    assert report["acquisitions_executed"] == 2
    assert report["succeeded"] == 3
    assert report["acquisitions_reused"][0]["acquisition_reused_from"] == "primary"
    primary = read_evidence(tmp_path / "out/primary", "primary")
    alias = read_evidence(tmp_path / "out/alias", "alias")
    unverified = read_evidence(tmp_path / "out/unverified_author", "unverified_author")
    assert alias.source.source_id == primary.source.source_id == "resume"
    assert alias.run == primary.run
    assert alias.resources == primary.resources
    assert unverified.run.run_id != primary.run.run_id
    assert updated["sources"]["alias"]["metadata"]["crawler_run_id"] == primary.run.run_id
    raw_map = json.loads((tmp_path / "out/alias/resume/mapa_resume.json").read_text(encoding="utf-8"))
    assert raw_map["source"]["entrypoint"] == primary.source.entrypoint
    assert raw_map["source"]["name"] == primary.source.name


def test_control_without_reuse_performs_three_physical_acquisitions(tmp_path):
    checkpoint, options, executions, calls = fixture(tmp_path, reuse=False)
    _, report = run_checkpointed_sources(checkpoint=checkpoint, **options)
    assert len(executions) == calls.count("/root") == report["acquisitions_executed"] == 3
    assert not report["acquisitions_reused"]


def test_complete_acquisition_reused_across_invocations_without_network(tmp_path):
    checkpoint, options, executions, calls = fixture(tmp_path)
    checkpoint, _ = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    before = list(calls)
    _, report = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    assert executions == ["primary"]
    assert calls == before
    assert report["acquisitions_executed"] == 0
    assert report["acquisitions_reused"][0]["logical_source_id"] == "alias"


def test_partial_reuse_advances_on_resume_and_keeps_prior_attempt_reports(tmp_path):
    checkpoint, options, executions, _ = fixture(tmp_path, partial=True)
    checkpoint, first = run_checkpointed_sources(checkpoint=checkpoint, **options)
    assert first["partial"] == 3
    first_alias = read_evidence(tmp_path / "out/alias", "alias")
    checkpoint, second = run_checkpointed_sources(checkpoint=checkpoint, **options)
    second_alias = read_evidence(tmp_path / "out/alias", "alias")
    assert second["partial"] == 3
    assert second_alias.run.run_id != first_alias.run.run_id
    assert second_alias.observation.coverage.urls_pending < first_alias.observation.coverage.urls_pending
    assert len(second_alias.resources) > len(first_alias.resources)
    assert len(list((tmp_path / "out/alias/reports").glob("run_*.json"))) == 2
    assert executions == ["primary", "unverified_author"] * 2


def test_changed_completed_profile_is_rejected_before_writing_configs_or_executing(tmp_path):
    checkpoint, options, executions, _ = fixture(tmp_path)
    checkpoint, _ = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    config_path = tmp_path / "work/source_configs/primary.yaml"
    before = config_path.read_bytes()
    options["source_config"]["sources"][0]["seeds"] = ["https://example.test/new-scope"]
    with pytest.raises(ValueError, match="SOURCE_CONFIG_CHANGED primary"):
        run_checkpointed_sources(checkpoint=checkpoint, **options)
    assert config_path.read_bytes() == before
    assert executions == ["primary"]


def test_missing_donor_catalog_falls_back_to_real_acquisition(tmp_path):
    checkpoint, options, executions, _ = fixture(tmp_path)
    checkpoint, _ = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    path = tmp_path / "out/primary/state/resource_catalog/resume.json"
    path.write_text('{"source_id":"another"}', encoding="utf-8")
    _, report = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    assert executions == ["primary", "alias"]
    assert report["acquisitions_executed"] == 1
    assert not report["acquisitions_reused"]


def test_production_reuse_is_explicit_and_excludes_uncertain_author():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config/source_execution_map.yaml").read_text(encoding="utf-8"))
    enabled = {row["logical_source_id"] for row in config["sources"] if row.get("reuse_acquisition")}
    assert enabled == {"aps_soat", "asfi", "asfi_finrural", "asfi_bcb", "mdryt"}
