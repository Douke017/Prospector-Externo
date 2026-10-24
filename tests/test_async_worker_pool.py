"""
Pruebas unitarias para el administrador de concurrencia y Bulkhead (AsyncWorkerPool).
"""

import time
import pytest
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool
from prospector_externo.domain.models import SourceConfig


def test_async_worker_pool_bulkhead_isolation():
    pool = AsyncWorkerPool(max_http_workers=2, max_browser_workers=1)

    http_config = SourceConfig(
        source_id="http_test",
        entrypoint="https://example.com",
        workflow="html"
    )
    browser_config = SourceConfig(
        source_id="browser_test",
        entrypoint="https://example.com",
        workflow="javascript"
    )

    def dummy_task(cfg):
        time.sleep(0.05)
        return f"OK_{cfg.workflow}"

    future_http = pool.submit_workflow(dummy_task, http_config)
    future_browser = pool.submit_workflow(dummy_task, browser_config)

    assert future_http.result() == "OK_html"
    assert future_browser.result() == "OK_javascript"

    pool.shutdown(wait=True)


def test_async_worker_pool_run_parallel():
    pool = AsyncWorkerPool(max_http_workers=4)

    items = [1, 2, 3, 4, 5]

    def square(n):
        return n * n

    results = pool.run_parallel(square, items, max_workers=4)
    assert sorted(results) == [1, 4, 9, 16, 25]

    pool.shutdown(wait=True)


def test_async_slots_are_independent_and_released_on_cancellation():
    """Las corutinas usan bulkheads reales; no solamente executors legacy."""
    import asyncio

    async def scenario():
        pool = AsyncWorkerPool(max_http_workers=2, max_browser_workers=1)
        browser = SourceConfig(source_id="js", entrypoint="https://example.test", workflow="javascript")
        html = SourceConfig(source_id="html", entrypoint="https://example.test", workflow="html")
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        entered = []

        async def first_browser():
            async with pool.async_source_slot(browser):
                entered.append("browser-1")
                first_started.set()
                await release_first.wait()

        async def second_browser():
            async with pool.async_source_slot(browser):
                entered.append("browser-2")

        first = asyncio.create_task(first_browser())
        await first_started.wait()
        second = asyncio.create_task(second_browser())
        async with pool.async_source_slot(html):
            entered.append("http")
        await asyncio.sleep(0)
        assert entered == ["browser-1", "http"], entered

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, timeout=1)
        assert entered == ["browser-1", "http", "browser-2"]
        pool.shutdown()

    import asyncio
    asyncio.run(scenario())


def test_async_pool_rejects_nonpositive_limits():
    with pytest.raises(ValueError, match="positivos"):
        AsyncWorkerPool(max_http_workers=0)
    with pytest.raises(ValueError, match="positivos"):
        AsyncWorkerPool(max_browser_workers=0)
