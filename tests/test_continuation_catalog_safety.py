"""Comparabilidad de ausencias y secuencias de cadencia ante evidencia incompleta."""
from datetime import datetime, timedelta, timezone

import pytest

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.domain.health import HealthStateMachine
from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.domain.models import ResourceCandidate, SourceConfig, Source
from prospector_externo.domain.observations import CoverageStats, SourceRunObservation
from prospector_externo.kernel.contracts import ExtractionResult


@pytest.mark.parametrize("change", [
    {"seeds": ["https://example.test/new"]}, {"max_depth": 1},
    {"allowed_hosts": ["cdn.example.test"]}, {"excluded_path_keywords": ["/old/"]},
])
def test_changed_scope_resets_absences_and_requires_three_comparable_runs(tmp_path, change):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    service = CatalogApplicationService(repo)
    cfg = SourceConfig(source_id="scope", entrypoint="https://example.test")
    resource = ResourceCandidate(source_id="scope", resource_key="doc", url="https://example.test/a.pdf")
    def reconcile(config, run, resources):
        return service.reconcile_and_checkpoint(config, run, ExtractionResult(
            source_id="scope", resources=resources, coverage=CoverageStats(stop_reason="QUEUE_EXHAUSTED"),
        ))
    reconcile(cfg, "baseline", [resource])
    reconcile(cfg, "absence_one", [])
    reconcile(cfg, "absence_two", [])
    assert repo.get_resource_catalog("scope")[0].consecutive_absences == 2
    updated = cfg.model_copy(update=change)
    observation = reconcile(updated, "new_scope", [])
    assert repo.get_resource_catalog("scope")[0].consecutive_absences == 0
    assert len(repo.list_resources("scope")) == 1
    assert observation.discovery_profile_fingerprint == DiscoveryFrontier.profile_fingerprint(updated)
    assert observation.discovery_profile_fingerprint != DiscoveryFrontier.profile_fingerprint(cfg)
    for i in range(1, 4):
        reconcile(updated, f"new_absence_{i}", [])
        assert repo.get_resource_catalog("scope")[0].consecutive_absences == i
        assert len(repo.list_resources("scope")) == (1 if i < 3 else 0)
    assert repo.get_source("scope").catalog_scope_fingerprint == observation.discovery_profile_fingerprint


@pytest.mark.parametrize("status,content", [
    ("FAILED", "NOT_EVALUATED"), ("PARTIAL_SUCCESS", "NO_CHANGE"), ("SUCCESS", "EMPTY_RESULT"),
])
def test_failure_partial_and_empty_break_consecutive_no_change_sequence(tmp_path, status, content):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    cfg = SourceConfig(source_id="cadence", entrypoint="https://example.test")
    repo.save_source(Source(source_id="cadence", name="Cadence", entrypoint=cfg.entrypoint,
                            workflow="html", consecutive_no_change_count=4))
    evaluator = CadencePolicyEvaluator(repo)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    interrupted = SourceRunObservation(source_id="cadence", run_id="break", workflow="html",
                                      execution_status=status, content_status=content, observed_at=start)
    evaluator.evaluate_and_update(cfg, interrupted)
    evaluator.evaluate_and_update(cfg, interrupted)  # Idempotencia incluso tras el reset.
    for i in range(1, 6):
        evaluator.evaluate_and_update(cfg, SourceRunObservation(
            source_id="cadence", run_id=f"after_{i}", workflow="html", execution_status="SUCCESS",
            content_status="NO_CHANGE", observed_at=start + timedelta(days=i),
        ))
        source = repo.get_source("cadence")
        assert source.consecutive_no_change_count == i
        assert source.update_category == ("DAILY" if i < 5 else "WEEKLY")


def test_two_confirmed_failures_over_24_hours_suspend_and_full_success_resets(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    cfg = SourceConfig(source_id="health", entrypoint="https://example.test")
    repo.save_source(Source(source_id="health", name="Health", entrypoint=cfg.entrypoint, workflow="html"))
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    evaluator = CadencePolicyEvaluator(repo)
    for run_id, when in [("first", start), ("second", start + timedelta(hours=25))]:
        evaluator.evaluate_and_update(cfg, SourceRunObservation(
            source_id="health", run_id=run_id, workflow="html", execution_status="FAILED",
            content_status="NOT_EVALUATED", failure_code="TIMEOUT", observed_at=when,
        ))
    source = repo.get_source("health")
    assert source.consecutive_failures == 2
    assert source.first_failure_at == start
    assert source.health_status == "SUSPENDED"
    evaluator.evaluate_and_update(cfg, SourceRunObservation(
        source_id="health", run_id="recovered", workflow="html", execution_status="SUCCESS",
        content_status="CHANGED", observed_at=start + timedelta(hours=26),
    ))
    assert repo.get_source("health").first_failure_at is None
    assert repo.get_source("health").consecutive_failures == 0


def test_elapsed_time_alone_without_new_failure_does_not_invent_suspension():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    health = HealthStateMachine()
    assert health.record_failure("TIMEOUT", at=start).value == "WARNING"
    health.record_success()
    assert health.record_failure("TIMEOUT", at=start + timedelta(days=2)).value == "WARNING"
