"""Contratos estáticos HU-15. No equivalen a un docker build real."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _compose():
    return yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))


def test_dual_images_and_mongo_are_separate():
    services = _compose()['services']
    assert set(services) == {'api', 'batch', 'mongo'}
    assert services['api']['build']['dockerfile'] == 'Dockerfile.api'
    assert services['batch']['build']['dockerfile'] == 'Dockerfile.batch'
    assert services['mongo']['image'].startswith('mongo:7')


def test_api_only_starts_on_loopback_and_checks_real_health():
    api = _compose()['services']['api']
    assert '127.0.0.1:8000:8000' in api['ports']
    assert api['environment']['PROSPECTOR_BACKEND'] == 'mongo'
    assert api['depends_on']['mongo']['condition'] == 'service_healthy'
    assert '/health' in str(api['healthcheck'])
    dockerfile = (ROOT / 'Dockerfile.api').read_text(encoding='utf-8')
    assert 'uvicorn' in dockerfile
    assert 'playwright install' not in dockerfile


def test_batch_is_explicit_and_has_browser_budget():
    batch = _compose()['services']['batch']
    assert 'batch' in batch['profiles']
    assert batch['command'] == ['--help']
    assert batch['mem_limit'] == '3g'
    assert batch['depends_on']['mongo']['condition'] == 'service_healthy'
    assert '--backend' in batch['entrypoint']
    assert '--config' in batch['entrypoint']


def test_batch_playwright_image_matches_python_package_version():
    dockerfile = (ROOT / 'Dockerfile.batch').read_text(encoding='utf-8')
    assert 'mcr.microsoft.com/playwright/python:v1.55.0-noble' in dockerfile
    assert "'playwright==1.55.0'" in dockerfile
    assert 'python -m apps.crawler_batch.main' not in (ROOT / 'Dockerfile.api').read_text(encoding='utf-8')


def test_build_context_does_not_include_historical_outputs_or_credentials():
    ignore = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    assert {'output/', '.venv', 'tests/', '.env'} <= set(ignore)
    assert (ROOT / 'README.md').exists()
    assert _compose()['volumes'].get('prospector_mongo_data') is None
