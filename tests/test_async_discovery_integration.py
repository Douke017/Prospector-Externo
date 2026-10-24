import asyncio
import tempfile
from pathlib import Path

import httpx
import yaml

import prospector_externo.workflows  # noqa: F401
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


def test_end_to_end_async_html_discovery_without_mass_head():
    async def scenario():
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path, request.url.query.decode()))
            assert request.method == "GET", "BATCH 2A no debe hacer HEAD masivo para enlaces evidentes"

            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            if request.url.path == "/":
                return httpx.Response(
                    200,
                    text="""<html><body>
                    <a href="/stats">Estadísticas</a>
                    <a href="/files/reporte-mayo-2026.xlsx?utm_source=test">Excel mayo 2026</a>
                    <a href="https://outside.test/other">Exterior</a>
                    </body></html>""",
                    request=request,
                )
            if request.url.path == "/stats":
                return httpx.Response(
                    200,
                    text="""<html><body>
                    <a href="/files/reporte-mayo-2026.xlsx">Duplicado</a>
                    <a href="/Descargar?path=boletin-junio-2026.pdf">Boletín Junio 2026</a>
                    </body></html>""",
                    request=request,
                )
            raise AssertionError(f"Request inesperado: {request.url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = root / "sources.yaml"
            cfg.write_text(
                yaml.safe_dump({
                    "sources": [{
                        "source_id": "smoke",
                        "name": "Smoke",
                        "entrypoint": "https://example.test/",
                        "workflow": "html",
                        "rate_limit_seconds": 0,
                        "max_depth": 2,
                        "max_urls": 20,
                        "max_requests": 20,
                    }]
                }),
                encoding="utf-8",
            )
            repo = LocalJsonRepositoryAdapter(root / "output")
            transport = httpx.MockTransport(handler)
            orchestrator = RunOrchestrator(
                catalog_repo=repo,
                report_repo=repo,
                config_path=cfg,
                http_runtime_factory=lambda: AsyncHttpRuntime(
                    transport=transport,
                    default_min_interval_seconds=0,
                ),
            )

            report = await orchestrator.run_batch_async(force=True)
            assert report.sources_processed == 1
            obs = report.source_results[0]
            assert obs.coverage.pages_visited == 2
            assert obs.coverage.resources_found == 2
            assert obs.coverage.requests_total == 4  # robots + sitemap probe + 2 HTML
            assert obs.coverage.sitemap_documents == 1
            assert obs.coverage.sitemap_errors == 1
            assert obs.coverage.stop_reason == "QUEUE_EXHAUSTED"

            snapshot = repo.get_latest_snapshot("smoke")
            assert snapshot is not None
            assert snapshot.total_resources == 2
            urls = {r.url for r in snapshot.resources}
            assert "https://example.test/files/reporte-mayo-2026.xlsx" in urls
            assert "https://example.test/Descargar?path=boletin-junio-2026.pdf" in urls
            assert all(method == "GET" for method, _, _ in requests)

    asyncio.run(scenario())


def test_two_logical_sources_share_host_politeness_but_have_independent_budgets():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            return httpx.Response(200, text="<html></html>", request=request)

        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        from prospector_externo.domain.models import SourceConfig

        a = runtime.session_for(SourceConfig(
            source_id="aps", entrypoint="https://example.test/a", rate_limit_seconds=0, max_requests=3
        ))
        b = runtime.session_for(SourceConfig(
            source_id="aps_soat", entrypoint="https://example.test/b", rate_limit_seconds=0, max_requests=3
        ))
        try:
            await a.fetch_html("https://example.test/a")
            await b.fetch_html("https://example.test/b")
        finally:
            await runtime.aclose()

        # robots se consulta una vez y queda cacheado por origin; cada source conserva su budget.
        assert calls == [("GET", "/robots.txt"), ("GET", "/a"), ("GET", "/b")]
        assert a.requests_used == 2
        assert b.requests_used == 1
        assert runtime.politeness.snapshot("example.test").requests_started == 3

    asyncio.run(scenario())


def test_html_skips_linked_images_and_still_catalogs_documents():
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.html_workflow import HtmlWorkflow

    async def scenario():
        requested = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requested.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if request.url.path == "/":
                return httpx.Response(200, text=(
                    '<a href="/uploads/foto.jpg">Foto</a>'
                    '<a href="/documentos">Documentos</a>'
                ), headers={"Content-Type": "text/html"})
            if request.url.path == "/documentos":
                return httpx.Response(200, text='<a href="/reporte.pdf">Reporte</a>',
                                      headers={"Content-Type": "text/html"})
            raise AssertionError(f"No se debe solicitar {request.url}")

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        config = SourceConfig(source_id="images", entrypoint="https://example.test/",
                              discover_sitemaps=False, rate_limit_seconds=0)
        try:
            result = await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert result.success
        assert [r.url for r in result.resources] == ["https://example.test/reporte.pdf"]
        assert result.coverage.urls_failed == 0
        assert result.coverage.stop_reason == "QUEUE_EXHAUSTED"
        assert requested == ["/robots.txt", "/", "/documentos"]

    asyncio.run(scenario())


def test_html_resource_cap_is_reported_as_partial_not_full_success():
    """Prueba camino real workflow -> cobertura -> catálogo -> reporte, sin red."""
    from prospector_externo.domain.observations import ExecutionStatus

    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return httpx.Response(200, text='User-agent: *\nAllow: /\n', request=request)
            if request.url.path == '/sitemap.xml':
                return httpx.Response(404, request=request)
            if request.url.path == '/':
                return httpx.Response(200, text='''<html><body>
                  <a href="/a.pdf">A</a><a href="/b.pdf">B</a>
                </body></html>''', request=request)
            raise AssertionError(f'URL inesperada: {request.url}')

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            cfg = root / 'sources.yaml'
            cfg.write_text(yaml.safe_dump({'sources': [{
                'source_id': 'cap', 'entrypoint': 'https://example.test/',
                'workflow': 'html', 'max_resources': 1,
                'rate_limit_seconds': 0, 'max_requests': 20,
            }]}), encoding='utf-8')
            repo = LocalJsonRepositoryAdapter(root / 'output')
            runner = RunOrchestrator(
                catalog_repo=repo, report_repo=repo, config_path=cfg,
                http_runtime_factory=lambda: AsyncHttpRuntime(
                    transport=httpx.MockTransport(handler), default_min_interval_seconds=0))
            report = await runner.run_batch_async(force=True)
            obs = report.source_results[0]
            assert obs.coverage.stop_reason == 'QUEUE_EXHAUSTED'
            assert obs.coverage.resources_limit_reached == 1
            assert obs.execution_status == ExecutionStatus.PARTIAL_SUCCESS
            assert report.sources_processed == 1
            assert repo.get_latest_snapshot('cap').total_resources == 1
            assert repo.get_source('cap').next_eligible_at is not None
            restored = LocalJsonRepositoryAdapter(root / 'output')
            assert restored.get_source_observations('cap')[0].coverage.resources_limit_reached == 1
            assert restored.get_report(report.run_id).source_results[0].execution_status == ExecutionStatus.PARTIAL_SUCCESS

    asyncio.run(scenario())


def test_html_archive_members_are_raw_only_and_no_fake_download_urls():
    import io
    import zipfile
    from prospector_externo.workflows.html_workflow import HtmlWorkflow
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.domain.projection import ResourceProjectionPolicy

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('2026/reporte.pdf', b'%PDF-1.4')
        zf.writestr('2026/resumen.xlsx', b'example')
        zf.writestr('2026/notas.txt', b'not selected')
    requests = []

    async def handler(req):
        requests.append(req.url.path)
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if req.url.path == '/':
            return httpx.Response(200, text='<a href="/download.zip?token=1">Descargar</a>',
                                  headers={'Content-Type': 'text/html'})
        if req.url.path == '/download.zip':
            return httpx.Response(200, content=archive.getvalue(),
                                  headers={'Content-Type': 'application/zip'})
        raise AssertionError(str(req.url))

    async def scenario():
        config = SourceConfig(source_id='zip', entrypoint='https://example.test/',
                              rate_limit_seconds=0, discover_sitemaps=False, max_requests=12)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        try:
            return await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

    result = asyncio.run(scenario())
    assert result.success is True
    assert result.coverage.archive_inspected == 1
    assert result.coverage.archive_members_found == 2
    assert result.coverage.requests_total == 3  # robots, HTML, bounded archive GET
    assert requests == ['/robots.txt', '/', '/download.zip']
    assert len(result.resources) == 3
    parent, *members = result.resources
    assert parent.extracted_from_archive is None
    assert all(member.extracted_from_archive == parent.url for member in members)
    assert all(member.url == parent.url for member in members)
    assert {m.title for m in members} == {'2026/reporte.pdf', '2026/resumen.xlsx'}
    assert len({m.resource_key for m in result.resources}) == 3
    assert all(ResourceProjectionPolicy.classify(member)[0] is False for member in members)


def test_archive_inspection_failure_is_partial_and_does_not_erase_parent():
    from prospector_externo.workflows.html_workflow import HtmlWorkflow
    from prospector_externo.domain.models import SourceConfig

    async def handler(req):
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if req.url.path == '/':
            return httpx.Response(200, text='<a href="/huge.zip">Documentos</a>',
                                  headers={'Content-Type': 'text/html'})
        if req.url.path == '/huge.zip':
            return httpx.Response(200, content=b'X' * 300,
                                  headers={'Content-Type': 'application/zip'})
        raise AssertionError(str(req.url))

    async def scenario():
        config = SourceConfig(source_id='huge', entrypoint='https://example.test/',
                              rate_limit_seconds=0, discover_sitemaps=False,
                              max_archive_bytes=100, max_requests=10)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        try:
            return await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
    result = asyncio.run(scenario())
    assert [resource.url for resource in result.resources] == ['https://example.test/huge.zip']
    assert result.coverage.archive_inspection_errors == 1
    assert result.coverage.is_partial_discovery()


def test_commented_html_archive_reuses_shared_inspection():
    import io
    import zipfile
    from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow
    from prospector_externo.domain.models import SourceConfig
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('datos.csv', b'datos')

    async def handler(req):
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if req.url.path == '/':
            return httpx.Response(200, text='<!-- <a href="/paquete.zip">Paquete</a> -->',
                                  headers={'Content-Type': 'text/html'})
        if req.url.path == '/paquete.zip':
            return httpx.Response(200, content=buf.getvalue())
        raise AssertionError(str(req.url))

    async def scenario():
        config = SourceConfig(source_id='commented', entrypoint='https://example.test/',
                              rate_limit_seconds=0, discover_sitemaps=False)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        try:
            return await CommentedHtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

    result = asyncio.run(scenario())
    assert result.coverage.archive_inspected == 1
    assert result.coverage.archive_members_found == 1
    assert len(result.resources) == 2
    assert result.resources[1].title == 'datos.csv'


def test_archive_inspection_limits_extra_requests_per_source():
    from prospector_externo.workflows.html_workflow import HtmlWorkflow
    from prospector_externo.domain.models import SourceConfig
    requested = []

    async def handler(req):
        requested.append(req.url.path)
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if req.url.path == '/':
            return httpx.Response(200, text='<a href="/a.zip">a</a><a href="/b.zip">b</a>')
        if req.url.path == '/a.zip':
            return httpx.Response(200, content=b'bad archive')
        raise AssertionError(str(req.url))

    async def scenario():
        config = SourceConfig(source_id='limited', entrypoint='https://example.test/',
                              rate_limit_seconds=0, discover_sitemaps=False,
                              max_archive_inspections=1)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        try:
            return await HtmlWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()
    result = asyncio.run(scenario())
    assert requested == ['/robots.txt', '/', '/a.zip']
    assert result.coverage.archive_inspection_errors == 1
    assert result.coverage.archive_inspection_deferred == 1
    assert len(result.resources) == 2


def test_javascript_driver_start_render_close_share_same_thread():
    """El driver sync no debe migrar de hilo durante su ciclo de vida."""
    import threading
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow

    class FakeDriver:
        def __init__(self): self.thread_ids = []
        def is_available(self): return True
        def fetch_dynamic_dom(self, url):
            self.thread_ids.append(threading.get_ident())
            return "<html><body><a href='/report.pdf'>Reporte</a></body></html>", None
        def close(self): self.thread_ids.append(threading.get_ident())

    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return httpx.Response(200, text='User-agent: *\nAllow: /', request=request)
            return httpx.Response(200, text='<html></html>', request=request)
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler), default_min_interval_seconds=0
        )
        cfg = SourceConfig(
            source_id='js-thread', entrypoint='https://example.test/a',
            seeds=['https://example.test/a', 'https://example.test/b'],
            workflow='javascript', rate_limit_seconds=0, max_depth=0,
            max_browser_pages=3, max_requests=25, inspect_archives=False,
        )
        wf = JavascriptWorkflow()
        wf.browser_driver = FakeDriver()
        wf.bind_http_session(runtime.session_for(cfg))
        try:
            result = await wf.run(cfg)
        finally:
            await runtime.aclose()
        assert result.success
        assert len(wf.browser_driver.thread_ids) == 3
        assert len(set(wf.browser_driver.thread_ids)) == 1
        assert wf.browser_driver.thread_ids[0] != threading.get_ident()

    asyncio.run(scenario())


