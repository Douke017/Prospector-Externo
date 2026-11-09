"""
Puerto abstracto para el repositorio de catálogo, snapshots y observaciones.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, List, Optional
from prospector_externo.domain.models import (
    FileProcessingStatus,
    ResourceCandidate,
    Snapshot,
    Source,
)
from prospector_externo.domain.observations import SourceRunObservation


class CatalogRepositoryPort(ABC):
    """Puerto de persistencia para el catálogo de inventario bruto."""

    supports_discovery_checkpoints = False

    def get_discovery_checkpoint(self, source_id: str) -> Optional[dict]:
        """Extensión opcional: adaptadores legacy no afirman soportar recuperación BFS."""
        return None

    def save_discovery_checkpoint(self, source_id: str, checkpoint: dict) -> None:
        raise NotImplementedError("El adaptador no soporta checkpoints de discovery")

    def query_sources(self, *, limit: Optional[int] = None, offset: int = 0) -> List[Source]:
        """Paginación estable; los adaptadores pueden ejecutarla en el servidor."""
        rows = sorted(self.list_sources(), key=lambda source: source.source_id)
        return rows[offset:offset + limit if limit is not None else None]

    def query_resources(self, source_id: Optional[str] = None, *,
                        limit: Optional[int] = None, offset: int = 0) -> List[ResourceCandidate]:
        rows = sorted(self.list_resources(source_id),
                      key=lambda resource: (resource.source_id, resource.resource_key, resource.url))
        return rows[offset:offset + limit if limit is not None else None]

    @abstractmethod
    def save_snapshot(self, snapshot: Snapshot) -> None:
        """Persiste un snapshot del inventario descubierto."""
        pass

    @abstractmethod
    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        """Recupera el último snapshot registrado para una fuente para comparación de cambios."""
        pass

    @abstractmethod
    def save_source_observation(self, observation: SourceRunObservation) -> None:
        """Persiste una observación operativa de una corrida."""
        pass

    @abstractmethod
    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        """Obtiene el historial de observaciones de una fuente."""
        pass

    @abstractmethod
    def save_source(self, source: Source) -> None:
        """Registra o actualiza el estado operativo de una fuente."""
        pass

    @abstractmethod
    def get_source(self, source_id: str) -> Optional[Source]:
        """Obtiene la ficha y estado de una fuente por su ID."""
        pass

    @abstractmethod
    def list_sources(self) -> List[Source]:
        """Lista todas las fuentes registradas en el catálogo."""
        pass

    @abstractmethod
    def get_resource_catalog(self, source_id: str) -> Optional[List[ResourceCandidate]]:
        """Lee el catálogo consolidado, incluidos REMOVED; None indica que aún no existe."""
        pass

    @abstractmethod
    def save_resource_catalog(
        self, source_id: str, run_id: str, resources: List[ResourceCandidate]
    ) -> None:
        """Persiste estado y contadores de recursos separados de la evidencia RAW."""
        pass

    @abstractmethod
    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        """Lista recursos descubiertos, opcionalmente filtrados por fuente."""
        pass

    def update_resource_processing_status(
        self,
        resource_key: str,
        status: FileProcessingStatus,
        error: Optional[str] = None,
    ) -> Optional[ResourceCandidate]:
        """Actualiza el estado de procesamiento de un archivo en el catálogo."""
        now = datetime.now(timezone.utc)
        for src in self.list_sources():
            cat = self.get_resource_catalog(src.source_id)
            if cat is None:
                snap = self.get_latest_snapshot(src.source_id)
                cat = snap.resources if snap else []
            updated_target: Optional[ResourceCandidate] = None
            for res in cat:
                if res.resource_key == resource_key:
                    res.processing_status = status
                    res.processing_updated_at = now
                    res.processing_error = error
                    updated_target = res
                    break
            if updated_target is not None:
                run_id = src.catalog_last_run_id or src.last_run_id or "manual_status_update"
                self.save_resource_catalog(src.source_id, run_id, cat)
                return updated_target
        return None

    def save_discovery_candidates(self, candidates: List[Any]) -> None:
        """Persiste fuentes preseleccionadas de exploración abierta (Objetivo 1)."""
        return None

    def list_discovery_candidates(self) -> List[Any]:
        """Lista fuentes preseleccionadas de exploración abierta."""
        return []

    def save_structural_alert(self, alert: Any) -> None:
        """Persiste una alerta temprana de cambio estructural (Objetivo 5)."""
        return None

    def list_structural_alerts(self, source_id: Optional[str] = None) -> List[Any]:
        """Lista alertas estructurales registradas."""
        return []
