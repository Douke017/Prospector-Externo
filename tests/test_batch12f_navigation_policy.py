"""BATCH 12F: la política de navegación protege operaciones y conserva recursos."""
from pathlib import Path

import yaml

from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.domain.models import SourceConfig


def make_config(**overrides):
    data = {
        "source_id": "test", "entrypoint": "https://example.test/",
        "seeds": ["https://example.test/publicaciones/"],
        "max_depth": 2, "max_urls": 4, "max_resources": 8,
        "excluded_path_keywords": ["/cdn-cgi/l/email-protection", "/cart/"],
    }
    data.update(overrides)
    return SourceConfig(**data)


def test_mutating_get_is_never_queued_and_does_not_use_url_budget():
    f = DiscoveryFrontier(make_config(max_urls=1))
    assert not f.enqueue("/producto/curso/?add-to-cart=2381", base_url="https://example.test/")
    assert not f.enqueue("/carrito?remove_item=123", base_url="https://example.test/")
    assert f.rejection_count("MUTATING_NAVIGATION_QUERY") == 2
    assert f.enqueue("/publicaciones/", base_url="https://example.test/")
    assert f.discovered_count == 1


def test_excluded_path_applies_to_http_url_and_trailing_slash():
    f = DiscoveryFrontier(make_config())
    assert not f.enqueue("https://example.test/cart")
    assert not f.enqueue("https://example.test/cart/")
    assert not f.enqueue("https://example.test/cdn-cgi/l/email-protection")
    assert f.rejection_count("EXCLUDED_NAVIGATION_PATH") == 3
    assert f.pending_count == 0


def test_exclusions_are_navigation_only_not_resource_catalog():
    f = DiscoveryFrontier(make_config())
    url = "https://example.test/cart/descarga.pdf"
    assert not f.enqueue(url)
    assert f.register_resource(url)
    assert not f.register_resource(url)
    assert f.discovered_count == 1


def test_public_download_queries_remain_eligible():
    f = DiscoveryFrontier(make_config())
    assert f.enqueue("/download?id=2026&token=public", base_url="https://example.test/")
    assert f.enqueue("/estadisticas?page=2", base_url="https://example.test/")


def test_not_all_query_parameters_are_mutating():
    f = DiscoveryFrontier(make_config())
    assert f.enqueue("/estadisticas?q=mensual&year=2026", base_url="https://example.test/")


def test_12f_configuration_scopes_fmi_and_blocks_irrelevant_actions():
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / "config/sources.yaml").read_text(encoding="utf-8"))
    cfg = {row["source_id"]: SourceConfig(**row) for row in doc["sources"]}
    assert len(cfg) == 39
    assert cfg["fmi"].allowed_hosts == ["data.imf.org"]
    assert not DiscoveryFrontier(cfg["fmi"]).enqueue("https://www.imf.org/en/news/podcasts")
    assert not DiscoveryFrontier(cfg["fam"]).enqueue("https://fam.org.bo/cdn-cgi/l/email-protection")
    assert not DiscoveryFrontier(cfg["ibch"]).enqueue("https://www.ibch.com/cart/")
    assert cfg["ibch"].ignore_robots_txt is False
    assert cfg["fam"].ignore_robots_txt is False
    assert cfg["fmi"].ignore_robots_txt is False
