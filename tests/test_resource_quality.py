"""Focused resource selection, URL identity and additive frontend contract regressions."""

from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.legacy_stats_exporter import LegacyStatsJsonExporter
from prospector_externo.domain.api_discovery import ApiDetector
from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.domain.models import ApiMetadata, ResourceCandidate, Source, SourceConfig, Snapshot
from prospector_externo.domain.models import DiscoveryType
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.projection import ProjectionBuilder
from prospector_externo.workflows.html_workflow import HtmlWorkflow
from test_mongo_query_and_replay_contract import repository  # Mongo fake fixture, no daemon


def resource(url, **kwargs):
    return ResourceCandidate(source_id="bbv", resource_key=kwargs.pop("resource_key", "key"),
                             url=url, raw_url=url, file_extension=".pdf", **kwargs)


def project(resources):
    return ProjectionBuilder.build(source_id="bbv", source_name="BBV",
        entrypoint="https://example.test/", run_id="historical", resources_hash="raw-hash",
        resources=resources)


@pytest.mark.parametrize("path,expected", [
    ("Estados Financieros/Gestión 2024/a.pdf", "Estados%20Financieros/Gesti%C3%B3n%202024/a.pdf"),
    ("a%20b.pdf", "a%20b.pdf"),
    ("a%2520b.pdf", "a%2520b.pdf"),
    ("a%2fb%3Fc%23d.pdf", "a%2fb%3Fc%23d.pdf"),
    ("a+b.pdf", "a+b.pdf"),
])
def test_path_encoding_idempotent_preserves_existing_escapes_and_query(path, expected):
    query = "?b=%2f&a=1&a=&token=x%20y+z&sig=%2B%2520#section"
    url = "https://example.test/" + path + query
    encoded = "https://example.test/" + expected + query
    assert UrlNormalizer.encode_download_url(url) == encoded
    assert UrlNormalizer.encode_download_url(encoded) == encoded
    normalized = UrlNormalizer.normalize(url)
    assert normalized == UrlNormalizer.normalize(normalized)
    assert UrlNormalizer.compute_resource_key("bbv", normalized) == UrlNormalizer.compute_resource_key(
        "bbv", UrlNormalizer.normalize(encoded))
    assert UrlNormalizer.compute_resource_key("other", normalized) != UrlNormalizer.compute_resource_key("bbv", normalized)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/plain,a.pdf", "file:///a.pdf", "/a.pdf", "//evil.test/a.pdf",
    "https://user:secret@example.test/a.pdf", "https://example.test:99999/a.pdf",
    "https://example.test:bad/a.pdf", "https://exa mple.test/a.pdf",
    "https://example.test/a.pdf?token=bad value", "https://example.test/a.pdf#bad fragment",
    "https://example.test/a\tb.pdf", "https://example.test/a\nb.pdf", "https://example.test/a\x00b.pdf",
    "https://example.test/a\\b.pdf", "https://example.test/%0d%0aHeader.pdf",
    "https://example.test/%5cb.pdf", "https://example.test/%2e%2e/private.pdf",
    "https://example.test/a%2.pdf", "https://example.test/a%b.pdf", "https://example.test/a\u00a0b.pdf",
    "https://[bad/a.pdf", " https://example.test/a.pdf", "https://%65xample.test/a.pdf",
    "https://example.test/api/item/{id}",
])
def test_unsafe_url_not_repaired_into_a_selected_reference(url):
    assert UrlNormalizer.encode_download_url(url) is None
    r = resource(url)
    before = r.model_dump()
    doc = project([r])
    assert doc.total_selected_resources == 0
    assert doc.decisions[0].reason_codes == ("invalid_download_url",)
    assert r.model_dump() == before


def test_discovery_encodes_before_hash_and_dedup_keeps_scope_and_provenance():
    config = SourceConfig(source_id="bbv", entrypoint="https://example.test/")
    workflow = HtmlWorkflow()
    first = workflow._make_resource(config=config, raw_url="/EEFF/a b.pdf?token=x%2Fy",
        current_url=config.entrypoint, title="Balance", discovery_type=DiscoveryType.COMMENTED_HTML)
    second = workflow._make_resource(config=config, raw_url="/EEFF/a%20b.pdf?token=x%2Fy",
        current_url=config.entrypoint, title="Balance", discovery_type=DiscoveryType.COMMENTED_HTML)
    assert first.resource_key == second.resource_key
    assert first.raw_url == "/EEFF/a b.pdf?token=x%2Fy"
    assert first.discovered_from_url == config.entrypoint
    frontier = DiscoveryFrontier(config)
    assert frontier.enqueue("/page with spaces", base_url=config.entrypoint)
    assert not frontier.enqueue("/page%20with%20spaces", base_url=config.entrypoint)
    assert not frontier.enqueue("https://outside.test/page with spaces")
    assert frontier.register_resource(first.url)
    assert not frontier.register_resource(second.url)


