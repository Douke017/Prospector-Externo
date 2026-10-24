"""
Controladores HTTP FastAPI para la fachada de consulta Catalog API Facade.
Solo lectura para consumidores externos (DATAX y Reportes Perdidos).
"""

from typing import List, Optional, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, ResourceCandidate
from prospector_externo.domain.observations import Run, RunReport, SourceRunObservation
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.projection import DataxProjection
from prospector_externo.application.projection_service import DataxProjectionService
from prospector_externo.workflows.base import ResourceDetector


def _validate_http_url_literal(value):
    """Rechaza entradas HTTP malformadas antes de la reparación de Pydantic."""
    if isinstance(value, str):
        if (not value or value != value.strip() or any(ord(ch) < 32 for ch in value)
                or "\\" in value):
            raise ValueError("URL HTTP/HTTPS malformada")
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.netloc:
            raise ValueError("URL HTTP/HTTPS absoluta requerida")
    return value


class MappingResourceInput(BaseModel):
    """Envoltorio de recurso para sistemas consumidores; no lee contenido remoto."""

    url: AnyHttpUrl

    @field_validator("url", mode="before")
    @classmethod
    def strict_url(cls, value):
        return _validate_http_url_literal(value)


class MappingInspectRequest(BaseModel):
    """Acepta {url} o {resource: {url}}; ambos son equivalentes."""

    url: Optional[AnyHttpUrl] = None
    resource: Optional[MappingResourceInput] = None

    @field_validator("url", mode="before")
    @classmethod
    def strict_url(cls, value):
        return _validate_http_url_literal(value)

    @model_validator(mode="after")
    def validate_resource_url(self) -> "MappingInspectRequest":
        candidates = [self.url, self.resource.url if self.resource is not None else None]
        present = [str(candidate) for candidate in candidates if candidate is not None]
        if not present:
            raise ValueError("Se requiere 'url' o 'resource.url'")
        for candidate in present:
            parsed = urlsplit(candidate)
            if (len(candidate) > 2048 or parsed.username is not None
                    or parsed.password is not None or not parsed.hostname):
                raise ValueError("URL no permitida para inspección")
        if len(present) == 2 and UrlNormalizer.normalize(present[0]) != UrlNormalizer.normalize(present[1]):
            raise ValueError("'url' y 'resource.url' deben referirse a la misma URL")
        return self


class MappingInspectResponse(BaseModel):
    """Hash de identidad URL, NO hash del contenido físico no descargado."""

    normalized_url: str
    file_extension: str = ""
    resource_type: Literal["file", "undetermined"]
    url_sha256: str = Field(description="SHA-256 hexadecimal de normalized_url en UTF-8")
    hash_basis: Literal["normalized_url"] = "normalized_url"
    inspection_mode: Literal["offline_url_only"] = "offline_url_only"


