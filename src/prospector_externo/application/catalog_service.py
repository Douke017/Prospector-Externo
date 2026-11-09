"""
Servicio de aplicación de catálogo (CatalogApplicationService).
Gestiona comparación de snapshots, checkpoints incrementales y creación de observaciones de corrida.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Tuple, List, Dict

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.domain.models import (
    Source,
    SourceConfig,
    Snapshot,
    ResourceCandidate,
    ChangeStatus,
    FileProcessingStatus,
)
from prospector_externo.domain.observations import SourceRunObservation, ExecutionStatus, ContentStatus
from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.kernel.contracts import ExtractionResult

logger = logging.getLogger("prospector.application.catalog")

REMOVAL_CONFIRMATIONS = 3

class CatalogApplicationService:
    """Centraliza la reconciliación histórica, persistencia de snapshots y observaciones operativas."""

    def __init__(self, catalog_repo: CatalogRepositoryPort):
        self.catalog_repo = catalog_repo

    @staticmethod
    def _resource_evidence(resource: ResourceCandidate) -> str:
        """Evidencia estable de contenido sin depender de metadata volátil de discovery."""
        if resource.content_hash:
            return f"sha256:{resource.content_hash}"
        if resource.etag:
            return f"etag:{resource.etag}"
        if resource.last_modified_header:
            return f"last-modified:{resource.last_modified_header}"
        return ""

    def compute_resources_hash(self, resources: List[ResourceCandidate]) -> str:
        """Hash consolidado por identidad de recurso + evidencia de contenido disponible."""
        signatures = []
        for resource in resources:
            evidence = self._resource_evidence(resource)
            signatures.append(
                f"{resource.resource_key}|{evidence}" if evidence else resource.resource_key
            )
        combined = "|".join(sorted(signatures))
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def reconcile_and_checkpoint(
        self,
        config: SourceConfig,
        run_id: str,
        extraction_result: ExtractionResult
    ) -> SourceRunObservation:
        """
        Compara el resultado de la extracción contra el último snapshot,
        persiste el checkpoint de la fuente y genera la observación trazable.
        """
        source_id = config.source_id
        scope_fingerprint = DiscoveryFrontier.profile_fingerprint(config)
        now = datetime.now(timezone.utc)

        # Registrar la fuente incluso si el primer intento falla: de otro modo
        # no se puede acumular salud entre ejecuciones sin snapshot.
        source_entity = self.catalog_repo.get_source(source_id) or Source(
            source_id=source_id,
            name=config.name or source_id.upper(),
            entrypoint=config.entrypoint,
            workflow=config.workflow,
            update_category=config.update_category,
        )
        source_entity.name = config.name or source_entity.name or source_id.upper()
        if not getattr(source_entity, "policy_customized", False):
            source_entity.entrypoint = config.entrypoint
            source_entity.workflow = config.workflow
        source_entity.last_run_id = run_id
        source_entity.last_run_at = now
        source_entity.updated_at = now
        self.catalog_repo.save_source(source_entity)

        # Reprocesar el mismo checkpoint no debe sumar una ausencia adicional.
        already_materialized = source_entity.catalog_last_run_id == run_id
        if already_materialized:
            previous = next(
                (obs for obs in self.catalog_repo.get_source_observations(source_id, limit=1)
                 if obs.run_id == run_id), None
            )
            if previous is not None:
                return previous

        # Si la extracción falló técnicamente
        if not extraction_result.success:
            obs = SourceRunObservation(
                source_id=source_id,
                run_id=run_id,
                discovery_cycle_id=extraction_result.metadata.get("discovery_cycle_id"),
                discovery_profile_fingerprint=scope_fingerprint,
                resume_status=extraction_result.metadata.get("resume_status"),
                workflow=config.workflow,
                execution_status=ExecutionStatus.FAILED,
                content_status=ContentStatus.NOT_EVALUATED,
                failure_code=extraction_result.failure_code,
                coverage=extraction_result.coverage,
                observed_at=now
            )
            self.catalog_repo.save_source_observation(obs)
            return obs

        # El inventario RAW se conserva aun cuando una pagina no produce
        # ResourceCandidate. Validar identidades antes de escribir el snapshot.
        for discovered in extraction_result.discovered_urls:
            if discovered.source_id != source_id:
                raise ValueError(
                    f"URL {discovered.normalized_url} no pertenece a {source_id}"
                )

        # Extracción exitosa: Reconciliación con histórico
        latest_snapshot = self.catalog_repo.get_latest_snapshot(source_id)
        current_hash = self.compute_resources_hash(extraction_result.resources)

        # El snapshot corresponde SOLO a esta corrida. El catálogo consolidado se
        # persiste por separado para que un recorrido limitado no borre el pasado.
        catalog_state = self.catalog_repo.get_resource_catalog(source_id)
        if catalog_state is None:
            # Migración conservadora de catálogos anteriores: se toma como punto
            # de partida el último snapshot disponible, sin reescribir evidencia.
            catalog_state = latest_snapshot.resources if latest_snapshot else []
        historical_keys: Dict[str, ResourceCandidate] = {
            r.resource_key: r.model_copy(deep=True) for r in catalog_state
        }
        scope_changed = bool(historical_keys) and source_entity.catalog_scope_fingerprint != scope_fingerprint
        current_keys = set()
        consolidated = {k: v.model_copy(deep=True) for k, v in historical_keys.items()}
        content_changed = False

        # Clasificar únicamente recursos observados en esta ejecución.
        for r in extraction_result.resources:
            if r.source_id != source_id:
                raise ValueError(f"Recurso {r.resource_key} no pertenece a {source_id}")
            if r.resource_key in current_keys:
                raise ValueError(f"Recurso duplicado en la corrida: {r.resource_key}")
            current_keys.add(r.resource_key)
            historical = historical_keys.get(r.resource_key)
            if historical is None or historical.change_status == ChangeStatus.REMOVED:
                r.change_status = ChangeStatus.NEW
                r.processing_status = FileProcessingStatus.PENDIENTE
            elif (self._resource_evidence(historical) != self._resource_evidence(r)
                  and (self._resource_evidence(historical) or self._resource_evidence(r))):
                r.change_status = ChangeStatus.MODIFIED
                r.processing_status = FileProcessingStatus.PENDIENTE
            else:
                r.change_status = ChangeStatus.UNCHANGED
                r.processing_status = historical.processing_status
                r.processing_updated_at = historical.processing_updated_at
                r.processing_error = historical.processing_error
            r.consecutive_absences = 0
            consolidated[r.resource_key] = r.model_copy(deep=True)
            if r.change_status in (ChangeStatus.NEW, ChangeStatus.MODIFIED):
                content_changed = True

        coverage = extraction_result.coverage
        is_complete = coverage.confirms_resource_absence() and not scope_changed
        # Ninguna salida parcial, sin razón verificable o con fallos confirma
        # ausencia. Las fuentes de formularios u otros cierres atípicos tampoco.
        for key, old in historical_keys.items():
            if key in current_keys:
                continue
            carried = consolidated[key]
            if carried.change_status == ChangeStatus.REMOVED:
                continue
            if scope_changed:
                carried.consecutive_absences = 0
            if is_complete and not already_materialized:
                carried.consecutive_absences += 1
                if carried.consecutive_absences >= REMOVAL_CONFIRMATIONS:
                    carried.change_status = ChangeStatus.REMOVED
                    content_changed = True
                else:
                    carried.change_status = ChangeStatus.NOT_EVALUATED
            else:
                carried.change_status = ChangeStatus.NOT_EVALUATED

        if content_changed:
            content_status = ContentStatus.CHANGED
        elif not extraction_result.resources:
            content_status = ContentStatus.EMPTY_RESULT
        else:
            content_status = ContentStatus.NO_CHANGE

        # Crear y persistir nuevo snapshot (Checkpoint incremental)
        new_snapshot = Snapshot(
            source_id=source_id,
            run_id=run_id,
            discovery_profile_fingerprint=scope_fingerprint,
            discovery_cycle_id=extraction_result.metadata.get("discovery_cycle_id"),
            captured_at=now,
            resources_hash=current_hash,
            total_resources=len(extraction_result.resources),
            execution_status=(
                ExecutionStatus.PARTIAL_SUCCESS.value
                if coverage.is_partial_discovery()
                else ExecutionStatus.SUCCESS.value
            ),
            resources=extraction_result.resources,
            discovered_urls=extraction_result.discovered_urls,
        )
        self.catalog_repo.save_snapshot(new_snapshot)
        # Persistir el estado consolidado y sus contadores sin alterar el RAW.
        self.catalog_repo.save_resource_catalog(
            source_id, run_id, list(consolidated.values())
        )
        current_source = self.catalog_repo.get_source(source_id)
        current_source.catalog_scope_fingerprint = scope_fingerprint
        self.catalog_repo.save_source(current_source)
        logger.info(f"Checkpoint incremental persistido con éxito para [{source_id}] ({len(new_snapshot.resources)} recursos).")

        # Generar observación de corrida
        observation = SourceRunObservation(
            source_id=source_id,
            run_id=run_id,
            discovery_cycle_id=extraction_result.metadata.get("discovery_cycle_id"),
            discovery_profile_fingerprint=scope_fingerprint,
            resume_status=extraction_result.metadata.get("resume_status"),
            workflow=config.workflow,
            execution_status=(
                ExecutionStatus.PARTIAL_SUCCESS
                if coverage.is_partial_discovery() else ExecutionStatus.SUCCESS
            ),
            content_status=content_status,
            failure_code=(
                str(coverage.stop_reason)
                if coverage.is_partial_discovery() and coverage.stop_reason
                   not in {"QUEUE_EXHAUSTED", "API_SEEDS_EXHAUSTED"}
                else None
            ),
            robots_override_applied=config.ignore_robots_txt,
            robots_override_reason=config.robots_override_reason if config.ignore_robots_txt else None,
            coverage=extraction_result.coverage,
            observed_at=now
        )
        self.catalog_repo.save_source_observation(observation)
        return observation