@pytest.mark.parametrize("path,reason", [
    ("/wp-json/oembed/1.0/embed?url=https%3A%2F%2Fexample.test", "api_auxiliary_oembed"),
    ("/blog/wp-json/oembed/1.0/embed/?format=xml", "api_auxiliary_oembed"),
    ("/?rest_route=%2Foembed%2F1.0%2Fembed", "api_auxiliary_oembed"),
    ("/wp-json/", "api_service_index_not_dataset"),
    ("/?rest_route=%2F", "api_service_index_not_dataset"),
    ("/wp-json/wp/v2/pages/30", "api_cms_metadata_not_dataset"),
    ("/wp-json/wp/v2/users/1", "api_cms_metadata_not_dataset"),
])
def test_auxiliary_api_stays_raw_but_is_not_a_data_document(path, reason):
    url = "https://example.test" + path
    r = resource(url, resource_type="api", api=ApiMetadata(identity="GET " + url, callable_by_policy=True))
    before = deepcopy(r)
    p = project([r])
    assert p.total_raw_resources == 1 and p.total_selected_resources == 0
    assert p.decisions[0].reason_codes == (reason,)
    assert r == before
    assert LegacyStatsJsonExporter.build(p).record_count == 0


@pytest.mark.parametrize("path", [
    "/api/cargaArchivos/descargarprospecto/uuid/CARAC",
    "/api/cargaArchivos/descargarinformecalificacion/uuid",
    "/api/data?name=oembed", "/wp-json/datax/v1/series",
    "/wp-json/wp/v2/media/123", "/api/oembed-statistics",
])
def test_extensionless_downloads_and_custom_wordpress_routes_survive(path):
    url = "https://example.test" + path
    r = resource(url, resource_type="api", api=ApiMetadata(identity="GET " + url, callable_by_policy=True))
    p = project([r])
    assert p.total_selected_resources == 1
    assert p.decisions[0].reason_codes == ("public_get_api",)
    assert LegacyStatsJsonExporter.build(p).record_count == 1


def test_discovered_api_reference_is_retained_for_auditable_exclusion():
    refs = ApiDetector.extract_references(
        '<link type="application/json+oembed" href="/wp-json/oembed/1.0/embed?url=x">'
        '<a href="/api/cargaArchivos/descargarprospecto/id/P">Prospecto</a>',
        base_url="https://example.test/")
    assert len(refs) == 2
    assert sum(ApiDetector.auxiliary_reason(r.url) is None for r in refs) == 1


def test_export_encodes_raw_path_while_preserving_signed_query_and_scope():
    r = resource("https://example.test/a%20b.pdf?a=1&b=2")
    r.raw_url = "https://example.test/a b.pdf?b=%2f&a=x+y&sig=%2B%2520"
    p = project([r])
    rep = p.families[0].periods[0].representations[0]
    assert LegacyStatsJsonExporter._download_url(rep) == r.raw_url.replace(" ", "%20")
    rep.raw_url = "https://outside.test/a%20b.pdf"
    assert LegacyStatsJsonExporter._download_url(rep) == r.url
    rep.raw_url = "https://example.test:443/a b.pdf?sig=%2B%2520&b=2&a=1"
    assert LegacyStatsJsonExporter._download_url(rep) == rep.raw_url.replace(" ", "%20")


@pytest.mark.parametrize("backend", ["json", "mongo_fake"])
def test_frontend_projection_is_additive_read_only_and_raw_pagination_unchanged(tmp_path, repository, monkeypatch, backend):
    repo = LocalJsonRepositoryAdapter(tmp_path) if backend == "json" else repository
    repo.save_source(Source(source_id="bbv", name="BBV", workflow="html", entrypoint="https://example.test/"))
    api_url = "https://example.test/wp-json/oembed/1.0/embed?url=x"
    rows = [resource("https://example.test/a b.pdf", resource_key="z"),
            resource(api_url, resource_key="a", resource_type="api", api=ApiMetadata(identity="GET " + api_url))]
    snap = Snapshot(source_id="bbv", run_id="historical", resources_hash="raw-hash", total_resources=2, resources=rows)
    repo.save_snapshot(snap)
    repo.save_resource_catalog("bbv", "historical", rows)
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    client = TestClient(app)
    raw_before = client.get("/catalog/resources?source_id=bbv").json()
    def no_write(*args, **kwargs):
        raise AssertionError("read route attempted persistence")
    for name in ("save_snapshot", "save_resource_catalog", "save_source"):
        monkeypatch.setattr(repo, name, no_write)
    response = client.get("/catalog/sources/bbv/projection")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "historical" and body["resources_hash"] == "raw-hash"
    assert body["total_raw_resources"] == 2 and body["total_selected_resources"] == 1
    rep = body["families"][0]["periods"][0]["representations"][0]
    assert rep["resource_key"] == "z" and rep["url"] == rows[0].url.replace(" ", "%20")
    assert rep["raw_url"] == rows[0].raw_url
    assert client.get("/catalog/resources?source_id=bbv").json() == raw_before
    assert client.get("/catalog/resources?source_id=bbv&limit=1&offset=1").json() == [rows[0].model_dump(mode="json")]
    assert client.get("/catalog/resources?source_id=bbv&limit=1&offset=2").json() == []
    assert client.get("/catalog/resources?source_id=bbv&limit=501").status_code == 422
    assert client.get("/catalog/resources?offset=-1").status_code == 422
    assert client.get("/catalog/sources/missing/projection").status_code == 404
    assert repo.get_latest_snapshot("bbv") == snap


def test_projection_missing_snapshot_is_404(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_source(Source(source_id="empty", name="empty", workflow="html", entrypoint="https://example.test"))
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    assert TestClient(app).get("/catalog/sources/empty/projection").status_code == 404
