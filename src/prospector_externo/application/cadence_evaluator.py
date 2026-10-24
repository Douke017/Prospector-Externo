"""
Evaluador de política de cadencia y actualización de salud (CadencePolicyEvaluator).
Ajusta conservadoramente la frecuencia de ejecución únicamente ante observaciones exitosas sin novedades.
"""

from datetime import datetime, timezone, timedelta
import logging

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.domain.models import Source, SourceConfig
from prospector_externo.domain.observations import SourceRunObservation, ExecutionStatus, ContentStatus
from prospector_externo.domain.health import HealthStateMachine, HealthStatus
from prospector_externo.domain.cadence import CadenceState, UpdateCategory

logger = logging.getLogger("prospector.application.cadence")


class CadencePolicyEvaluator:
    """Actualiza salud y próxima fecha de elegibilidad según el resultado de la corrida."""

    def __init__(self, catalog_repo: CatalogRepositoryPort):
        self.catalog_repo = catalog_repo

    def evaluate_and_update(
        self,
        config: SourceConfig,
        observation: SourceRunObservation
    ) -> None:
        source_id = config.source_id
        source = self.catalog_repo.get_source(source_id)
        if not source:
            return
        # Reintentar un checkpoint no debe contar la misma observación otra vez.
        if source.last_policy_run_id == observation.run_id:
            return

        now = observation.observed_at

        if (observation.execution_status in {ExecutionStatus.FAILED, ExecutionStatus.PARTIAL_SUCCESS}
                or (observation.execution_status == ExecutionStatus.SUCCESS
                    and observation.content_status == ContentStatus.EMPTY_RESULT)):
            # La secuencia exige observaciones completas NO_CHANGE consecutivas.
            # No reutilizar un prefijo de cuatro éxitos tras un fallo o parcial.
            source.consecutive_no_change_count = 0
            source.weekly_no_change_count = 0
            source.weekly_no_change_since_at = None

        if observation.execution_status == ExecutionStatus.PARTIAL_SUCCESS:
            # El inventario parcial requiere reintento; no adelantar cadencia
            # mensual/semanal ni registrar un éxito de salud.
            source.next_eligible_at = now + timedelta(hours=4)
            source.updated_at = now
            source.last_policy_run_id = observation.run_id
            self.catalog_repo.save_source(source)
            return

        # 1. Si la ejecución falló, recuperar el contador persistido.
        if observation.execution_status == ExecutionStatus.FAILED:
            health = HealthStateMachine(
                current_status=HealthStatus(source.health_status),
                consecutive_failures=source.consecutive_failures,
                first_failure_at=source.first_failure_at,
            )
            new_health = health.record_failure(reason=observation.failure_code or "ERROR", at=now)
            source.health_status = new_health.value
            source.consecutive_failures = health.consecutive_failures
            source.first_failure_at = health.first_failure_at
            logger.warning(f"Salud de [{source_id}] actualizada a: {source.health_status} por error: {observation.failure_code}")

            # Reintento corto (ej. 4 horas) para errores no permanentes si no está suspendida
            if new_health != HealthStatus.SUSPENDED:
                source.next_eligible_at = now + timedelta(hours=4)
            source.updated_at = now
            source.last_policy_run_id = observation.run_id
            self.catalog_repo.save_source(source)
            return

        if observation.execution_status != ExecutionStatus.SUCCESS:
            return

        # 2. Éxito completo: restablecer salud y evaluar cambios en la cadencia.
        health = HealthStateMachine(
            current_status=HealthStatus(source.health_status),
            consecutive_failures=source.consecutive_failures,
        )
        health.record_success()
        source.health_status = health.current_status.value
        source.consecutive_failures = health.consecutive_failures
        source.first_failure_at = None

        cadence = CadenceState(
            category=UpdateCategory(source.update_category),
            next_eligible_at=source.next_eligible_at,
            consecutive_no_change_count=source.consecutive_no_change_count,
            weekly_no_change_count=source.weekly_no_change_count,
            weekly_no_change_since_at=source.weekly_no_change_since_at,
        )

        if observation.content_status == ContentStatus.CHANGED:
            # Restablecer cadencia base configurada
            base_cat = UpdateCategory(config.update_category)
            cadence.register_change(base_cat, now=now)
            source.update_category = cadence.category.value
            source.next_eligible_at = cadence.next_eligible_at
            logger.info(f"Novedades detectadas en [{source_id}]. Cadencia restablecida a {source.update_category}")

        elif observation.content_status == ContentStatus.NO_CHANGE:
            cadence.register_no_change(now=now)
            source.update_category = cadence.category.value
            source.next_eligible_at = cadence.next_eligible_at
            logger.info(f"Sin novedades en [{source_id}]. Próxima elegibilidad calculada: {source.next_eligible_at.isoformat()}")

        elif observation.content_status == ContentStatus.EMPTY_RESULT:
            # EMPTY_RESULT no prueba que el portal conserve el inventario. Debe
            # revisarse pronto, sin computarlo como una observación NO_CHANGE.
            source.next_eligible_at = now + timedelta(hours=4)

        source.consecutive_no_change_count = cadence.consecutive_no_change_count
        source.weekly_no_change_count = cadence.weekly_no_change_count
        source.weekly_no_change_since_at = cadence.weekly_no_change_since_at
        source.last_policy_run_id = observation.run_id
        source.updated_at = now
        self.catalog_repo.save_source(source)