def test_javascript_driver_closes_on_cancellation_in_own_thread():
    """Cancelaciones coordinan close() sin cruzar el event-loop y greenlets."""
    import threading
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow

    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    thread_ids = []
    class FakeDriver:
        def is_available(self): return True
        def fetch_dynamic_dom(self, url):
            thread_ids.append(threading.get_ident())
            started.set()
            release.wait(timeout=2)
            return '<html></html>', None
        def close(self):
            thread_ids.append(threading.get_ident())
            closed.set()

    async def scenario():
        async def handler(request):
            if request.url.path == '/robots.txt':
                return httpx.Response(200, text='User-agent: *\nAllow: /', request=request)
            return httpx.Response(200, text='<html></html>', request=request)
        runtime=AsyncHttpRuntime(
            transport=httpx.MockTransport(handler), default_min_interval_seconds=0)
        cfg=SourceConfig(source_id='js-cancel', entrypoint='https://example.test/a',
                         workflow='javascript', max_depth=0, rate_limit_seconds=0,
                         inspect_archives=False)
        wf=JavascriptWorkflow()
        wf.browser_driver=FakeDriver()
        wf.bind_http_session(runtime.session_for(cfg))
        try:
            task=asyncio.create_task(wf.run(cfg))
            while not started.is_set():
                await asyncio.sleep(0.002)
            task.cancel()
            release.set()
            try:
                await asyncio.wait_for(task, timeout=3)
                raise AssertionError('No se propagó CancelledError')
            except asyncio.CancelledError:
                pass
            assert closed.wait(timeout=1)
            assert len(thread_ids)==2 and thread_ids[0] == thread_ids[1]
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_javascript_preflight_http_404_does_not_use_browser_page_quota():
    """UNData 8F: enlaces rotos no consumen navegaciones reales de Playwright."""
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow

    class FakeDriver:
        def __init__(self):
            self.rendered = []
            self.closed = False
        def is_available(self): return True
        def fetch_dynamic_dom(self, url):
            self.rendered.append(url)
            return '<html><body><a href="/reporte.pdf">Reporte</a></body></html>', None
        def close(self): self.closed = True

    async def scenario():
        requested = []
        async def handler(request):
            requested.append(request.url.path)
            if request.url.path == '/robots.txt':
                return httpx.Response(200, text='User-agent: *\nAllow: /', request=request)
            if request.url.path in {'/undatacommons/areas', '/undatacommons/about'}:
                return httpx.Response(404, request=request)
            return httpx.Response(200, text='<html></html>', request=request)

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        cfg = SourceConfig(
            source_id='undata', entrypoint='https://example.test/', workflow='javascript',
            seeds=[
                'https://example.test/undatacommons/areas',
                'https://example.test/undatacommons/about',
                'https://example.test/datos',
                'https://example.test/pendiente',
            ], rate_limit_seconds=0, max_depth=0, max_browser_pages=1,
            max_requests=15, discover_sitemaps=False, inspect_archives=False,
        )
        wf = JavascriptWorkflow()
        driver = FakeDriver()
        wf.browser_driver = driver
        wf.bind_http_session(runtime.session_for(cfg))
        try:
            result = await wf.run(cfg)
        finally:
            await runtime.aclose()
        assert result.success is True
        assert driver.rendered == ['https://example.test/datos']
        assert driver.closed
        assert result.coverage.urls_failed == 2
        assert result.coverage.urls_pending == 1
        assert result.coverage.stop_reason == 'BROWSER_PAGE_LIMIT'
        assert result.coverage.resources_found == 1
        assert requested.count('/robots.txt') == 1
        assert '/pendiente' not in requested

    asyncio.run(scenario())


