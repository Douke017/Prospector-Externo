"""Modelos de dominio principales del Prospector Externo."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field, model_validator
import uuid


class ChangeStatus(str, Enum):
    NEW = "NEW"
    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    REMOVED = "REMOVED"
    NOT_EVALUATED = "NOT_EVALUATED"


class FileProcessingStatus(str, Enum):
    """Estado auditable de procesamiento a nivel de archivo (Objetivo 4)."""

    PENDIENTE = "PENDIENTE"
    EN_PROCESO = "EN_PROCESO"
    PROCESADO_EXITOSAMENTE = "PROCESADO_EXITOSAMENTE"
    ERROR = "ERROR"


class DiscoveryType(str, Enum):
    HTML = "html"
    JAVASCRIPT = "javascript"
    COMMENTED_HTML = "commented_html"
    API = "api"
    ARCHIVE_INTERNAL = "archive_internal"
    CUSTOM = "custom"
    SITEMAP = "sitemap"


class ResourceType(str, Enum):
    FILE = "file"
    API = "api"


class ApiMetadata(BaseModel):
    """Metadata específica de una referencia/endpoint API público."""

    identity: str
    format: Optional[str] = None
    method: str = "GET"
    documentation_url: Optional[str] = None
    is_openapi: bool = False
    is_geojson: bool = False
    has_pagination: bool = False
    records_detected: Optional[int] = None
    operation_id: Optional[str] = None
    auth_required: bool = False
    unresolved_required_params: Tuple[str, ...] = ()
    callable_by_policy: bool = False

    # Enriquecimiento BATCH 3B. Describe la inspección; no convierte al Prospector
    # en consumidor de negocio de la API.
    pages_sampled: int = 0
    records_sampled: int = 0
    pagination_strategy: Optional[str] = None
    documentation_probed: bool = False
    spec_version: Optional[str] = None


class SourceConfig(BaseModel):
    """Configuración declarativa de una fuente cargada desde sources.yaml."""

    source_id: str
    name: str = ""
    entrypoint: str
    workflow: str = "html"
    seeds: List[str] = Field(default_factory=list)
    update_category: str = "DAILY"

    # Resiliencia y contingencia (Objetivo 3)
    contingency_url: Optional[str] = None
    contingency_urls: List[str] = Field(default_factory=list)

    # Compatibilidad histórica. En el nuevo motor NO son gates destructivos del catálogo bruto.
    allowed_extensions: List[str] = Field(
        default_factory=lambda: [".pdf", ".xlsx", ".csv", ".zip"]
    )
    excluded_path_keywords: List[str] = Field(default_factory=list)

    ignore_robots_txt: bool = False
    robots_override_reason: Optional[str] = None
    rate_limit_seconds: float = 1.0

    # Límites seguros de discovery por fuente.
    max_depth: int = 2
    max_urls: int = 250
    max_resources: int = 1000
    max_runtime_seconds: float = Field(default=900.0, gt=0, allow_inf_nan=False)
    max_requests: int = Field(default=250, gt=0)
    max_query_variants: int = 25
    max_consecutive_errors: int = 5
    allowed_hosts: List[str] = Field(default_factory=list)
    max_redirects: int = 5

    # Inteligencia de cobertura / anti-spider-trap.
    max_calendar_variants: int = 36
    max_url_length: int = 2048
    max_query_keys: int = 12
    pagination_min_pages: int = 2
    pagination_empty_streak: int = 2
    pagination_window: int = 3

    # Sitemap discovery bounded.
    discover_sitemaps: bool = True
    max_sitemap_documents: int = 8
    max_sitemap_urls: int = 500
    max_sitemap_bytes: int = 1_000_000

    # API discovery bounded. Solo operaciones públicas GET y evidencia explícita.
    discover_apis: bool = True
    # Alcance explícitamente acotado: solo se consultan seeds curadas y
    # NUNCA se interpreta su agotamiento como cobertura completa del sitio.
    api_seed_scope: Literal["default", "curated"] = "default"
    max_api_endpoints: int = 200
    max_api_response_bytes: int = 2_000_000
    follow_api_pagination: bool = True
    max_api_pages: int = 5
    max_api_records_sampled: int = 5_000
    max_api_stagnant_pages: int = 2
    probe_api_documentation: bool = True
    max_api_documents: int = 8
    max_api_document_depth: int = 1
    max_api_document_bytes: int = 2_000_000

    # Descubrimiento opcional y acotado de miembros ZIP/TAR en RAM.
    inspect_archives: bool = True
    max_archive_inspections: int = Field(default=2, ge=0)
    max_archive_bytes: int = Field(default=4_000_000, gt=0)
    max_archive_entries: int = Field(default=200, gt=0)
    max_archive_uncompressed_bytes: int = Field(default=32_000_000, gt=0)

    # Browser workflow bounded.
    max_browser_pages: int = 15

    # Configuración declarativa para los pocos workflows custom justificados.
    custom_config: Dict[str, Any] = Field(default_factory=dict)

    # Contrato opcional de agrupamiento downstream. El crawler no lo ejecuta;
    # se aplica en la etapa offline de proyección DATAX.
    grouping_contract: Optional[str] = None

    @model_validator(mode="after")
    def validate_curated_api_seeds(self) -> "SourceConfig":
        if self.contingency_url and not self.contingency_urls:
            self.contingency_urls = [self.contingency_url]
        elif self.contingency_urls and not self.contingency_url:
            self.contingency_url = self.contingency_urls[0]
        if self.api_seed_scope == "curated":
            if self.workflow != "api":
                raise ValueError("api_seed_scope=curated exige workflow=api")
            if not self.seeds:
                raise ValueError("api_seed_scope=curated exige seeds explícitas")
        return self


class DiscoveredUrl(BaseModel):
    """Registro de una página o URL descubierta durante el recorrido."""

    normalized_url: str
    raw_url: str
    source_id: str
    discovery_type: DiscoveryType = DiscoveryType.HTML
    parent_url: Optional[str] = None
    depth: int = 0
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    http_status: Optional[int] = None


class ResourceCandidate(BaseModel):
    """Recurso público descubierto y auditado a nivel de archivo."""

    resource_key: str
    url: str
    source_id: str
    title: str = ""
    file_extension: str = ""
    content_type: Optional[str] = None
    content_length_bytes: Optional[int] = None
    last_modified_header: Optional[str] = None
    etag: Optional[str] = None
    discovered_from_url: Optional[str] = None
    extracted_from_archive: Optional[str] = None
    period_label: Optional[str] = None
    content_hash: Optional[str] = None
    change_status: ChangeStatus = ChangeStatus.NEW
    processing_status: FileProcessingStatus = FileProcessingStatus.PENDIENTE
    processing_updated_at: Optional[datetime] = None
    processing_error: Optional[str] = None
    # Ausencias consecutivas verificadas en recorridos completos comparables.
    consecutive_absences: int = Field(default=0, ge=0)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Evidencia adicional del discovery bruto.
    raw_url: Optional[str] = None
    discovery_method: str = "html_link"
    anchor_text: Optional[str] = None
    context_text: Optional[str] = None
    http_status: Optional[int] = None

    # API es metadata de discovery, no un modelo FILE/REPORT de Analize.
    resource_type: ResourceType = ResourceType.FILE
    api: Optional[ApiMetadata] = None


class Snapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    run_id: str
    discovery_profile_fingerprint: Optional[str] = None
    discovery_cycle_id: Optional[str] = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resources_hash: str
    total_resources: int
    # Los snapshots legacy sin estado explícito se leen como anteriormente.
    execution_status: Optional[str] = None
    resources: List[ResourceCandidate] = Field(default_factory=list)
    # Inventario RAW de paginas/enlaces de esta corrida, incluso sin recursos.
    # Campo opcional compatible con snapshots anteriores al Batch 6A.
    discovered_urls: List[DiscoveredUrl] = Field(default_factory=list)


class Source(BaseModel):
    source_id: str
    name: str
    entrypoint: str
    workflow: str
    health_status: str = "ACTIVE"
    update_category: str = "DAILY"

    # Diagnósticos reales de auditoría y alertas estructurales (Objetivo 5)
    audit_code: Optional[str] = None
    audit_note: Optional[str] = None
    dom_structural_hash: Optional[str] = None
    api_schema_hash: Optional[str] = None
    baseline_selectors: List[str] = Field(default_factory=list)
    baseline_resource_count: int = 0

    # Parámetros declarativos de política de crawleo (Objetivo 1 y 2)
    policy_customized: bool = False
    seeds: List[str] = Field(default_factory=list)
    allowed_extensions: List[str] = Field(default_factory=list)
    rate_limit_seconds: float = 1.0
    max_urls: int = 250
    max_resources: int = 1000
    ignore_robots_txt: bool = False

    # Resiliencia y contingencia (Objetivo 3)
    contingency_url: Optional[str] = None
    contingency_urls: List[str] = Field(default_factory=list)
    active_contingency_url: Optional[str] = None

    # Filtros de acceso y dominio autorizado (Objetivo 1)
    excluded_path_keywords: List[str] = Field(default_factory=list)
    allowed_hosts: List[str] = Field(default_factory=list)

    # Conservados entre corridas para evitar reiniciar las máquinas de estado.
    # Los catálogos anteriores, sin estos campos, siguen siendo compatibles.
    consecutive_failures: int = Field(default=0, ge=0)
    first_failure_at: Optional[datetime] = None
    consecutive_no_change_count: int = Field(default=0, ge=0)
    weekly_no_change_count: int = Field(default=0, ge=0)
    weekly_no_change_since_at: Optional[datetime] = None
    last_policy_run_id: Optional[str] = None
    # Identifica la última reconciliación materializada; None indica catálogo legacy.
    catalog_last_run_id: Optional[str] = None
    catalog_scope_fingerprint: Optional[str] = None
    next_eligible_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run_id: Optional[str] = None
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
