"""Servicio de gestión, deduplicación y despacho multicanal de alertas (Objetivo 5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Sequence

from prospector_externo.adapters.alerts.dispatchers import (
    SlackTeamsAlertDispatcher,
    SmtpAlertDispatcher,
    TaigaApiAlertDispatcher,
    WebhookAlertDispatcher,
)
from prospector_externo.domain.models import Source
from prospector_externo.domain.structural_monitor import (
    AlertDeliveryLog,
    AlertSeverity,
    AlertStatus,
    StructuralAlert,
    build_maintenance_report,
)
from prospector_externo.ports.alert_dispatcher import AlertChannelConfig, AlertDispatcherPort
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


_SEVERITY_RANK: dict[AlertSeverity, int] = {
    "MEDIA": 1,
    "ALTA": 2,
    "CRITICA": 3,
}


class AlertNotificationService:
    """Deduplica alertas recurrentes, administra su ciclo de vida y despacha push."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        dispatchers: Optional[Sequence[AlertDispatcherPort]] = None,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.dispatchers: list[AlertDispatcherPort] = (
            list(dispatchers)
            if dispatchers is not None
            else [
                WebhookAlertDispatcher(),
                SlackTeamsAlertDispatcher(),
                SmtpAlertDispatcher(),
                TaigaApiAlertDispatcher(),
            ]
        )

    @staticmethod
    def _is_active_for_dedup(alert: StructuralAlert, now: datetime) -> bool:
        if alert.status == "RESUELTA" or alert.resolved:
            return False
        if alert.status == "SILENCIADA" and alert.muted_until is not None:
            muted_utc = (
                alert.muted_until
                if alert.muted_until.tzinfo is not None
                else alert.muted_until.replace(tzinfo=timezone.utc)
            )
            if muted_utc <= now:
                return False
        return True

    def record_and_dispatch(
        self,
        alert: StructuralAlert,
        *,
        source: Optional[Source] = None,
    ) -> tuple[StructuralAlert, bool]:
        """Registra la alerta con deduplicación; devuelve (alerta_persistida, es_nueva)."""
        now = datetime.now(timezone.utc)
        if not alert.dedup_key:
            alert.dedup_key = alert.compute_dedup_key()

        existing_alerts = self.catalog_repo.list_structural_alerts(source_id=alert.source_id)
        for existing in existing_alerts:
            existing_key = existing.dedup_key or existing.compute_dedup_key()
            if existing_key == alert.dedup_key and self._is_active_for_dedup(existing, now):
                existing.occurrences += 1
                existing.last_seen_at = now
                if alert.structural_diff is not None:
                    existing.structural_diff = alert.structural_diff
                if alert.diagnostic_summary:
                    existing.diagnostic_summary = alert.diagnostic_summary
                self.catalog_repo.save_structural_alert(existing)
                return existing, False

        # Nueva alerta: despachar si severidad es ALTA o CRITICA (y cumple min_severity del canal)
        markdown_body: Optional[str] = None
        if source is not None:
            markdown_body = build_maintenance_report(source, alert).taiga_markdown

        if _SEVERITY_RANK.get(alert.severity, 1) >= _SEVERITY_RANK["ALTA"]:
            logs = self._dispatch_to_channels(alert, markdown_body=markdown_body)
            alert.delivery_logs.extend(logs)

        alert.last_seen_at = now
        self.catalog_repo.save_structural_alert(alert)
        return alert, True

    def test_channel(self, channel: AlertChannelConfig) -> AlertDeliveryLog:
        """Envía una alerta sintética de prueba a un canal configurado."""
        probe_alert = StructuralAlert(
            alert_id="alrt_probe_test",
            source_id="system_probe",
            source_name="Prueba de Conectividad DATAX",
            entrypoint="https://datax.local/health",
            alert_code="DOM_CHANGED",
            severity="ALTA",
            diagnostic_summary="Verificación de entrega push desde el módulo de alertas DATAX.",
        )
        for dispatcher in self.dispatchers:
            if dispatcher.supports(channel.channel_type):
                return dispatcher.dispatch(
                    channel,
                    probe_alert,
                    markdown_body="Prueba de conectividad de canal de alertas DATAX.",
                )
        return AlertDeliveryLog(
            channel_id=channel.channel_id,
            channel_type=channel.channel_type,
            channel_name=channel.name,
            delivered=False,
            detail=f"Sin despachador registrado para tipo '{channel.channel_type}'.",
        )

    def update_alert_lifecycle(
        self,
        alert_id: str,
        *,
        status: AlertStatus,
        assignee: Optional[str] = None,
        ticket_url: Optional[str] = None,
        muted_until: Optional[datetime] = None,
        resolution_note: Optional[str] = None,
    ) -> StructuralAlert:
        """Transiciona el ciclo de vida de una alerta validando reglas de negocio."""
        now = datetime.now(timezone.utc)
        if status == "SILENCIADA":
            if muted_until is None:
                raise ValueError("El estado SILENCIADA exige fecha de expiración 'muted_until'.")
            muted_utc = (
                muted_until
                if muted_until.tzinfo is not None
                else muted_until.replace(tzinfo=timezone.utc)
            )
            if muted_utc <= now:
                raise ValueError("La fecha 'muted_until' debe ser posterior al instante actual.")

        target = self._find_alert(alert_id)
        if target is None:
            raise KeyError(f"Alerta '{alert_id}' no encontrada")

        target.status = status
        target.resolved = status == "RESUELTA"
        if assignee is not None:
            target.assignee = assignee.strip() or None
        if ticket_url is not None:
            target.ticket_url = ticket_url.strip() or None
        if muted_until is not None:
            target.muted_until = muted_until
        elif status != "SILENCIADA":
            target.muted_until = None
        if resolution_note is not None:
            target.resolution_note = resolution_note.strip() or None
        if status == "RESUELTA":
            target.resolved_at = now
        else:
            target.resolved_at = None

        self.catalog_repo.save_structural_alert(target)
        return target

    def _find_alert(self, alert_id: str) -> Optional[StructuralAlert]:
        for alert in self.catalog_repo.list_structural_alerts():
            if alert.alert_id == alert_id:
                return alert
        return None

    def _dispatch_to_channels(
        self,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> List[AlertDeliveryLog]:
        logs: list[AlertDeliveryLog] = []
        alert_rank = _SEVERITY_RANK.get(alert.severity, 1)
        for channel in self.catalog_repo.list_alert_channels():
            if not channel.enabled:
                continue
            if alert_rank < _SEVERITY_RANK.get(channel.min_severity, 2):
                continue
            for dispatcher in self.dispatchers:
                if dispatcher.supports(channel.channel_type):
                    logs.append(
                        dispatcher.dispatch(channel, alert, markdown_body=markdown_body)
                    )
                    break
        return logs
