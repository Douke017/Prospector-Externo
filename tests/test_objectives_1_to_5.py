"""Pruebas integrales de los 5 Objetivos Específicos del Prospector Externo."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import (
    LocalJsonRepositoryAdapter,
)
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.models import (
    FileProcessingStatus,
    ResourceCandidate,
    Source,
    SourceConfig,
)
from prospector_externo.domain.structural_monitor import (
    StructuralAlert,
    build_maintenance_report,
    compute_api_schema_hash,
    compute_dom_skeleton_hash,
)
from prospector_externo.kernel.contracts import ExtractionResult


def _build_test_client(tmp_path: Path) -> tuple[TestClient, LocalJsonRepositoryAdapter, Path]:
    out_dir = tmp_path / "output"
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "source_id": "finrural",
                        "name": "FINRURAL",
                        "entrypoint": "https://www.finrural.org.bo",
                        "workflow": "html",
                        "contingency_urls": ["https://respaldo.finrural.org.bo"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repo = LocalJsonRepositoryAdapter(out_dir)
    app = FastAPI()
    app.include_router(
        create_catalog_router(catalog_repo=repo, report_repo=repo, config_path=cfg_path)
    )
    return TestClient(app), repo, cfg_path


def test_obj1_open_discovery_and_promotion(tmp_path: Path) -> None:
    client, repo, _ = _build_test_client(tmp_path)

    resp = client.post(
        "/discovery/open-search?live_probe=false",
        json={
            "query": "estadísticas cartera boletín financiero",
            "sector": "TODOS",
            "target_extensions": [".xlsx", ".pdf"],
            "trusted_domains_only": True,
            "min_combined_score": 40,
        },
    )
    assert resp.status_code == 200
    candidates = resp.json()
    assert len(candidates) >= 3
    first = candidates[0]
    assert first["combined_score"] >= 40
    assert first["status"] == "PRESELECCIONADA"

    promote_resp = client.post(
        f"/discovery/candidates/{first['candidate_id']}/promote",
        json={
            "source_id": "nueva_fuente_gob",
            "name": "Portal Institucional Promovido",
            "update_category": "MONTHLY",
            "workflow": "html",
        },
    )
    assert promote_resp.status_code == 200
    promoted = promote_resp.json()
    assert promoted["source_id"] == "nueva_fuente_gob"
    assert promoted["policy_customized"] is True
    assert repo.get_source("nueva_fuente_gob") is not None


def test_obj3_automatic_failover_with_contingency_urls(tmp_path: Path, monkeypatch) -> None:
    _, repo, cfg_path = _build_test_client(tmp_path)
    orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=cfg_path)

    visited_entrypoints: list[str] = []

    def fake_dispatch(config: SourceConfig) -> ExtractionResult:
        visited_entrypoints.append(config.entrypoint)
        if "respaldo.finrural" not in config.entrypoint:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="HTTP_5XX",
                resources=[],
            )
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[
                ResourceCandidate(
                    resource_key="rk_backup_01",
                    url="https://respaldo.finrural.org.bo/cartera_2026.xlsx",
                    source_id=config.source_id,
                    title="Cartera Respaldo 2026",
                    file_extension=".xlsx",
                    content_hash="a" * 64,
                )
            ],
        )

    monkeypatch.setattr(orchestrator.dispatcher, "dispatch", fake_dispatch)
    report = orchestrator.run_batch(force=True)
    assert report.sources_processed == 1
    assert visited_entrypoints == [
        "https://www.finrural.org.bo",
        "https://respaldo.finrural.org.bo",
    ]
    saved_source = repo.get_source("finrural")
    assert saved_source is not None
    assert saved_source.active_contingency_url == "https://respaldo.finrural.org.bo"


def test_obj4_file_processing_status_lifecycle(tmp_path: Path, monkeypatch) -> None:
    client, repo, cfg_path = _build_test_client(tmp_path)
    orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=cfg_path)

    current_hash = {"val": "1" * 64}

    def fake_dispatch(config: SourceConfig) -> ExtractionResult:
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[
                ResourceCandidate(
                    resource_key="rk_doc_01",
                    url="https://www.finrural.org.bo/boletin_ene2026.xlsx",
                    source_id=config.source_id,
                    title="Boletín Enero 2026",
                    file_extension=".xlsx",
                    content_hash=current_hash["val"],
                )
            ],
        )

    monkeypatch.setattr(orchestrator.dispatcher, "dispatch", fake_dispatch)
    orchestrator.run_batch(force=True)

    # Inicialmente PENDIENTE
    res_list = client.get("/catalog/resources?processing_status=PENDIENTE").json()
    assert len(res_list) == 1
    assert res_list[0]["processing_status"] == "PENDIENTE"

    # Sobreescritura manual sin motivo debe ser rechazada con 422 (v2.0)
    rejected_resp = client.patch(
        "/catalog/resources/rk_doc_01/status",
        json={"processing_status": "PROCESADO_EXITOSAMENTE"},
    )
    assert rejected_resp.status_code == 422

    # Callback desde PROSPECTOR_INTERNO marca PROCESADO_EXITOSAMENTE
    patch_resp = client.patch(
        "/catalog/resources/rk_doc_01/status",
        json={
            "processing_status": "PROCESADO_EXITOSAMENTE",
            "actor": "PROSPECTOR_INTERNO",
            "download_id": "dl_test_01",
            "sha256": "1" * 64,
        },
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["processing_status"] == "PROCESADO_EXITOSAMENTE"

    # Segunda corrida sin cambios preserva PROCESADO_EXITOSAMENTE
    orchestrator.run_batch(force=True)
    res_unchanged = client.get("/catalog/resources").json()
    assert res_unchanged[0]["processing_status"] == FileProcessingStatus.PROCESADO_EXITOSAMENTE.value

    # Tercera corrida con archivo modificado restablece PENDIENTE
    current_hash["val"] = "2" * 64
    orchestrator.run_batch(force=True)
    res_modified = client.get("/catalog/resources").json()
    assert res_modified[0]["change_status"] == "MODIFIED"
    assert res_modified[0]["processing_status"] == FileProcessingStatus.PENDIENTE.value


def test_obj5_structural_hashes_and_taiga_maintenance_report(tmp_path: Path) -> None:
    html_v1 = """
    <html><body>
      <section class="publicaciones-grid">
        <div class="card-reporte"><a href="/files/reporte_2026_01.xlsx">Enero 2026</a></div>
      </section>
    </body></html>
    """
    html_v2_same_structure = """
    <html><body>
      <section class="publicaciones-grid">
        <div class="card-reporte"><a href="/files/reporte_2026_02.xlsx">Febrero 2026</a></div>
      </section>
    </body></html>
    """
    html_v3_redesigned = """
    <html><body>
      <main id="nuevo-portal"><table class="tabla-datos"><tr><td>Sin enlaces</td></tr></table></main>
    </body></html>
    """

    h1, sel1 = compute_dom_skeleton_hash(html_v1)
    h2, _ = compute_dom_skeleton_hash(html_v2_same_structure)
    h3, _ = compute_dom_skeleton_hash(html_v3_redesigned)

    assert h1 == h2
    assert h1 != h3
    assert any("DOC:" in s for s in sel1)

    api_h1, _ = compute_api_schema_hash('{"data": [{"entidad": "BNI", "monto": 1500.5}]}')
    api_h2, _ = compute_api_schema_hash('{"data": [{"entidad": "FIE", "monto": 9999.0}]}')
    api_h3, _ = compute_api_schema_hash('{"resultados": [{"sigla": "FIE"}]}')
    assert api_h1 == api_h2
    assert api_h1 != api_h3

    client, repo, _ = _build_test_client(tmp_path)
    src = Source(
        source_id="bcb",
        name="Banco Central de Bolivia",
        entrypoint="https://www.bcb.gob.bo",
        workflow="html",
        health_status="WARNING",
        audit_code="DOM_CHANGED",
        audit_note="Reemplazo de contenedor de boletines semanales.",
        dom_structural_hash=h1,
        baseline_selectors=sel1,
        contingency_urls=["https://web.archive.org/web/20260101/https://www.bcb.gob.bo"],
    )
    repo.save_source(src)
    alert = StructuralAlert(
        source_id="bcb",
        source_name=src.name,
        entrypoint=src.entrypoint,
        alert_code="DOM_CHANGED",
        severity="CRITICA",
        previous_signature=h1,
        current_signature=h3,
        affected_selectors=sel1,
        diagnostic_summary="Mutación verificada en árbol DOM.",
    )
    repo.save_structural_alert(alert)

    alerts_resp = client.get("/alerts")
    assert alerts_resp.status_code == 200
    assert len(alerts_resp.json()) >= 1

    rep_resp = client.get("/alerts/bcb/report")
    assert rep_resp.status_code == 200
    rep_data = rep_resp.json()
    assert rep_data["alert_code"] == "DOM_CHANGED"
    assert "[MANTENIMIENTO PROSPECTOR]" in rep_data["taiga_markdown"]
    assert "Plan de Remediación Técnica" in rep_data["taiga_markdown"]
