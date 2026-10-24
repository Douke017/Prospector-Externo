import asyncio
import json

import httpx

from prospector_externo.domain.models import ResourceType, SourceConfig
from prospector_externo.infrastructure.http_policy import RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.workflows.api_workflow import ApiWorkflow


def _robots():
    return httpx.Response(200, text='User-agent: *\nAllow: /\n')


def test_api_workflow_follows_bounded_explicit_pagination_and_aggregates_records():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            page = request.url.params.get('page')
            if request.url.path == '/api/data' and page == '1':
                return httpx.Response(200, json={
                    'items': [
                        {'id': 1, 'url': '/files/a.pdf', 'title': 'A'},
                        {'id': 2},
                    ],
                    'next': '/api/data?dataset=ipc&page=2',
                }, headers={'Content-Type': 'application/json'})
            if request.url.path == '/api/data' and page == '2':
                return httpx.Response(200, json={
                    'items': [
                        {'id': 3, 'url': '/files/b.xlsx', 'title': 'B'},
                        {'id': 4},
                    ],
                    'next': '/api/data?dataset=ipc&page=3',
                }, headers={'Content-Type': 'application/json'})
            if request.url.path == '/api/data' and page == '3':
                return httpx.Response(200, json={'items': [{'id': 5}]}, headers={'Content-Type': 'application/json'})
            raise AssertionError(f'request inesperado: {request.url}')

        runtime = AsyncHttpRuntime(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0, jitter_ratio=0),
        )
        try:
            config = SourceConfig(
                source_id='paged',
                entrypoint='https://example.test/api/data?dataset=ipc&page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_requests=10,
                max_api_pages=3,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api_resources = [r for r in result.resources if r.resource_type == ResourceType.API]
        files = [r for r in result.resources if r.resource_type == ResourceType.FILE]
        assert result.success is True
        assert len(api_resources) == 1
        assert {r.url for r in files} == {
            'https://example.test/files/a.pdf',
            'https://example.test/files/b.xlsx',
        }
        api = api_resources[0].api
        assert api is not None
        assert api.pages_sampled == 3
        assert api.records_sampled == 5
        assert api.records_detected == 5
        assert api.pagination_strategy == 'json_next'
        assert result.coverage.api_pages_visited == 3
        assert result.coverage.api_records_sampled == 5
        assert result.coverage.requests_total == 4  # robots + 3 páginas
        assert len(calls) == 4

    asyncio.run(scenario())


def test_api_workflow_stops_duplicate_pagination_payload_without_looping():
    async def scenario():
        calls = []
        duplicate = {'items': [{'id': 1}], 'next': '/api/data?page=2'}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(200, json=duplicate, headers={'Content-Type': 'application/json'})

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='dup',
                entrypoint='https://example.test/api/data?page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_api_pages=20,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.pages_sampled == 1
        assert result.coverage.api_pagination_stopped == 1
        assert len(calls) == 3  # robots + page1 + page2, duplicate detectado

    asyncio.run(scenario())


def test_api_workflow_probes_only_explicit_openapi_document_and_never_executes_operations():
    async def scenario():
        calls = []
        spec = {
            'openapi': '3.0.3',
            'info': {'title': 'Public API'},
            'paths': {
                '/stats': {
                    'get': {'operationId': 'stats', 'responses': {'200': {'content': {'application/json': {}}}}},
                    'post': {'operationId': 'mutate'},
                }
            },
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == '/robots.txt':
                return _robots()
            if request.url.path == '/docs':
                return httpx.Response(
                    200,
                    text='<script>SwaggerUIBundle({url: "/openapi.json"})</script>',
                    headers={'Content-Type': 'text/html'},
                )
            if request.url.path == '/openapi.json':
                return httpx.Response(200, json=spec, headers={'Content-Type': 'application/json'})
            if request.url.path == '/stats':
                raise AssertionError('una operación OpenAPI descubierta no debe ejecutarse')
            raise AssertionError(f'inesperado: {request.url}')

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='docs_probe',
                entrypoint='https://example.test/docs',
                workflow='api',
                rate_limit_seconds=0,
                max_api_documents=2,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert calls == ['/robots.txt', '/docs', '/openapi.json']
        assert result.coverage.api_documents_probed == 1
        assert result.coverage.openapi_documents == 1
        assert result.coverage.api_non_get_operations_skipped == 1
        stats = next(r for r in result.resources if r.api and r.api.operation_id == 'stats')
        assert stats.api is not None
        assert stats.api.spec_version == '3.0.3'
        spec_resource = next(r for r in result.resources if r.url.endswith('/openapi.json'))
        assert spec_resource.api is not None
        assert spec_resource.api.documentation_probed is True
        assert spec_resource.api.spec_version == '3.0.3'

    asyncio.run(scenario())


def test_api_workflow_respects_max_api_pages_even_when_next_exists():
    async def scenario():
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == '/robots.txt':
                return _robots()
            page = int(request.url.params.get('page', '1'))
            return httpx.Response(
                200,
                json={'items': [{'id': page}], 'next': f'/api/data?page={page + 1}'},
                headers={'Content-Type': 'application/json'},
            )

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='cap_pages',
                entrypoint='https://example.test/api/data?page=1',
                workflow='api',
                rate_limit_seconds=0,
                max_api_pages=2,
                max_requests=20,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.pages_sampled == 2
        assert result.coverage.api_pages_visited == 2
        assert len(calls) == 3  # robots + dos páginas

    asyncio.run(scenario())


def test_api_workflow_catalogs_advanced_ndjson_without_pagination_guessing():
    async def scenario():
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == '/robots.txt':
                return _robots()
            return httpx.Response(
                200,
                text='{"id":1}\n{"id":2}\n',
                headers={'Content-Type': 'application/x-ndjson'},
            )

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            config = SourceConfig(
                source_id='ndjson',
                entrypoint='https://example.test/api/feed',
                workflow='api',
                rate_limit_seconds=0,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        api = next(r for r in result.resources if r.resource_type == ResourceType.API).api
        assert api is not None
        assert api.format == 'ndjson'
        assert api.records_detected == 2
        assert api.pages_sampled == 1
        assert api.records_sampled == 2

    asyncio.run(scenario())


def _undata_observations_payload(indicator: str, entity: str, count: int) -> dict:
    return {
        "byVariable": {
            indicator: {
                "byEntity": {
                    entity: {
                        "orderedFacets": [
                            {"observations": [
                                {"date": "2023", "value": index + 1}
                                for index in range(count)
                            ]},
                        ],
                    },
                },
            },
        },
        "facets": {},
    }


def test_undata_datacommons_observations_count_only_explicit_series():
    from prospector_externo.domain.api_discovery import ApiDetector

    payload = _undata_observations_payload("undata/unodc/VIC_HOM_RT", "country/ZAF", 3)
    payload["byVariable"]["malformed"] = {"byEntity": [1, 2, 3]}
    payload["byVariable"]["other"] = {
        "byEntity": {"country/SSD": {"orderedFacets": [
            {"observations": [{"date": "2023", "value": 42}]},
            {"observations": []},
            {"observations": "not-a-list"},
        ]}},
    }
    assert ApiDetector.records_detected(json.dumps(payload), "json") == 4
    assert ApiDetector.records_detected(json.dumps({"byVariable": {}}), "json") == 0
    assert ApiDetector.records_detected(json.dumps({"questions": [1, 2]}), "json") is None
    assert ApiDetector.records_detected(json.dumps([1, 2]), "json") == 2


def test_undata_two_verified_api_seeds_catalogued_without_binary_downloads():
    from prospector_externo.domain.api_discovery import ApiDetector

    async def scenario():
        host = "unsd-datacommons.gcp.un-icc.cloud"
        base = f"https://{host}/core/api/v2/observation"
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return _robots()
            assert request.method == "GET"
            assert request.url.path == "/core/api/v2/observation"
            variable = request.url.params["variable.dcids"]
            entity = request.url.params["entity.dcids"]
            assert request.url.params.get("date") == "LATEST"
            assert variable in {"undata/unodc/VIC_HOM_RT", "undata/sdg/VC_DTH_TOTN"}
            return httpx.Response(200, json=_undata_observations_payload(variable, entity, 1))

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        seeds = [
            base + "?date=LATEST&variable.dcids=undata%2Funodc%2FVIC_HOM_RT&entity.dcids=country%2FZAF",
            base + "?date=LATEST&variable.dcids=undata%2Fsdg%2FVC_DTH_TOTN&entity.dcids=country%2FSSD",
        ]
        try:
            config = SourceConfig(
                source_id="undata",
                entrypoint=seeds[0],
                seeds=[seeds[1]],
                workflow="api",
                rate_limit_seconds=0,
                max_requests=8,
                max_api_response_bytes=131_072,
                follow_api_pagination=False,
                probe_api_documentation=False,
            )
            result = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert result.success is True
        assert len(result.resources) == 2
        assert all(resource.resource_type == ResourceType.API for resource in result.resources)
        assert all(resource.api.records_detected == 1 for resource in result.resources)
        assert all(resource.api.records_sampled == 1 for resource in result.resources)
        assert all(resource.content_hash for resource in result.resources)
        assert result.coverage.api_records_sampled == 2
        assert result.coverage.api_endpoints == 2
        assert len(calls) == 3  # robots + dos GET con datos; sin descargas binarias

    asyncio.run(scenario())


def test_undata_production_config_curated_preserves_identity_and_explicit_scope():
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit

    import yaml

    document = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/sources.yaml").read_text(encoding="utf-8"))
    row = next(source for source in document["sources"] if source["source_id"] == "undata")
    config = SourceConfig.model_validate(row)

    assert config.source_id == "undata"
    assert config.entrypoint == "https://data.un.org"
    assert config.workflow == "api"
    assert config.api_seed_scope == "curated"
    assert config.allowed_hosts == ["unsd-datacommons.gcp.un-icc.cloud"]
    assert len(config.seeds) == 2
    assert config.max_requests == 8
    assert config.max_api_response_bytes == 131_072
    assert config.follow_api_pagination is False
    assert config.probe_api_documentation is False
    assert len(ApiWorkflow._seed_urls(config)) == 2
    def semantic_query(url):
        return {key: sorted(values) for key, values in parse_qs(urlsplit(url).query).items()}
    assert [semantic_query(url) for url in ApiWorkflow._seed_urls(config)] == [
        semantic_query(url) for url in config.seeds
    ]
    assert all(urlsplit(url).hostname == config.allowed_hosts[0] for url in config.seeds)
    assert {
        (parse_qs(urlsplit(url).query)["variable.dcids"][0],
         parse_qs(urlsplit(url).query)["entity.dcids"][0])
        for url in config.seeds
    } == {
        ("undata/unodc/VIC_HOM_RT", "country/ZAF"),
        ("undata/sdg/VC_DTH_TOTN", "country/SSD"),
    }


def test_curated_api_scope_rejects_invalid_configs():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SourceConfig(source_id="x", entrypoint="https://example.test", workflow="javascript",
                     api_seed_scope="curated", seeds=["https://example.test/api"])
    with pytest.raises(ValidationError):
        SourceConfig(source_id="x", entrypoint="https://example.test", workflow="api",
                     api_seed_scope="curated")
    with pytest.raises(ValidationError):
        SourceConfig(source_id="x", entrypoint="https://example.test", workflow="api",
                     api_seed_scope="unknown", seeds=["https://example.test/api"])


def test_curated_undata_api_runtime_never_declares_complete_coverage():
    from pathlib import Path
    import yaml

    async def scenario():
        document = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/sources.yaml").read_text(encoding="utf-8"))
        config = SourceConfig.model_validate(next(row for row in document["sources"]
                                                 if row["source_id"] == "undata"))
        calls = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/robots.txt":
                return _robots()
            assert request.method == "GET"
            assert request.url.host == "unsd-datacommons.gcp.un-icc.cloud"
            assert request.url.path == "/core/api/v2/observation"
            return httpx.Response(200, json=_undata_observations_payload(
                request.url.params["variable.dcids"], request.url.params["entity.dcids"], 1,
            ))

        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(handler))
        try:
            extraction = await ApiWorkflow(runtime.session_for(config)).run(config)
        finally:
            await runtime.aclose()

        assert extraction.success is True
        assert extraction.coverage.resources_found == 2
        assert extraction.coverage.api_records_sampled == 2
        assert extraction.coverage.stop_reason == "CURATED_API_SEEDS_EXHAUSTED"
        assert extraction.coverage.is_partial_discovery()
        assert not extraction.coverage.confirms_resource_absence()
        assert len(calls) == 3  # robots + 2 APIs, jamás data.un.org portal
        assert all(resource.content_hash for resource in extraction.resources)

    asyncio.run(scenario())
