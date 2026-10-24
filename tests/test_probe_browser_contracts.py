"""Deterministic browser adapter contracts; these do not certify live websites."""
import json
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml

from apps.browser_characterization import main as characterization
from apps.custom_probe import main as custom


class Route:
    def __init__(self, resource_type, method="GET"):
        self.request = SimpleNamespace(resource_type=resource_type, method=method)
        self.action = None
    def abort(self): self.action = "abort"
    def continue_(self): self.action = "continue"


class Page:
    def __init__(self, fail=False):
        self.url = ""
        self.fail = fail
        self.routes = []
    def route(self, pattern, callback): self.route_callback = callback
    def on(self, event, callback): self.response_callback = callback
    def goto(self, url, **kwargs):
        self.url = url
        if self.fail: raise TimeoutError("fixture navigation")
        for kind, method in [("image", "GET"), ("font", "GET"), ("media", "GET"), ("document", "GET"), ("xhr", "POST")]:
            route = Route(kind, method)
            self.route_callback(route)
            self.routes.append(route)
        for kind in ["fetch", "fetch", "image"]:
            self.response_callback(SimpleNamespace(request=SimpleNamespace(resource_type=kind, method="GET"),
                url="https://fixture.test/api/records", status=200, headers={"content-type": "application/json"}))
    def wait_for_load_state(self, *args, **kwargs): raise TimeoutError("networkidle is optional")
    def wait_for_timeout(self, *args): pass
    def evaluate(self, *args): pass
    def title(self): return "Fixture records"
    def content(self): return "<html>fixture</html>"
    def eval_on_selector_all(self, selector, script):
        if selector == "form":
            return [{"method": "post", "action": "/submit"},
                {"method": "get", "action": "https://other.test/search"},
                {"method": "get", "action": "/search?existing=1", "fields": [
                    {"name": "q", "value": "records"}, {"name": "secret", "type": "password", "value": "hidden"},
                    {"name": "disabled", "disabled": True, "value": "no"}]}]
        return ["/table.csv", "/report.pdf", "/data", "/table.csv", "#skip", "javascript:void(0)"]


class Browser:
    def __init__(self, fail=False): self.contexts = []; self.fail = fail; self.closed = False
    def new_context(self, **kwargs):
        assert kwargs["ignore_https_errors"] is False
        assert kwargs["accept_downloads"] is False
        context = SimpleNamespace(page=Page(self.fail), closed=False)
        context.new_page = lambda: context.page
        def close(): context.closed = True
        context.close = close
        self.contexts.append(context)
        return context
    def close(self): self.closed = True


class PlaywrightContext:
    def __init__(self, browser):
        self.browser = browser
        self.pw = SimpleNamespace(chromium=SimpleNamespace(launch=self.launch), stop=lambda: None)
    def launch(self, **kwargs):
        if kwargs.get("channel") == "missing": raise RuntimeError("not installed")
        return self.browser
    def start(self): return self.pw
    def __enter__(self): return self.pw
    def __exit__(self, *args): pass


def install_browser(monkeypatch, browser):
    import playwright.sync_api
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: PlaywrightContext(browser))
    monkeypatch.setattr(characterization, "launch_specs", lambda pw: [
        {"kind": "channel", "name": "missing", "kwargs": {"channel": "missing"}},
        {"kind": "channel", "name": "fixture", "kwargs": {"channel": "fixture"}}])


def test_characterization_cli_records_download_urls_and_closes_browser(tmp_path, monkeypatch):
    browser = Browser()
    install_browser(monkeypatch, browser)
    config = tmp_path / "candidates.yaml"
    config.write_text(yaml.safe_dump({"sources": [{"source_id": "fixture", "logical_code": "FIXTURE", "effective_entrypoint": "https://fixture.test/"}]}))
    output = tmp_path / "browser"
    monkeypatch.setattr(sys, "argv", ["browser", "--candidates", str(config), "--output-dir", str(output), "--settle-ms", "1"])
    assert characterization.main() == 0
    result = json.loads((output / "latest.json").read_text())["results"][0]
    assert result["direct_downloads"] == 2
    assert result["xhr_fetch_events"] == 1
    assert result["blocked_assets"] == {"image": 1, "font": 1, "media": 1}
    assert result["status"] == "BROWSER_FILE_DISCOVERY"
    assert browser.closed and all(c.closed for c in browser.contexts)
    assert list(output.rglob("*.csv")) == [output / "latest.csv"]
    assert not (output / "sources/fixture/table.csv").exists()