def test_javascript_failed_preflights_still_respect_http_budget():
    """No abrir Chromium si cada URL falla, sin burlar max_requests."""
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.javascript_workflow import JavascriptWorkflow

    class FakeDriver:
        def __init__(self): self.calls = 0
        def is_available(self): return True
        def fetch_dynamic_dom(self, url):
            self.calls += 1
            raise AssertionError('No debe abrir navegador')
        def close(self): pass

    async def scenario():
        async def handler(request):
            if request.url.path == '/robots.txt':
                return httpx.Response(200, text='User-agent: *\nAllow: /', request=request)
            return httpx.Response(404, request=request)
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler),
                                   default_min_interval_seconds=0)
        cfg = SourceConfig(
            source_id='broken', entrypoint='https://example.test/a', workflow='javascript',
            seeds=['https://example.test/a', 'https://example.test/b',
                   'https://example.test/c', 'https://example.test/d'],
            rate_limit_seconds=0, max_depth=0, max_browser_pages=1,
            max_requests=3, discover_sitemaps=False, inspect_archives=False,
        )
        wf = JavascriptWorkflow()
        driver = FakeDriver()
        wf.browser_driver = driver
        wf.bind_http_session(runtime.session_for(cfg))
        try:
            result = await wf.run(cfg)
        finally:
            await runtime.aclose()
        assert not result.success
        assert driver.calls == 0
        assert result.coverage.requests_total <= 3
        assert result.coverage.urls_pending >= 1
        assert result.coverage.stop_reason == 'REQUEST_BUDGET_REACHED'

    asyncio.run(scenario())


