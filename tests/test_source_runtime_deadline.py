"""Deadline asíncrono por fuente, conservación de RAW y cancelación diferenciada."""
import asyncio
import time

import httpx
import pytest

from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


@pytest.mark.parametrize("workflow", ["html", "commented_html", "api", "custom"])
def test_deadline_cancels_hung_request_with_explicit_failure(workflow):
    async def scenario():
        cancelled = asyncio.Event()
        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        cfg = SourceConfig(source_id="deadline", entrypoint="https://example.test/start",
                           seeds=["https://example.test/start"], workflow=workflow,
                           rate_limit_seconds=0, max_runtime_seconds=.15,
                           discover_sitemaps=False, inspect_archives=False,
                           custom_config={"strategy": "data_endpoint", "data_endpoints": ["https://example.test/start"]})
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            started = time.monotonic()
            result = await SourceDispatcher(runtime).dispatch(cfg)
            assert time.monotonic() - started < 2
            assert not result.success
            assert result.failure_code == "MAX_RUNTIME"
            assert result.coverage.stop_reason == "MAX_RUNTIME"
            assert result.coverage.timeouts == 1
            assert cancelled.is_set()
        finally:
            await runtime.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("workflow", ["html", "commented_html"])
def test_deadline_preserves_completed_page_and_pending_cursor(tmp_path, workflow):
    from test_discovery_checkpoint_resume import attempt, source_config
    cfg = source_config(workflow).model_copy(update={"max_requests": 10, "max_runtime_seconds": 1.0})
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.path == "/root":
            return httpx.Response(200, text='<a href="/a.pdf">A</a><a href="/slow">Slow</a>')
        await asyncio.Event().wait()
    repo, report = attempt(tmp_path, cfg, calls, handler=handler)
    obs = report.source_results[0]
    assert obs.execution_status.value == "PARTIAL_SUCCESS"
    assert obs.failure_code == "MAX_RUNTIME"
    assert len(repo.get_latest_snapshot("resume").resources) == 1
    state = repo.get_discovery_checkpoint("resume")
    assert state["frontier"]["pending"] == ["https://example.test/slow"]
    calls.clear()
    _, recovered = attempt(tmp_path, cfg.model_copy(update={"max_runtime_seconds": 5}), calls)
    assert "/root" not in calls
    assert recovered.source_results[0].execution_status.value == "SUCCESS"


def test_external_cancellation_remains_interruption_not_source_timeout():
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            entered.set()
            await asyncio.Event().wait()
        cfg = SourceConfig(source_id="cancel", entrypoint="https://example.test", rate_limit_seconds=0,
                           discover_sitemaps=False)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            task = asyncio.create_task(SourceDispatcher(runtime).dispatch(cfg))
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await runtime.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("workflow", ["html", "commented_html"])
def test_timeout_with_only_historical_raw_is_failed_and_retains_cursor(tmp_path, workflow):
    from test_discovery_checkpoint_resume import attempt, source_config
    cfg = source_config(workflow)
    repo, _ = attempt(tmp_path, cfg, [])
    previous = repo.get_latest_snapshot("resume")
    async def hung(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        await asyncio.Event().wait()
    repo, report = attempt(tmp_path, cfg.model_copy(update={"max_runtime_seconds": .5}), [], handler=hung)
    assert report.source_results[0].execution_status.value == "FAILED"
    assert report.source_results[0].failure_code == "MAX_RUNTIME"
    assert repo.get_source("resume").consecutive_failures == 1
    assert repo.get_latest_snapshot("resume") == previous
    cursor = repo.get_discovery_checkpoint("resume")
    assert len(cursor["resources"]) == 1
    assert len(cursor["frontier"]["pending"]) == 3


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_deadline_is_rejected_in_configuration(value):
    with pytest.raises(ValueError):
        SourceConfig(source_id="invalid", entrypoint="https://example.test", max_runtime_seconds=value)
