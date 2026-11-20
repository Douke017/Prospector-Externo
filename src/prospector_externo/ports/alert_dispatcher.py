"""Puerto de despacho multicanal de alertas estructurales (Objetivo 5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Protocol
import uuid

from pydantic import BaseModel, Field

from prospector_externo.domain.structural_monitor import (
    AlertDeliveryLog,
    AlertSeverity,
    StructuralAlert,
)


ChannelType = Literal["WEBHOOK", "SLACK", "TEAMS", "SMTP", "TAIGA"]


class AlertChannelConfig(BaseModel):
    """Configuración declarativa de un canal de notificación push (sin secretos planos)."""

    channel_id: str = Field(default_factory=lambda: f"chn_{uuid.uuid4().hex[:8]}")
    name: str = Field(..., min_length=2, max_length=120)
    channel_type: ChannelType
    enabled: bool = True
    min_severity: AlertSeverity = "ALTA"
    endpoint_url: Optional[str] = None
    secret_env_var: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_use_tls: bool = True
    smtp_from: Optional[str] = None
    smtp_recipients: List[str] = Field(default_factory=list)
    smtp_user_env_var: Optional[str] = None
    smtp_password_env_var: Optional[str] = None
    taiga_project_id: Optional[int] = None
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertDispatcherPort(Protocol):
    """Contrato para despachadores de alertas hacia canales técnicos externos."""

    def supports(self, channel_type: ChannelType) -> bool:
        """Indica si el adaptador soporta el tipo de canal solicitado."""
        ...

    def dispatch(
        self,
        channel: AlertChannelConfig,
        alert: StructuralAlert,
        *,
        markdown_body: Optional[str] = None,
    ) -> AlertDeliveryLog:
        """Despacha una alerta y devuelve el registro auditable del intento de entrega."""
        ...
