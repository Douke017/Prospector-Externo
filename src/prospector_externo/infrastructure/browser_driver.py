"""Playwright browser driver used by JavascriptWorkflow.

Policy:
- Prefer the Chromium revision managed by Playwright.
- If that revision is not installed, fall back to the locally installed
  Google Chrome channel.
- Never download or install a browser automatically at runtime.
- Keep browser/context/page lifecycle bounded to avoid memory growth.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

try:
    import psutil
except ImportError:  # Dependencia opcional del extra [browser].
    psutil = None
from typing import Optional, Tuple

logger = logging.getLogger("prospector.infrastructure.browser")

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        sync_playwright,
    )

    HAS_PLAYWRIGHT = True
except ImportError:
    Browser = BrowserContext = Page = Playwright = object  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]
    HAS_PLAYWRIGHT = False


class PlaywrightDriver:
    """Bounded Playwright driver with a controlled system-Chrome fallback."""

    MAX_PAGES_BEFORE_RESTART = 15
    MAX_BROWSER_RSS_BYTES = 1024 * 1024 * 1024

    def __init__(
        self, headless: bool = True, timeout_ms: int = 25000,
        max_browser_rss_bytes: int = MAX_BROWSER_RSS_BYTES,
    ):
        if max_browser_rss_bytes <= 0:
            raise ValueError("max_browser_rss_bytes debe ser positivo")
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._pages_navigated = 0
        self._browser_source: Optional[str] = None
        self.max_browser_rss_bytes = max_browser_rss_bytes
        self.allowed_hosts = None

    @property
    def browser_source(self) -> Optional[str]:
        """Return the active browser source after launch."""
        return self._browser_source

    def is_available(self) -> bool:
        """Indicate whether the Playwright Python package is installed."""
        return HAS_PLAYWRIGHT

    @staticmethod
    def _launch_args() -> list[str]:
        return [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--js-flags=--max-old-space-size=512",
        ]

    @staticmethod
    def _is_missing_managed_browser(exc: Exception) -> bool:
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in (
                "executable doesn't exist",
                "playwright install",
                "download new browsers",
            )
        )

    def _launch_browser(self) -> Browser:
        assert self._playwright is not None

        launch_kwargs = {
            "headless": self.headless,
            "args": self._launch_args(),
        }

        try:
            browser = self._playwright.chromium.launch(**launch_kwargs)
            self._browser_source = "playwright-chromium"
            return browser
        except Exception as managed_exc:
            if not self._is_missing_managed_browser(managed_exc):
                raise

            logger.warning(
                "Playwright Chromium no está instalado; intentando Chrome del sistema."
            )

            try:
                browser = self._playwright.chromium.launch(
                    channel="chrome",
                    **launch_kwargs,
                )
                self._browser_source = "system-chrome"
                logger.info(
                    "JavascriptWorkflow usará Chrome del sistema mediante Playwright."
                )
                return browser
            except Exception as chrome_exc:
                raise RuntimeError(
                    "No se pudo iniciar ni Chromium administrado por Playwright "
                    "ni Chrome del sistema. "
                    f"managed={managed_exc!s}; system_chrome={chrome_exc!s}"
                ) from chrome_exc

    def _browser_tree_rss_bytes(self) -> Optional[int]:
        """Mide el RSS del driver Playwright y sus procesos descendientes.

        Playwright Python no publica un PID de Chromium. La ruta al proceso
        driver es interna y por eso se comprueba defensivamente. Si cambia la
        API interna no se devuelve un consumo inventado.
        """
        if psutil is None or self._playwright is None:
            return None
        impl = getattr(self._playwright, "_impl_obj", None)
        connection = getattr(impl, "_connection", None)
        transport = getattr(connection, "_transport", None)
        proc = getattr(transport, "_proc", None)
        pid = getattr(proc, "pid", None)
        if not pid:
            return None
        try:
            root = psutil.Process(pid)
            processes = [root, *root.children(recursive=True)]
            total = 0
            for child in processes:
                try:
                    total += child.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            return total
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def _recycle_on_memory_pressure(self) -> bool:
        if self._browser is None:
            return False
        rss = self._browser_tree_rss_bytes()
        if rss is None:
            # Los fakes de pruebas no exponen PID. Un runtime Playwright real
            # debe fallar explícitamente si no puede medir sus procesos.
            impl = getattr(self._playwright, "_impl_obj", None)
            if impl is not None:
                self.close()
                raise RuntimeError(
                    "BROWSER_MEMORY_MONITOR_UNAVAILABLE: no se pudo medir "
                    "el árbol de procesos de Chromium"
                )
            return False
        if rss < self.max_browser_rss_bytes:
            return False
        logger.warning(
            "Chromium/Playwright RSS %d bytes supera límite %d; reciclando.",
            rss, self.max_browser_rss_bytes,
        )
        self.close()
        return True

    def _ensure_browser(self) -> None:
        """Start or recycle the browser while preserving bounded memory use."""
        if not HAS_PLAYWRIGHT or sync_playwright is None:
            raise RuntimeError(
                "Playwright no está instalado en el entorno. "
                "Instala el paquete Python antes de ejecutar workflows JavaScript."
            )
        if psutil is None:
            raise RuntimeError(
                "BROWSER_MEMORY_MONITOR_UNAVAILABLE: instalar psutil con "
                "python -m pip install '.[browser]'"
            )
        if self._browser is not None:
            self._recycle_on_memory_pressure()

        if (
            self._browser is not None
            and self._pages_navigated >= self.MAX_PAGES_BEFORE_RESTART
        ):
            logger.info(
                "Reciclando navegador Playwright para liberar memoria acumulada."
            )
            self.close()

        if self._browser is None:
            self._playwright = sync_playwright().start()
            try:
                self._browser = self._launch_browser()
            except Exception:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
                self._browser_source = None
                raise

            self._pages_navigated = 0

    def fetch_dynamic_dom(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Load one URL in an ephemeral context and return rendered DOM."""
        self._ensure_browser()

        context: Optional[BrowserContext] = None
        page: Optional[Page] = None
        attempted_navigation = False

        try:
            assert self._browser is not None

            context = self._browser.new_context(
                user_agent="DataX-ProspectorBot/1.0 (+http://datax.bo/bot)",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.set_default_navigation_timeout(self.timeout_ms)

            allowed_hosts = self.allowed_hosts or {urlsplit(url).hostname}
            def route_request(route, request):
                host = (urlsplit(getattr(request, "url", url)).hostname or "").lower().rstrip(".")
                if (request.resource_type in {"image", "media", "font", "stylesheet"}
                        or host not in allowed_hosts):
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", route_request)

            attempted_navigation = True
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            content = page.content()
            return content, None

        except Exception as exc:
            return None, f"PLAYWRIGHT_ERROR: {exc!s}"
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

            if attempted_navigation:
                self._pages_navigated += 1
            # Muestreo posterior tras liberar page/context; si el RSS creció,
            # cerrar Chromium antes de la siguiente navegación.
            if self._browser is not None:
                self._recycle_on_memory_pressure()

    def close(self) -> None:
        """Close browser and Playwright runtime safely."""
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        self._pages_navigated = 0
        self._browser_source = None
