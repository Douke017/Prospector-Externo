"""Observaciones y reportes de corrida trazables."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"


class ContentStatus(str, Enum):
    CHANGED = "CHANGED"
    NO_CHANGE = "NO_CHANGE"
    EMPTY_RESULT = "EMPTY_RESULT"
    NOT_EVALUATED = "NOT_EVALUATED"


class RunStatus(str, Enum):
    """Estado del proceso batch; independiente de cada observación de fuente."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class Run(BaseModel):
    """Registro persistente del ciclo de vida, incluso sin reporte final."""

    run_id: str
    pipeline_run_id: Optional[str] = None
    started_at: datetime
    status: RunStatus = RunStatus.RUNNING
    finished_at: Optional[datetime] = None
    selected_source_ids: List[str] = Field(default_factory=list)
    sources_selected: int = Field(default=0, ge=0)
    sources_processed: int = Field(default=0, ge=0)
    sources_failed: int = Field(default=0, ge=0)
    sources_skipped: int = Field(default=0, ge=0)
    failure_code: Optional[str] = None
    # Identidad del proceso propietario para recuperación tras kill brusco.
    # None conserva el contrato de corridas históricas: no se recuperan a ciegas.
    owner_host: Optional[str] = None
    owner_pid: Optional[int] = None
    owner_process_started_at: Optional[float] = None
    legacy_inferred: bool = False

    @classmethod
    def from_legacy_report(cls, report: "RunReport") -> "Run":
        """Compatibilidad de lectura; no inventa una observación de ejecución."""
        partial = any(
            obs.execution_status == ExecutionStatus.PARTIAL_SUCCESS
            for obs in report.source_results
        )
        status = RunStatus.PARTIAL if report.sources_failed or partial else RunStatus.COMPLETED
        if report.sources_failed and not report.sources_processed:
            status = RunStatus.FAILED
        return cls(
            run_id=report.run_id, started_at=report.started_at,
            finished_at=report.finished_at, status=status,
            selected_source_ids=[obs.source_id for obs in report.source_results],
            sources_selected=report.sources_selected,
            sources_processed=report.sources_processed,
            sources_failed=report.sources_failed,
            sources_skipped=report.sources_skipped,
            legacy_inferred=True,
        )


class CoverageStats(BaseModel):
    """Métricas técnicas de cobertura de una fuente durante una corrida."""

    pages_visited: int = 0
    resumed_from_checkpoint: bool = False
    urls_visited_before_resume: int = Field(default=0, ge=0)
    urls_discovered: int = 0
    resources_found: int = 0
    urls_rejected: int = 0
    urls_failed: int = 0
    urls_pending: int = 0
    resources_limit_reached: int = 0
    requests_total: int = 0
    http_403: int = 0
    http_429: int = 0
    timeouts: int = 0
    robots_disallowed: int = 0
    sitemap_documents: int = 0
    sitemap_urls: int = 0
    sitemap_errors: int = 0
    spider_traps_blocked: int = 0
    query_variants_blocked: int = 0
    pagination_pages: int = 0
    pagination_families_stopped: int = 0
    api_endpoints: int = 0
    api_documentation_found: int = 0
    openapi_documents: int = 0
    api_non_get_operations_skipped: int = 0
    api_auth_required: int = 0
    api_unresolved_operations: int = 0
    api_pages_visited: int = 0
    api_records_sampled: int = 0
    api_pagination_stopped: int = 0
    api_documents_probed: int = 0
    api_document_errors: int = 0
    archive_inspected: int = 0
    archive_members_found: int = 0
    archive_inspection_errors: int = 0
    archive_inspection_deferred: int = 0
    stop_reason: Optional[str] = None

    def is_partial_discovery(self) -> bool:
        """El recorrido conservó hallazgos, pero no prueba cobertura completa.

        Las exclusiones deliberadas de scope y errores opcionales de sitemap no
        bastan por sí solos para degradar el resultado. Fallos de acceso,
        truncamientos y paginación incompleta sí impiden declarar SUCCESS total.
        """
        complete_reasons = {"QUEUE_EXHAUSTED", "API_SEEDS_EXHAUSTED", "CUSTOM_FORM_CONFIRMED"}
        reason = getattr(self.stop_reason, "value", self.stop_reason)
        # En código legacy/fixtures puede faltar el motivo sin haberse efectuado
        # ninguna visita. Una ejecución REAL con visitas y sin cierre conocido
        # se considera parcial en vez de inventar agotamiento de cola.
        if reason is not None and reason not in complete_reasons:
            return True
        if reason is None and self.pages_visited > 0:
            return True
        return any((
            self.urls_pending,
            self.urls_failed,
            self.resources_limit_reached,
            self.timeouts,
            self.http_403,
            self.http_429,
            self.robots_disallowed,
            self.pagination_families_stopped,
            self.api_pagination_stopped,
            self.api_document_errors,
            self.archive_inspection_errors,
            self.archive_inspection_deferred,
            self.api_auth_required,
            self.api_unresolved_operations,
        ))

    def confirms_resource_absence(self) -> bool:
        """Solo la cobertura exitosa y comparable permite contar ausencias."""
        return (self.stop_reason in {"QUEUE_EXHAUSTED", "API_SEEDS_EXHAUSTED"}
                and not self.resumed_from_checkpoint
                and not self.is_partial_discovery())


class SourceRunObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    run_id: str
    discovery_cycle_id: Optional[str] = None
    discovery_profile_fingerprint: Optional[str] = None
    resume_status: Optional[str] = None
    workflow: str
    execution_status: ExecutionStatus
    content_status: ContentStatus
    failure_code: Optional[str] = None
    skip_reason: Optional[str] = None
    robots_override_applied: bool = False
    robots_override_reason: Optional[str] = None
    coverage: CoverageStats = Field(default_factory=CoverageStats)
    next_eligible_at: Optional[datetime] = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RunReport(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sources_selected: int = 0
    sources_processed: int = 0
    sources_skipped: int = 0
    sources_failed: int = 0
    sources_with_changes: int = 0
    sources_without_changes: int = 0
    source_results: List[SourceRunObservation] = Field(default_factory=list)
