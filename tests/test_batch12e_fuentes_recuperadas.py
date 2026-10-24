"""Lote 12E: coherencia durable del roster recuperado sin llamar a Internet."""
from collections import Counter
from pathlib import Path

import yaml

from apps.b13_pipeline.main import inventory_summary, operational_roster
from src.execution.checkpointed_batch import validate_source_mapping
from src.prospector_externo.domain.models import SourceConfig

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config'
RECOVERED = {'ibch', 'fam', 'otros_historicos', 'fmi'}


def read_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding='utf-8'))


def test_12e_complete_inventory_and_b13_roster():
    assert inventory_summary(ROOT) == {'total': 52, 'operational': 45, 'status_only': 7}
    roster = operational_roster(ROOT)
    assert len(roster) == len(set(roster)) == 45
    assert RECOVERED <= set(roster)
    assert 'mefp' not in roster  # TLS hold: no promotion without verified TLS


def test_12e_mapping_is_bijective_for_new_physical_sources():
    plan = read_yaml('source_operational_plan.yaml')
    physical = read_yaml('sources.yaml')
    mapping = read_yaml('source_execution_map.yaml')
    result = validate_source_mapping(plan, physical, mapping)
    assert result['operational_sources'] == result['matched_sources'] == 45
    assert result['unmatched_sources'] == 0
    assert not result['failures']
    rows = mapping['sources']
    assert len(rows) == 45
    assert len(physical['sources']) == 39
    assert mapping['logical_operational_sources'] == 45
    assert mapping['physical_config_sources'] == 39
    for sid in RECOVERED:
        entries = [x for x in rows if x['logical_source_id'] == sid]
        assert len(entries) == 1
        assert entries[0]['config_source_id'] == sid


def test_12e_no_unsafe_robots_bypass_and_curated_seeds():
    config = read_yaml('sources.yaml')
    data = {x['source_id']: x for x in config['sources']}
    assert len(data) == len(config['sources']) == 39
    for sid in RECOVERED:
        x = SourceConfig(**data[sid])
        assert x.workflow == 'html'
        assert x.seeds
        assert x.ignore_robots_txt is False
        assert x.max_requests <= 160
        assert x.max_runtime_seconds <= 480
        assert x.rate_limit_seconds >= 1.0
        assert x.allowed_hosts


def test_12e_plan_and_final_matrix_agree():
    plan = read_yaml('source_operational_plan.yaml')
    matrix = read_yaml('final_source_matrix.yaml')
    assert len(plan['sources']) == len(matrix['sources']) == 52
    prow = {x['source_id']: x for x in plan['sources']}
    mrow = {x['source_id']: x for x in matrix['sources']}
    assert set(prow) == set(mrow)
    for sid in RECOVERED:
        assert prow[sid]['next_phase'] == 'OPERATIONAL_CONFIG'
        assert mrow[sid]['next_phase'] == 'LIVE_AUDIT'
        assert mrow[sid]['config_source_id'] == sid
        assert '12C' in mrow[sid]['decision_note']
    assert prow['mefp']['next_phase'] == 'B10_STATUS'
    assert mrow['mefp']['final_status'] == 'TLS_TECHNICAL_HOLD'
    assert plan['summary_by_next_phase'] == dict(Counter(x['next_phase'] for x in prow.values()))
    assert plan['summary_by_operational_status'] == dict(Counter(x['operational_status'] for x in prow.values()))
    assert plan['summary_by_workflow'] == dict(Counter(x.get('workflow_strategy') or 'UNRESOLVED' for x in prow.values()))
