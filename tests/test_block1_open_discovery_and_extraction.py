"""Pruebas del Bloque 1: Descubrimiento Abierto, TLS X.509, Extracción Científica, Dry-Run y SSE."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tarfile
import time
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import yaml

from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import (
    LocalJsonRepositoryAdapter,
)
from prospector_externo.application.open_discovery_service import (
    DiscardCandidateRequest,
    OpenDiscoverySearchRequest,
    OpenDiscoveryService,
)
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.application.run_control import RunControlRegistry
from prospector_externo.domain.models import ResourceCandidate, Source, SourceConfig
from prospector_externo.domain.observations import RunStatus
from prospector_externo.infrastructure.tls_inspector import (
    TlsInspectionResult,
    TlsX509Inspector,
)
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.ports.open_search_provider import OpenSearchHit
from prospector_externo.workflows.base import BaseWorkflow, ResourceDetector
from prospector_externo.workflows.html_workflow import HtmlWorkflow


class _FakeExternalSearchProvider:
    """Proveedor de búsqueda externa mockeado fuera del directorio curado."""

    provider_name = "mock_ckan_external"

    def search(
        self,
        query: str,
        *,
        sector: str = "TODOS",
        max_results: int = 10,
    ) -> list[OpenSearchHit]:
        return [
            OpenSearchHit(
                title="Portal Hidrocarburos Datos Abiertos — Series Crudas",
                url="https://datos.geoenergia.gob.bo/dataset/series-raw",
                sector="ENERGIA",
                provider=self.provider_name,
                snippet=f"Repositorio abierto {query} con archivos parquet y csv",
                extra_sample_urls=[
                    "https://datos.geoenergia.gob.bo/files/produccion_2026.parquet",
                    "https://datos.geoenergia.gob.bo/files/demanda_2026.csv",
                ],
            ),
            OpenSearchHit(
                title="Portal Certificado Expirado",
                url="https://expirado.energia.gob.bo/estadisticas",
                sector="ENERGIA",
                provider=self.provider_name,
                snippet="Series con certificado TLS expirado",
            ),
        ][:max_results]


class _FakeTlsInspector(TlsX509Inspector):
    """Inspector TLS determinístico para simular certificados válidos y vencidos."""

    def inspect(self, url: str) -> TlsInspectionResult:
        if "expirado.energia.gob.bo" in url:
            return TlsInspectionResult(
                tls_valid=False,
                error_code="CERT_EXPIRED",
                error_detail="El certificado X.509 del host ha expirado.",
            )
        if url.startswith("http://"):
            return TlsInspectionResult(
                tls_valid=False,
                error_code="NOT_HTTPS",
                error_detail="La URL no utiliza protocolo HTTPS.",
            )
        return TlsInspectionResult(
            tls_valid=True,
            protocol="TLSv1.3",
            issuer="CN=Let's Encrypt E5",
            not_after="2027-06-01T00:00:00+00:00",
        )


def _build_api_client(tmp_path: Path) -> tuple[TestClient, LocalJsonRepositoryAdapter, Path]:
    out_dir = tmp_path / "output"
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "source_id": "portal_test",
                        "name": "Portal de Prueba",
                        "entrypoint": "https://datos.prueba.gob.bo/repositorio",
                        "workflow": "html",
                        "allowed_extensions": [".csv", ".parquet", ".zip"],
                        "max_urls": 5,
                        "max_resources": 20,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repo = LocalJsonRepositoryAdapter(out_dir)
    repo.save_source(
        Source(
            source_id="portal_test",
            name="Portal de Prueba",
            entrypoint="https://datos.prueba.gob.bo/repositorio",
            workflow="html",
            allowed_extensions=[".csv", ".parquet", ".zip"],
        )
    )
    app = FastAPI()
    app.include_router(
        create_catalog_router(catalog_repo=repo, report_repo=repo, config_path=cfg_path)
    )
    return TestClient(app), repo, cfg_path


def test_task_1_1_open_search_provider_and_real_tls_validation(tmp_path: Path) -> None:
    repo = LocalJsonRepositoryAdapter(tmp_path / "out")

    def handler(request: httpx.Request) -> httpx.Response:
        if "geoenergia" in str(request.url):
            html = (
                "<html><head><title>Portal GeoEnergia Datos Crudos</title></head>"
                "<body><a href='/files/extra_2026.json'>JSON</a></body></html>"
            )
            return httpx.Response(200, text=html)
        return httpx.Response(200, text="<html><title>Sitio Expirado</title></html>")

    service = OpenDiscoveryService(
        catalog_repo=repo,
        search_providers=[_FakeExternalSearchProvider()],
        tls_inspector=_FakeTlsInspector(),
        http_transport=httpx.MockTransport(handler),
    )

    results = service.search_and_preselect(
        OpenDiscoverySearchRequest(
            query="produccion energia series",
            sector="TODOS",
            target_extensions=[".parquet", ".csv", ".json"],
            include_curated_baseline=False,
            min_combined_score=30,
        ),
        live_probe=True,
    )

    urls = {c.entrypoint for c in results}
    assert "https://datos.geoenergia.gob.bo/dataset/series-raw" in urls
    assert "https://expirado.energia.gob.bo/estadisticas" in urls

    valid_cand = next(c for c in results if "geoenergia" in c.entrypoint)
    expired_cand = next(c for c in results if "expirado" in c.entrypoint)

    assert valid_cand.discovery_provider == "mock_ckan_external"
    assert valid_cand.tls_valid is True
    assert valid_cand.tls_protocol == "TLSv1.3"
    assert len(valid_cand.sample_resource_urls) == 3
    assert ".parquet" in valid_cand.detected_extensions
    assert ".json" in valid_cand.detected_extensions

    assert expired_cand.tls_valid is False
    assert expired_cand.tls_error_code == "CERT_EXPIRED"

    # El inspector real detecta sin red que HTTP plano no tiene TLS
    real_http = TlsX509Inspector().inspect("http://sin-cifrado.example.bo/datos")
    assert real_http.tls_valid is False
    assert real_http.error_code == "NOT_HTTPS"


def test_task_1_2_discard_candidate_with_reason_and_reactivation(tmp_path: Path) -> None:
    client, _, _ = _build_api_client(tmp_path)

    search_payload = {
        "query": "estadísticas cartera boletín financiero",
        "sector": "TODOS",
        "target_extensions": [".xlsx", ".pdf"],
        "trusted_domains_only": True,
        "min_combined_score": 40,
    }
    resp = client.post("/discovery/open-search?live_probe=false", json=search_payload)
    assert resp.status_code == 200
    initial = resp.json()
    assert len(initial) >= 3
    target_id = initial[0]["candidate_id"]
    assert "sample_resource_urls" in initial[0]

    # Motivo vacío o demasiado corto es rechazado con 422
    bad_discard = client.post(
        f"/discovery/candidates/{target_id}/discard",
        json={"reason": "   "},
    )
    assert bad_discard.status_code == 422

    # Descarte válido persiste estado y motivo
    discard_resp = client.post(
        f"/discovery/candidates/{target_id}/discard",
        json={"reason": "Portal duplicado con datos ya cubiertos por BCB"},
    )
    assert discard_resp.status_code == 200
    discarded = discard_resp.json()
    assert discarded["status"] == "DESCARTADA"
    assert "duplicado" in discarded["discard_reason"]
    assert discarded["discarded_at"] is not None

    # Una nueva búsqueda NO debe devolver el candidato descartado por defecto
    second_search = client.post("/discovery/open-search?live_probe=false", json=search_payload)
    assert second_search.status_code == 200
    second_ids = {item["candidate_id"] for item in second_search.json()}
    assert target_id not in second_ids

    # Listado con include_discarded=false lo excluye; include_discarded=true lo muestra
    active_list = client.get("/discovery/candidates?include_discarded=false").json()
    assert target_id not in {item["candidate_id"] for item in active_list}
    all_list = client.get("/discovery/candidates?include_discarded=true").json()
    assert target_id in {item["candidate_id"] for item in all_list}

    # Reactivación explícita lo devuelve a PRESELECCIONADA
    reactivate_resp = client.post(f"/discovery/candidates/{target_id}/reactivate")
    assert reactivate_resp.status_code == 200
    reactivated = reactivate_resp.json()
    assert reactivated["status"] == "PRESELECCIONADA"
    assert reactivated["discard_reason"] is None

    third_search = client.post("/discovery/open-search?live_probe=false", json=search_payload)
    assert target_id in {item["candidate_id"] for item in third_search.json()}


def test_task_1_3_scientific_formats_http_metadata_304_and_archives() -> None:
    # 1. Formatos científicos y extensión compuesta .tar.gz
    cases = {
        "https://datos.gob.bo/dataset/serie.parquet": (".parquet", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/series.json": (".json", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/indicadores.xml": (".xml", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/microdatos.sav": (".sav", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/encuesta.dta": (".dta", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/raw.dat": (".dat", "TABULAR_CIENTIFICO"),
        "https://datos.gob.bo/dataset/log.txt": (".txt", "DOCUMENTAL"),
        "https://datos.gob.bo/dataset/paquete.tar.gz": (".tar.gz", "COMPRIMIDO"),
        "https://datos.gob.bo/dataset/archivo.rar": (".rar", "COMPRIMIDO"),
        "https://datos.gob.bo/dataset/boletin.pdf": (".pdf", "DOCUMENTAL"),
    }
    for url, (expected_ext, expected_cat) in cases.items():
        ext = ResourceDetector.extension_for(url)
        assert ext == expected_ext
        assert ResourceDetector.classify_resource_category(ext) == expected_cat

    # 2. Captura de ETag, Last-Modified, Content-Length y Content-Disposition (RFC 5987 y quoted)
    assert (
        BaseWorkflow.extract_content_disposition_filename(
            "attachment; filename*=UTF-8''serie%20bruta%202026.parquet"
        )
        == "serie bruta 2026.parquet"
    )
    cand = ResourceCandidate(
        resource_key="rk_meta_1",
        url="https://datos.gob.bo/download?id=99",
        source_id="test_src",
        title="Serie",
        file_extension=".parquet",
    )
    BaseWorkflow.apply_http_metadata(
        cand,
        {
            "ETag": '"abc123etag"',
            "Last-Modified": "Wed, 15 Apr 2026 12:00:00 GMT",
            "Content-Length": "1048576",
            "Content-Type": "application/vnd.apache.parquet",
            "Content-Disposition": 'attachment; filename="serie_2026.parquet"',
        },
        status=200,
    )
    assert cand.etag == '"abc123etag"'
    assert cand.last_modified_header == "Wed, 15 Apr 2026 12:00:00 GMT"
    assert cand.content_length_bytes == 1048576
    assert cand.declared_filename == "serie_2026.parquet"
    assert cand.resource_category == "TABULAR_CIENTIFICO"

    # 3 y 4. Revalidación condicional HTTP 304 e inspección de archivos .zip y .tar.gz sintéticos
    from prospector_externo.domain.observations import CoverageStats
    from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("2026/balance.csv", "col1,col2\n1,2\n")
        zf.writestr("2026/metadatos.json", '{"v": 1}')
    zip_bytes = zip_buf.getvalue()

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        payload_bytes = b"a,b\n10,20\n"
        info = tarfile.TarInfo(name="datos/serie.csv")
        info.size = len(payload_bytes)
        tf.addfile(info, io.BytesIO(payload_bytes))
    tar_bytes = tar_buf.getvalue()

    seen_head_headers: dict[str, str] = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.method == "HEAD":
            seen_head_headers.update({k.lower(): v for k, v in request.headers.items()})
            return httpx.Response(304, headers={"ETag": '"abc123etag"'})
        if str(request.url).endswith(".zip"):
            return httpx.Response(
                200,
                content=zip_bytes,
                headers={"Content-Type": "application/zip", "Content-Length": str(len(zip_bytes))},
            )
        if str(request.url).endswith(".tar.gz"):
            return httpx.Response(
                200,
                content=tar_bytes,
                headers={"Content-Type": "application/gzip", "Content-Length": str(len(tar_bytes))},
            )
        return httpx.Response(404)

    async def _run_http_checks() -> tuple[ResourceCandidate, list[ResourceCandidate]]:
        runtime = AsyncHttpRuntime(transport=httpx.MockTransport(mock_handler))
        cfg = SourceConfig(
            source_id="src_arch",
            name="Archivo",
            entrypoint="https://datos.gob.bo",
            workflow="html",
            allowed_extensions=[".csv", ".json", ".zip", ".tar.gz"],
            ignore_robots_txt=False,
            inspect_archives=True,
            max_resources=20,
        )
        wf = HtmlWorkflow(http_session=runtime.session_for(cfg))
        try:
            updated = await wf.revalidate_resource_headers(
                cand,
                previous_etag='"abc123etag"',
                previous_last_modified="Wed, 15 Apr 2026 12:00:00 GMT",
            )
            zip_cand = ResourceCandidate(
                resource_key="rk_zip",
                url="https://datos.gob.bo/paquete_2026.zip",
                source_id="src_arch",
                title="Paquete ZIP",
                file_extension=".zip",
            )
            tar_cand = ResourceCandidate(
                resource_key="rk_tar",
                url="https://datos.gob.bo/paquete_2026.tar.gz",
                source_id="src_arch",
                title="Paquete TAR.GZ",
                file_extension=".tar.gz",
            )
            resources = [zip_cand, tar_cand]
            cov = CoverageStats()
            await wf._inspect_archive_candidates(config=cfg, resources=resources, coverage=cov)
            return updated, resources
        finally:
            await runtime.aclose()

    revalidated_cand, expanded = asyncio.run(_run_http_checks())
    assert revalidated_cand.http_status == 304
    assert revalidated_cand.revalidated_not_modified is True
    assert seen_head_headers.get("if-none-match") == '"abc123etag"'
    assert seen_head_headers.get("if-modified-since") == "Wed, 15 Apr 2026 12:00:00 GMT"

    zip_cand, tar_cand = expanded[0], expanded[1]
    assert {e["filename"] for e in zip_cand.archive_entries} == {
        "2026/balance.csv",
        "2026/metadatos.json",
    }
    assert {e["filename"] for e in tar_cand.archive_entries} == {"datos/serie.csv"}
    member_titles = {r.title for r in expanded if r.extracted_from_archive}
    assert "2026/balance.csv" in member_titles
    assert "datos/serie.csv" in member_titles


def test_task_1_4_dry_run_no_mutation_and_cancel_with_sse(tmp_path: Path, monkeypatch) -> None:
    client, repo, cfg_path = _build_api_client(tmp_path)
    yaml_before = cfg_path.read_text(encoding="utf-8")

    # Mockear SourceDispatcher.dispatch para que dry-run devuelva muestras sin red externa
    from prospector_externo.application.dispatcher import SourceDispatcher

    def fake_dispatch_dry(self, config: SourceConfig) -> ExtractionResult:
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[
                ResourceCandidate(
                    resource_key="rk_dry_1",
                    url="https://datos.prueba.gob.bo/raw/serie_2026.parquet",
                    source_id=config.source_id,
                    title="Serie Cruda 2026",
                    file_extension=".parquet",
                    resource_category="DATOS_TABULARES",
                    discovery_method="html_link",
                    content_length_bytes=4096,
                )
            ],
        )

    monkeypatch.setattr(SourceDispatcher, "dispatch", fake_dispatch_dry)

    dry_resp = client.post(
        "/catalog/sources/portal_test/dry-run",
        json={"max_pages": 2, "max_resources": 10},
    )
    assert dry_resp.status_code == 200
    body = dry_resp.json()
    assert body["success"] is True
    assert body["mutated_catalog"] is False
    assert body["total_resources_detected"] == 1
    assert body["sample_resources"][0]["file_extension"] == ".parquet"

    # Cero mutaciones en catálogo, reportes ni sources.yaml
    assert repo.list_resources(source_id="portal_test") == []
    assert repo.list_reports() == []
    assert cfg_path.read_text(encoding="utf-8") == yaml_before

    # Cancelación limpia en RunOrchestrator -> RunStatus.CANCELLED + eventos SSE
    control = RunControlRegistry()
    orchestrator = RunOrchestrator(
        catalog_repo=repo,
        report_repo=repo,
        config_path=cfg_path,
        run_control=control,
    )

    async def slow_dispatch(config: SourceConfig) -> ExtractionResult:
        await asyncio.sleep(5.0)
        return ExtractionResult(source_id=config.source_id, success=True, resources=[])

    monkeypatch.setattr(orchestrator.dispatcher, "dispatch", slow_dispatch)

    run_id = "run_cancel_test_001"
    control.register(run_id)

    async def _run_and_cancel() -> None:
        task = asyncio.create_task(
            orchestrator.run_batch_async(source_id_filter="portal_test", force=True, run_id=run_id)
        )
        await asyncio.sleep(0.15)
        assert control.request_cancel(run_id) is True
        await task

    asyncio.run(_run_and_cancel())

    saved_run = repo.get_run(run_id)
    assert saved_run is not None
    assert saved_run.status == RunStatus.CANCELLED
    assert saved_run.failure_code == "CANCELLED_BY_OPERATOR"
    assert repo.get_report(run_id) is not None

    # Stream SSE sobre corrida completada/cancelada devuelve eventos válidos text/event-stream
    sse_resp = client.get(f"/runs/{run_id}/stream")
    assert sse_resp.status_code == 200
    assert sse_resp.headers["content-type"].startswith("text/event-stream")
    assert "event: run_cancelled" in sse_resp.text
    data_lines = [line for line in sse_resp.text.splitlines() if line.startswith("data: ")]
    assert len(data_lines) >= 1
    parsed_ev = json.loads(data_lines[0].removeprefix("data: "))
    assert parsed_ev["run_id"] == run_id
    assert parsed_ev["data"]["status"] == "CANCELLED"

    # Cancelar una corrida ya finalizada devuelve 409; desconocida devuelve 404
    assert client.post(f"/runs/{run_id}/cancel").status_code == 409
    assert client.post("/runs/run_inexistente/cancel").status_code == 404
    assert client.get("/runs/run_inexistente/stream").status_code == 404
