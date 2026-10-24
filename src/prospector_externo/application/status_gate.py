"""
Filtro de elegibilidad operativa y de cadencia (SourceStatusGate).
Determina qué fuentes participan en la corrida actual según salud y cadencia.
"""

from datetime import datetime, timezone
from typing import Tuple, Optional
from prospector_externo.domain.models import Source, SourceConfig
from prospector_externo.domain.health import HealthStatus


class SourceStatusGate:
    """Evalúa si una fuente debe procesarse en la corrida actual."""

    @classmethod
    def evaluate_eligibility(
        cls,
        config: SourceConfig,
        source_state: Optional[Source],
        force: bool = False,
        now: Optional[datetime] = None,
        recovering_pipeline: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Retorna: (es_elegible, motivo_de_exclusion_si_aplica).
        """
        if force:
            return True, None

        if not source_state:
            # Fuente nueva: siempre elegible
            return True, None

        # 1. Verificar salud
        if source_state.health_status == HealthStatus.SUSPENDED.value:
            return False, "SUSPENDED: Fuente suspendida por fallos persistentes acumulados"

        # Verified recovery of an unfinished source in the SAME pipeline. It is
        # not a periodic round and never overrides suspension or HTTP policies.
        if recovering_pipeline:
            return True, None

        # 2. Verificar cadencia de actualización
        current_time = now or datetime.now(timezone.utc)
        if source_state.next_eligible_at and current_time < source_state.next_eligible_at:
            return False, f"NOT_DUE: Próxima evaluación programada para {source_state.next_eligible_at.isoformat()}"

        return True, None