def create_catalog_router(
    catalog_repo: CatalogRepositoryPort,
    report_repo: RunReportRepositoryPort
) -> APIRouter:
    """Crea el enrutador de FastAPI inyectando los puertos de persistencia."""
    router = APIRouter()

    def paginate(items, limit, offset, key):
        """Conserva la lista legacy cuando no hay paginación solicitada.

        Al paginar, ordena por una identidad estable para que dos peticiones
        sucesivas no dependan del orden interno del backend JSON o Mongo.
        """
        if limit is None and offset == 0:
            return items
        ordered = sorted(items, key=key)
        return ordered[offset:offset + limit if limit is not None else None]

    @router.post(
        "/mapping/inspect",
        response_model=MappingInspectResponse,
        tags=["Mapping"],
        summary="Normaliza e identifica una URL de recurso sin acceso remoto",
    )
    def inspect_mapping(payload: MappingInspectRequest) -> MappingInspectResponse:
        """HU-20: inspección offline, sin descargar, crawlear ni modificar MongoDB.

        Una extensión detectada indica solo inferencia por URL, no existencia
        de un archivo. El digest es de la URL canónica, nunca del contenido.
        """
        submitted = payload.url or payload.resource.url
        normalized = UrlNormalizer.normalize(str(submitted))
        extension = ResourceDetector.extension_for(normalized)
        return MappingInspectResponse(
            normalized_url=normalized,
            file_extension=extension,
            resource_type="file" if extension else "undetermined",
            url_sha256=UrlNormalizer.compute_url_hash(normalized),
        )

    @router.get("/catalog/resources", response_model=List[ResourceCandidate])
    def get_resources(
        source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente"),
        limit: Optional[int] = Query(None, ge=1, le=500, description="Tamaño de página; sin límite para clientes legacy"),
        offset: int = Query(0, ge=0, description="Cantidad de recursos a omitir"),
    ):
        """Consulta recursos con paginación opcional, sin romper el contrato legacy."""
        if (limit is not None or offset) and hasattr(catalog_repo, "query_resources"):
            return catalog_repo.query_resources(source_id, limit=limit, offset=offset)
        resources = catalog_repo.list_resources(source_id=source_id)
        return paginate(
            resources, limit, offset,
            lambda resource: (resource.source_id, resource.resource_key, resource.url),
        )

    @router.get("/catalog/sources", response_model=List[Source])
    def get_sources(
        limit: Optional[int] = Query(None, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        """Consulta las fuentes; la paginación es optativa para compatibilidad."""
        if (limit is not None or offset) and hasattr(catalog_repo, "query_sources"):
            return catalog_repo.query_sources(limit=limit, offset=offset)
        return paginate(catalog_repo.list_sources(), limit, offset, lambda source: source.source_id)

    @router.get("/catalog/sources/{source_id}", response_model=Source)
    def get_source_detail(source_id: str):
        """Consulta el detalle y estado operativo de una fuente."""
        src = catalog_repo.get_source(source_id)
        if not src:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        return src

    @router.get("/catalog/sources/{source_id}/projection", response_model=DataxProjection)
    def get_source_projection(source_id: str):
        """Live technical selection from the latest snapshot, without HTTP/writes.

        Selected references live in families[].periods[].representations[].
        Decisions include exclusions; run_id/resources_hash identify the input.
        Uses default grouping, not an export's optional custom grouping contract.
        This full projection is unpaginated; RAW pagination is unchanged.
        Selection does not verify remote availability or replace a historical ZIP.
        """
        source = catalog_repo.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        snapshot = catalog_repo.get_latest_snapshot(source_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"Snapshot de '{source_id}' no encontrado")
        return DataxProjectionService().project(source, snapshot)

    @router.get("/runs", response_model=List[RunReport])
    def list_runs(limit: int = Query(20, ge=1, le=100)):
        """Lista las corridas recientes."""
        return report_repo.list_reports(limit=limit)

    # Metadatos de procesos locales son internos; no exponer hostname ni PID.
    @router.get("/runs/{run_id}", response_model=Run, response_model_exclude={
        "owner_host", "owner_pid", "owner_process_started_at",
    })
    def get_run_status(run_id: str):
        """Estado real de la corrida, incluso antes de que exista el reporte."""
        run = report_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Corrida '{run_id}' no encontrada")
        return run

    @router.get("/runs/{run_id}/report", response_model=RunReport)
    def get_run_report(run_id: str):
        """Obtiene el reporte consolidado de una corrida."""
        rep = report_repo.get_report(run_id)
        if not rep:
            raise HTTPException(status_code=404, detail=f"Reporte de corrida '{run_id}' no encontrado")
        return rep

    @router.get("/sources/{source_id}/observations", response_model=List[SourceRunObservation])
    def get_observations(source_id: str, limit: int = Query(20, ge=1, le=50)):
        """Consulta el historial de observaciones de una fuente."""
        return catalog_repo.get_source_observations(source_id, limit=limit)

    return router
