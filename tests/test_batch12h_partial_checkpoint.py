"""B12H: exit code 3 is not automatically a failed crawl."""
from __future__ import annotations

import json
from pathlib import Path

from src.execution.checkpointed_batch import run_checkpointed_sources
from src.persistence.model import FAILED, PARTIAL, RUNNING, SUCCEEDED, new_checkpoint, resume_candidates, transition
from src.persistence.store import JsonCheckpointStore


def _fixtures():
    sources = []
    for i in range(52):
        sources.append({
            'source_id': f's{i}',
            'logical_code': f'S{i}',
            'next_phase': 'OPERATIONAL_CONFIG' if i < 45 else 'B10_STATUS',
        })
    return {'sources': sources}, {'sources': [{'source_id': f's{i}'} for i in range(45)]}


def _execute(tmp_path: Path, runner, *, checkpoint=None):
    plan, config = _fixtures()
    if checkpoint is None:
        checkpoint = new_checkpoint(plan, run_id='partial-12h-test')
    store = JsonCheckpointStore(tmp_path / 'checkpoint.json')
    store.save(checkpoint)
    return run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=checkpoint,
        store=store,
        work_dir=tmp_path / 'work',
        output_root=tmp_path / 'out',
        max_sources=1,
        executor=runner,
    )


def _create_report(output_dir: Path, *, state: str, name: str = 'run_new.json', source_id: str = 's0'):
    path = output_dir / 'reports' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'run_id': name[:-5],
        'source_results': [{
            'source_id': source_id,
            'execution_status': state,
            'coverage': {'stop_reason': 'REQUEST_BUDGET_REACHED',
                         'urls_pending': 59, 'resources_found': 311,
                         'urls_failed': 0},
        }]
    }), encoding='utf-8')


def test_partial_evidence_is_distinguished_from_failure_and_remains_retryable(tmp_path):
    def runner(**kwargs):
        _create_report(kwargs['output_dir'], state='PARTIAL_SUCCESS')
        return 3

    checkpoint, report = _execute(tmp_path, runner)
    row = checkpoint['sources']['s0']
    assert row['state'] == PARTIAL
    assert row['resource_count'] == 311
    assert row['last_error'] is None
    assert row['metadata']['stop_reason'] == 'REQUEST_BUDGET_REACHED'
    assert row['metadata']['urls_pending'] == 59
    assert report['partial'] == 1
    assert report['failed'] == 0
    assert 's0' in resume_candidates(checkpoint)
    assert 's0' not in resume_candidates(checkpoint, include_failed=False)
    assert 's0' in report['remaining_resume_candidates']


def test_exit_code_3_without_matching_new_partial_report_remains_failed(tmp_path):
    checkpoint, report = _execute(tmp_path, lambda **kw: 3)
    assert checkpoint['sources']['s0']['state'] == FAILED
    assert report['failed'] == 1
    assert report['partial'] == 0


def test_exit_code_3_with_failed_status_is_not_partial(tmp_path):
    def runner(**kwargs):
        _create_report(kwargs['output_dir'], state='FAILED')
        return 3
    checkpoint, report = _execute(tmp_path, runner)
    assert checkpoint['sources']['s0']['state'] == FAILED
    assert report['failed'] == 1


def test_exit_code_3_with_mismatched_physical_source_is_not_partial(tmp_path):
    def runner(**kwargs):
        _create_report(kwargs['output_dir'], state='PARTIAL_SUCCESS', source_id='s9')
        return 3
    checkpoint, report = _execute(tmp_path, runner)
    assert checkpoint['sources']['s0']['state'] == FAILED
    assert report['partial'] == 0


def test_stale_partial_report_does_not_mask_new_failed_attempt(tmp_path):
    old_path = tmp_path / 'out' / 's0'
    _create_report(old_path, state='PARTIAL_SUCCESS', name='run_old.json')
    checkpoint, report = _execute(tmp_path, lambda **kwargs: 3)
    assert checkpoint['sources']['s0']['state'] == FAILED
    assert report['partial'] == 0


def test_partial_checkpoint_transition_can_be_resumed(tmp_path):
    plan, _ = _fixtures()
    checkpoint = new_checkpoint(plan)
    checkpoint = transition(checkpoint, 's0', RUNNING)
    checkpoint = transition(checkpoint, 's0', PARTIAL, resource_count=11)
    checkpoint = transition(checkpoint, 's0', RUNNING)
    checkpoint = transition(checkpoint, 's0', SUCCEEDED, resource_count=11)
    assert checkpoint['sources']['s0']['attempts'] == 2
    assert checkpoint['sources']['s0']['state'] == SUCCEEDED
