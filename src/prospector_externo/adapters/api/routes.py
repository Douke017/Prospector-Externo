"""Rutas de FastAPI para la fachada de Catálogo, Exploración, Resiliencia y Alertas."""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, AsyncIterator, Dict, List, Literal, Optional
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
import yaml

from prospector_externo.application.alert_notification_service import AlertNotificationService
from prospector_externo.application.contingency_service import (
    ContingencyCandidate,
    ContingencyDiscoveryService,
    ContingencyEvaluationResponse,
    ResilienceCoverageReport,
)
from prospector_externo.application.open_discovery_service import (
    CandidateStatus,
    DiscardCandidateRequest,
    OpenDiscoveryCandidate,
    OpenDiscoverySearchRequest,
    OpenDiscoveryService,
    PromoteCandidateRequest,
)
from prospector_externo.application.orchestrator import (
    DryRunExtractionResponse,
    RunOrchestrator,
)
from prospector_externo.application.projection_service import DataxProjectionService
from prospector_externo.application.run_control import RUN_CONTROL, TERMINAL_EVENT_TYPES
from prospector_externo.domain.models import (
    FailoverEvent,
    FileProcessingStatus,
    ResourceCandidate,
    ResourceStatusAuditEvent,
    ResourceVersion,
    Source,
    SourceConfig,
    StatusActor,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import (
    Run,
    RunReport,
    RunStatus,
    SourceRunObservation,
)
from prospector_externo.domain.projection import DataxProjection
from prospector_externo.domain.structural_monitor import (
    AlertDeliveryLog,
    AlertStatus,
    MaintenanceTechnicalReport,
    StructuralAlert,
    build_maintenance_report,
    map_audit_severity,
    verify_source_structure_live,
)
from prospector_externo.ports.alert_dispatcher import AlertChannelConfig
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.workflows.base import ResourceDetector


_RUN_DISPATCH_LOCK = threading.Lock()


def _validate_http_url_literal(value: Any) -> Any:
    """Rechaza entradas HTTP malformadas antes de la reparación de Pydantic."""
    if isinstance(value, str):
        if (
            not value
            or value != value.strip()
            or any(ord(ch) < 32 for ch in value)
            or "\\" in value
        ):
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
    def strict_url(cls, value: Any) -> Any:
        return _validate_http_url_literal(value)


class MappingInspectRequest(BaseModel):
    """Acepta {url} o {resource: {url}}; ambos son equivalentes."""

    url: Optional[AnyHttpUrl] = None
    resource: Optional[MappingResourceInput] = None

    @field_validator("url", mode="before")
    @classmethod
    def strict_url(cls, value: Any) -> Any:
        return _validate_http_url_literal(value)

    @model_validator(mode="after")
    def require_single_consistent_url(self) -> "MappingInspectRequest":
        candidates = [self.url, self.resource.url if self.resource is not None else None]
        present = [str(candidate) for candidate in candidates if candidate is not None]
        if not present:
            raise ValueError("Se requiere 'url' o 'resource.url'")
        for candidate in present:
            parsed = urlsplit(candidate)
            if (
                len(candidate) > 2048
                or parsed.username is not None
                or parsed.password is not None
                or not parsed.hostname
            ):
                raise ValueError("URL no permitida para inspección")
        if (
            len(present) == 2
            and UrlNormalizer.normalize(present[0]) != UrlNormalizer.normalize(present[1])
        ):
            raise ValueError("'url' y 'resource.url' deben coincidir")
        return self


class MappingInspectResponse(BaseModel):
    """Identidad canónica determinística para cruce con DATAX/Reportes Perdidos."""

    normalized_url: str
    file_extension: str
    resource_type: Literal["file", "undetermined"]
    url_sha256: str
    hash_basis: Literal["normalized_url"] = "normalized_url"
    inspection_mode: Literal["offline_url_only"] = "offline_url_only"


class SourcePolicyUpdateRequest(BaseModel):
    """Parámetros de política y configuración de una fuente editables por el analista."""

    entrypoint: Optional[str] = None
    seeds: Optional[List[str]] = None
    update_category: Optional[Literal["DAILY", "WEEKLY", "MONTHLY"]] = None
    next_eligible_at: Optional[str] = None
    allowed_extensions: Optional[List[str]] = None
    rate_limit_seconds: Optional[float] = Field(None, ge=0.0, le=60.0)
    max_urls: Optional[int] = Field(None, ge=1, le=5000)
    max_resources: Optional[int] = Field(None, ge=1, le=20000)
    ignore_robots_txt: Optional[bool] = None
    health_status: Optional[Literal["ACTIVE", "WARNING", "SUSPENDED"]] = None
    audit_code: Optional[str] = None
    audit_note: Optional[str] = None
    contingency_url: Optional[str] = None
    contingency_urls: Optional[List[str]] = None
    excluded_path_keywords: Optional[List[str]] = None
    allowed_hosts: Optional[List[str]] = None

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return _validate_http_url_literal(value)
        return value

    @field_validator("seeds")
    @classmethod
    def validate_seeds(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is not None:
            return [UrlNormalizer.normalize(_validate_http_url_literal(item)) for item in value]
        return value

    @field_validator("contingency_urls")
    @classmethod
    def validate_contingency_urls(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is not None:
            return [_validate_http_url_literal(item) for item in value if item.strip()]
        return value


class ResourceStatusUpdateRequest(BaseModel):
    """Solicitud auditable de actualización de estado o callback de Prospector Interno."""

    processing_status: FileProcessingStatus
    processing_error: Optional[str] = None
    actor: StatusActor = "MANUAL_OPERATOR"
    override_reason: Optional[str] = None
    download_id: Optional[str] = None
    sha256: Optional[str] = None
    idempotency_key: Optional[str] = None


class ContingencyProbeRequest(BaseModel):
    """Solicitud de verificación de salud individual para una URL de contingencia."""

    url: str = Field(..., min_length=8)

    @field_validator("url")
    @classmethod
    def validate_probe_url(cls, value: str) -> str:
        return _validate_http_url_literal(value)


class AlertLifecycleUpdateRequest(BaseModel):
    """Transición de ciclo de vida de una alerta estructural o de mantenimiento."""

    status: AlertStatus
    assignee: Optional[str] = None
    ticket_url: Optional[str] = None
    muted_until: Optional[datetime] = None
    resolution_note: Optional[str] = None


class TriggerRunRequest(BaseModel):
    """Solicitud para disparar una corrida de extracción dirigida (Objetivo 2)."""

    source_id: Optional[str] = Field(
        default=None,
        description="Identificador de fuente específica; None procesa las elegibles.",
    )
    force: bool = True


class TriggerRunResponse(BaseModel):
    """Confirmación de inicio o ejecución de una corrida de rastreo."""

    status: Literal["STARTED", "COMPLETED", "BUSY"]
    message: str
    source_id: Optional[str] = None
    run_id: Optional[str] = None


class CancelRunResponse(BaseModel):
    """Confirmación de solicitud de cancelación cooperativa de una corrida."""

    run_id: str
    status: Literal["CANCELLING", "CANCELLED"]
    message: str


class DryRunPolicyRequest(BaseModel):
    """Parámetros opcionales para simular extracción sin persistir cambios."""

    entrypoint: Optional[str] = None
    seeds: Optional[List[str]] = None
    allowed_extensions: Optional[List[str]] = None
    excluded_path_keywords: Optional[List[str]] = None
    allowed_hosts: Optional[List[str]] = None
    rate_limit_seconds: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    ignore_robots_txt: Optional[bool] = None
    max_pages: int = Field(default=3, ge=1, le=10)
    max_resources: int = Field(default=25, ge=1, le=100)

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            return _validate_http_url_literal(value)
        return value

    @field_validator("seeds")
    @classmethod
    def validate_seeds(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is not None:
            return [UrlNormalizer.normalize(_validate_http_url_literal(item)) for item in value]
        return value


class StorageStatusResponse(BaseModel):
    """Métricas operativas del almacén de datos (MongoDB y catálogo JSON de respaldo)."""

    status: Literal["HEALTHY", "DEGRADED", "OFFLINE"]
    database_type: str
    mongo_connected: bool
    mongo_database: str
    collections: Dict[str, int]
    local_state_healthy: bool
    local_sources_count: int
    local_resources_count: int
    local_state_path: str
    last_backup_at: Optional[str] = None
    backups_count: int = 0


class BackupSyncResponse(BaseModel):
    """Resultado de la instantánea de respaldo del catálogo."""

    status: Literal["SUCCESS", "FAILED"]
    backup_id: str
    backup_path: str
    sources_backed_up: int
    resources_backed_up: int
    timestamp: str


def _resolve_config_yaml_path() -> Optional[Path]:
    candidates = [
        Path.cwd() / "config" / "sources.yaml",
        Path.cwd() / "Prospector-Externo" / "config" / "sources.yaml",
        Path(__file__).resolve().parents[4] / "config" / "sources.yaml",
    ]
    return next((p for p in candidates if p.is_file()), None)


def _sync_source_to_yaml_safe(source: Source) -> None:
    """Actualiza la fuente en config/sources.yaml si ya existe, respetando aislamiento en tests."""
    if os.environ.get("PROSPECTOR_DISABLE_YAML_SYNC") == "1":
        return

    target_yaml = _resolve_config_yaml_path()
    if not target_yaml:
        return
    try:
        with open(target_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        sources_list = data.get("sources", [])
        found = False
        for item in sources_list:
            if isinstance(item, dict) and item.get("source_id") == source.source_id:
                item["entrypoint"] = source.entrypoint
                item["seeds"] = source.seeds
                item["update_category"] = source.update_category
                item["allowed_extensions"] = source.allowed_extensions
                item["rate_limit_seconds"] = source.rate_limit_seconds
                item["max_urls"] = source.max_urls
                item["max_resources"] = source.max_resources
                item["ignore_robots_txt"] = source.ignore_robots_txt
                item["contingency_url"] = source.contingency_url
                item["contingency_urls"] = getattr(source, "contingency_urls", [])
                item["excluded_path_keywords"] = source.excluded_path_keywords
                item["allowed_hosts"] = source.allowed_hosts
                found = True
                break
        if found:
            tmp_yaml = target_yaml.with_suffix(".yaml.tmp")
            with open(tmp_yaml, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            tmp_yaml.replace(target_yaml)
    except OSError:
        return


def create_catalog_router(
    catalog_repo: CatalogRepositoryPort,
    report_repo: RunReportRepositoryPort,
    config_path: Optional[Path] = None,
) -> APIRouter:
    """Crea el enrutador de FastAPI inyectando los puertos de persistencia."""
    router = APIRouter()
    open_discovery_svc = OpenDiscoveryService(catalog_repo)
    contingency_svc = ContingencyDiscoveryService(catalog_repo)
    alert_notification_svc = AlertNotificationService(catalog_repo)

    def paginate(items: List[Any], limit: Optional[int], offset: int, key: Any) -> List[Any]:
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
        submitted = payload.url or (payload.resource.url if payload.resource else None)
        normalized = UrlNormalizer.normalize(str(submitted))
        extension = ResourceDetector.extension_for(normalized)
        return MappingInspectResponse(
            normalized_url=normalized,
            file_extension=extension,
            resource_type="file" if extension else "undetermined",
            url_sha256=UrlNormalizer.compute_url_hash(normalized),
        )

    # =========================================================================
    # OBJETIVO 1: Búsqueda y Filtrado General en Web Abierta
    # =========================================================================

    @router.post(
        "/discovery/open-search",
        response_model=List[OpenDiscoveryCandidate],
        tags=["Discovery"],
        summary="Explora y preselecciona fuentes públicas con índice de confianza y relevancia",
    )
    def run_open_discovery_search(
        payload: OpenDiscoverySearchRequest,
        live_probe: bool = Query(True, description="Verificar conectividad HTTP y archivos en vivo"),
    ) -> List[OpenDiscoveryCandidate]:
        return open_discovery_svc.search_and_preselect(payload, live_probe=live_probe)

    @router.get(
        "/discovery/candidates",
        response_model=List[OpenDiscoveryCandidate],
        tags=["Discovery"],
        summary="Lista las fuentes preseleccionadas en exploraciones abiertas",
    )
    def list_open_discovery_candidates(
        include_discarded: bool = Query(
            True, description="Incluir candidatos previamente descartados"
        ),
        status: Optional[CandidateStatus] = Query(
            None, description="Filtrar por estado (PRESELECCIONADA, APROBADA, DESCARTADA)"
        ),
    ) -> List[OpenDiscoveryCandidate]:
        return open_discovery_svc.list_candidates(
            include_discarded=include_discarded,
            status=status,
        )

    @router.post(
        "/discovery/candidates/{candidate_id}/promote",
        response_model=Source,
        tags=["Discovery"],
        summary="Promueve una fuente preseleccionada al catálogo oficial",
    )
    def promote_open_discovery_candidate(
        candidate_id: str,
        payload: PromoteCandidateRequest,
    ) -> Source:
        try:
            return open_discovery_svc.promote_candidate(candidate_id, payload)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post(
        "/discovery/candidates/{candidate_id}/discard",
        response_model=OpenDiscoveryCandidate,
        tags=["Discovery"],
        summary="Descarta de forma persistente un candidato con motivo auditable",
    )
    def discard_open_discovery_candidate(
        candidate_id: str,
        payload: DiscardCandidateRequest,
    ) -> OpenDiscoveryCandidate:
        if not payload.reason.strip():
            raise HTTPException(
                status_code=422,
                detail="Debe registrar un motivo no vacío para descartar el candidato.",
            )
        try:
            return open_discovery_svc.discard_candidate(candidate_id, payload)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post(
        "/discovery/candidates/{candidate_id}/reactivate",
        response_model=OpenDiscoveryCandidate,
        tags=["Discovery"],
        summary="Reactiva explícitamente un candidato previamente descartado",
    )
    def reactivate_open_discovery_candidate(
        candidate_id: str,
    ) -> OpenDiscoveryCandidate:
        try:
            return open_discovery_svc.reactivate_candidate(candidate_id)
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    # =========================================================================
    # OBJETIVOS 2 y 4: Catálogo, Extracción Dirigida y Trazabilidad de Archivos
    # =========================================================================

    def _filter_resources(
        *,
        source_id: Optional[str] = None,
        search: Optional[str] = None,
        extension: Optional[str] = None,
        processing_status: Optional[FileProcessingStatus] = None,
        resource_category: Optional[str] = None,
    ) -> List[ResourceCandidate]:
        resources = catalog_repo.list_resources(source_id=source_id)
        if extension:
            ext_norm = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            resources = [r for r in resources if (r.file_extension or "").lower() == ext_norm]
        if processing_status is not None:
            resources = [r for r in resources if r.processing_status == processing_status]
        if resource_category and resource_category.strip():
            cat_upper = resource_category.strip().upper()
            resources = [r for r in resources if (r.resource_category or "").upper() == cat_upper]
        if search and search.strip():
            term = search.strip().lower()
            resources = [
                r
                for r in resources
                if term in r.url.lower()
                or term in (r.title or "").lower()
                or term in r.resource_key.lower()
                or term in (r.declared_filename or "").lower()
                or term in (r.content_hash or "").lower()
            ]
        return resources

    @router.get("/catalog/resources", response_model=List[ResourceCandidate])
    def get_resources(
        response: Response,
        source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente"),
        search: Optional[str] = Query(None, description="Texto en título, URL, hash o clave"),
        extension: Optional[str] = Query(None, description="Extensión (.pdf, .xlsx, .parquet...)"),
        processing_status: Optional[FileProcessingStatus] = Query(
            None, description="Estado de procesamiento a nivel de archivo"
        ),
        resource_category: Optional[str] = Query(
            None, description="Categoría tipológica (TABULAR_CIENTIFICO, COMPRIMIDO, DOCUMENTAL)"
        ),
        sort_by: Optional[
            Literal[
                "discovered_at",
                "title",
                "source_id",
                "file_extension",
                "content_length_bytes",
                "current_version",
                "processing_status",
            ]
        ] = Query(None, description="Campo de ordenamiento server-side"),
        sort_dir: Literal["asc", "desc"] = Query("asc", description="Dirección de ordenamiento"),
        limit: Optional[int] = Query(None, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> List[ResourceCandidate]:
        has_extra_filters = bool(
            search
            or extension
            or processing_status is not None
            or resource_category
            or sort_by is not None
        )
        if (
            not has_extra_filters
            and (limit is not None or offset)
            and hasattr(catalog_repo, "query_resources")
        ):
            total_all = len(catalog_repo.list_resources(source_id=source_id))
            response.headers["X-Total-Count"] = str(total_all)
            response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
            return catalog_repo.query_resources(source_id, limit=limit, offset=offset)

        resources = _filter_resources(
            source_id=source_id,
            search=search,
            extension=extension,
            processing_status=processing_status,
            resource_category=resource_category,
        )
        response.headers["X-Total-Count"] = str(len(resources))
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

        if sort_by is not None:
            reverse = sort_dir == "desc"

            def _sort_key(res: ResourceCandidate) -> Any:
                if sort_by == "discovered_at":
                    return res.discovered_at.isoformat()
                if sort_by == "content_length_bytes":
                    return res.content_length_bytes or -1
                if sort_by == "current_version":
                    return res.current_version
                if sort_by == "processing_status":
                    return res.processing_status.value
                return str(getattr(res, sort_by, "") or "").lower()

            ordered = sorted(resources, key=_sort_key, reverse=reverse)
            return ordered[offset : offset + limit if limit is not None else None]

        return paginate(
            resources,
            limit,
            offset,
            lambda resource: (resource.source_id, resource.resource_key, resource.url),
        )

    @router.get(
        "/catalog/resources/export",
        tags=["Catalog"],
        summary="Exporta el subconjunto filtrado del catálogo como manifiesto JSONL, CSV o Parquet",
    )
    def export_resources_manifest(
        format: Literal["jsonl", "csv", "parquet"] = Query("jsonl"),
        source_id: Optional[str] = Query(None),
        search: Optional[str] = Query(None),
        extension: Optional[str] = Query(None),
        processing_status: Optional[FileProcessingStatus] = Query(None),
        resource_category: Optional[str] = Query(None),
    ) -> Response:
        resources = _filter_resources(
            source_id=source_id,
            search=search,
            extension=extension,
            processing_status=processing_status,
            resource_category=resource_category,
        )
        rows = [
            {
                "resource_key": r.resource_key,
                "source_id": r.source_id,
                "url": r.url,
                "title": r.title,
                "file_extension": r.file_extension,
                "resource_category": r.resource_category,
                "period_label": r.period_label,
                "version": r.current_version,
                "content_hash": r.content_hash,
                "processing_status": r.processing_status.value,
                "change_status": r.change_status.value,
                "content_type": r.content_type,
                "content_length_bytes": r.content_length_bytes,
                "declared_filename": r.declared_filename,
                "etag": r.etag,
                "last_modified_header": r.last_modified_header,
                "http_status": r.http_status,
                "discovered_at": r.discovered_at.isoformat(),
            }
            for r in resources
        ]
        headers = {
            "Content-Disposition": f'attachment; filename="datax_catalog_manifest.{format}"',
            "X-Total-Count": str(len(rows)),
            "Access-Control-Expose-Headers": "X-Total-Count, Content-Disposition",
        }
        if format == "jsonl":
            body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
            if body:
                body += "\n"
            return Response(content=body, media_type="application/x-ndjson", headers=headers)

        fieldnames = [
            "resource_key",
            "source_id",
            "url",
            "title",
            "file_extension",
            "resource_category",
            "period_label",
            "version",
            "content_hash",
            "processing_status",
            "change_status",
            "content_type",
            "content_length_bytes",
            "declared_filename",
            "etag",
            "last_modified_header",
            "http_status",
            "discovered_at",
        ]
        if format == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return Response(
                content=buf.getvalue(),
                media_type="text/csv; charset=utf-8",
                headers=headers,
            )

        # Formato columnar binario Apache Parquet mediante DuckDB in-memory
        import duckdb

        with tempfile.TemporaryDirectory() as tmpdir:
            json_in = Path(tmpdir) / "manifest.json"
            pq_out = Path(tmpdir) / "manifest.parquet"
            con = duckdb.connect(":memory:")
            try:
                if rows:
                    json_in.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                    con.execute(
                        f"COPY (SELECT * FROM read_json_auto('{json_in.as_posix()}')) "
                        f"TO '{pq_out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
                else:
                    cols_sql = ", ".join(f"CAST(NULL AS VARCHAR) AS {col}" for col in fieldnames)
                    con.execute(
                        f"COPY (SELECT {cols_sql} WHERE FALSE) "
                        f"TO '{pq_out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                    )
            finally:
                con.close()
            pq_bytes = pq_out.read_bytes()
        return Response(
            content=pq_bytes,
            media_type="application/vnd.apache.parquet",
            headers=headers,
        )

    @router.get(
        "/catalog/resources/{resource_key}/versions",
        response_model=List[ResourceVersion],
        tags=["Catalog"],
        summary="Lista el historial inmutable de versiones (v1..vN) de un recurso",
    )
    def get_resource_versions(resource_key: str) -> List[ResourceVersion]:
        current = catalog_repo.get_resource_by_key(resource_key)
        versions = catalog_repo.list_resource_versions(resource_key)
        if current is None and not versions:
            raise HTTPException(
                status_code=404,
                detail=f"Recurso '{resource_key}' no encontrado en el catálogo",
            )
        if not versions and current is not None:
            return [
                ResourceVersion(
                    resource_key=current.resource_key,
                    source_id=current.source_id,
                    version_number=max(1, current.current_version),
                    run_id="baseline",
                    url=current.url,
                    content_hash=current.content_hash,
                    etag=current.etag,
                    last_modified_header=current.last_modified_header,
                    content_length_bytes=current.content_length_bytes,
                    declared_filename=current.declared_filename,
                    detected_at=current.discovered_at,
                )
            ]
        return versions

    @router.get(
        "/catalog/resources/{resource_key}/audit",
        response_model=List[ResourceStatusAuditEvent],
        tags=["Catalog"],
        summary="Lista la bitácora inmutable de transiciones de estado de un archivo",
    )
    def get_resource_status_audit(resource_key: str) -> List[ResourceStatusAuditEvent]:
        current = catalog_repo.get_resource_by_key(resource_key)
        events = catalog_repo.list_resource_status_audit(resource_key)
        if current is None and not events:
            raise HTTPException(
                status_code=404,
                detail=f"Recurso '{resource_key}' no encontrado en el catálogo",
            )
        return events

    @router.patch(
        "/catalog/resources/{resource_key}/status",
        response_model=ResourceCandidate,
        tags=["Catalog"],
        summary="Actualiza el estado de procesamiento vía callback de Interno o override manual auditado",
    )
    def update_resource_status(
        resource_key: str,
        payload: ResourceStatusUpdateRequest,
    ) -> ResourceCandidate:
        if payload.actor == "MANUAL_OPERATOR":
            reason = (payload.override_reason or "").strip()
            if len(reason) < 5:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Toda sobreescritura manual de estado exige 'override_reason' "
                        "explícito no vacío (mínimo 5 caracteres)."
                    ),
                )

        if payload.idempotency_key:
            prior_event = catalog_repo.find_status_audit_by_idempotency_key(
                payload.idempotency_key
            )
            if prior_event is not None:
                existing_res = catalog_repo.get_resource_by_key(resource_key)
                if existing_res is not None:
                    return existing_res

        current = catalog_repo.get_resource_by_key(resource_key)
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=f"Recurso '{resource_key}' no encontrado en el catálogo",
            )
        prev_status = current.processing_status

        updated = catalog_repo.update_resource_processing_status(
            resource_key=resource_key,
            status=payload.processing_status,
            error=payload.processing_error,
            content_hash=payload.sha256,
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=f"Recurso '{resource_key}' no encontrado en el catálogo",
            )

        catalog_repo.save_resource_status_audit(
            ResourceStatusAuditEvent(
                resource_key=resource_key,
                source_id=updated.source_id,
                previous_status=prev_status,
                new_status=payload.processing_status,
                actor=payload.actor,
                override_reason=(payload.override_reason or "").strip() or None,
                download_id=payload.download_id,
                sha256=payload.sha256,
                error_detail=payload.processing_error,
                idempotency_key=payload.idempotency_key,
            )
        )
        return updated

    @router.get("/catalog/sources", response_model=List[Source])
    def get_sources(
        limit: Optional[int] = Query(None, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> List[Source]:
        if (limit is not None or offset) and hasattr(catalog_repo, "query_sources"):
            return catalog_repo.query_sources(limit=limit, offset=offset)
        return paginate(catalog_repo.list_sources(), limit, offset, lambda source: source.source_id)

    @router.get("/catalog/sources/{source_id}", response_model=Source)
    def get_source_detail(source_id: str) -> Source:
        src = catalog_repo.get_source(source_id)
        if not src:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        return src

    @router.patch("/catalog/sources/{source_id}", response_model=Source)
    def update_source_policy(source_id: str, payload: SourcePolicyUpdateRequest) -> Source:
        source = catalog_repo.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")

        if payload.entrypoint is not None:
            source.entrypoint = payload.entrypoint
        if payload.seeds is not None:
            source.seeds = payload.seeds
        if payload.update_category is not None:
            source.update_category = payload.update_category
        if payload.next_eligible_at is not None:
            source.next_eligible_at = datetime.fromisoformat(
                payload.next_eligible_at.replace("Z", "+00:00")
            )
        if payload.allowed_extensions is not None:
            source.allowed_extensions = payload.allowed_extensions
        if payload.rate_limit_seconds is not None:
            source.rate_limit_seconds = payload.rate_limit_seconds
        if payload.max_urls is not None:
            source.max_urls = payload.max_urls
        if payload.max_resources is not None:
            source.max_resources = payload.max_resources
        if payload.ignore_robots_txt is not None:
            source.ignore_robots_txt = payload.ignore_robots_txt
        if payload.health_status is not None:
            source.health_status = payload.health_status
        if payload.audit_code is not None:
            source.audit_code = payload.audit_code or None
        if payload.audit_note is not None:
            source.audit_note = payload.audit_note or None
        if payload.contingency_urls is not None:
            source.contingency_urls = payload.contingency_urls
            source.contingency_url = payload.contingency_urls[0] if payload.contingency_urls else None
        elif payload.contingency_url is not None:
            source.contingency_url = payload.contingency_url or None
            source.contingency_urls = [payload.contingency_url] if payload.contingency_url else []
        if payload.excluded_path_keywords is not None:
            source.excluded_path_keywords = payload.excluded_path_keywords
        if payload.allowed_hosts is not None:
            source.allowed_hosts = payload.allowed_hosts

        source.policy_customized = True
        source.updated_at = datetime.now(timezone.utc)
        catalog_repo.save_source(source)
        _sync_source_to_yaml_safe(source)
        return source

    @router.post(
        "/catalog/sources/{source_id}/dry-run",
        response_model=DryRunExtractionResponse,
        tags=["Catalog"],
        summary="Simula la extracción dirigida sobre una fuente sin persistir cambios",
    )
    async def dry_run_source_extraction(
        source_id: str,
        payload: Optional[DryRunPolicyRequest] = None,
    ) -> DryRunExtractionResponse:
        req = payload or DryRunPolicyRequest()
        source = catalog_repo.get_source(source_id)
        resolved_cfg = config_path or _resolve_config_yaml_path()
        base_config: Optional[SourceConfig] = None

        if resolved_cfg and resolved_cfg.is_file():
            try:
                raw_cfg = yaml.safe_load(resolved_cfg.read_text(encoding="utf-8")) or {}
                for item in raw_cfg.get("sources", []):
                    if (
                        isinstance(item, dict)
                        and str(item.get("source_id", "")).lower() == source_id.lower()
                    ):
                        base_config = SourceConfig.model_validate(item)
                        break
            except Exception:
                base_config = None

        if source is None and base_config is None:
            raise HTTPException(
                status_code=404, detail=f"Fuente '{source_id}' no encontrada"
            )

        if base_config is None and source is not None:
            base_config = SourceConfig(
                source_id=source.source_id,
                name=source.name,
                entrypoint=source.entrypoint,
                workflow=source.workflow,
                update_category=source.update_category,
                seeds=list(source.seeds) if source.seeds else [source.entrypoint],
                allowed_extensions=list(source.allowed_extensions),
                rate_limit_seconds=source.rate_limit_seconds,
                max_urls=source.max_urls,
                max_resources=source.max_resources,
                ignore_robots_txt=source.ignore_robots_txt,
                excluded_path_keywords=list(source.excluded_path_keywords),
                allowed_hosts=list(source.allowed_hosts),
                contingency_url=source.contingency_url,
                contingency_urls=list(source.contingency_urls),
            )
        elif base_config is not None and source is not None and source.policy_customized:
            base_config = base_config.model_copy(
                update={
                    "entrypoint": source.entrypoint or base_config.entrypoint,
                    "seeds": list(source.seeds) if source.seeds else base_config.seeds,
                    "allowed_extensions": (
                        list(source.allowed_extensions)
                        if source.allowed_extensions
                        else base_config.allowed_extensions
                    ),
                    "rate_limit_seconds": source.rate_limit_seconds,
                    "ignore_robots_txt": source.ignore_robots_txt,
                    "excluded_path_keywords": list(source.excluded_path_keywords),
                    "allowed_hosts": (
                        list(source.allowed_hosts)
                        if source.allowed_hosts
                        else base_config.allowed_hosts
                    ),
                }
            )

        assert base_config is not None
        updates: Dict[str, Any] = {}
        if req.entrypoint is not None:
            updates["entrypoint"] = req.entrypoint
            if req.seeds is None:
                updates["seeds"] = [req.entrypoint]
        if req.seeds is not None:
            updates["seeds"] = req.seeds
        if req.allowed_extensions is not None:
            updates["allowed_extensions"] = req.allowed_extensions
        if req.excluded_path_keywords is not None:
            updates["excluded_path_keywords"] = req.excluded_path_keywords
        if req.allowed_hosts is not None:
            updates["allowed_hosts"] = req.allowed_hosts
        if req.rate_limit_seconds is not None:
            updates["rate_limit_seconds"] = req.rate_limit_seconds
        if req.ignore_robots_txt is not None:
            updates["ignore_robots_txt"] = req.ignore_robots_txt

        effective_cfg = base_config.model_copy(update=updates) if updates else base_config
        orchestrator = RunOrchestrator(
            catalog_repo=catalog_repo,
            report_repo=report_repo,
            config_path=resolved_cfg,
        )
        return await orchestrator.dry_run_source_async(
            effective_cfg,
            max_pages=req.max_pages,
            max_resources=req.max_resources,
        )

    # =========================================================================
    # OBJETIVO 3: Resiliencia y Evaluación Automática de Contingencias
    # =========================================================================

    @router.post(
        "/catalog/sources/{source_id}/contingency/discover",
        response_model=ContingencyEvaluationResponse,
        tags=["Resilience"],
        summary="Busca, evalúa en vivo y registra URLs alternativas de respaldo",
    )
    def discover_source_contingencies(
        source_id: str,
        auto_register: bool = Query(True, description="Registrar automáticamente las URLs aptas"),
    ) -> ContingencyEvaluationResponse:
        source = catalog_repo.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        result = contingency_svc.discover_and_evaluate(source, auto_register=auto_register)
        if result.auto_registered_count > 0:
            _sync_source_to_yaml_safe(source)
        return result

    @router.post(
        "/catalog/sources/{source_id}/contingency/probe",
        response_model=ContingencyCandidate,
        tags=["Resilience"],
        summary="Verifica en vivo una URL de respaldo individual (HTTP, latencia, TLS, archivos)",
    )
    def probe_source_contingency_url(
        source_id: str,
        payload: ContingencyProbeRequest,
    ) -> ContingencyCandidate:
        source = catalog_repo.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        return contingency_svc.probe_single_url(
            payload.url,
            origin_type="CONFIGURADA",
            update_category=source.update_category,
        )

    @router.get(
        "/catalog/resilience/coverage",
        response_model=ResilienceCoverageReport,
        tags=["Resilience"],
        summary="Audita la cobertura de resiliencia e identifica puntos únicos de fallo (0 respaldos)",
    )
    def get_resilience_coverage() -> ResilienceCoverageReport:
        return contingency_svc.compute_resilience_coverage(catalog_repo.list_sources())

    @router.get(
        "/catalog/sources/{source_id}/failovers",
        response_model=List[FailoverEvent],
        tags=["Resilience"],
        summary="Lista el historial de conmutaciones automáticas (failover_log) de una fuente",
    )
    def list_source_failovers(
        source_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> List[FailoverEvent]:
        return catalog_repo.list_failover_events(source_id=source_id, limit=limit)

    @router.get(
        "/catalog/failovers",
        response_model=List[FailoverEvent],
        tags=["Resilience"],
        summary="Lista global de eventos de conmutación automática a contingencia",
    )
    def list_all_failovers(
        source_id: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ) -> List[FailoverEvent]:
        return catalog_repo.list_failover_events(source_id=source_id, limit=limit)

    # =========================================================================
    # OBJETIVO 5: Monitoreo Estructural, Canales Push y Reporte Técnico Taiga
    # =========================================================================

    @router.get(
        "/alerts",
        response_model=List[StructuralAlert],
        tags=["Alerts"],
        summary="Lista alertas tempranas de cambios estructurales, volumen o bloqueos de acceso",
    )
    def list_alerts(
        source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente"),
        status: Optional[AlertStatus] = Query(None, description="Filtrar por ciclo de vida"),
    ) -> List[StructuralAlert]:
        non_incident_codes = {"OPERATIONAL_OK", "OK", "NONE", "SIN_INCIDENCIA"}
        persisted: List[StructuralAlert] = [
            a
            for a in catalog_repo.list_structural_alerts(source_id=source_id)
            if (a.alert_code or "").upper() not in non_incident_codes
        ]
        seen_sources = {a.source_id.lower() for a in persisted}

        for src in catalog_repo.list_sources():
            if source_id and src.source_id.lower() != source_id.lower():
                continue
            if src.source_id.lower() in seen_sources:
                continue
            raw_code = (src.audit_code or "").strip().upper()
            has_audit_incident = bool(raw_code and raw_code not in non_incident_codes)
            if src.health_status in ("WARNING", "SUSPENDED") or has_audit_incident:
                code = raw_code if has_audit_incident else "DOM_CHANGED"
                persisted.append(
                    StructuralAlert(
                        alert_id=f"alrt_cat_{src.source_id}",
                        source_id=src.source_id,
                        source_name=src.name,
                        entrypoint=src.entrypoint,
                        alert_code=code,
                        severity=map_audit_severity(code),
                        previous_signature=src.dom_structural_hash or src.api_schema_hash,
                        current_signature=src.dom_structural_hash or src.api_schema_hash,
                        affected_selectors=src.baseline_selectors,
                        diagnostic_summary=(
                            src.audit_note
                            or f"Incidencia operativa '{code}' detectada en {src.name}."
                        ),
                        contingency_active=src.active_contingency_url,
                        detected_at=src.updated_at,
                        last_seen_at=src.updated_at,
                    )
                )
        if status is not None:
            persisted = [a for a in persisted if a.status == status]
        return sorted(persisted, key=lambda a: a.detected_at, reverse=True)

    @router.get(
        "/alerts/channels",
        response_model=List[AlertChannelConfig],
        tags=["Alerts"],
        summary="Lista canales de notificación push configurados (Webhook, Slack, Teams, SMTP, Taiga)",
    )
    def list_alert_channels() -> List[AlertChannelConfig]:
        return catalog_repo.list_alert_channels()

    @router.post(
        "/alerts/channels",
        response_model=AlertChannelConfig,
        tags=["Alerts"],
        summary="Crea o actualiza un canal de notificación push de alertas",
    )
    def save_alert_channel(payload: AlertChannelConfig) -> AlertChannelConfig:
        catalog_repo.save_alert_channel(payload)
        return payload

    @router.delete(
        "/alerts/channels/{channel_id}",
        tags=["Alerts"],
        summary="Elimina un canal de notificación push",
    )
    def delete_alert_channel(channel_id: str) -> Dict[str, Any]:
        deleted = catalog_repo.delete_alert_channel(channel_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Canal '{channel_id}' no encontrado")
        return {"deleted": True, "channel_id": channel_id}

    @router.post(
        "/alerts/channels/{channel_id}/test",
        response_model=AlertDeliveryLog,
        tags=["Alerts"],
        summary="Envía una notificación de prueba al canal especificado",
    )
    def test_alert_channel(channel_id: str) -> AlertDeliveryLog:
        channels = catalog_repo.list_alert_channels()
        target = next((c for c in channels if c.channel_id == channel_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Canal '{channel_id}' no encontrado")
        return alert_notification_svc.test_channel(target)

    @router.patch(
        "/alerts/{alert_id}",
        response_model=StructuralAlert,
        tags=["Alerts"],
        summary="Transiciona el ciclo de vida de una alerta (RECONOCIDA, EN_MANTENIMIENTO, SILENCIADA, RESUELTA)",
    )
    def update_alert_lifecycle(
        alert_id: str,
        payload: AlertLifecycleUpdateRequest,
    ) -> StructuralAlert:
        # Materializar alerta derivada del catálogo si aún no existía en structural_alerts
        if alert_id.startswith("alrt_cat_"):
            src_id = alert_id.removeprefix("alrt_cat_")
            existing_persisted = [
                a for a in catalog_repo.list_structural_alerts() if a.alert_id == alert_id
            ]
            if not existing_persisted:
                src = catalog_repo.get_source(src_id)
                if src is not None:
                    code = (src.audit_code or "DOM_CHANGED").strip().upper()
                    catalog_repo.save_structural_alert(
                        StructuralAlert(
                            alert_id=alert_id,
                            source_id=src.source_id,
                            source_name=src.name,
                            entrypoint=src.entrypoint,
                            alert_code=code,
                            severity=map_audit_severity(code),
                            previous_signature=src.dom_structural_hash or src.api_schema_hash,
                            current_signature=src.dom_structural_hash or src.api_schema_hash,
                            affected_selectors=src.baseline_selectors,
                            diagnostic_summary=(
                                src.audit_note
                                or f"Incidencia operativa '{code}' detectada en {src.name}."
                            ),
                            contingency_active=src.active_contingency_url,
                            detected_at=src.updated_at,
                            last_seen_at=src.updated_at,
                        )
                    )
        try:
            return alert_notification_svc.update_alert_lifecycle(
                alert_id,
                status=payload.status,
                assignee=payload.assignee,
                ticket_url=payload.ticket_url,
                muted_until=payload.muted_until,
                resolution_note=payload.resolution_note,
            )
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        except KeyError as err:
            raise HTTPException(status_code=404, detail=str(err)) from err

    @router.post(
        "/catalog/sources/{source_id}/verify-structure",
        response_model=MaintenanceTechnicalReport,
        tags=["Alerts"],
        summary="Inspecciona en vivo el DOM o API de una fuente y genera el Informe Técnico",
    )
    def verify_source_structure_endpoint(source_id: str) -> MaintenanceTechnicalReport:
        source = catalog_repo.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        updated_source, alert, report = verify_source_structure_live(source)
        catalog_repo.save_source(updated_source)
        if alert is not None:
            alert_notification_svc.record_and_dispatch(alert, source=updated_source)
        return report

    @router.get(
        "/alerts/{source_id}/report",
        response_model=MaintenanceTechnicalReport,
        tags=["Alerts"],
        summary="Genera el Informe Técnico de Mantenimiento en JSON y Markdown para Taiga",
    )
    def get_source_maintenance_report(source_id: str) -> MaintenanceTechnicalReport:
        source = catalog_repo.get_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        alerts = catalog_repo.list_structural_alerts(source_id=source_id)
        latest_alert = alerts[0] if alerts else None
        return build_maintenance_report(source, latest_alert)

    # =========================================================================
    # Almacenamiento, Proyección y Ejecución de Corridas
    # =========================================================================

    @router.get("/catalog/storage/status", response_model=StorageStatusResponse)
    def get_storage_status() -> StorageStatusResponse:
        mongo_connected = False
        mongo_db_name = "prospector_externo"
        collections_count = {
            "sources": 0,
            "discovered_urls": 0,
            "resource_candidates": 0,
            "runs": 0,
            "snapshots": 0,
        }

        if hasattr(catalog_repo, "db") and getattr(catalog_repo, "db") is not None:
            try:
                catalog_repo.check_health()
                mongo_connected = True
                mongo_db_name = getattr(catalog_repo, "db").name
                for col_name in collections_count:
                    collections_count[col_name] = catalog_repo.db[col_name].count_documents({})
            except Exception:
                mongo_connected = False
        else:
            try:
                from pymongo import MongoClient

                mongo_uri = os.environ.get("PROSPECTOR_MONGO_URI", "mongodb://localhost:27018")
                client: Any = MongoClient(mongo_uri, serverSelectionTimeoutMS=1000)
                client.admin.command("ping")
                mongo_connected = True
                db = client[mongo_db_name]
                for col_name in collections_count:
                    collections_count[col_name] = db[col_name].count_documents({})
                client.close()
            except Exception:
                mongo_connected = False

        state_dir = Path.cwd() / "output" / "state"
        if not state_dir.is_dir():
            state_dir = Path.cwd() / "Prospector-Externo" / "output" / "state"
        if not state_dir.is_dir() and hasattr(catalog_repo, "state_dir"):
            state_dir = getattr(catalog_repo, "state_dir")

        local_sources_count = 0
        local_resources_count = 0
        local_state_healthy = False
        sources_json_file = state_dir / "sources.json"
        if sources_json_file.is_file():
            try:
                data = json.loads(sources_json_file.read_text(encoding="utf-8"))
                local_sources_count = len(data)
                local_state_healthy = True
            except (OSError, ValueError):
                local_state_healthy = False

        res_catalog_dir = state_dir / "resource_catalog"
        if res_catalog_dir.is_dir():
            for file_p in res_catalog_dir.glob("*.json"):
                try:
                    rc_data = json.loads(file_p.read_text(encoding="utf-8"))
                    local_resources_count += len(rc_data.get("resources", []))
                except (OSError, ValueError, AttributeError):
                    continue

        backups_dir = state_dir / "backups"
        backups_count = 0
        last_backup_at = None
        if backups_dir.is_dir():
            backup_files = sorted(
                backups_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            backups_count = len(backup_files)
            if backup_files:
                last_backup_at = datetime.fromtimestamp(
                    backup_files[0].stat().st_mtime, tz=timezone.utc
                ).isoformat()

        db_type = (
            "mongodb"
            if hasattr(catalog_repo, "db") and getattr(catalog_repo, "db") is not None
            else "local_json"
        )
        overall_status: Literal["HEALTHY", "DEGRADED", "OFFLINE"] = (
            "HEALTHY" if (mongo_connected or local_state_healthy) else "DEGRADED"
        )

        return StorageStatusResponse(
            status=overall_status,
            database_type=db_type,
            mongo_connected=mongo_connected,
            mongo_database=mongo_db_name,
            collections=collections_count,
            local_state_healthy=local_state_healthy,
            local_sources_count=local_sources_count,
            local_resources_count=local_resources_count,
            local_state_path=str(state_dir),
            last_backup_at=last_backup_at,
            backups_count=backups_count,
        )

    @router.post("/catalog/storage/sync-backup", response_model=BackupSyncResponse)
    def trigger_storage_backup() -> BackupSyncResponse:
        now = datetime.now(timezone.utc)
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        backup_id = f"backup_{ts_str}"

        state_dir = Path.cwd() / "output" / "state"
        if not state_dir.is_dir():
            state_dir = Path.cwd() / "Prospector-Externo" / "output" / "state"
        if not state_dir.is_dir() and hasattr(catalog_repo, "state_dir"):
            state_dir = getattr(catalog_repo, "state_dir")

        backups_dir = state_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        backup_file = backups_dir / f"{backup_id}.json"

        sources = catalog_repo.list_sources()
        sources_payload = [s.model_dump(mode="json") for s in sources]
        try:
            resources_count = len(catalog_repo.list_resources())
        except Exception:
            resources_count = 0

        backup_data = {
            "backup_id": backup_id,
            "created_at": now.isoformat(),
            "sources_count": len(sources_payload),
            "resources_count": resources_count,
            "sources": sources_payload,
        }

        temp_file = backup_file.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps(backup_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_file.replace(backup_file)

        return BackupSyncResponse(
            status="SUCCESS",
            backup_id=backup_id,
            backup_path=str(backup_file),
            sources_backed_up=len(sources_payload),
            resources_backed_up=resources_count,
            timestamp=now.isoformat(),
        )

    @router.get("/catalog/sources/{source_id}/projection", response_model=DataxProjection)
    def get_source_projection(source_id: str) -> DataxProjection:
        source = catalog_repo.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Fuente '{source_id}' no encontrada")
        snapshot = catalog_repo.get_latest_snapshot(source_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail=f"Snapshot de '{source_id}' no encontrado"
            )
        return DataxProjectionService().project(source, snapshot)

    @router.post(
        "/runs",
        response_model=TriggerRunResponse,
        tags=["Runs"],
        summary="Dispara una corrida asíncrona de extracción dirigida con bloqueo de concurrencia",
    )
    def trigger_extraction_run(
        payload: TriggerRunRequest,
        background_tasks: BackgroundTasks,
    ) -> TriggerRunResponse:
        resolved_cfg = config_path or _resolve_config_yaml_path()
        if resolved_cfg is None or not resolved_cfg.is_file():
            raise HTTPException(
                status_code=500,
                detail="Archivo maestro de fuentes (config/sources.yaml) no disponible.",
            )

        if payload.source_id:
            src_check = catalog_repo.get_source(payload.source_id)
            if src_check is None:
                raw_cfg = yaml.safe_load(resolved_cfg.read_text(encoding="utf-8")) or {}
                known_ids = {
                    str(it.get("source_id", "")).lower()
                    for it in raw_cfg.get("sources", [])
                    if isinstance(it, dict)
                }
                if payload.source_id.lower() not in known_ids:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Fuente '{payload.source_id}' no encontrada",
                    )

        if not _RUN_DISPATCH_LOCK.acquire(blocking=False):
            return TriggerRunResponse(
                status="BUSY",
                message="Ya existe una corrida de extracción activa en curso.",
                source_id=payload.source_id,
            )

        run_id = (
            f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            f"_{uuid.uuid4().hex[:6]}"
        )
        RUN_CONTROL.register(run_id)

        def _execute_in_background() -> None:
            try:
                orchestrator = RunOrchestrator(
                    catalog_repo=catalog_repo,
                    report_repo=report_repo,
                    config_path=resolved_cfg,
                    run_control=RUN_CONTROL,
                )
                asyncio.run(
                    orchestrator.run_batch_async(
                        source_id_filter=payload.source_id,
                        force=payload.force,
                        run_id=run_id,
                    )
                )
            finally:
                _RUN_DISPATCH_LOCK.release()

        background_tasks.add_task(_execute_in_background)
        target_desc = f"la fuente '{payload.source_id}'" if payload.source_id else "el catálogo elegible"
        return TriggerRunResponse(
            status="STARTED",
            message=f"Extracción dirigida iniciada en segundo plano para {target_desc}.",
            source_id=payload.source_id,
            run_id=run_id,
        )

    @router.post(
        "/runs/{run_id}/cancel",
        response_model=CancelRunResponse,
        tags=["Runs"],
        summary="Solicita la cancelación limpia de una corrida en ejecución",
    )
    def cancel_extraction_run(run_id: str) -> CancelRunResponse:
        control_state = RUN_CONTROL.get(run_id)
        if control_state is not None:
            if control_state.finished_event.is_set():
                raise HTTPException(
                    status_code=409,
                    detail=f"La corrida '{run_id}' ya finalizó y no puede cancelarse.",
                )
            RUN_CONTROL.request_cancel(run_id)
            return CancelRunResponse(
                run_id=run_id,
                status="CANCELLING",
                message=f"Cancelación limpia solicitada para la corrida '{run_id}'.",
            )

        persisted = report_repo.get_run(run_id)
        if persisted is None:
            raise HTTPException(status_code=404, detail=f"Corrida '{run_id}' no encontrada")
        if persisted.status != RunStatus.RUNNING:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"La corrida '{run_id}' se encuentra en estado '{persisted.status.value}' "
                    "y no puede cancelarse."
                ),
            )
        persisted.status = RunStatus.CANCELLED
        persisted.finished_at = datetime.now(timezone.utc)
        persisted.failure_code = "CANCELLED_BY_OPERATOR"
        report_repo.save_run(persisted)
        RUN_CONTROL.publish(
            run_id,
            "run_cancelled",
            {"status": RunStatus.CANCELLED.value, "failure_code": persisted.failure_code},
        )
        return CancelRunResponse(
            run_id=run_id,
            status="CANCELLED",
            message=f"Corrida '{run_id}' marcada como cancelada.",
        )

    @router.get(
        "/runs/{run_id}/stream",
        tags=["Runs"],
        summary="Stream SSE en tiempo real del progreso de una corrida de extracción",
    )
    async def stream_run_progress(run_id: str) -> StreamingResponse:
        control_state = RUN_CONTROL.get(run_id)
        persisted = report_repo.get_run(run_id)
        if control_state is None and persisted is None:
            raise HTTPException(status_code=404, detail=f"Corrida '{run_id}' no encontrada")

        async def event_generator() -> AsyncIterator[str]:
            if control_state is None:
                assert persisted is not None
                event_name = (
                    "run_cancelled"
                    if persisted.status == RunStatus.CANCELLED
                    else "run_failed"
                    if persisted.status in (RunStatus.FAILED, RunStatus.INTERRUPTED)
                    else "run_completed"
                )
                payload = {
                    "event": event_name,
                    "run_id": persisted.run_id,
                    "timestamp": (
                        persisted.finished_at or persisted.started_at
                    ).isoformat(),
                    "data": {
                        "status": persisted.status.value,
                        "sources_processed": persisted.sources_processed,
                        "sources_failed": persisted.sources_failed,
                        "sources_skipped": persisted.sources_skipped,
                    },
                }
                yield f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return

            cursor = 0
            heartbeat_ticks = 0
            while True:
                with control_state.condition:
                    pending = control_state.events[cursor:]
                    is_done = control_state.finished_event.is_set()
                if pending:
                    heartbeat_ticks = 0
                    for ev in pending:
                        cursor += 1
                        ev_name = ev.get("event", "message")
                        yield f"event: {ev_name}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        if ev_name in TERMINAL_EVENT_TYPES:
                            return
                elif is_done:
                    return
                else:
                    heartbeat_ticks += 1
                    if heartbeat_ticks >= 30:
                        heartbeat_ticks = 0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/runs", response_model=List[RunReport])
    def list_runs(limit: int = Query(20, ge=1, le=100)) -> List[RunReport]:
        return report_repo.list_reports(limit=limit)

    @router.get(
        "/runs/{run_id}",
        response_model=Run,
        response_model_exclude={"owner_host", "owner_pid", "owner_process_started_at"},
    )
    def get_run_status(run_id: str) -> Run:
        run = report_repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Corrida '{run_id}' no encontrada")
        return run

    @router.get("/runs/{run_id}/report", response_model=RunReport)
    def get_run_report(run_id: str) -> RunReport:
        rep = report_repo.get_report(run_id)
        if not rep:
            raise HTTPException(
                status_code=404, detail=f"Reporte de corrida '{run_id}' no encontrado"
            )
        return rep

    @router.get("/sources/{source_id}/observations", response_model=List[SourceRunObservation])
    def get_observations(
        source_id: str, limit: int = Query(20, ge=1, le=50)
    ) -> List[SourceRunObservation]:
        return catalog_repo.get_source_observations(source_id, limit=limit)

    return router
