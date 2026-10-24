"""Canario opt-in: Chromium real, servidor local y 30 páginas con render JS."""
import asyncio
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time

import pytest

from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.browser_driver import PlaywrightDriver
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow
from prospector_externo.kernel.registry import WorkflowRegistry


@pytest.mark.skipif(os.environ.get("DATAX_BROWSER_INTEGRATION") != "1",
                    reason="Chromium real opt-in: DATAX_BROWSER_INTEGRATION=1")
def test_real_chromium_30_pages_recycling_memory_assets_and_scope(tmp_path, monkeypatch):
    server_calls = Counter()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            server_calls[self.path] += 1
            if self.path == "/robots.txt":
                body, mime = b"User-agent: *\nAllow: /", "text/plain"
            elif self.path.startswith("/page/"):
                n = int(self.path.rsplit("/", 1)[1])
                next_link = f'<a href="/page/{n+1}">Next</a>' if n < 29 else ""
                body = (f'''<html><head><link rel="stylesheet" href="/asset.css"></head>
<body><img src="/asset.png"><video autoplay src="/asset.mp4"></video>
<style>@font-face{{font-family:test;src:url('/asset.woff2')}}body{{font-family:test}}</style>
<div id="data"></div><script>
document.getElementById('data').innerHTML='<a href="/data/{n}.csv?utm_source=fixture#section">Data {n}</a>{next_link}';
fetch('http://localhost:{self.server.server_port}/outside-scope').catch(()=>{{}});
</script></body></html>''').encode()
                mime = "text/html"
            else:
                body, mime = b"ok", "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    metrics = {"browser_launches": 0, "max_contexts": 0, "active_contexts": 0,
               "peak_sampled_rss_bytes": 0, "blocked_request_types": Counter(), "blocked_scope": 0}
    class RecordingDriver(PlaywrightDriver):
        def _launch_browser(self):
            browser = super()._launch_browser()
            metrics["browser_launches"] += 1
            original = browser.new_context
            def new_context(**kwargs):
                context = original(**kwargs)
                metrics["active_contexts"] += 1
                metrics["max_contexts"] = max(metrics["max_contexts"], metrics["active_contexts"])
                close, new_page = context.close, context.new_page
                def close_context():
                    try:
                        return close()
                    finally:
                        metrics["active_contexts"] -= 1
                def page():
                    p = new_page()
                    register = p.route
                    def route(pattern, callback):
                        def tracked(route, request):
                            if request.resource_type in {"image", "media", "font", "stylesheet"}:
                                metrics["blocked_request_types"][request.resource_type] += 1
                            if request.url.startswith("http://localhost:"):
                                metrics["blocked_scope"] += 1
                            return callback(route, request)
                        return register(pattern, tracked)
                    p.route = route
                    return p
                context.close, context.new_page = close_context, page
                return context
            browser.new_context = new_context
            return browser
        def _browser_tree_rss_bytes(self):
            rss = super()._browser_tree_rss_bytes()
            if rss is not None:
                metrics["peak_sampled_rss_bytes"] = max(metrics["peak_sampled_rss_bytes"], rss)
            return rss
    driver = RecordingDriver(timeout_ms=5000)
    workflow = JavascriptWorkflow()
    workflow.browser_driver = driver
    monkeypatch.setattr(WorkflowRegistry, "resolve", classmethod(lambda cls, name: workflow))
    url = f"http://127.0.0.1:{server.server_port}/page/0"
    config = SourceConfig(source_id="browser_fixture", entrypoint=url, seeds=[url], workflow="javascript",
                          max_depth=30, max_urls=40, max_browser_pages=30, max_requests=70,
                          max_runtime_seconds=110, rate_limit_seconds=0,
                          discover_sitemaps=False, discover_apis=False, inspect_archives=False)
    started = time.perf_counter()
    async def scenario():
        runtime = AsyncHttpRuntime()
        try:
            return await SourceDispatcher(runtime).dispatch(config)
        finally:
            await runtime.aclose()
    try:
        result = asyncio.run(scenario())
        metrics.update(wall_seconds=round(time.perf_counter() - started, 3),
                       resources=len(result.resources), pages=result.coverage.pages_visited,
                       failure_code=result.failure_code, server_calls=dict(server_calls))
        path = tmp_path / "browser_30_pages.json"
        path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        print("BROWSER_30_PAGES " + json.dumps({**metrics, "evidence": str(path)}))
        assert result.success, result.error_message or result.failure_code
        assert len(result.resources) == 30
        assert result.coverage.pages_visited == 30
        assert result.coverage.stop_reason == "QUEUE_EXHAUSTED"
        assert metrics["browser_launches"] >= 2
        assert metrics["max_contexts"] == 1
        assert 0 < metrics["peak_sampled_rss_bytes"] < 1024 ** 3
        assert all(metrics["blocked_request_types"][kind] > 0 for kind in ["image", "stylesheet", "font"])
        assert metrics["blocked_scope"] > 0
        assert server_calls["/outside-scope"] == 0
        assert all("utm_source" not in r.url and "#" not in r.url for r in result.resources)
        assert all(r.discovery_method == "javascript" for r in result.resources)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