def test_bbv_hu21_priority_seeds_not_preempted_by_sitemap():
    """BBV: 5 semillas oficiales, robots obligatorio, sin sitemaps masivos.

    Protege contra la captura de una sola pagina por MAX_URLS de B13.
    Mantiene la profundidad original para rastreos de produccion; la prueba
    limita a profundidad 0 para verificar especificamente las cinco semillas.
    """
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow

    config_path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    sources = yaml.safe_load(config_path.read_text(encoding="utf-8"))["sources"]
    bbv_rows = [row for row in sources if row.get("source_id") == "bbv"]
    assert len(bbv_rows) == 1
    cfg = SourceConfig.model_validate(bbv_rows[0])
    assert cfg.workflow == "commented_html"
    assert len(cfg.seeds) == 5
    assert cfg.discover_sitemaps is False
    assert cfg.ignore_robots_txt is False
    # No se altera el limite de profundidad de produccion, solo para este test.
    assert "max_depth" not in bbv_rows[0]

    async def scenario():
        requested = []
        paths = {httpx.URL(url).path: i for i, url in enumerate(cfg.seeds)}

        async def handler(request: httpx.Request) -> httpx.Response:
            requested.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=(
                    "User-agent: *\nAllow: /\n"
                    "Sitemap: https://www.bbv.com.bo/sitemap.xml\n"
                ), request=request)
            if request.url.path in paths:
                index = paths[request.url.path]
                return httpx.Response(200, text=(
                    f'<html><a href="/wp-content/uploads/doc-{index}.pdf">'
                    f'Documento {index}</a></html>'
                ), request=request)
            raise AssertionError(f"BBV no debia solicitar: {request.url}")

        # Unicamente presupuesto de test: se respetan los 5 seeds reales.
        probe_config = cfg.model_copy(update={
            "rate_limit_seconds": 0, "max_depth": 0, "max_requests": 10,
            "discover_apis": False, "inspect_archives": False,
        })
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        try:
            result = await CommentedHtmlWorkflow(
                runtime.session_for(probe_config)
            ).run(probe_config)
        finally:
            await runtime.aclose()

        assert result.success is True
        assert result.coverage.pages_visited == 5
        assert result.coverage.resources_found == 5
        assert result.coverage.stop_reason == "QUEUE_EXHAUSTED"
        assert result.coverage.requests_total == 6
        assert requested[0] == "/robots.txt"
        assert set(requested[1:]) == set(paths)
        assert "/sitemap.xml" not in requested
        assert len({resource.resource_key for resource in result.resources}) == 5

    asyncio.run(scenario())


