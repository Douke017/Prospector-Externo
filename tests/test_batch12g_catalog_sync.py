"""Contrato 12G: JSON por fuente -> Mongo sin recrawling ni datos globales obsoletos."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.catalog_sync.main import main as sync_cli
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.catalog_sync import collect_evidence, sync_evidence
from prospector_externo.domain.models import ResourceCandidate, Snapshot, Source
from prospector_externo.domain.observations import (
    ContentStatus, ExecutionStatus, Run, RunReport, RunStatus, SourceRunObservation,
)


class SpyMongo:
    def __init__(self):
        self.sources = {}
        self.runs = {}
        self.snapshots = {}
        self.reports = {}
        self.observations = {}
        self.catalogs = {}

    def get_source(self, source_id):
        return self.sources.get(source_id)

    def save_source(self, value):
        self.sources[value.source_id] = value.model_copy(deep=True)

    def save_run(self, value):
        self.runs[value.run_id] = value.model_copy(deep=True)

    def save_snapshot(self, value):
        self.snapshots[(value.source_id, value.run_id)] = value.model_copy(deep=True)

    def save_source_observation(self, value):
        self.observations[(value.source_id, value.run_id)] = value.model_copy(deep=True)

    def save_report(self, value):
        self.reports[value.run_id] = value.model_copy(deep=True)

    def save_resource_catalog(self, source_id, run_id, resources):
        self.catalogs[source_id] = (run_id, list(resources))


def make_evidence(tmp_path: Path, logical_id="fam", physical_id="fam", run_id="run_12g_test", *, when=None):
    when = when or datetime(2026, 10, 21, tzinfo=timezone.utc)
    root = tmp_path / "output" / "checkpointed" / "demo" / logical_id
    shared = tmp_path / "shared"
    repo = LocalJsonRepositoryAdapter(root, catalog_dir=shared)
    source = Source(
        source_id=physical_id, name="Fuente prueba", entrypoint="https://example.test",
        workflow="html", last_run_at=when, last_run_id=run_id, updated_at=when,
    )
    repo.save_source(source)
    run = Run(
        run_id=run_id, started_at=when, finished_at=when + timedelta(seconds=1),
        status=RunStatus.COMPLETED, sources_selected=1, sources_processed=1,
        selected_source_ids=[physical_id],
    )
    repo.save_run(run)
    row = ResourceCandidate(resource_key="doc1", source_id=physical_id,
                            url="https://example.test/doc.pdf", file_extension=".pdf")
    snapshot = Snapshot(source_id=physical_id, run_id=run_id,
                        resources_hash="a" * 64, total_resources=1,
                        resources=[row], captured_at=when)
    repo.save_snapshot(snapshot)
    obs = SourceRunObservation(source_id=physical_id, run_id=run_id,
                               workflow="html", execution_status=ExecutionStatus.SUCCESS,
                               content_status=ContentStatus.CHANGED, observed_at=when)
    repo.save_source_observation(obs)
    repo.save_report(RunReport(run_id=run_id, started_at=when,
                               finished_at=when + timedelta(seconds=1),
                               sources_selected=1, sources_processed=1,
                               sources_with_changes=1, source_results=[obs]))
    repo.save_resource_catalog(physical_id, run_id, [row])
    return root


def test_shared_json_mirrors_reconciled_catalog_and_offline_preflight(tmp_path):
    root = make_evidence(tmp_path)
    copied = root / "state" / "resource_catalog" / "fam.json"
    assert copied.exists()
    assert json.loads(copied.read_text(encoding="utf-8"))["run_id"] == "run_12g_test"
    rows = collect_evidence(tmp_path / "output" / "checkpointed", "demo")
    assert len(rows) == 1
    assert rows[0].resources and len(rows[0].resources) == 1
    assert sync_cli(["--pipeline-run-id", "demo", "--output-root",
                     str(tmp_path / "output" / "checkpointed"), "--check"]) == 0
    assert sync_cli(["--evidence-dir", str(root), "--check"]) == 0


def test_sync_reuses_json_and_is_idempotent(tmp_path):
    make_evidence(tmp_path)
    rows = collect_evidence(tmp_path / "output" / "checkpointed", "demo")
    target = SpyMongo()
    one = sync_evidence(target, rows)
    two = sync_evidence(target, rows)
    assert one == two
    assert len(target.sources) == len(target.runs) == len(target.snapshots) == 1
    assert len(target.reports) == len(target.observations) == len(target.catalogs) == 1
    assert target.catalogs["fam"][1][0].url == "https://example.test/doc.pdf"


def test_sync_rejects_missing_reconciled_evidence_no_fallback_to_raw(tmp_path):
    root = make_evidence(tmp_path)
    (root / "state" / "resource_catalog" / "fam.json").unlink()
    with pytest.raises(ValueError, match="Falta catálogo reconciliado"):
        collect_evidence(tmp_path / "output" / "checkpointed", "demo")


def test_sync_rejects_wrong_run_catalog(tmp_path):
    root = make_evidence(tmp_path)
    p = root / "state" / "resource_catalog" / "fam.json"
    obj = json.loads(p.read_text(encoding="utf-8"))
    obj["run_id"] = "run_from_another_batch"
    p.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="no pertenece a esta corrida"):
        collect_evidence(tmp_path / "output" / "checkpointed", "demo")


def test_sync_refuses_overwrite_newer_mongo_source(tmp_path):
    make_evidence(tmp_path)
    rows = collect_evidence(tmp_path / "output" / "checkpointed", "demo")
    target = SpyMongo()
    target.sources["fam"] = rows[0].source.model_copy(update={
        "last_run_at": rows[0].source.last_run_at + timedelta(days=1),
        "last_run_id": "newer",
    })
    with pytest.raises(ValueError, match="STALE_EVIDENCE"):
        sync_evidence(target, rows)
    assert target.sources["fam"].last_run_id == "newer"
    assert target.runs == {}


def test_partial_resume_imports_only_finished_sources(tmp_path):
    make_evidence(tmp_path, logical_id="fam", physical_id="fam")
    (tmp_path / "output" / "checkpointed" / "demo" / "fmi").mkdir()
    rows = collect_evidence(tmp_path / "output" / "checkpointed", "demo")
    assert [r.logical_source_id for r in rows] == ["fam"]
    assert len(sync_evidence(SpyMongo(), rows)["results"]) == 1


def test_sync_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="--pipeline-run-id"):
        collect_evidence(tmp_path, "../other")


def test_shared_physical_source_keeps_latest_live_catalog_on_retry(tmp_path):
    t0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
    make_evidence(tmp_path, logical_id="bcb", physical_id="bcb", run_id="run_first", when=t0)
    make_evidence(tmp_path, logical_id="asfi_bcb", physical_id="bcb",
                  run_id="run_second", when=t0 + timedelta(hours=1))
    rows = collect_evidence(tmp_path / "output" / "checkpointed", "demo")
    target = SpyMongo()
    for _ in range(2):
        response = sync_evidence(target, rows)
    assert response["sources_imported"] == 2
    assert len(target.runs) == len(target.reports) == len(target.snapshots) == 2
    assert target.sources["bcb"].last_run_id == "run_second"
    assert target.catalogs["bcb"][0] == "run_second"


def test_resumed_source_syncs_latest_attempt_and_preserves_history(tmp_path):
    t0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
    root = make_evidence(tmp_path, run_id="run_z_old", when=t0)
    historical = root / "reports/run_run_z_old.json"
    before = historical.read_bytes()
    make_evidence(tmp_path, run_id="run_a_new", when=t0 + timedelta(hours=1))
    rows = collect_evidence(tmp_path / "output/checkpointed", "demo")
    assert [r.run.run_id for r in rows] == ["run_a_new"]
    target = SpyMongo()
    assert sync_evidence(target, rows) == sync_evidence(target, rows)
    assert target.catalogs["fam"][0] == "run_a_new"
    assert historical.read_bytes() == before
    assert (root / "state/snapshots/fam_run_z_old.json").exists()


def test_resumed_source_does_not_fall_back_to_previous_report(tmp_path):
    root = make_evidence(tmp_path)
    source_path = root / "state/sources.json"
    data = json.loads(source_path.read_text(encoding="utf-8"))
    data["fam"]["last_run_id"] = "run_interrupted_without_report"
    source_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="último reporte"):
        collect_evidence(tmp_path / "output/checkpointed", "demo")


def test_latest_failed_attempt_is_not_replaced_with_old_success(tmp_path):
    t0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
    root = make_evidence(tmp_path, run_id="old", when=t0)
    make_evidence(tmp_path, run_id="new", when=t0 + timedelta(hours=1))
    repo = LocalJsonRepositoryAdapter(root)
    report = repo.get_report("new")
    report.sources_processed = 0
    report.sources_failed = 1
    report.source_results[0].execution_status = ExecutionStatus.FAILED
    report.source_results[0].failure_code = "HTTP_403"
    repo.save_source_observation(report.source_results[0])
    repo.save_report(report)
    run = repo.get_run("new")
    run.status = RunStatus.FAILED
    repo.save_run(run)
    rows = collect_evidence(tmp_path / "output/checkpointed", "demo")
    assert rows[0].run.run_id == "new"
    assert rows[0].resources is None
    assert rows[0].observation.execution_status == ExecutionStatus.FAILED


@pytest.mark.parametrize("changes", [
    {"status": "RUNNING"}, {"selected_source_ids": ["another_source"]},
])
def test_sync_rejects_incomplete_or_mismatched_run(tmp_path, changes):
    root = make_evidence(tmp_path)
    path = root / "state/runs/run_run_12g_test.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="no finalizada o asociada a otra fuente"):
        collect_evidence(tmp_path / "output/checkpointed", "demo")
