"""Rutas de FastAPI para la fachada de Catálogo, Exploración, Resiliencia y Alertas."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
import yaml

from prospector_externo.application.contingency_service import (
    ContingencyDiscoveryService,
    ContingencyEvaluationResponse,
)
from prospector_externo.application.open_discovery_service import (
    OpenDiscoveryCandidate,
    OpenDiscoverySearchRequest,
    OpenDiscoveryService,
    PromoteCandidateRequest,
)
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.application.projection_service import DataxProjectionService
from prospector_externo.domain.models import (
    FileProcessingStatus,
    ResourceCandidate,
    Source,
)
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.domain.observations import Run, RunReport, SourceRunObservation
from prospector_externo.domain.projection import DataxProjection
from prospector_externo.domain.structural_monitor import (
    MaintenanceTechnicalReport,
    StructuralAlert,
    build_maintenance_report,
    map_audit_severity,
    verify_source_structure_live,
)
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
    """Solicitud de actualización del estado de procesamiento de un archivo (Objetivo 4)."""

    processing_status: FileProcessingStatus
    processing_error: Optional[str] = None


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
    def list_open_discovery_candidates() -> List[OpenDiscoveryCandidate]:
        return open_discovery_svc.list_candidates()

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

    # =========================================================================
    # OBJETIVOS 2 y 4: Catálogo, Extracción Dirigida y Trazabilidad de Archivos
    # =========================================================================

    @router.get("/catalog/resources", response_model=List[ResourceCandidate])
    def get_resources(
        source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente"),
        search: Optional[str] = Query(None, description="Texto en título, URL o clave"),
        extension: Optional[str] = Query(None, description="Extensión (.pdf, .xlsx, .csv, .zip)"),
        processing_status: Optional[FileProcessingStatus] = Query(
            None, description="Estado de procesamiento a nivel de archivo"
        ),
        limit: Optional[int] = Query(None, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> List[ResourceCandidate]:
        has_extra_filters = bool(search or extension or processing_status is not None)
        if (
            not has_extra_filters
            and (limit is not None or offset)
            and hasattr(catalog_repo, "query_resources")
        ):
            return catalog_repo.query_resources(source_id, limit=limit, offset=offset)

        resources = catalog_repo.list_resources(source_id=source_id)
        if extension:
            ext_norm = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            resources = [r for r in resources if (r.file_extension or "").lower() == ext_norm]
        if processing_status is not None:
            resources = [r for r in resources if r.processing_status == processing_status]
        if search and search.strip():
            term = search.strip().lower()
            resources = [
                r for r in resources
                if term in r.url.lower()
                or term in (r.title or "").lower()
                or term in r.resource_key.lower()
            ]
        return paginate(
            resources,
            limit,
            offset,
            lambda resource: (resource.source_id, resource.resource_key, resource.url),
        )

    @router.patch(
        "/catalog/resources/{resource_key}/status",
        response_model=ResourceCandidate,
        tags=["Catalog"],
        summary="Actualiza el estado de procesamiento auditable de un archivo extraído",
    )
    def update_resource_status(
        resource_key: str,
        payload: ResourceStatusUpdateRequest,
    ) -> ResourceCandidate:
        updated = catalog_repo.update_resource_processing_status(
            resource_key=resource_key,
            status=payload.processing_status,
            error=payload.processing_error,
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail=f"Recurso '{resource_key}' no encontrado en el catálogo",
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

    # =========================================================================
    # OBJETIVO 5: Monitoreo Estructural y Reporte Técnico para Taiga
    # =========================================================================

    @router.get(
        "/alerts",
        response_model=List[StructuralAlert],
        tags=["Alerts"],
        summary="Lista alertas tempranas de cambios estructurales o bloqueos de acceso",
    )
    def list_alerts(
        source_id: Optional[str] = Query(None, description="Filtrar por ID de fuente"),
    ) -> List[StructuralAlert]:
        non_incident_codes = {"OPERATIONAL_OK", "OK", "NONE", "SIN_INCIDENCIA"}
        persisted: List[StructuralAlert] = [
            a
            for a in catalog_repo.list_structural_alerts(source_id=source_id)
            if (a.alert_code or "").upper() not in non_incident_codes
        ]
        seen_sources = {a.source_id.lower() for a in persisted}

        # Incluir incidencias activas de auditoría del catálogo aún no persistidas como alerta puntual
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
                    )
                )
        return sorted(persisted, key=lambda a: a.detected_at, reverse=True)

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
            catalog_repo.save_structural_alert(alert)
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

        def _execute_in_background() -> None:
            try:
                orchestrator = RunOrchestrator(
                    catalog_repo=catalog_repo,
                    report_repo=report_repo,
                    config_path=resolved_cfg,
                )
                asyncio.run(
                    orchestrator.run_batch_async(
                        source_id_filter=payload.source_id,
                        force=payload.force,
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
