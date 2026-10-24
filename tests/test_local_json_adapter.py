"""
Pruebas unitarias para el adaptador de persistencia LocalJsonRepositoryAdapter.
Verifica exportación a los 3 formatos (Standard, Tree, Compact) y persistencia de reportes.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from prospector_externo.domain.models import Snapshot, ResourceCandidate, Source, SourceConfig
from prospector_externo.domain.observations import (
    RunReport, CoverageStats, SourceRunObservation, ExecutionStatus, ContentStatus
)
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.observations import CoverageStats


def test_partial_crawl_preserves_evidence_and_schedules_retry(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    config = SourceConfig(source_id="anapo", entrypoint="https://example.test",
                          update_category="MONTHLY")
    resource = ResourceCandidate(resource_key="doc", source_id="anapo",
                                 url="https://example.test/doc.pdf")
    observation = CatalogApplicationService(repo).reconcile_and_checkpoint(
        config, "partial-run", ExtractionResult(
            source_id="anapo", success=True, resources=[resource],
            coverage=CoverageStats(stop_reason="MAX_CONSECUTIVE_ERRORS", urls_pending=77),
        ),
    )
    assert observation.execution_status == ExecutionStatus.PARTIAL_SUCCESS
    assert observation.failure_code == "MAX_CONSECUTIVE_ERRORS"
    assert repo.get_latest_snapshot("anapo").total_resources == 1
    CadencePolicyEvaluator(repo).evaluate_and_update(config, observation)
    source = repo.get_source("anapo")
    assert source.health_status == "ACTIVE"
    assert datetime.now(timezone.utc) < source.next_eligible_at < datetime.now(timezone.utc) + timedelta(hours=5)


def test_local_json_adapter_snapshot_export(tmp_path: Path):
    adapter = LocalJsonRepositoryAdapter(base_output_dir=tmp_path)

    resource = ResourceCandidate(
        resource_key="test_source:123456",
        url="https://example.org/doc/reporte_2026.pdf",
        source_id="test_source",
        title="Reporte 2026",
        file_extension=".pdf",
        content_length_bytes=2048,
        discovered_at=datetime.now(timezone.utc)
    )

    snapshot = Snapshot(
        source_id="test_source",
        run_id="run_test_001",
        captured_at=datetime.now(timezone.utc),
        resources_hash="hash_test_123",
        total_resources=1,
        resources=[resource]
    )

    adapter.save_snapshot(snapshot)

    # Verificar que existen los 3 archivos en tmp_path/test_source/
    source_dir = tmp_path / "test_source"
    assert (source_dir / "mapa_test_source.json").exists()
    assert (source_dir / "mapa_test_source_tree.json").exists()
    assert (source_dir / "mapa_test_source_compact.json").exists()

    # Validar formato estándar
    with open(source_dir / "mapa_test_source.json", "r", encoding="utf-8") as f:
        std_data = json.load(f)
    assert std_data["version"] == "1.0.0"
    assert std_data["source"]["id"] == "test_source"
    assert len(std_data["datasets"][0]["resources"]) == 1
    assert std_data["datasets"][0]["resources"][0]["file_extension"] == ".pdf"

    # Validar formato compacto
    with open(source_dir / "mapa_test_source_compact.json", "r", encoding="utf-8") as f:
        compact_data = json.load(f)
    assert compact_data["source_id"] == "test_source"
    assert len(compact_data["resources"]) == 1
    assert compact_data["resources"][0]["ext"] == ".pdf"

    # Validar formato árbol
    with open(source_dir / "mapa_test_source_tree.json", "r", encoding="utf-8") as f:
        tree_data = json.load(f)
    assert tree_data["type"] == "source"
    assert len(tree_data["children"]) == 1

    # Validar recuperación de último snapshot
    loaded_snap = adapter.get_latest_snapshot("test_source")
    assert loaded_snap is not None
    assert loaded_snap.run_id == "run_test_001"
    assert len(loaded_snap.resources) == 1


def test_local_json_adapter_run_report(tmp_path: Path):
    adapter = LocalJsonRepositoryAdapter(base_output_dir=tmp_path)

    report = RunReport(
        run_id="run_rep_001",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        sources_selected=1,
        sources_processed=1,
        sources_skipped=0,
        sources_failed=0,
        sources_with_changes=1,
        source_results=[
            SourceRunObservation(
                source_id="test_source",
                run_id="run_rep_001",
                workflow="html",
                execution_status=ExecutionStatus.SUCCESS,
                content_status=ContentStatus.CHANGED,
                coverage=CoverageStats(urls_discovered=10, resources_found=1)
            )
        ]
    )

    adapter.save_report(report)

    report_path = tmp_path / "reports" / "run_run_rep_001.json"
    assert report_path.exists()

    latest = adapter.get_report("run_rep_001")
    assert latest is not None
    assert latest.run_id == "run_rep_001"
    assert len(latest.source_results) == 1


def test_shared_catalog_preserves_b13_evidence_and_exact_latest_source(tmp_path: Path):
    catalog_root = tmp_path / "output"
    evidence_root = catalog_root / "checkpointed" / "b13-e2e" / "asfi"
    writer = LocalJsonRepositoryAdapter(evidence_root, catalog_dir=catalog_root)
    writer.save_source(Source(
        source_id="asfi_valores", name="ASFI Valores",
        entrypoint="https://example.org", workflow="html",
    ))
    now = datetime.now(timezone.utc)
    writer.save_snapshot(Snapshot(
        source_id="asfi_valores", run_id="z-older", captured_at=now,
        resources_hash="old", total_resources=1,
        resources=[ResourceCandidate(
            resource_key="doc", source_id="asfi_valores",
            url="https://example.org/doc.pdf",
        )],
    ))
    observation = SourceRunObservation(
        source_id="asfi_valores", run_id="z-older", workflow="html",
        execution_status=ExecutionStatus.SUCCESS,
        content_status=ContentStatus.CHANGED,
    )
    writer.save_source_observation(observation)
    writer.save_report(RunReport(
        run_id="z-older", started_at=now, source_results=[observation],
    ))

    api = LocalJsonRepositoryAdapter(catalog_root)
    b13 = LocalJsonRepositoryAdapter(evidence_root)
    for reader in (api, b13):
        assert [source.source_id for source in reader.list_sources()] == ["asfi_valores"]
        assert len(reader.list_resources()) == 1
        assert reader.get_report("z-older") is not None
        assert reader.get_source_observations("asfi_valores")

    api.save_snapshot(Snapshot(
        source_id="asfi_valores", run_id="a-newer",
        captured_at=now + timedelta(seconds=1),
        resources_hash="new", total_resources=0,
    ))
    api.save_source_observation(SourceRunObservation(
        source_id="asfi_valores", run_id="a-newer", workflow="html",
        execution_status=ExecutionStatus.SUCCESS,
        content_status=ContentStatus.NO_CHANGE,
        observed_at=observation.observed_at + timedelta(seconds=1),
    ))
    assert api.get_latest_snapshot("asfi_valores").run_id == "a-newer"
    assert api.get_latest_snapshot("asfi") is None
    assert [obs.run_id for obs in api.get_source_observations("asfi_valores")] == ["a-newer", "z-older"]
    assert api.get_source_observations("asfi") == []
    assert b13.get_latest_snapshot("asfi_valores").run_id == "z-older"


def test_health_failures_persist_across_runs_and_recover(tmp_path: Path):
    """La primera falla crea la fuente; tres fallas guardadas la suspenden."""
    config = SourceConfig(source_id="new_source", entrypoint="https://example.test",
                          update_category="DAILY")
    for attempt in (1, 2, 3):
        repo = LocalJsonRepositoryAdapter(tmp_path)
        observation = CatalogApplicationService(repo).reconcile_and_checkpoint(
            config, f"failure-{attempt}",
            ExtractionResult(source_id="new_source", success=False,
                             failure_code="TIMEOUT"),
        )
        assert observation.execution_status == ExecutionStatus.FAILED
        CadencePolicyEvaluator(repo).evaluate_and_update(config, observation)
        source = LocalJsonRepositoryAdapter(tmp_path).get_source("new_source")
        assert source is not None
        assert source.consecutive_failures == attempt
        assert source.health_status == ("SUSPENDED" if attempt == 3 else "WARNING")
        assert repo.get_latest_snapshot("new_source") is None

    # Tras recuperación completa (p. ej. ejecución forzada), se restablece la salud.
    repo = LocalJsonRepositoryAdapter(tmp_path)
    observation = CatalogApplicationService(repo).reconcile_and_checkpoint(
        config, "recovery", ExtractionResult(source_id="new_source", success=True,
                                               coverage=CoverageStats(stop_reason="QUEUE_EXHAUSTED")),
    )
    CadencePolicyEvaluator(repo).evaluate_and_update(config, observation)
    source = LocalJsonRepositoryAdapter(tmp_path).get_source("new_source")
    assert source.health_status == "ACTIVE"
    assert source.consecutive_failures == 0


def test_cadence_no_change_count_persists_between_runs(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    config = SourceConfig(source_id="cadence", entrypoint="https://example.test",
                          update_category="DAILY")
    repo.save_source(Source(source_id="cadence", name="Cadence",
                            entrypoint=config.entrypoint, workflow="html"))
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def evaluate(content, run, when):
        reopened = LocalJsonRepositoryAdapter(tmp_path)
        observation = SourceRunObservation(
            source_id="cadence", run_id=run, workflow="html",
            execution_status=ExecutionStatus.SUCCESS, content_status=content,
            observed_at=when,
        )
        CadencePolicyEvaluator(reopened).evaluate_and_update(config, observation)
        return LocalJsonRepositoryAdapter(tmp_path).get_source("cadence")

    for i in range(1, 6):
        source = evaluate(ContentStatus.NO_CHANGE, f"no-change-{i}",
                          initial + timedelta(days=i - 1))
        assert source.consecutive_no_change_count == i
    assert source.update_category == "WEEKLY"
    assert source.weekly_no_change_count == 0

    for i in range(1, 5):
        source = evaluate(ContentStatus.NO_CHANGE, f"weekly-{i}",
                          initial + timedelta(days=4, weeks=i))
        assert source.weekly_no_change_count == i
    assert source.update_category == "MONTHLY"
    assert source.consecutive_no_change_count == 9

    source = evaluate(ContentStatus.CHANGED, "change", initial + timedelta(weeks=6))
    assert source.update_category == "DAILY"
    assert source.consecutive_no_change_count == 0
    assert source.weekly_no_change_count == 0
    assert source.weekly_no_change_since_at is None


def test_empty_success_does_not_count_as_no_change(tmp_path: Path):
    config = SourceConfig(source_id="cadence", entrypoint="https://example.test")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(Source(source_id="cadence", name="Cadence",
                            entrypoint=config.entrypoint, workflow="html",
                            consecutive_no_change_count=4))
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    obs = SourceRunObservation(source_id="cadence", run_id="empty-1", workflow="html",
                               execution_status=ExecutionStatus.SUCCESS,
                               content_status=ContentStatus.EMPTY_RESULT, observed_at=t0)
    CadencePolicyEvaluator(repo).evaluate_and_update(config, obs)
    source = LocalJsonRepositoryAdapter(tmp_path).get_source('cadence')
    assert source.update_category == 'DAILY'
    assert source.consecutive_no_change_count == 0
    assert source.next_eligible_at == t0 + timedelta(hours=4)

    # Reintentar el mismo run_id tras reiniciar no debe duplicar la evaluación.
    obs2 = obs.model_copy(update={'content_status': ContentStatus.NO_CHANGE})
    CadencePolicyEvaluator(LocalJsonRepositoryAdapter(tmp_path)).evaluate_and_update(config, obs2)
    source = LocalJsonRepositoryAdapter(tmp_path).get_source('cadence')
    assert source.consecutive_no_change_count == 0
    assert source.last_policy_run_id == 'empty-1'


def test_failed_and_partial_never_advance_no_change_counter(tmp_path: Path):
    config = SourceConfig(source_id="cadence", entrypoint="https://example.test")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(Source(source_id="cadence", name="Cadence",
                            entrypoint=config.entrypoint, workflow="html",
                            consecutive_no_change_count=4))
    t0 = datetime(2026, 3, 5, tzinfo=timezone.utc)
    for state, run_id in ((ExecutionStatus.FAILED, 'failed-1'),
                          (ExecutionStatus.PARTIAL_SUCCESS, 'partial-1')):
        obs = SourceRunObservation(source_id="cadence", run_id=run_id, workflow="html",
                                   execution_status=state,
                                   content_status=ContentStatus.NO_CHANGE,
                                   observed_at=t0)
        CadencePolicyEvaluator(LocalJsonRepositoryAdapter(tmp_path)).evaluate_and_update(config, obs)
        source = LocalJsonRepositoryAdapter(tmp_path).get_source('cadence')
        assert source.update_category == 'DAILY'
        assert source.consecutive_no_change_count == 0


def test_partial_retains_health_but_breaks_no_change_sequence(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    config = SourceConfig(source_id="partial", entrypoint="https://example.test")
    repo.save_source(Source(
        source_id="partial", name="Partial", entrypoint=config.entrypoint,
        workflow="html", health_status="WARNING", consecutive_failures=2,
        consecutive_no_change_count=6,
    ))
    observation = SourceRunObservation(
        source_id="partial", run_id="partial-1", workflow="html",
        execution_status=ExecutionStatus.PARTIAL_SUCCESS,
        content_status=ContentStatus.CHANGED,
        failure_code="REQUEST_BUDGET_REACHED",
    )
    CadencePolicyEvaluator(repo).evaluate_and_update(config, observation)
    source = LocalJsonRepositoryAdapter(tmp_path).get_source("partial")
    assert source.health_status == "WARNING"
    assert source.consecutive_failures == 2
    assert source.consecutive_no_change_count == 0
    assert source.update_category == "DAILY"
    assert datetime.now(timezone.utc) < source.next_eligible_at < datetime.now(timezone.utc) + timedelta(hours=5)


def _resource_for_lifecycle(key: str, *, etag: str = 'etag1') -> ResourceCandidate:
    return ResourceCandidate(
        resource_key=key, source_id='lifecycle',
        url=f'https://example.test/{key}.pdf', etag=etag,
    )


def _run_lifecycle(root: Path, run_id: str, resources, *, reason='QUEUE_EXHAUSTED',
                   success=True, pending=0, failed_urls=0):
    repo = LocalJsonRepositoryAdapter(root)
    config = SourceConfig(source_id='lifecycle', entrypoint='https://example.test')
    obs = CatalogApplicationService(repo).reconcile_and_checkpoint(
        config, run_id,
        ExtractionResult(
            source_id='lifecycle', success=success, resources=resources,
            failure_code='TIMEOUT' if not success else None,
            coverage=CoverageStats(stop_reason=reason, urls_pending=pending,
                                   urls_failed=failed_urls),
        ),
    )
    return repo, obs


def test_resource_three_confirmed_absences_without_mutating_raw(tmp_path: Path):
    repo, _ = _run_lifecycle(tmp_path, 'run-0', [_resource_for_lifecycle('doc')])
    assert len(repo.list_resources('lifecycle')) == 1
    for attempt in (1, 2, 3):
        repo, obs = _run_lifecycle(tmp_path, f'absent-{attempt}', [])
        current = repo.get_resource_catalog('lifecycle')
        assert len(current) == 1
        assert current[0].consecutive_absences == attempt
        assert current[0].change_status == (
            'REMOVED' if attempt == 3 else 'NOT_EVALUATED'
        )
        assert len(repo.list_resources('lifecycle')) == (0 if attempt == 3 else 1)
        assert repo.get_latest_snapshot('lifecycle').resources == []
        assert repo.get_latest_snapshot('lifecycle').run_id == f'absent-{attempt}'
        assert obs.content_status == (
            ContentStatus.CHANGED if attempt == 3 else ContentStatus.EMPTY_RESULT
        )
    # La evidencia original sigue disponible sin copias sintéticas de recursos.
    old_snap = Snapshot.model_validate(json.loads(
        (tmp_path / 'state' / 'snapshots' / 'lifecycle_run-0.json').read_text()
    ))
    assert [x.resource_key for x in old_snap.resources] == ['doc']


def test_partial_failure_and_unknown_coverage_never_confirm_absences(tmp_path: Path):
    _run_lifecycle(tmp_path, 'initial', [_resource_for_lifecycle('a'),
                                     _resource_for_lifecycle('b')])
    for run, kwargs in (
        ('partial-budget', {'reason': 'REQUEST_BUDGET_REACHED', 'pending': 4}),
        ('partial-errors', {'reason': 'MAX_CONSECUTIVE_ERRORS', 'failed_urls': 1}),
        ('unknown-scope', {'reason': None}),
        ('failed', {'success': False}),
    ):
        repo, obs = _run_lifecycle(tmp_path, run, [], **kwargs)
        assert set(x.resource_key for x in repo.list_resources('lifecycle')) == {'a', 'b'}
        assert all(x.consecutive_absences == 0 for x in repo.get_resource_catalog('lifecycle'))
        assert obs.execution_status in {ExecutionStatus.PARTIAL_SUCCESS,
                                        ExecutionStatus.SUCCESS, ExecutionStatus.FAILED}
    assert repo.get_latest_snapshot('lifecycle').run_id == 'unknown-scope'


def test_reappearance_clears_absence_and_does_not_fake_changes(tmp_path: Path):
    _run_lifecycle(tmp_path, 'start', [_resource_for_lifecycle('doc')])
    _run_lifecycle(tmp_path, 'absent-once', [])
    repo, obs = _run_lifecycle(tmp_path, 'return', [_resource_for_lifecycle('doc')])
    state = repo.get_resource_catalog('lifecycle')[0]
    assert state.consecutive_absences == 0
    assert state.change_status == 'UNCHANGED'
    assert obs.content_status == ContentStatus.NO_CHANGE
    _run_lifecycle(tmp_path, 'absent-again', [])
    repo, _ = _run_lifecycle(tmp_path, 'partial-again', [], reason='MAX_URLS', pending=3)
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 1
    repo, obs = _run_lifecycle(tmp_path, 'changed-return', [_resource_for_lifecycle('doc', etag='etag2')])
    assert obs.content_status == ContentStatus.CHANGED
    assert repo.get_resource_catalog('lifecycle')[0].change_status == 'MODIFIED'
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0


def test_repeated_run_id_does_not_increase_absence(tmp_path: Path):
    _run_lifecycle(tmp_path, 'start', [_resource_for_lifecycle('doc')])
    _, original = _run_lifecycle(tmp_path, 'repeated', [])
    repo, duplicate = _run_lifecycle(tmp_path, 'repeated', [])
    assert duplicate.observation_id == original.observation_id
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 1


def test_legacy_latest_snapshot_is_baseline_for_initial_catalog(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(Source(source_id='lifecycle', name='Legacy',
                            entrypoint='https://example.test', workflow='html'))
    repo.save_snapshot(Snapshot(
        source_id='lifecycle', run_id='historic', resources_hash='historic',
        total_resources=1, resources=[_resource_for_lifecycle('historic')],
    ))
    assert repo.get_resource_catalog('lifecycle') is None
    assert len(repo.list_resources('lifecycle')) == 1
    repo, obs = _run_lifecycle(tmp_path, 'upgrade', [], reason='QUEUE_EXHAUSTED')
    assert obs.content_status == ContentStatus.EMPTY_RESULT
    # El snapshot legacy no registra el alcance: la primera reconciliación lo
    # establece sin inventar una ausencia comparable.
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0
    assert len(repo.list_resources('lifecycle')) == 1


def test_foreign_source_or_duplicate_resource_rejected_before_snapshot(tmp_path: Path):
    import pytest
    config = SourceConfig(source_id='lifecycle', entrypoint='https://example.test')
    repo = LocalJsonRepositoryAdapter(tmp_path)
    foreign = ResourceCandidate(resource_key='bad', source_id='other', url='https://example.test')
    for resources in ([foreign], [_resource_for_lifecycle('x'), _resource_for_lifecycle('x')]):
        with pytest.raises(ValueError):
            CatalogApplicationService(repo).reconcile_and_checkpoint(
                config, 'invalid', ExtractionResult(source_id='lifecycle', success=True,
                    resources=resources, coverage=CoverageStats(stop_reason='QUEUE_EXHAUSTED')),
            )
        assert repo.get_latest_snapshot('lifecycle') is None


def test_crash_after_catalog_write_before_observation_does_not_count_twice(tmp_path: Path):
    _run_lifecycle(tmp_path, 'initial', [_resource_for_lifecycle('doc')])
    _run_lifecycle(tmp_path, 'crash-demo', [])
    # Representa pérdida de observación luego de haber confirmado el catálogo.
    (tmp_path / 'state' / 'observations' / 'lifecycle_crash-demo.json').unlink()
    repo, _ = _run_lifecycle(tmp_path, 'crash-demo', [])
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 1
    assert repo.get_source_observations('lifecycle', limit=1)[0].run_id == 'crash-demo'


def test_reappearance_after_confirmed_removal_returns_to_catalog(tmp_path: Path):
    _run_lifecycle(tmp_path, 'initial', [_resource_for_lifecycle('doc')])
    for attempt in range(1, 4):
        _run_lifecycle(tmp_path, f'absent-{attempt}', [])
    repo, obs = _run_lifecycle(tmp_path, 'rediscovered', [_resource_for_lifecycle('doc')])
    assert repo.list_resources('lifecycle')[0].change_status == 'NEW'
    assert repo.list_resources('lifecycle')[0].consecutive_absences == 0
    assert obs.content_status == ContentStatus.CHANGED


def test_partial_with_new_documents_preserves_unseen_prior_documents(tmp_path: Path):
    _run_lifecycle(tmp_path, 'initial', [_resource_for_lifecycle('old')])
    repo, obs = _run_lifecycle(
        tmp_path, 'partial-new', [_resource_for_lifecycle('new')],
        reason='REQUEST_BUDGET_REACHED', pending=8,
    )
    assert obs.execution_status == ExecutionStatus.PARTIAL_SUCCESS
    assert obs.content_status == ContentStatus.CHANGED
    assert {r.resource_key for r in repo.list_resources('lifecycle')} == {'old', 'new'}
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0
    assert [r.resource_key for r in repo.get_latest_snapshot('lifecycle').resources] == ['new']


def test_queue_exhausted_with_blocked_resources_is_not_removal_proof(tmp_path: Path):
    _run_lifecycle(tmp_path, 'initial', [_resource_for_lifecycle('doc')])
    repo = LocalJsonRepositoryAdapter(tmp_path)
    obs = CatalogApplicationService(repo).reconcile_and_checkpoint(
        SourceConfig(source_id='lifecycle', entrypoint='https://example.test'),
        'blocked', ExtractionResult(source_id='lifecycle', success=True, resources=[],
            coverage=CoverageStats(stop_reason='QUEUE_EXHAUSTED', robots_disallowed=1)),
    )
    assert obs.content_status == ContentStatus.EMPTY_RESULT
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0
    assert len(repo.list_resources('lifecycle')) == 1


def test_partial_evidence_prevents_false_removal_and_cadence_advance(tmp_path: Path):
    """Anomalías con cola agotada no equivalen a un recorrido comprobablemente completo."""
    cases = (
        {'stop_reason': 'QUEUE_EXHAUSTED', 'http_403': 1},
        {'stop_reason': 'QUEUE_EXHAUSTED', 'http_429': 1},
        {'stop_reason': 'QUEUE_EXHAUSTED', 'timeouts': 1},
        {'stop_reason': 'QUEUE_EXHAUSTED', 'robots_disallowed': 1},
        {'stop_reason': 'QUEUE_EXHAUSTED', 'pagination_families_stopped': 1},
        {'stop_reason': 'QUEUE_EXHAUSTED', 'resources_limit_reached': 1},
        {'stop_reason': 'API_SEEDS_EXHAUSTED', 'api_auth_required': 1},
        {'stop_reason': 'API_SEEDS_EXHAUSTED', 'api_pagination_stopped': 1},
        {'stop_reason': 'API_SEEDS_EXHAUSTED', 'api_document_errors': 1},
        {'stop_reason': 'API_SEEDS_EXHAUSTED', 'api_unresolved_operations': 1},
        {'stop_reason': 'UNKNOWN_ABORT', 'urls_pending': 0},
        {'pages_visited': 2},  # Visitas pero falta una razón de cierre verificable.
    )
    from prospector_externo.domain.observations import CoverageStats
    config = SourceConfig(source_id='lifecycle', entrypoint='https://example.test')
    for i, kwargs in enumerate(cases):
        root = tmp_path / f'case-{i}'
        _run_lifecycle(root, 'baseline', [_resource_for_lifecycle('doc')])
        repo = LocalJsonRepositoryAdapter(root)
        observation = CatalogApplicationService(repo).reconcile_and_checkpoint(
            config, f'limited-{i}', ExtractionResult(source_id='lifecycle', success=True,
                resources=[], coverage=CoverageStats(**kwargs)))
        assert observation.execution_status == ExecutionStatus.PARTIAL_SUCCESS, kwargs
        assert observation.content_status == ContentStatus.EMPTY_RESULT
        assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0
        assert len(repo.list_resources('lifecycle')) == 1
        CadencePolicyEvaluator(repo).evaluate_and_update(config, observation)
        assert repo.get_source('lifecycle').next_eligible_at == (
            observation.observed_at + timedelta(hours=4))


def test_intentional_scope_and_optional_sitemap_issues_do_not_fake_partial(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    cfg = SourceConfig(source_id='lifecycle', entrypoint='https://example.test')
    obs = CatalogApplicationService(repo).reconcile_and_checkpoint(
        cfg, 'complete', ExtractionResult(source_id='lifecycle', success=True,
            coverage=CoverageStats(
                stop_reason='QUEUE_EXHAUSTED', pages_visited=2,
                sitemap_errors=1, urls_rejected=3, query_variants_blocked=1)))
    assert obs.execution_status == ExecutionStatus.SUCCESS
    assert obs.coverage.confirms_resource_absence() is True


def test_custom_form_can_succeed_but_cannot_confirm_absence(tmp_path: Path):
    _run_lifecycle(tmp_path, 'before', [_resource_for_lifecycle('doc')])
    repo = LocalJsonRepositoryAdapter(tmp_path)
    cfg = SourceConfig(source_id='lifecycle', entrypoint='https://example.test')
    obs = CatalogApplicationService(repo).reconcile_and_checkpoint(
        cfg, 'form', ExtractionResult(source_id='lifecycle', success=True,
            resources=[], coverage=CoverageStats(stop_reason='CUSTOM_FORM_CONFIRMED')))
    assert obs.execution_status == ExecutionStatus.SUCCESS
    assert obs.coverage.confirms_resource_absence() is False
    assert repo.get_resource_catalog('lifecycle')[0].consecutive_absences == 0


def test_discovered_urls_survive_checkpoint_without_downloadable_resources(tmp_path: Path):
    """Inventario bruto no equivale a documentos; sobrevive reabrir el repositorio."""
    from prospector_externo.domain.models import DiscoveredUrl, DiscoveryType

    config = SourceConfig(source_id="pages", entrypoint="https://portal.test")
    pages = [
        DiscoveredUrl(
            normalized_url=f"https://portal.test/p/{i}",
            raw_url=f"/p/{i}", source_id="pages",
            discovery_type=DiscoveryType.HTML, parent_url="https://portal.test",
            depth=1, http_status=200,
        ) for i in range(2)
    ]
    repo = LocalJsonRepositoryAdapter(tmp_path)
    observation = CatalogApplicationService(repo).reconcile_and_checkpoint(
        config, "pages-001", ExtractionResult(
            source_id="pages", resources=[], discovered_urls=pages,
            coverage=CoverageStats(stop_reason="QUEUE_EXHAUSTED", pages_visited=2),
        ),
    )
    assert observation.execution_status == ExecutionStatus.SUCCESS
    reopened = LocalJsonRepositoryAdapter(tmp_path)
    snapshot = reopened.get_latest_snapshot("pages")
    assert snapshot.run_id == "pages-001"
    assert snapshot.resources == []
    assert [p.normalized_url for p in snapshot.discovered_urls] == [
        "https://portal.test/p/0", "https://portal.test/p/1"
    ]
    assert snapshot.discovered_urls[0].parent_url == "https://portal.test"
    assert snapshot.discovered_urls[0].http_status == 200
    assert reopened.list_resources("pages") == []


def test_discovered_urls_preserve_per_run_evidence_for_partial_retrieval(tmp_path: Path):
    """Una corrida parcial no reemplaza el historial RAW anterior."""
    from prospector_externo.domain.models import DiscoveredUrl

    config = SourceConfig(source_id="partial_pages", entrypoint="https://portal.test")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    service = CatalogApplicationService(repo)

    def page(path):
        return DiscoveredUrl(normalized_url=f"https://portal.test/{path}",
                             raw_url=f"/{path}", source_id="partial_pages")

    first = service.reconcile_and_checkpoint(config, "crawl-1", ExtractionResult(
        source_id="partial_pages", discovered_urls=[page("a"), page("b")],
        coverage=CoverageStats(stop_reason="QUEUE_EXHAUSTED"),
    ))
    second = service.reconcile_and_checkpoint(config, "crawl-2", ExtractionResult(
        source_id="partial_pages", discovered_urls=[page("c")],
        coverage=CoverageStats(stop_reason="REQUEST_BUDGET_REACHED", urls_pending=10),
    ))
    assert first.execution_status == ExecutionStatus.SUCCESS
    assert second.execution_status == ExecutionStatus.PARTIAL_SUCCESS
    snapshot_path = tmp_path / "state" / "snapshots"
    first_snapshot = Snapshot.model_validate_json(
        (snapshot_path / "partial_pages_crawl-1.json").read_text(encoding="utf-8")
    )
    second_snapshot = Snapshot.model_validate_json(
        (snapshot_path / "partial_pages_crawl-2.json").read_text(encoding="utf-8")
    )
    assert [p.raw_url for p in first_snapshot.discovered_urls] == ["/a", "/b"]
    assert [p.raw_url for p in second_snapshot.discovered_urls] == ["/c"]
    assert first_snapshot.run_id != second_snapshot.run_id


def test_discovered_url_source_identity_is_validated(tmp_path: Path):
    from prospector_externo.domain.models import DiscoveredUrl
    import pytest

    config = SourceConfig(source_id="real", entrypoint="https://portal.test")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    with pytest.raises(ValueError, match="no pertenece"):
        CatalogApplicationService(repo).reconcile_and_checkpoint(
            config, "bad-id", ExtractionResult(
                source_id="real", discovered_urls=[DiscoveredUrl(
                    normalized_url="https://other.test/page",
                    raw_url="https://other.test/page", source_id="other",
                )],
            ),
        )
    assert repo.get_latest_snapshot("real") is None


def test_mongo_snapshot_indexes_discovered_urls_by_source_and_normalized_url():
    """Prueba del contrato de escritura Mongo sin requerir servidor live."""
    from unittest.mock import Mock
    from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
    from prospector_externo.domain.models import DiscoveredUrl

    adapter = MongoPersistenceAdapter.__new__(MongoPersistenceAdapter)
    adapter.snapshots_col = Mock()
    adapter.discovered_urls_col = Mock()
    discovered = DiscoveredUrl(source_id="aps", normalized_url="https://aps.test/boletin",
                               raw_url="/boletin", parent_url="https://aps.test/")
    snapshot = Snapshot(source_id="aps", run_id="run-mongo-1", resources_hash="hash",
                        total_resources=0, discovered_urls=[discovered])
    adapter.save_snapshot(snapshot)
    assert adapter.snapshots_col.update_one.call_count == 1
    assert adapter.discovered_urls_col.update_one.call_count == 1
    args, kwargs = adapter.discovered_urls_col.update_one.call_args
    assert args[0] == {"source_id": "aps", "normalized_url": "https://aps.test/boletin"}
    assert args[1]["$setOnInsert"]["first_seen_run_id"] == "run-mongo-1"
    assert args[1]["$set"]["last_seen_run_id"] == "run-mongo-1"
    assert kwargs["upsert"] is True
    assert snapshot.discovered_urls[0].source_id == "aps"

    # El contrato rechaza una identidad cruzada ANTES del upsert.
    adapter.snapshots_col.reset_mock()
    snapshot.discovered_urls[0].source_id = "otra"
    import pytest
    with pytest.raises(ValueError, match="otra fuente"):
        adapter.save_snapshot(snapshot)
    adapter.snapshots_col.update_one.assert_not_called()


def test_snapshot_atomic_replace_failure_keeps_previous_json(tmp_path: Path, monkeypatch):
    """Reescribir un snapshot no debe dejar JSON truncado si falla el rename."""
    import os
    import pytest

    repo = LocalJsonRepositoryAdapter(tmp_path)
    older = Snapshot(source_id="bcb", run_id="same-run", resources_hash="first",
                     total_resources=0)
    repo.save_snapshot(older)
    path = tmp_path / "state" / "snapshots" / "bcb_same-run.json"
    original_bytes = path.read_bytes()
    real_replace = os.replace

    def interrupted_replace(src, dst):
        if Path(dst) == path:
            raise OSError("simulated interrupted replacement")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    newer = older.model_copy(update={"resources_hash": "second"})
    with pytest.raises(OSError, match="simulated interrupted"):
        repo.save_snapshot(newer)
    assert path.read_bytes() == original_bytes
    assert not list(path.parent.glob("*.tmp"))


def test_sources_atomic_replace_failure_keeps_catalog(tmp_path: Path, monkeypatch):
    import os
    import pytest

    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(Source(source_id="asfi", name="ASFI", entrypoint="https://asfi.test", workflow="html"))
    path = tmp_path / "state" / "sources.json"
    original_bytes = path.read_bytes()
    real_replace = os.replace

    def interrupted_replace(src, dst):
        if Path(dst) == path:
            raise OSError("simulated interrupted replacement")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interrupted"):
        repo.save_source(Source(source_id="bcb", name="BCB", entrypoint="https://bcb.test", workflow="html"))
    assert path.read_bytes() == original_bytes
    assert [source.source_id for source in repo.list_sources()] == ["asfi"]
    assert not list(path.parent.glob("*.tmp"))


def test_save_source_does_not_overwrite_previously_corrupt_sources_file(tmp_path: Path):
    import pytest

    repo = LocalJsonRepositoryAdapter(tmp_path)
    path = tmp_path / "state" / "sources.json"
    path.write_text('{"asfi": ', encoding="utf-8")
    with pytest.raises(ValueError, match="catálogo de fuentes inválido"):
        repo.save_source(Source(source_id="bcb", name="BCB",
                                entrypoint="https://bcb.test", workflow="html"))
    assert path.read_text(encoding="utf-8") == '{"asfi": '


def test_observation_and_report_atomic_replace_preserves_original(tmp_path: Path, monkeypatch):
    import os
    import pytest

    repo = LocalJsonRepositoryAdapter(tmp_path)
    obs = SourceRunObservation(source_id="bcb", run_id="same", workflow="html",
                               execution_status=ExecutionStatus.SUCCESS,
                               content_status=ContentStatus.CHANGED)
    report = RunReport(run_id="same", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    repo.save_source_observation(obs)
    repo.save_report(report)
    paths = [tmp_path / "state" / "observations" / "bcb_same.json",
             tmp_path / "reports" / "run_same.json"]
    before = {path: path.read_bytes() for path in paths}
    real_replace = os.replace

    def interrupted_replace(src, dst):
        if Path(dst) in before:
            raise OSError("simulated interrupted replacement")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interrupted"):
        repo.save_source_observation(obs.model_copy(update={"failure_code": "NETWORK"}))
    with pytest.raises(OSError, match="simulated interrupted"):
        repo.save_report(report.model_copy(update={"sources_failed": 1}))
    assert all(path.read_bytes() == before[path] for path in paths)
    assert not list((tmp_path / "state" / "observations").glob("*.tmp"))
    assert not list((tmp_path / "reports").glob("*.tmp"))


def test_list_reports_uses_start_time_not_run_id(tmp_path: Path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    first = RunReport(run_id="z-old", started_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    second = RunReport(run_id="a-new", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    repo.save_report(first)
    repo.save_report(second)
    assert [report.run_id for report in repo.list_reports(limit=1)] == ["a-new"]
    assert [report.run_id for report in repo.list_reports()] == ["a-new", "z-old"]


def test_evidence_mirror_replace_failure_preserves_previous_copy(tmp_path: Path, monkeypatch):
    """El archivo espejo B13 tampoco debe quedar truncado si falla su sustitución."""
    import os
    import pytest

    shared = tmp_path / "output"
    evidence = shared / "checkpointed" / "one" / "bcb"
    repo = LocalJsonRepositoryAdapter(evidence, catalog_dir=shared)
    source = Source(source_id="bcb", name="BCB", entrypoint="https://bcb.test", workflow="html")
    repo.save_source(source)
    snap = Snapshot(source_id="bcb", run_id="r1", resources_hash="one", total_resources=0)
    repo.save_snapshot(snap)
    mirror = evidence / "state" / "snapshots" / "bcb_r1.json"
    before = mirror.read_bytes()
    real_replace = os.replace

    def interrupted_replace(src, dst):
        if Path(dst) == mirror:
            raise OSError("simulated interrupted mirror")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interrupted mirror"):
        repo.save_snapshot(snap.model_copy(update={"resources_hash": "two"}))
    assert mirror.read_bytes() == before
    assert not list(mirror.parent.glob("*.tmp"))


def test_curated_api_partial_preserves_undata_legacy_resources_and_no_removal(tmp_path: Path):
    """Dos endpoints curados no pueden certificar ausencia del histórico UNData."""
    repo = LocalJsonRepositoryAdapter(tmp_path)
    service = CatalogApplicationService(repo)
    legacy = ResourceCandidate(
        resource_key="undata:historico", source_id="undata",
        url="https://data.un.org/legacy.json", content_hash="old-hash",
    )
    old_config = SourceConfig(source_id="undata", entrypoint="https://data.un.org", workflow="javascript")
    old_obs = service.reconcile_and_checkpoint(old_config, "legacy-before-8K", ExtractionResult(
        source_id="undata", success=True, resources=[legacy],
        coverage=CoverageStats(stop_reason="QUEUE_EXHAUSTED"),
    ))
    assert old_obs.execution_status == ExecutionStatus.SUCCESS

    new_config = SourceConfig(
        source_id="undata", entrypoint="https://data.un.org", workflow="api",
        api_seed_scope="curated", seeds=["https://api.example.test/observation"],
    )
    api = ResourceCandidate(
        resource_key="undata:api-curada", source_id="undata",
        url=new_config.seeds[0], content_hash="new-api-hash",
    )
    for i in range(1, 5):
        obs = service.reconcile_and_checkpoint(new_config, f"curated-{i}", ExtractionResult(
            source_id="undata", success=True, resources=[api.model_copy(deep=True)],
            coverage=CoverageStats(stop_reason="CURATED_API_SEEDS_EXHAUSTED", api_records_sampled=1),
        ))
        assert obs.execution_status == ExecutionStatus.PARTIAL_SUCCESS
        assert obs.failure_code == "CURATED_API_SEEDS_EXHAUSTED"
        snapshot = repo.get_latest_snapshot("undata")
        assert snapshot.total_resources == 1
        assert snapshot.execution_status == "PARTIAL_SUCCESS"
        standard = json.loads((tmp_path / "undata" / "mapa_undata.json").read_text(encoding="utf-8"))
        assert standard["run"]["status"] == "PARTIAL_SUCCESS"
        catalog = {item.resource_key: item for item in repo.get_resource_catalog("undata")}
        assert set(catalog) == {"undata:historico", "undata:api-curada"}
        assert catalog["undata:historico"].consecutive_absences == 0
        assert catalog["undata:historico"].change_status != "REMOVED"
        assert repo.get_source("undata").entrypoint == "https://data.un.org"
        assert repo.get_source("undata").workflow == "api"
