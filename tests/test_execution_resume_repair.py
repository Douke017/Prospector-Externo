"""Focused Windows persistence and same-pipeline recovery regressions; no public HTTP."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest
import yaml

from apps.b13_pipeline.main import inspect_and_project
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.models import ResourceCandidate, Snapshot, Source
from prospector_externo.domain.observations import Run, RunStatus
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from src.execution.checkpointed_batch import run_checkpointed_sources
from src.persistence.model import FAILED, RUNNING, SKIPPED_STATUS, SUCCEEDED, transition
from test_physical_acquisition_reuse import fixture


@pytest.mark.parametrize("sid,operation", [("asfi_valores", "discovery"), ("statistics_denmark", "snapshot")])
def test_long_windows_path_with_spaces_keeps_temporaries_short(tmp_path, monkeypatch, sid, operation):
    """Reproduce the original absolute parent lengths even with a short pytest root."""
    import tempfile
    rid = "run_20261023_222232_11b620"
    # Targets fit MAX_PATH; copying their basename into the temp prefix did not.
    target_parent_length = 214 if operation == "discovery" else 202
    suffix = Path(sid) / "state" / (f"discovery_progress/{sid}" if operation == "discovery" else "snapshots")
    pad = target_parent_length - len(str(tmp_path / suffix)) - 1
    assert pad > 0
    base = tmp_path / ("path with spaces " + "x" * (pad - len("path with spaces "))) / sid
    repo = LocalJsonRepositoryAdapter(base, catalog_dir=tmp_path / "catalog")
    original = tempfile.NamedTemporaryFile
    attempted = []
    def constrained_temp(*args, **kwargs):
        # Also model Windows' limit on non-Windows CI; no FS mocking of content.
        length = len(str(Path(kwargs["dir"]).resolve() / (kwargs.get("prefix", "tmp") + "12345678" + kwargs.get("suffix", ""))))
        attempted.append(length)
        if length >= 260:
            raise FileNotFoundError("Windows MAX_PATH temp filename")
        return original(*args, **kwargs)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", constrained_temp)
    if operation == "discovery":
        value = {"source_id": sid, "last_run_id": rid, "fixture": True}
        repo.save_discovery_checkpoint(sid, value)
        assert repo.get_discovery_checkpoint(sid) == value
        archived = base / "state/discovery_progress" / sid / f"{rid}.json"
        assert json.loads(archived.read_text(encoding="utf-8")) == value
    else:
        snap = Snapshot(source_id=sid, run_id=rid, resources_hash="hash", total_resources=1,
                        resources=[ResourceCandidate(source_id=sid, resource_key="key", url="https://example.test/a.pdf")])
        repo.save_snapshot(snap)
        mirror = base / "state/snapshots" / f"{sid}_{rid}.json"
        assert Snapshot.model_validate_json(mirror.read_text(encoding="utf-8")) == snap
    assert attempted and max(attempted) < 260


def api_runner(tmp_path, monkeypatch, *, fail_snapshot=False):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [{"source_id": "statistics_denmark", "workflow": "api",
        "entrypoint": "https://api.example.test/v1/tables?lang=en", "seeds": ["https://api.example.test/v1/tables?lang=en"],
        "api_seed_scope": "curated", "rate_limit_seconds": 0}]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=[{"id": "TABLE", "text": "Public table"}])
    runner = RunOrchestrator(repo, repo, cfg,
        http_runtime_factory=lambda: AsyncHttpRuntime(transport=httpx.MockTransport(handler)))
    if fail_snapshot:
        def fail(*args, **kwargs):
            raise FileNotFoundError("fixture snapshot mirror after HTTP 200")
        monkeypatch.setattr(repo, "save_snapshot", fail)
    return runner, repo, calls


def test_http_200_followed_by_persistence_error_is_reported_failed_and_retryable(tmp_path, monkeypatch):
    runner, repo, calls = api_runner(tmp_path, monkeypatch, fail_snapshot=True)
    report = runner.run_batch(resume_discovery=True, pipeline_run_id="same-pipeline")
    assert "/v1/tables" in calls
    assert report.sources_failed == 1 and report.sources_processed == 0
    obs = report.source_results[0]
    assert obs.execution_status.value == "FAILED" and obs.failure_code == "SOURCE_PERSISTENCE_ERROR"
    assert obs.coverage.requests_total > 0
    assert repo.get_run(report.run_id).status == RunStatus.FAILED
    assert repo.get_report(report.run_id) == report
    assert repo.get_source_observations("statistics_denmark")[0] == obs
    assert repo.get_latest_snapshot("statistics_denmark") is None


@pytest.mark.parametrize("same_pipeline,suspended,expected", [(True, False, "PARTIAL_SUCCESS"), (False, False, "SKIPPED"), (True, True, "SKIPPED")])
def test_child_recovery_respects_pipeline_identity_and_suspension(tmp_path, monkeypatch, same_pipeline, suspended, expected):
    runner, repo, calls = api_runner(tmp_path, monkeypatch)
    repo.save_source(Source(source_id="statistics_denmark", name="Statistics", workflow="api", entrypoint="https://api.example.test/",
        health_status="SUSPENDED" if suspended else "WARNING", last_run_id="previous",
        next_eligible_at=datetime.now(timezone.utc) + timedelta(hours=4)))
    repo.save_run(Run(run_id="previous", pipeline_run_id="same-pipeline" if same_pipeline else "other-pipeline",
        status="FAILED", started_at=datetime.now(timezone.utc), selected_source_ids=["statistics_denmark"], sources_selected=1))
    report = runner.run_batch(resume_discovery=True, pipeline_run_id="same-pipeline")
    assert report.source_results[0].execution_status.value == expected
    assert bool(calls) == (expected == "PARTIAL_SUCCESS")
    if suspended:
        assert report.source_results[0].skip_reason.startswith("SUSPENDED:")


@pytest.mark.parametrize("state", ["failed", "legacy_skip", "interrupted"])
def test_parent_resume_recovers_failed_and_false_not_due_skip_without_recrawling_complete(tmp_path, state):
    checkpoint, options, _, _ = fixture(tmp_path)
    # Establish the actual acquisition config fingerprints via one bounded attempt.
    options["executor"] = lambda **kwargs: 2
    checkpoint, _ = run_checkpointed_sources(checkpoint=checkpoint, max_sources=1, **options)
    checkpoint = transition(checkpoint, "unverified_author", RUNNING)
    checkpoint = transition(checkpoint, "unverified_author", SUCCEEDED)
    original_complete = json.loads(json.dumps(checkpoint["sources"]["unverified_author"]))
    repo = LocalJsonRepositoryAdapter(options["catalog_dir"])
    repo.save_source(Source(source_id="resume", name="Source", workflow="html", entrypoint="https://example.test/",
        health_status="WARNING", next_eligible_at=datetime.now(timezone.utc) + timedelta(hours=4)))
    if state == "legacy_skip":
        checkpoint = transition(checkpoint, "primary", SKIPPED_STATUS,
            metadata={"skip_reason": "NOT_DUE: old false terminal skip", "skip_report_run_id": "preserved-skip"})
    elif state == "interrupted":
        # Emulate a stored RUNNING attempt whose worker died before result commit.
        checkpoint["sources"]["primary"]["state"] = RUNNING
    attempted = []
    def execute(**kwargs):
        assert kwargs["force"] is False
        attempted.append(kwargs["output_dir"].name)
        return 0
    options["executor"] = execute
    resumed, report = run_checkpointed_sources(checkpoint=checkpoint, resume=True, respect_eligibility=True,
        selected_source_ids=["primary", "unverified_author"], **options)
    assert attempted == ["primary"]
    assert resumed["sources"]["primary"]["state"] == SUCCEEDED
    assert resumed["sources"]["primary"]["attempts"] == 2
    assert resumed["sources"]["unverified_author"] == original_complete
    assert report["recovered_eligibility_skips"] == (["primary"] if state == "legacy_skip" else [])
    assert report["recovered_interrupted"] == (["primary"] if state == "interrupted" else [])


def test_pending_source_in_periodic_round_keeps_cadence_and_no_acquisition(tmp_path):
    checkpoint, options, _, _ = fixture(tmp_path)
    repo = LocalJsonRepositoryAdapter(options["catalog_dir"])
    repo.save_source(Source(source_id="resume", name="Source", workflow="html", entrypoint="https://example.test/",
        next_eligible_at=datetime.now(timezone.utc) + timedelta(hours=4)))
    def prohibited(**kwargs):
        pytest.fail("periodic NOT_DUE source must not acquire")
    options["executor"] = prohibited
    checkpoint, report = run_checkpointed_sources(checkpoint=checkpoint, respect_eligibility=True,
        selected_source_ids=["primary"], **options)
    assert checkpoint["sources"]["primary"]["state"] == SKIPPED_STATUS
    assert report["acquisitions_executed"] == 0


def test_failed_checkpoint_without_report_is_not_presented_as_pending(tmp_path):
    path = tmp_path / ".runtime/b13_pipeline/failed/checkpoint.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"sources": {"statistics_denmark": {"state": FAILED,
        "last_error": "crawler_batch_exit_code_1", "metadata": {"config_source_id": "statistics_denmark"}}}}), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="failed", roster=["statistics_denmark", "untouched"])
    assert rows[0]["classification"] == "EXECUTION_FAILED" and rows[0]["execution_status"] == "FAILED"
    assert rows[0]["artifact"] is None and rows[0]["legacy_records"] == 0
    assert rows[1]["classification"] == "PENDING"
