"""Pruebas del Bloque 2: Resiliencia Automática, Versionamiento, Estado Auditable y Alertas Push."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import yaml

from prospector_externo.adapters.alerts.dispatchers import (
    SlackTeamsAlertDispatcher,
    SmtpAlertDispatcher,
    TaigaApiAlertDispatcher,
    WebhookAlertDispatcher,
)
from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import (
    LocalJsonRepositoryAdapter,
)
from prospector_externo.application.alert_notification_service import AlertNotificationService
from prospector_externo.application.contingency_service import (
    ContingencyDiscoveryService,
    classify_backup_freshness,
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
    evaluate_structure_payload,
    evaluate_volume_drop,
)
from prospector_externo.infrastructure.tls_inspector import (
    TlsInspectionResult,
    TlsX509Inspector,
)
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.ports.alert_dispatcher import AlertChannelConfig


class _OkTlsInspector(TlsX509Inspector):
    def inspect(self, url: str) -> TlsInspectionResult:
        return TlsInspectionResult(tls_valid=True, protocol="TLSv1.3", issuer="CN=TestCA")


def _build_client(tmp_path: Path) -> tuple[TestClient, LocalJsonRepositoryAdapter, Path]:
    out_dir = tmp_path / "output"
    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "source_id": "src_bcb",
                        "name": "Banco Central",
                        "entrypoint": "https://www.bcb.gob.bo/datos",
                        "workflow": "html",
                        "update_category": "DAILY",
                        "contingency_urls": [
                            "https://web.archive.org/web/20250101120000/https://www.bcb.gob.bo/datos",
                            "https://mirror.bcb.gob.bo/datos",
                        ],
                    },
                    {
                        "source_id": "src_spof",
                        "name": "Portal Sin Respaldo",
                        "entrypoint": "https://sinrespaldo.gob.bo/series",
                        "workflow": "html",
                        "update_category": "WEEKLY",
                        "contingency_urls": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    repo = LocalJsonRepositoryAdapter(out_dir)
    repo.save_source(
        Source(
            source_id="src_bcb",
            name="Banco Central",
            entrypoint="https://www.bcb.gob.bo/datos",
            workflow="html",
            update_category="DAILY",
            contingency_urls=[
                "https://web.archive.org/web/20250101120000/https://www.bcb.gob.bo/datos",
                "https://mirror.bcb.gob.bo/datos",
            ],
        )
    )
    repo.save_source(
        Source(
            source_id="src_spof",
            name="Portal Sin Respaldo",
            entrypoint="https://sinrespaldo.gob.bo/series",
            workflow="html",
            update_category="WEEKLY",
            contingency_urls=[],
        )
    )
    app = FastAPI()
    app.include_router(
        create_catalog_router(catalog_repo=repo, report_repo=repo, config_path=cfg_path)
    )
    return TestClient(app), repo, cfg_path


def test_task_2_1_wayback_never_replaces_active_source_failover_log_and_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    client, repo, cfg_path = _build_client(tmp_path)

    # 1. Clasificación estricta de frescura
    wb_class, wb_ts = classify_backup_freshness(
        "https://web.archive.org/web/20250101120000/https://www.bcb.gob.bo/datos", "WAYBACK_CDX"
    )
    assert wb_class == "HISTORICO_SOLO_LECTURA"
    assert wb_ts == "2025-01-01T12:00:00+00:00"
    live_class, _ = classify_backup_freshness("https://mirror.bcb.gob.bo/datos")
    assert live_class == "ESPEJO_VIVO"

    # 2. ContingencyDiscoveryService nunca auto-registra Wayback en fuentes DAILY
    def mock_http(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "cdx/search/cdx" in u:
            return httpx.Response(
                200,
                json=[
                    ["timestamp", "original", "statuscode", "mimetype"],
                    ["20250101120000", "https://sinrespaldo.gob.bo/series", "200", "text/html"],
                ],
            )
        if "datos.gob.bo/api/3/action/package_search" in u:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "results": [
                            {
                                "resources": [
                                    {"url": "https://mirror.datos.gob.bo/series/2026.parquet"}
                                ]
                            }
                        ]
                    }
                },
            )
        if "web.archive.org/web/" in u:
            return httpx.Response(
                200, text="<html><a href='archivo.csv'>csv</a></html>", headers={"Content-Type": "text/html"}
            )
        if "mirror.datos.gob.bo" in u:
            return httpx.Response(
                200, content=b"PAR1dummy", headers={"Content-Type": "application/octet-stream"}
            )
        return httpx.Response(404)

    cont_svc = ContingencyDiscoveryService(
        repo,
        tls_inspector=_OkTlsInspector(),
        http_transport=httpx.MockTransport(mock_http),
    )
    spof_src = repo.get_source("src_spof")
    assert spof_src is not None
    eval_res = cont_svc.discover_and_evaluate(spof_src, auto_register=True)
    assert any(c.freshness_class == "HISTORICO_SOLO_LECTURA" and not c.eligible_for_live_failover for c in eval_res.candidates)
    assert all("web.archive.org" not in u for u in eval_res.updated_contingency_urls)
    assert "https://mirror.datos.gob.bo/series/2026.parquet" in eval_res.updated_contingency_urls

    # 3. En RunOrchestrator, Wayback es omitido y conmuta al espejo vivo, registrando failover_events con latencia
    orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=cfg_path)
    visited: list[str] = []

    def fake_dispatch(config: SourceConfig) -> ExtractionResult:
        visited.append(config.entrypoint)
        if "mirror.bcb.gob.bo" not in config.entrypoint:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="HTTP_503",
                resources=[],
            )
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[
                ResourceCandidate(
                    resource_key="rk_live_1",
                    url="https://mirror.bcb.gob.bo/datos/serie_2026.parquet",
                    source_id=config.source_id,
                    title="Serie Espejo",
                    file_extension=".parquet",
                    content_hash="a" * 64,
                )
            ],
        )

    monkeypatch.setattr(orchestrator.dispatcher, "dispatch", fake_dispatch)
    orchestrator.run_batch(source_id_filter="src_bcb", force=True)

    # Nunca visitó web.archive.org para una fuente DAILY
    assert visited == ["https://www.bcb.gob.bo/datos", "https://mirror.bcb.gob.bo/datos"]

    failovers_resp = client.get("/catalog/sources/src_bcb/failovers")
    assert failovers_resp.status_code == 200
    events = failovers_resp.json()
    assert any(ev["succeeded"] is True and ev["resolved_url"] == "https://mirror.bcb.gob.bo/datos" and ev["latency_ms"] >= 0 for ev in events)
    assert any(ev["freshness_class"] == "HISTORICO_SOLO_LECTURA" and ev["succeeded"] is False for ev in events)

    # 4. Cobertura de resiliencia detecta fuentes con 0 respaldos
    repo.save_source(
        Source(
            source_id="src_zero_backup",
            name="Sin Backup Real",
            entrypoint="https://cero.gob.bo",
            workflow="html",
            contingency_urls=[],
        )
    )
    cov_resp = client.get("/catalog/resilience/coverage")
    assert cov_resp.status_code == 200
    cov = cov_resp.json()
    assert cov["zero_backup_sources"] >= 1
    assert any(item["source_id"] == "src_zero_backup" and item["risk_level"] == "PUNTO_UNICO_DE_FALLO" for item in cov["single_points_of_failure"])


def test_task_2_2_audited_status_machine_versioning_and_manifest_exports(
    tmp_path: Path, monkeypatch
) -> None:
    client, repo, cfg_path = _build_client(tmp_path)
    orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=cfg_path)

    version_state = {"hash": "1" * 64, "etag": '"v1-etag"', "size": 1024}

    def fake_dispatch(config: SourceConfig) -> ExtractionResult:
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[
                ResourceCandidate(
                    resource_key="rk_ver_01",
                    url="https://www.bcb.gob.bo/datos/serie_2026.parquet",
                    source_id=config.source_id,
                    title="Serie Monetaria Cruda",
                    file_extension=".parquet",
                    resource_category="TABULAR_CIENTIFICO",
                    content_hash=version_state["hash"],
                    etag=version_state["etag"],
                    content_length_bytes=version_state["size"],
                )
            ],
        )

    monkeypatch.setattr(orchestrator.dispatcher, "dispatch", fake_dispatch)

    # Corrida 1 -> v1
    orchestrator.run_batch(source_id_filter="src_bcb", force=True)

    # Override manual SIN motivo -> HTTP 422
    no_reason = client.patch(
        "/catalog/resources/rk_ver_01/status",
        json={"processing_status": "ERROR", "actor": "MANUAL_OPERATOR"},
    )
    assert no_reason.status_code == 422

    # Callback idempotente desde PROSPECTOR_INTERNO -> 200 sin duplicar auditoría
    cb_payload = {
        "processing_status": "PROCESADO_EXITOSAMENTE",
        "actor": "PROSPECTOR_INTERNO",
        "download_id": "dl_9001",
        "sha256": "1" * 64,
        "idempotency_key": "idem_dl_9001_ok",
    }
    cb1 = client.patch("/catalog/resources/rk_ver_01/status", json=cb_payload)
    cb2 = client.patch("/catalog/resources/rk_ver_01/status", json=cb_payload)
    assert cb1.status_code == 200
    assert cb2.status_code == 200

    # Override manual CON motivo -> 200 + auditoría
    manual_ok = client.patch(
        "/catalog/resources/rk_ver_01/status",
        json={
            "processing_status": "PENDIENTE",
            "actor": "MANUAL_OPERATOR",
            "override_reason": "Re-procesamiento solicitado por verificación de esquema raw",
        },
    )
    assert manual_ok.status_code == 200

    audit_resp = client.get("/catalog/resources/rk_ver_01/audit")
    assert audit_resp.status_code == 200
    audit_events = audit_resp.json()
    assert len(audit_events) == 2  # idempotencia evitó el 3ro
    assert audit_events[0]["actor"] == "MANUAL_OPERATOR"
    assert "verificación" in audit_events[0]["override_reason"]
    assert audit_events[1]["actor"] == "PROSPECTOR_INTERNO"
    assert audit_events[1]["download_id"] == "dl_9001"

    # Corrida 2 con cambio de hash y ETag -> crea v2 preservando v1
    version_state["hash"] = "2" * 64
    version_state["etag"] = '"v2-etag"'
    version_state["size"] = 2048
    orchestrator.run_batch(source_id_filter="src_bcb", force=True)

    vers_resp = client.get("/catalog/resources/rk_ver_01/versions")
    assert vers_resp.status_code == 200
    versions = vers_resp.json()
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[0]["content_hash"] == "1" * 64
    assert versions[1]["version_number"] == 2
    assert versions[1]["content_hash"] == "2" * 64

    # Header X-Total-Count en /catalog/resources
    list_resp = client.get("/catalog/resources?source_id=src_bcb&sort_by=current_version&sort_dir=desc")
    assert list_resp.status_code == 200
    assert list_resp.headers.get("X-Total-Count") == "1"
    assert list_resp.json()[0]["current_version"] == 2

    # Exportación de manifiesto en JSONL, CSV y Parquet parseables
    jsonl_resp = client.get("/catalog/resources/export?format=jsonl&source_id=src_bcb")
    assert jsonl_resp.status_code == 200
    jsonl_rows = [json.loads(line) for line in jsonl_resp.text.strip().splitlines()]
    assert len(jsonl_rows) == 1
    assert jsonl_rows[0]["resource_key"] == "rk_ver_01"
    assert jsonl_rows[0]["version"] == 2

    csv_resp = client.get("/catalog/resources/export?format=csv&source_id=src_bcb")
    assert csv_resp.status_code == 200
    csv_rows = list(csv.DictReader(io.StringIO(csv_resp.text)))
    assert len(csv_rows) == 1
    assert csv_rows[0]["content_hash"] == "2" * 64

    pq_resp = client.get("/catalog/resources/export?format=parquet&source_id=src_bcb")
    assert pq_resp.status_code == 200
    assert pq_resp.content[:4] == b"PAR1"
    pq_file = tmp_path / "downloaded_manifest.parquet"
    pq_file.write_bytes(pq_resp.content)
    con = duckdb.connect(":memory:")
    try:
        pq_rows = con.execute(
            f"SELECT resource_key, version, content_hash FROM read_parquet('{pq_file.as_posix()}')"
        ).fetchall()
    finally:
        con.close()
    assert pq_rows == [("rk_ver_01", 2, "2" * 64)]


def test_task_2_3_structural_diff_html_json_and_volume_drop() -> None:
    # 1. HTML DOM antes y después
    src_html = Source(
        source_id="src_dom",
        name="Portal HTML",
        entrypoint="https://portal.gob.bo/boletines",
        workflow="html",
    )
    html_before = """
    <html><body>
      <section class="grid-boletines">
        <div class="card"><a href="/files/ene.xlsx">Enero</a></div>
      </section>
    </body></html>
    """
    html_after = """
    <html><body>
      <main id="v2-app">
        <table class="nueva-tabla"><tr><td><a href="/files/feb.parquet">Febrero</a></td></tr></table>
      </main>
    </body></html>
    """
    src_html, alert_base, _ = evaluate_structure_payload(src_html, html_before, content_type="text/html")
    assert alert_base is None
    assert len(src_html.baseline_selectors) >= 2

    src_html, alert_changed, report = evaluate_structure_payload(
        src_html, html_after, content_type="text/html"
    )
    assert alert_changed is not None
    assert alert_changed.alert_code == "DOM_CHANGED"
    assert alert_changed.structural_diff is not None
    assert len(alert_changed.structural_diff.removed_selectors) >= 1
    assert len(alert_changed.structural_diff.added_selectors) >= 1
    assert report.structural_diff is not None

    # 2. JSON API schema antes y después (cambio de tipo + campo eliminado/agregado)
    src_api = Source(
        source_id="src_api",
        name="API Datos",
        entrypoint="https://api.gob.bo/v1/series",
        workflow="api",
    )
    json_before = json.dumps({"data": [{"entidad": "A", "monto": 100.5, "legacy_col": "x"}]})
    json_after = json.dumps({"data": [{"entidad": "A", "monto": "100.5", "nuevo_campo": True}]})
    src_api, api_base, _ = evaluate_structure_payload(
        src_api, json_before, content_type="application/json"
    )
    assert api_base is None
    src_api, api_changed, _ = evaluate_structure_payload(
        src_api, json_after, content_type="application/json"
    )
    assert api_changed is not None
    assert api_changed.alert_code == "API_CHANGED"
    assert api_changed.structural_diff is not None
    assert any("monto" in f for f in api_changed.structural_diff.changed_fields)
    assert any("legacy_col" in s for s in api_changed.structural_diff.removed_selectors)
    assert any("nuevo_campo" in s for s in api_changed.structural_diff.added_selectors)

    # 3. Detección de caída abrupta de volumen (VOLUME_DROP) aun con HTTP 200
    no_drop = evaluate_volume_drop(src_html, current_resources_count=18, historical_counts=[20, 19, 21])
    assert no_drop is None

    drop_zero = evaluate_volume_drop(src_html, current_resources_count=0, historical_counts=[20, 19, 21])
    assert drop_zero is not None
    assert drop_zero.alert_code == "VOLUME_DROP"
    assert drop_zero.severity == "CRITICA"
    assert drop_zero.structural_diff is not None


def test_task_2_4_multichannel_dispatch_deduplication_and_lifecycle(tmp_path: Path) -> None:
    client, repo, _ = _build_client(tmp_path)

    webhook_hits: list[dict] = []
    smtp_sent: list[str] = []

    def mock_webhook_transport(request: httpx.Request) -> httpx.Response:
        webhook_hits.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True})

    class _FakeSmtp:
        def __enter__(self) -> "_FakeSmtp":
            return self

        def __exit__(self, *args) -> None:
            return None

        def starttls(self) -> None:
            return None

        def login(self, user: str, pwd: str) -> None:
            return None

        def send_message(self, msg) -> None:
            smtp_sent.append(str(msg["Subject"]))

    # Registrar canales Webhook y SMTP en el repositorio
    wh_channel = AlertChannelConfig(
        channel_id="chn_wh_1",
        name="Webhook Ingeniería",
        channel_type="WEBHOOK",
        endpoint_url="https://hooks.datax.internal/alerts",
        min_severity="ALTA",
    )
    smtp_channel = AlertChannelConfig(
        channel_id="chn_smtp_1",
        name="Correo Guardia",
        channel_type="SMTP",
        smtp_host="smtp.datax.internal",
        smtp_recipients=["datos@datax.internal"],
        min_severity="CRITICA",
    )
    repo.save_alert_channel(wh_channel)
    repo.save_alert_channel(smtp_channel)

    svc = AlertNotificationService(
        repo,
        dispatchers=[
            WebhookAlertDispatcher(transport=httpx.MockTransport(mock_webhook_transport)),
            SlackTeamsAlertDispatcher(transport=httpx.MockTransport(mock_webhook_transport)),
            SmtpAlertDispatcher(smtp_factory=lambda h, p: _FakeSmtp()),
            TaigaApiAlertDispatcher(transport=httpx.MockTransport(mock_webhook_transport)),
        ],
    )

    alert_1 = StructuralAlert(
        source_id="src_bcb",
        source_name="Banco Central",
        entrypoint="https://www.bcb.gob.bo/datos",
        alert_code="DOM_CHANGED",
        severity="CRITICA",
        previous_signature="sig_old",
        current_signature="sig_new_abc",
        diagnostic_summary="Contenedor principal de boletines eliminado.",
    )
    saved_1, is_new_1 = svc.record_and_dispatch(alert_1)
    assert is_new_1 is True
    assert saved_1.occurrences == 1
    assert len(webhook_hits) == 1
    assert len(smtp_sent) == 1
    assert len(saved_1.delivery_logs) == 2

    # Repetición de la misma alerta -> deduplica, incrementa occurrences, NO reenvía push
    alert_dup = StructuralAlert(
        source_id="src_bcb",
        source_name="Banco Central",
        entrypoint="https://www.bcb.gob.bo/datos",
        alert_code="DOM_CHANGED",
        severity="CRITICA",
        previous_signature="sig_old",
        current_signature="sig_new_abc",
        diagnostic_summary="Contenedor principal de boletines eliminado.",
    )
    saved_2, is_new_2 = svc.record_and_dispatch(alert_dup)
    assert is_new_2 is False
    assert saved_2.alert_id == saved_1.alert_id
    assert saved_2.occurrences == 2
    assert len(webhook_hits) == 1  # cero notificaciones duplicadas
    assert len(smtp_sent) == 1

    # API de ciclo de vida: SILENCIADA sin muted_until -> 422
    bad_mute = client.patch(
        f"/alerts/{saved_1.alert_id}",
        json={"status": "SILENCIADA"},
    )
    assert bad_mute.status_code == 422

    # SILENCIADA con expiración futura -> 200
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    ok_mute = client.patch(
        f"/alerts/{saved_1.alert_id}",
        json={
            "status": "SILENCIADA",
            "muted_until": future_iso,
            "assignee": "ing_datos_1",
        },
    )
    assert ok_mute.status_code == 200
    assert ok_mute.json()["status"] == "SILENCIADA"

    # Transición a RESUELTA con ticket y nota
    ok_res = client.patch(
        f"/alerts/{saved_1.alert_id}",
        json={
            "status": "RESUELTA",
            "assignee": "ing_datos_1",
            "ticket_url": "https://taiga.datax.bo/issue/412",
            "resolution_note": "Selectores CSS actualizados y dry-run verificado.",
        },
    )
    assert ok_res.status_code == 200
    resolved_body = ok_res.json()
    assert resolved_body["status"] == "RESUELTA"
    assert resolved_body["resolved"] is True
    assert resolved_body["ticket_url"] == "https://taiga.datax.bo/issue/412"

    # Endpoints de canales CRUD
    ch_create = client.post(
        "/alerts/channels",
        json={
            "name": "Slack Datos",
            "channel_type": "SLACK",
            "endpoint_url": "https://hooks.slack.com/services/T00/B00/XXX",
            "min_severity": "ALTA",
        },
    )
    assert ch_create.status_code == 200
    created_id = ch_create.json()["channel_id"]
    assert any(c["channel_id"] == created_id for c in client.get("/alerts/channels").json())
    assert client.delete(f"/alerts/channels/{created_id}").status_code == 200
