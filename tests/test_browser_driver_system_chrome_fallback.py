from __future__ import annotations

import pytest

import prospector_externo.infrastructure.browser_driver as browser_driver


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, *, managed_error=None, chrome_error=None):
        self.managed_error = managed_error
        self.chrome_error = chrome_error
        self.calls = []
        self.browser = _FakeBrowser()

    def launch(self, **kwargs):
        self.calls.append(kwargs)

        if kwargs.get("channel") == "chrome":
            if self.chrome_error is not None:
                raise self.chrome_error
            return self.browser

        if self.managed_error is not None:
            raise self.managed_error
        return self.browser


class _FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class _Starter:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def test_prefers_managed_playwright_chromium(monkeypatch):
    chromium = _FakeChromium()
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()
    driver._ensure_browser()

    assert driver.browser_source == "playwright-chromium"
    assert len(chromium.calls) == 1
    assert "channel" not in chromium.calls[0]

    driver.close()
    assert pw.stopped is True


def test_falls_back_to_system_chrome_when_managed_browser_is_missing(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError(
            "BrowserType.launch: Executable doesn't exist. "
            "Please run playwright install"
        )
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()
    driver._ensure_browser()

    assert driver.browser_source == "system-chrome"
    assert len(chromium.calls) == 2
    assert "channel" not in chromium.calls[0]
    assert chromium.calls[1]["channel"] == "chrome"

    driver.close()


def test_does_not_mask_unrelated_managed_browser_error(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError("sandbox initialization failed")
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()

    with pytest.raises(RuntimeError, match="sandbox initialization failed"):
        driver._ensure_browser()

    assert len(chromium.calls) == 1
    assert pw.stopped is True
    assert driver.browser_source is None


def test_reports_both_errors_when_system_chrome_also_fails(monkeypatch):
    chromium = _FakeChromium(
        managed_error=RuntimeError("Executable doesn't exist"),
        chrome_error=RuntimeError("Chrome channel not found"),
    )
    pw = _FakePlaywright(chromium)

    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(
        browser_driver,
        "sync_playwright",
        lambda: _Starter(pw),
    )

    driver = browser_driver.PlaywrightDriver()

    with pytest.raises(RuntimeError) as exc_info:
        driver._ensure_browser()

    message = str(exc_info.value)
    assert "Chromium administrado por Playwright" in message
    assert "Chrome del sistema" in message
    assert "Chrome channel not found" in message
    assert pw.stopped is True


def test_browser_recycles_when_process_tree_exceeds_rss_budget(monkeypatch):
    chromium = _FakeChromium()
    pw = _FakePlaywright(chromium)
    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(browser_driver, "sync_playwright", lambda: _Starter(pw))
    driver = browser_driver.PlaywrightDriver(max_browser_rss_bytes=1024)
    driver._ensure_browser()
    monkeypatch.setattr(driver, "_browser_tree_rss_bytes", lambda: 1024)
    driver._ensure_browser()
    assert len(chromium.calls) == 2
    assert pw.stopped is True
    driver.close()


def test_page_budget_recycles_even_after_failed_navigations(monkeypatch):
    class FailPage:
        closed = False
        def set_default_timeout(self, timeout): pass
        def set_default_navigation_timeout(self, timeout): pass
        def route(self, *args): pass
        def goto(self, *args, **kwargs): raise RuntimeError("navigation timeout")
        def close(self): self.closed = True
    class Context:
        closed = False
        def __init__(self): self.page = FailPage()
        def new_page(self): return self.page
        def close(self): self.closed = True
    class Browser:
        def __init__(self): self.contexts=[]
        def new_context(self, **kwargs):
            context=Context(); self.contexts.append(context); return context
    driver = browser_driver.PlaywrightDriver()
    fake=Browser()
    monkeypatch.setattr(driver, "_ensure_browser", lambda: None)
    monkeypatch.setattr(driver, "_recycle_on_memory_pressure", lambda: False)
    driver._browser = fake
    for _ in range(3):
        dom, error = driver.fetch_dynamic_dom("https://example.test/")
        assert dom is None and "navigation timeout" in error
    assert driver._pages_navigated == 3
    assert all(c.closed and c.page.closed for c in fake.contexts)


def test_rejects_browser_without_memory_monitor(monkeypatch):
    monkeypatch.setattr(browser_driver, "HAS_PLAYWRIGHT", True)
    monkeypatch.setattr(browser_driver, "psutil", None)
    with pytest.raises(RuntimeError, match="BROWSER_MEMORY_MONITOR_UNAVAILABLE"):
        browser_driver.PlaywrightDriver()._ensure_browser()


def test_invalid_rss_limit_is_rejected():
    with pytest.raises(ValueError, match="positivo"):
        browser_driver.PlaywrightDriver(max_browser_rss_bytes=0)


def test_runtime_process_without_pid_does_not_silently_disable_monitor(monkeypatch):
    driver = browser_driver.PlaywrightDriver()
    driver._browser = _FakeBrowser()
    class PlaywrightRuntime:
        _impl_obj = object()
    driver._playwright = PlaywrightRuntime()
    monkeypatch.setattr(driver, "_browser_tree_rss_bytes", lambda: None)
    with pytest.raises(RuntimeError, match="BROWSER_MEMORY_MONITOR_UNAVAILABLE"):
        driver._recycle_on_memory_pressure()
    assert driver._browser is None


def test_memory_measurement_sums_driver_and_browser_descendants(monkeypatch):
    class Proc:
        def __init__(self, rss): self.rss = rss
        def memory_info(self): return type('Memory', (), {'rss': self.rss})()
        def children(self, recursive):
            assert recursive is True
            return [Proc(180), Proc(220)]
    class Module:
        NoSuchProcess = type('NoSuchProcess', (Exception,), {})
        AccessDenied = type('AccessDenied', (Exception,), {})
        ZombieProcess = type('ZombieProcess', (Exception,), {})
        @staticmethod
        def Process(pid):
            assert pid == 1234
            return Proc(100)
    class FakePlaywright:
        _impl_obj = type('Impl', (), {'_connection': type('Conn', (), {
            '_transport': type('Trans', (), {
                '_proc': type('OSProc', (), {'pid': 1234})()
            })()
        })()})()
    monkeypatch.setattr(browser_driver, 'psutil', Module)
    driver = browser_driver.PlaywrightDriver(max_browser_rss_bytes=600)
    driver._playwright = FakePlaywright()
    assert driver._browser_tree_rss_bytes() == 500


def test_browser_restarts_after_page_limit_on_failed_navigation(monkeypatch):
    chromium = _FakeChromium()
    pw = _FakePlaywright(chromium)
    monkeypatch.setattr(browser_driver, 'HAS_PLAYWRIGHT', True)
    monkeypatch.setattr(browser_driver, 'sync_playwright', lambda: _Starter(pw))
    driver = browser_driver.PlaywrightDriver()
    driver._ensure_browser()
    driver._pages_navigated = driver.MAX_PAGES_BEFORE_RESTART
    monkeypatch.setattr(driver, '_browser_tree_rss_bytes', lambda: 0)
    driver._ensure_browser()
    assert len(chromium.calls) == 2
    driver.close()