def test_asfi_hu21_priority_public_document_pages_inside_unchanged_request_budget():
    """ASFI: priorizar secciones de alto rendimiento documentadas en B13.

    El scope priorizado NO amplía presupuestos de producción ni elude robots.
    En el test se desactiva solamente navegación secundaria y descargas opcionales.
    """
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.domain.normalizer import UrlNormalizer
    from prospector_externo.workflows.html_workflow import HtmlWorkflow

    config_path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    sources = yaml.safe_load(config_path.read_text(encoding="utf-8"))["sources"]
    rows = [row for row in sources if row.get("source_id") == "asfi_valores"]
    assert len(rows) == 1
    row = rows[0]
    cfg = SourceConfig.model_validate(row)
    assert cfg.workflow == "html"
    assert cfg.ignore_robots_txt is False
    assert cfg.discover_sitemaps is True  # No alterar política original.
    assert cfg.max_depth == 2
    assert cfg.max_urls == 250
    assert cfg.max_requests == 250
    assert cfg.max_resources == 1000
    assert cfg.rate_limit_seconds == 1.0
    assert cfg.seeds == [
        "https://www.asfi.gob.bo",
        "https://www.asfi.gob.bo/pb/decretos-supremos-referidos-servicios-financieros",
        "https://www.asfi.gob.bo/pb/decretos",
        "https://www.asfi.gob.bo/auditorias?page=1",
    ]

    async def scenario():
        requested = []
        urls = {UrlNormalizer.normalize(url): index for index, url in enumerate(cfg.seeds)}

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            requested.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n", request=request)
            if request.url.path == "/sitemap.xml":
                return httpx.Response(404, request=request)
            index = urls.get(str(request.url))
            if index is not None:
                return httpx.Response(
                    200,
                    text=(
                        "<html><body><a href='/files/asfi-%d.pdf'>Documento ASFI %d</a></body></html>"
                        % (index, index)
                    ),
                    request=request,
                )
            raise AssertionError(f"No se esperaba GET a: {request.url}")

        probe = cfg.model_copy(update={
            "rate_limit_seconds": 0, "max_depth": 0,
            "max_requests": 9, "discover_apis": False,
            "inspect_archives": False,
        })
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        try:
            result = await HtmlWorkflow(runtime.session_for(probe)).run(probe)
        finally:
            await runtime.aclose()

        assert result.success is True
        assert result.coverage.stop_reason == "QUEUE_EXHAUSTED"
        assert result.coverage.pages_visited == 4
        assert result.coverage.resources_found == 4
        assert result.coverage.requests_total == 6  # robots, sitemap 404, 4 HTML.
        assert result.coverage.urls_failed == 0
        assert len({r.resource_key for r in result.resources}) == 4
        assert requested[0].endswith("/robots.txt")
        assert requested[1].endswith("/sitemap.xml")
        assert requested[2:] == [UrlNormalizer.normalize(url) for url in cfg.seeds]

    asyncio.run(scenario())


