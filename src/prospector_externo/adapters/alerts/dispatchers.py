"""Adaptadores de notificación push: Webhook JSON, Slack/Teams, SMTP y API Taiga."""

from __future__ import annotations

from email.message import EmailMessage
import hashlib
import hmac
import json
import os
import smtplib
from typing import Callable, Optional

import httpx

from prospector_externo.domain.structural_monitor import AlertDeliveryLog, StructuralAlert
from prospector_externo.ports.alert_dispatcher import AlertChannelConfig, ChannelType


class WebhookAlertDispatcher:
    """Despacha alertas en JSON firmado opcionalmente con HMAC-SHA256 vía env var."""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    def supports(self, channel_type: ChannelType) -> bool:
        return channel_type == "WEBHOOK"

    def dispatch(
        self,
        channel: AlertChannelConfig,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> AlertDeliveryLog:
        if not channel.endpoint_url:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail="endpoint_url no configurado para el webhook.",
            )
        body_dict = {
            "event": "structural_alert",
            "alert": alert.model_dump(mode="json"),
            "markdown": markdown_body,
        }
        raw_body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **channel.extra_headers}
        if channel.secret_env_var:
            secret = os.environ.get(channel.secret_env_var, "")
            if secret:
                sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
                headers["X-DATAX-Signature"] = f"sha256={sig}"

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(channel.endpoint_url, content=raw_body, headers=headers)
            ok = 200 <= resp.status_code < 300
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=ok,
                http_status=resp.status_code,
                detail="Entregado" if ok else f"HTTP {resp.status_code}: {resp.text[:160]}",
            )
        except httpx.HTTPError as exc:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail=f"Error HTTP: {type(exc).__name__}: {str(exc)[:160]}",
            )


class SlackTeamsAlertDispatcher:
    """Despacha notificaciones estructuradas hacia Incoming Webhooks de Slack o MS Teams."""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    def supports(self, channel_type: ChannelType) -> bool:
        return channel_type in ("SLACK", "TEAMS")

    def dispatch(
        self,
        channel: AlertChannelConfig,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> AlertDeliveryLog:
        if not channel.endpoint_url:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail="URL de webhook de mensajería no configurada.",
            )
        headline = f"[{alert.severity}] [{alert.alert_code}] {alert.source_name} ({alert.source_id})"
        if channel.channel_type == "SLACK":
            payload = {
                "text": f"{headline}\n{alert.diagnostic_summary}\nURL: {alert.entrypoint}",
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text", "text": headline[:150]}},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Diagnóstico:* {alert.diagnostic_summary}\n"
                                f"*Fuente:* `{alert.source_id}` | *Ocurrencias:* {alert.occurrences}\n"
                                f"*Enlace:* {alert.entrypoint}"
                            ),
                        },
                    },
                ],
            }
        else:
            payload = {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": headline,
                "themeColor": "DC2626" if alert.severity == "CRITICA" else "D97706",
                "title": headline,
                "text": markdown_body or alert.diagnostic_summary,
            }

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(channel.endpoint_url, json=payload, headers=channel.extra_headers)
            ok = 200 <= resp.status_code < 300
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=ok,
                http_status=resp.status_code,
                detail="Entregado" if ok else f"HTTP {resp.status_code}: {resp.text[:160]}",
            )
        except httpx.HTTPError as exc:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail=f"Error de envío: {type(exc).__name__}",
            )


class SmtpAlertDispatcher:
    """Despacha alertas técnicas por correo SMTP leyendo credenciales desde variables de entorno."""

    def __init__(
        self,
        *,
        smtp_factory: Optional[Callable[[str, int], smtplib.SMTP]] = None,
        timeout: float = 10.0,
    ) -> None:
        self._smtp_factory = smtp_factory
        self.timeout = timeout

    def supports(self, channel_type: ChannelType) -> bool:
        return channel_type == "SMTP"

    def dispatch(
        self,
        channel: AlertChannelConfig,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> AlertDeliveryLog:
        if not channel.smtp_host or not channel.smtp_recipients:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail="Falta smtp_host o destinatarios (smtp_recipients).",
            )
        msg = EmailMessage()
        msg["Subject"] = (
            f"[DATAX Prospector][{alert.severity}][{alert.alert_code}] {alert.source_name}"
        )
        msg["From"] = channel.smtp_from or "alertas-prospector@datax.local"
        msg["To"] = ", ".join(channel.smtp_recipients)
        msg.set_content(markdown_body or alert.diagnostic_summary)

        user = os.environ.get(channel.smtp_user_env_var, "") if channel.smtp_user_env_var else ""
        password = (
            os.environ.get(channel.smtp_password_env_var, "")
            if channel.smtp_password_env_var
            else ""
        )

        try:
            server = (
                self._smtp_factory(channel.smtp_host, channel.smtp_port)
                if self._smtp_factory is not None
                else smtplib.SMTP(channel.smtp_host, channel.smtp_port, timeout=self.timeout)
            )
            with server as conn:
                if channel.smtp_use_tls and hasattr(conn, "starttls"):
                    conn.starttls()
                if user and password:
                    conn.login(user, password)
                conn.send_message(msg)
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=True,
                detail=f"Correo enviado a {len(channel.smtp_recipients)} destinatario(s).",
            )
        except Exception as exc:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail=f"Error SMTP: {type(exc).__name__}: {str(exc)[:160]}",
            )


class TaigaApiAlertDispatcher:
    """Crea incidencias técnicas directamente en la API de Taiga usando token en env var."""

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.timeout = timeout
        self.transport = transport

    def supports(self, channel_type: ChannelType) -> bool:
        return channel_type == "TAIGA"

    def dispatch(
        self,
        channel: AlertChannelConfig,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> AlertDeliveryLog:
        if not channel.endpoint_url:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail="endpoint_url de API Taiga no configurado.",
            )
        token = os.environ.get(channel.secret_env_var, "") if channel.secret_env_var else ""
        headers = {"Content-Type": "application/json", **channel.extra_headers}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "project": channel.taiga_project_id,
            "subject": f"[{alert.alert_code}] {alert.source_name} ({alert.source_id})",
            "description": markdown_body or alert.diagnostic_summary,
            "tags": ["prospector-externo", alert.alert_code.lower(), alert.severity.lower()],
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.post(channel.endpoint_url, json=payload, headers=headers)
            ok = 200 <= resp.status_code < 300
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=ok,
                http_status=resp.status_code,
                detail="Incidencia creada en Taiga" if ok else f"HTTP {resp.status_code}",
            )
        except httpx.HTTPError as exc:
            return AlertDeliveryLog(
                channel_id=channel.channel_id,
                channel_type=channel.channel_type,
                channel_name=channel.name,
                delivered=False,
                detail=f"Error Taiga API: {type(exc).__name__}",
            )