def test_browser_error_and_custom_form_policy_leave_context_closed(tmp_path):
    broken = Browser(fail=True)
    result = characterization.browser_characterize_source(broken,
        {"source_id": "broken", "logical_code": "BROKEN", "historical_entrypoint": "https://fixture.test/"},
        timeout_ms=20, settle_ms=0, max_html_chars=100, max_network_events=2, output_dir=tmp_path)
    assert result["status"] == "BROWSER_EXECUTION_ERROR"
    assert "TimeoutError" in result["navigation_error"] and broken.contexts[0].closed
    browser = Browser()
    result = custom.browser_custom_probe(browser, "fixture", "https://fixture.test/", tmp_path, max_pages=2)
    assert len(result["pages_visited"]) == 2
    assert result["structured_files"] == ["https://fixture.test/table.csv"]
    assert result["blocked_non_idempotent_requests"] == 2
    assert result["blocked_non_get_forms"] == 2
    assert all("secret" not in url and "disabled" not in url for url in result["pages_visited"])
    assert browser.contexts[0].closed
    assert any(route.action == "abort" and route.request.method == "POST" for route in browser.contexts[0].page.routes)


def test_custom_cli_api_network_identity_and_browser_strategies(tmp_path, monkeypatch):
    browser = Browser()
    install_browser(monkeypatch, browser)
    monkeypatch.setattr(custom, "discover_browser_runtime", characterization.discover_browser_runtime)
    client_cls = httpx.Client
    calls = []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"records": [{"id": 1}]})
    def client(**kwargs):
        assert kwargs["verify"] is True
        return client_cls(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(custom.httpx, "Client", client)
    rows = []
    for sid, strategy in [("api", "API_SEMANTIC_PROBE"), ("network", "NETWORK_ENDPOINT_PROBE"),
            ("unknown", "IDENTITY_RESEARCH"), ("unsafe", "IDENTITY_RESEARCH"),
            ("identity", "IDENTITY_RESEARCH"), ("browser", "SAFE_BROWSER_INTERACTION")]:
        rows.append({"source_id": sid, "recommended_strategy": strategy, "effective_entrypoint": "https://fixture.test/",
            "api_evidence": {"sample_resources": [{"url": "https://fixture.test/data.json"}]},
            "browser_evidence": {"xhr_fetch_urls": ["https://fixture.test/data.json", "https://other.test/no.json"]}})
    strategies = tmp_path / "strategies.yaml"
    strategies.write_text(yaml.safe_dump({"sources": rows}))
    identities = tmp_path / "identities.yaml"
    identities.write_text(yaml.safe_dump({"sources": [
        {"source_id": "unsafe", "status": "UNRESOLVED_NO_SAFE_SUCCESSOR"},
        {"source_id": "identity", "status": "REVIEWED", "candidate_entrypoint": "https://fixture.test/"}]}))
    output = tmp_path / "custom"
    monkeypatch.setattr(sys, "argv", ["custom", "--strategies", str(strategies), "--identity-resolution", str(identities), "--output-dir", str(output)])
    assert custom.main() == 0
    results = {r["source_id"]: r for r in json.loads((output / "latest.json").read_text())["results"]}
    assert results["api"]["status"] == "CUSTOM_DATA_ENDPOINT_CONFIRMED"
    assert results["network"]["http_results"][-1]["skip_reason"] == "cross_site"
    assert results["unknown"]["status"] == results["unsafe"]["status"] == "CUSTOM_IDENTITY_UNRESOLVED"
    assert results["browser"]["status"] == "CUSTOM_STRUCTURED_FILES_CONFIRMED"
    assert calls and all(url.startswith("https://fixture.test/") for url in calls)
    assert browser.closed and all(c.closed for c in browser.contexts)


def test_http_probe_bounds_bytes_and_reports_parse_transport_errors():
    def respond(request):
        if request.url.path == "/fail": raise httpx.ConnectError("fixture", request=request)
        return httpx.Response(200, content=b'{"records":' + b" " * 100, headers={"content-type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        results = custom.probe_http_json(client, ["https://fixture.test/big", "https://fixture.test/fail"], "https://fixture.test/", max_bytes=15)
    assert results[0]["sample_bytes"] == 15 and results[0]["json_shape"].startswith("JSON_PARSE_ERROR")
    assert "ConnectError" in results[1]["error"]