# BATCH 11E: una exclusión de robots es política, no una avería de red.
# Debe conservarse en cobertura parcial sin detener páginas permitidas.
import pytest


@pytest.mark.parametrize('workflow_name', ['html', 'commented_html'])
def test_robots_disallowed_does_not_exhaust_consecutive_error_budget(workflow_name):
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.html_workflow import HtmlWorkflow
    from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow

    async def scenario():
        requests = []

        async def handler(req: httpx.Request) -> httpx.Response:
            requests.append(req.url.path)
            if req.url.path == '/robots.txt':
                return httpx.Response(
                    200, text='User-agent: *\nDisallow: /blocked/\nAllow: /\n',
                    request=req,
                )
            if req.url.path == '/allowed':
                return httpx.Response(
                    200, text='<html><a href="/informe.pdf">Informe</a></html>',
                    headers={'content-type': 'text/html'}, request=req,
                )
            raise AssertionError(f'La URL no autorizada nunca debe solicitarse: {req.url}')

        config = SourceConfig(
            source_id=f'robots-neutral-{workflow_name}',
            entrypoint='https://example.test/blocked/1',
            seeds=[
                'https://example.test/blocked/1',
                'https://example.test/blocked/2',
                'https://example.test/blocked/3',
                'https://example.test/allowed',
            ],
            workflow=workflow_name,
            discover_sitemaps=False,
            max_depth=0,
            max_requests=12,
            max_consecutive_errors=2,
            rate_limit_seconds=0,
        )
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        workflow_type = HtmlWorkflow if workflow_name == 'html' else CommentedHtmlWorkflow
        try:
            result = await workflow_type(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert result.success is True
        assert result.coverage.stop_reason == 'QUEUE_EXHAUSTED'
        assert result.coverage.robots_disallowed == 3
        assert result.coverage.urls_failed == 3  # métrica histórica preservada
        assert result.coverage.is_partial_discovery() is True
        assert result.coverage.confirms_resource_absence() is False
        assert [r.url for r in result.resources] == ['https://example.test/informe.pdf']
        assert requests == ['/robots.txt', '/allowed']

    asyncio.run(scenario())


@pytest.mark.parametrize('workflow_name', ['html', 'commented_html'])
def test_robots_unreachable_still_stops_after_real_consecutive_errors(workflow_name):
    from prospector_externo.domain.models import SourceConfig
    from prospector_externo.workflows.html_workflow import HtmlWorkflow
    from prospector_externo.workflows.commented_html import CommentedHtmlWorkflow

    async def scenario():
        requests = []

        async def handler(req: httpx.Request) -> httpx.Response:
            requests.append(req.url.path)
            if req.url.path == '/robots.txt':
                return httpx.Response(503, request=req)
            raise AssertionError(f'No debe navegar sin robots accesible: {req.url}')

        config = SourceConfig(
            source_id=f'robots-unreachable-{workflow_name}',
            entrypoint='https://example.test/a',
            seeds=['https://example.test/a', 'https://example.test/b', 'https://example.test/c'],
            workflow=workflow_name,
            discover_sitemaps=False,
            max_requests=20,
            max_consecutive_errors=2,
            rate_limit_seconds=0,
        )
        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            default_min_interval_seconds=0,
        )
        workflow_type = HtmlWorkflow if workflow_name == 'html' else CommentedHtmlWorkflow
        try:
            result = await workflow_type(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert result.success is False
        assert result.coverage.stop_reason == 'MAX_CONSECUTIVE_ERRORS'
        assert result.coverage.urls_failed == 2
        assert result.coverage.robots_disallowed == 0
        assert result.resources == []
        assert requests and all(path == '/robots.txt' for path in requests)

    asyncio.run(scenario())
