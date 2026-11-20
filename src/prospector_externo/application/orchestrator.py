"""RunOrchestrator async con runtime HTTP compartido por corrida."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import socket
import threading
import time
import uuid

import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional

from pydantic import BaseModel, Field
import yaml

from prospector_externo.application.aggregator import ResultAggregator
from prospector_externo.application.alert_notification_service import AlertNotificationService
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.contingency_service import (
    ACTIVE_UPDATE_CATEGORIES,
    ContingencyDiscoveryService,
    classify_backup_freshness,
)
from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.application.report_builder import RunReportBuilder
from prospector_externo.application.run_control import RUN_CONTROL, RunControlRegistry
from prospector_externo.application.status_gate import SourceStatusGate
from prospector_externo.domain.models import FailoverEvent, ResourceCandidate, SourceConfig
from prospector_externo.domain.observations import (
    ContentStatus,
    ExecutionStatus,
    Run,
    RunStatus,
    RunReport,
    SourceRunObservation,
)
from prospector_externo.domain.structural_monitor import evaluate_volume_drop
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort

logger = logging.getLogger("prospector.application.orchestrator")


class DryRunSampleResource(BaseModel):
    """Muestra acotada de un recurso detectado durante simulación Dry-Run."""

    resource_key: str
    url: str
    title: str
    file_extension: str
    resource_category: str = "DOCUMENTAL"
    discovery_method: str = "html_link"
    period_label: Optional[str] = None
    http_status: Optional[int] = None
    content_length_bytes: Optional[int] = None


class DryRunExtractionResponse(BaseModel):
    """Resultado de una simulación de extracción sin efectos colaterales en persistencia."""

    source_id: str
    entrypoint: str
    workflow: str
    success: bool
    elapsed_ms: float
    pages_visited: int = 0
    urls_discovered: int = 0
    total_resources_detected: int = 0
    stop_reason: Optional[str] = None
    failure_code: Optional[str] = None
    matched_methods: List[str] = Field(default_factory=list)
    sample_resources: List[DryRunSampleResource] = Field(default_factory=list)
    mutated_catalog: bool = False


class RunOrchestrator:
    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        report_repo: RunReportRepositoryPort,
        config_path: Optional[Path] = None,
        worker_pool: Optional[AsyncWorkerPool] = None,
        http_runtime_factory: Optional[Callable[[], AsyncHttpRuntime]] = None,
        max_source_concurrency: int = 4,
        run_control: Optional[RunControlRegistry] = None,
        contingency_service: Optional[ContingencyDiscoveryService] = None,
        alert_service: Optional[AlertNotificationService] = None,
        auto_discover_contingency: bool = False,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.report_repo = report_repo
        self.config_path = config_path
        self.status_gate = SourceStatusGate()
        self.dispatcher = SourceDispatcher()
        self.catalog_service = CatalogApplicationService(catalog_repo)
        self.cadence_evaluator = CadencePolicyEvaluator(catalog_repo)
        self.http_runtime_factory = http_runtime_factory or AsyncHttpRuntime
        self.max_source_concurrency = max(1, max_source_concurrency)
        self.worker_pool = worker_pool
        self.run_control = run_control or RUN_CONTROL
        self.contingency_service = contingency_service
        self.alert_service = alert_service or AlertNotificationService(catalog_repo)
        self.auto_discover_contingency = auto_discover_contingency

    async def dry_run_source_async(
        self,
        config: SourceConfig,
        *,
        max_pages: int = 3,
        max_resources: int = 25,
    ) -> DryRunExtractionResponse:
        """Ejecuta una prueba de extracción en seco sin escribir en MongoDB ni sources.yaml."""
        t0 = time.perf_counter()
        bounded_cfg = config.model_copy(
            update={
                "max_urls": min(config.max_urls, max(1, max_pages)),
                "max_requests": min(config.max_requests, max(4, max_pages * 3)),
                "max_resources": min(config.max_resources, max(1, max_resources)),
                "max_runtime_seconds": min(config.max_runtime_seconds, 20.0),
                "inspect_archives": False,
            }
        )
        runtime = self.http_runtime_factory()
        isolated_dispatcher = SourceDispatcher()
        isolated_dispatcher.set_http_runtime(runtime)
        try:
            dispatched = isolated_dispatcher.dispatch(bounded_cfg)
            extraction = await dispatched if inspect.isawaitable(dispatched) else dispatched
        finally:
            await runtime.aclose()

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        sample_items = [
            DryRunSampleResource(
                resource_key=r.resource_key,
                url=r.url,
                title=r.title,
                file_extension=r.file_extension,
                resource_category=r.resource_category,
                discovery_method=r.discovery_method,
                period_label=r.period_label,
                http_status=r.http_status,
                content_length_bytes=r.content_length_bytes,
            )
            for r in extraction.resources[:max_resources]
        ]
        methods = sorted({r.discovery_method for r in extraction.resources if r.discovery_method})
        return DryRunExtractionResponse(
            source_id=bounded_cfg.source_id,
            entrypoint=bounded_cfg.entrypoint,
            workflow=bounded_cfg.workflow,
            success=bool(extraction.success),
            elapsed_ms=elapsed_ms,
            pages_visited=extraction.coverage.pages_visited,
            urls_discovered=extraction.coverage.urls_discovered,
            total_resources_detected=len(extraction.resources),
            stop_reason=extraction.coverage.stop_reason,
            failure_code=extraction.failure_code,
            matched_methods=methods,
            sample_resources=sample_items,
            mutated_catalog=False,
        )

    @staticmethod
    def _owner_process_is_gone(run: Run) -> bool:
        """Solo confirma muerte del propietario local, no inactividad por tiempo.

        Un PID reutilizado no se confunde con el proceso original. Registros
        antiguos o de otros hosts quedan RUNNING hasta intervención supervisada.
        """
        if (run.status != RunStatus.RUNNING
                or run.owner_host != socket.gethostname()
                or run.owner_pid is None
                or run.owner_process_started_at is None):
            return False
        try:
            process = psutil.Process(run.owner_pid)
            if abs(process.create_time() - run.owner_process_started_at) > 0.01:
                return True  # El PID pertenece ahora a otro proceso.
            return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, OSError):
            # Un fallo de permisos/inspección no demuestra muerte del proceso.
            return False

    def recover_abandoned_runs(self, *, limit: int = 1000) -> list[str]:
        """Reconciliación conservadora, sin crawling ni cambio de checkpoints.

        Si ya existe reporte final, restaura su estado real. En otro caso
        declara INTERRUPTED y reconstruye contadores desde observaciones
        efectivamente persistidas por fuente/corrida.
        """
        recovered: list[str] = []
        for candidate in self.report_repo.list_runs(limit=limit):
            if not self._owner_process_is_gone(candidate):
                continue
            # Volver a leer antes de actualizar: otro reconciliador pudo cerrar.
            run = self.report_repo.get_run(candidate.run_id)
            if run is None or not self._owner_process_is_gone(run):
                continue
            report = self.report_repo.get_report(run.run_id)
            if report is not None:
                finalized = Run.from_legacy_report(report)
                run.status = finalized.status
                run.finished_at = report.finished_at
                run.sources_processed = report.sources_processed
                run.sources_failed = report.sources_failed
                run.sources_skipped = report.sources_skipped
                run.failure_code = None
            else:
                processed = failed = skipped = 0
                for source_id in dict.fromkeys(run.selected_source_ids):
                    matches = [o for o in self.catalog_repo.get_source_observations(
                        source_id, limit=1000,
                    ) if o.run_id == run.run_id]
                    if not matches:
                        continue
                    status = matches[0].execution_status
                    if status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS):
                        processed += 1
                    elif status == ExecutionStatus.FAILED:
                        failed += 1
                    elif status == ExecutionStatus.SKIPPED:
                        skipped += 1
                run.status = RunStatus.INTERRUPTED
                run.finished_at = datetime.now(timezone.utc)
                run.sources_processed = processed
                run.sources_failed = failed
                run.sources_skipped = skipped
                run.failure_code = "OWNER_PROCESS_TERMINATED"
            self.report_repo.save_run(run)
            recovered.append(run.run_id)
            logger.warning("Corrida abandonada reconciliada: %s -> %s",
                           run.run_id, run.status.value)
        return recovered

    def load_configs(self) -> List[SourceConfig]:
        if not self.config_path or not self.config_path.exists():
            raise FileNotFoundError(
                f"Archivo de configuración de fuentes no encontrado: {self.config_path}"
            )
        with open(self.config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        configs = [SourceConfig.model_validate(item) for item in data.get("sources", [])]
        by_id = {cfg.source_id.lower(): cfg for cfg in configs}

        # Superponer políticas personalizadas desde UI y fuentes promovidas de descubrimiento abierto
        for persisted in self.catalog_repo.list_sources():
            key = persisted.source_id.lower()
            if key in by_id and getattr(persisted, "policy_customized", False):
                cfg = by_id[key]
                cfg.entrypoint = persisted.entrypoint or cfg.entrypoint
                if persisted.seeds:
                    cfg.seeds = list(persisted.seeds)
                if persisted.allowed_extensions:
                    cfg.allowed_extensions = list(persisted.allowed_extensions)
                cfg.rate_limit_seconds = persisted.rate_limit_seconds
                cfg.max_urls = persisted.max_urls
                cfg.max_resources = persisted.max_resources
                cfg.ignore_robots_txt = persisted.ignore_robots_txt
                cfg.update_category = persisted.update_category or cfg.update_category
                if persisted.contingency_urls:
                    cfg.contingency_urls = list(persisted.contingency_urls)
                    cfg.contingency_url = persisted.contingency_urls[0]
                if persisted.excluded_path_keywords:
                    cfg.excluded_path_keywords = list(persisted.excluded_path_keywords)
                if persisted.allowed_hosts:
                    cfg.allowed_hosts = list(persisted.allowed_hosts)
            elif key not in by_id and getattr(persisted, "policy_customized", False):
                promoted_cfg = SourceConfig(
                    source_id=persisted.source_id,
                    name=persisted.name,
                    entrypoint=persisted.entrypoint,
                    workflow=persisted.workflow,
                    seeds=list(persisted.seeds),
                    update_category=persisted.update_category,
                    allowed_extensions=list(persisted.allowed_extensions) or [".pdf", ".xlsx", ".csv", ".zip"],
                    rate_limit_seconds=persisted.rate_limit_seconds,
                    max_urls=persisted.max_urls,
                    max_resources=persisted.max_resources,
                    ignore_robots_txt=persisted.ignore_robots_txt,
                    contingency_urls=list(persisted.contingency_urls),
                    excluded_path_keywords=list(persisted.excluded_path_keywords),
                    allowed_hosts=list(persisted.allowed_hosts),
                )
                configs.append(promoted_cfg)
                by_id[key] = promoted_cfg

        return configs

    async def run_batch_async(
        self,
        source_id_filter: Optional[str] = None,
        force: bool = False,
        resume_discovery: bool = False,
        pipeline_run_id: Optional[str] = None,
        run_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> RunReport:
        # Las corridas nuevas no deben presentar como activas las que ya
        # terminaron por kill en este mismo host. Se omiten las no verificables.
        self.recover_abandoned_runs()
        started_at = datetime.now(timezone.utc)
        run_id = run_id or f"run_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        control_state = self.run_control.register(run_id)
        effective_cancel = cancel_event or control_state.cancel_event
        all_configs = self.load_configs()

        if source_id_filter:
            target_configs = [
                cfg for cfg in all_configs
                if cfg.source_id.lower() == source_id_filter.lower()
            ]
            if not target_configs:
                persisted_src = self.catalog_repo.get_source(source_id_filter)
                if persisted_src is not None and persisted_src.entrypoint:
                    target_configs = [
                        SourceConfig(
                            source_id=persisted_src.source_id,
                            name=persisted_src.name,
                            entrypoint=persisted_src.entrypoint,
                            workflow=persisted_src.workflow or "html",
                            seeds=list(persisted_src.seeds) or [persisted_src.entrypoint],
                            update_category=persisted_src.update_category,
                            allowed_extensions=list(persisted_src.allowed_extensions) or [".pdf", ".xlsx", ".csv", ".zip"],
                            rate_limit_seconds=min(float(persisted_src.rate_limit_seconds or 0.5), 0.5),
                            max_urls=min(int(persisted_src.max_urls or 15), 25),
                            max_resources=min(int(persisted_src.max_resources or 50), 100),
                            ignore_robots_txt=persisted_src.ignore_robots_txt,
                            contingency_urls=list(persisted_src.contingency_urls),
                            excluded_path_keywords=list(persisted_src.excluded_path_keywords),
                            allowed_hosts=list(persisted_src.allowed_hosts),
                        )
                    ]
            if not target_configs:
                raise ValueError(
                    f"Fuente '{source_id_filter}' no encontrada en la configuración"
                )
        else:
            target_configs = all_configs

        aggregator = ResultAggregator()
        eligible: List[SourceConfig] = []

        run = Run(
            run_id=run_id,
            pipeline_run_id=pipeline_run_id,
            started_at=started_at,
            status=RunStatus.RUNNING,
            selected_source_ids=[cfg.source_id for cfg in target_configs],
            sources_selected=len(target_configs),
            owner_host=socket.gethostname(),
            owner_pid=os.getpid(),
            owner_process_started_at=psutil.Process(os.getpid()).create_time(),
        )
        # Se escribe antes de cualquier solicitud o checkpoint: un fallo no
        # puede hacer desaparecer la existencia de la corrida.
        self.report_repo.save_run(run)
        self.run_control.publish(
            run_id,
            "run_started",
            {
                "sources_selected": len(target_configs),
                "selected_source_ids": run.selected_source_ids,
            },
        )
        logger.info("Corrida iniciada: %s (%d fuentes seleccionadas)",
                    run_id, len(target_configs))

        try:
            for config in target_configs:
                source_state = self.catalog_repo.get_source(config.source_id)
                prior_run = (self.report_repo.get_run(source_state.last_run_id)
                             if resume_discovery and pipeline_run_id and source_state
                             and source_state.last_run_id else None)
                recovering_pipeline = bool(
                    prior_run is not None and prior_run.pipeline_run_id == pipeline_run_id
                    and config.source_id in prior_run.selected_source_ids
                    and prior_run.status in {RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.PARTIAL}
                )
                is_eligible, skip_reason = self.status_gate.evaluate_eligibility(
                    config=config, source_state=source_state,
                    force=force, now=started_at,
                    recovering_pipeline=recovering_pipeline,
                )
                if is_eligible:
                    eligible.append(config)
                    continue

                obs = SourceRunObservation(
                    source_id=config.source_id, run_id=run_id,
                    workflow=config.workflow,
                    execution_status=ExecutionStatus.SKIPPED,
                    content_status=ContentStatus.NOT_EVALUATED,
                    skip_reason=skip_reason,
                    observed_at=datetime.now(timezone.utc),
                )
                self.catalog_repo.save_source_observation(obs)
                aggregator.add_observation(obs)

            runtime = self.http_runtime_factory()
            # Se reutiliza el pool ya definido en infraestructura. Los slots
            # de navegador (máx. 2) no consumen slots HTTP mientras esperan.
            pool = self.worker_pool or AsyncWorkerPool(
                max_http_workers=self.max_source_concurrency,
                max_browser_workers=min(2, self.max_source_concurrency),
            )
            try:
                self.dispatcher.set_http_runtime(runtime)
                self.dispatcher.set_discovery_progress(
                    self.catalog_repo, run_id=run_id, resume=resume_discovery,
                    pipeline_run_id=pipeline_run_id,
                )
                semaphore = asyncio.Semaphore(self.max_source_concurrency)

                def _is_cancelled() -> bool:
                    return effective_cancel.is_set() or control_state.cancel_event.is_set()

                async def process_source(config: SourceConfig) -> None:
                    from urllib.parse import urlsplit

                    if _is_cancelled():
                        obs = SourceRunObservation(
                            source_id=config.source_id,
                            run_id=run_id,
                            workflow=config.workflow,
                            execution_status=ExecutionStatus.SKIPPED,
                            content_status=ContentStatus.NOT_EVALUATED,
                            skip_reason="CANCELLED_BY_OPERATOR",
                            observed_at=datetime.now(timezone.utc),
                        )
                        self.catalog_repo.save_source_observation(obs)
                        aggregator.add_observation(obs)
                        return

                    async with pool.async_source_slot(config):
                        async with semaphore:
                            if _is_cancelled():
                                obs = SourceRunObservation(
                                    source_id=config.source_id,
                                    run_id=run_id,
                                    workflow=config.workflow,
                                    execution_status=ExecutionStatus.SKIPPED,
                                    content_status=ContentStatus.NOT_EVALUATED,
                                    skip_reason="CANCELLED_BY_OPERATOR",
                                    observed_at=datetime.now(timezone.utc),
                                )
                                self.catalog_repo.save_source_observation(obs)
                                aggregator.add_observation(obs)
                                return

                            self.run_control.publish(
                                run_id,
                                "source_started",
                                {"source_id": config.source_id, "workflow": config.workflow},
                            )
                            dispatched = self.dispatcher.dispatch(config)
                            extraction = (await dispatched if inspect.isawaitable(dispatched)
                                          else dispatched)
                            used_contingency_url: Optional[str] = None

                            # Failover automático en cascada + auto-descubrimiento (Objetivo 3)
                            if not extraction.success and not _is_cancelled():
                                is_active_src = (
                                    config.update_category or "DAILY"
                                ).upper() in ACTIVE_UPDATE_CATEGORIES
                                primary_failure = extraction.failure_code
                                candidate_backups: list[tuple[str, bool]] = []
                                for b_url in config.contingency_urls:
                                    freshness, _ = classify_backup_freshness(b_url)
                                    if freshness == "HISTORICO_SOLO_LECTURA" and is_active_src:
                                        self.catalog_repo.save_failover_event(
                                            FailoverEvent(
                                                source_id=config.source_id,
                                                run_id=run_id,
                                                failed_url=config.entrypoint,
                                                resolved_url=None,
                                                failure_code=primary_failure,
                                                latency_ms=0.0,
                                                succeeded=False,
                                                auto_discovered=False,
                                                freshness_class="HISTORICO_SOLO_LECTURA",
                                                detail=(
                                                    f"Omitida URL histórica '{b_url}': prohibido "
                                                    "reemplazar fuente vigente con captura congelada."
                                                ),
                                            )
                                        )
                                        continue
                                    candidate_backups.append((b_url, False))

                                if not candidate_backups and (
                                    self.auto_discover_contingency
                                    or self.contingency_service is not None
                                ):
                                    disc_svc = self.contingency_service or ContingencyDiscoveryService(
                                        self.catalog_repo
                                    )
                                    src_obj = self.catalog_repo.get_source(config.source_id)
                                    if src_obj is not None:
                                        try:
                                            disc_res = await asyncio.to_thread(
                                                disc_svc.discover_and_evaluate,
                                                src_obj,
                                                True,
                                            )
                                            for cand in disc_res.candidates:
                                                if (
                                                    cand.eligible_for_live_failover
                                                    and cand.reachable
                                                    and cand.confidence_score >= 65
                                                ):
                                                    candidate_backups.append((cand.url, True))
                                        except Exception:
                                            logger.warning(
                                                "Auto-descubrimiento de contingencia falló para [%s]",
                                                config.source_id,
                                            )

                                for backup_url, was_auto_disc in candidate_backups:
                                    if _is_cancelled():
                                        break
                                    backup_host = urlsplit(backup_url).netloc.lower()
                                    fallback_hosts = list(config.allowed_hosts)
                                    if fallback_hosts and backup_host and backup_host not in fallback_hosts:
                                        fallback_hosts.append(backup_host)
                                    fallback_cfg = config.model_copy(
                                        update={
                                            "entrypoint": backup_url,
                                            "seeds": [backup_url],
                                            "allowed_hosts": fallback_hosts,
                                        }
                                    )
                                    logger.warning(
                                        "Activando failover automático para [%s] -> %s",
                                        config.source_id,
                                        backup_url,
                                    )
                                    fb_t0 = time.perf_counter()
                                    fb_dispatched = self.dispatcher.dispatch(fallback_cfg)
                                    fb_extraction = (
                                        await fb_dispatched
                                        if inspect.isawaitable(fb_dispatched)
                                        else fb_dispatched
                                    )
                                    fb_latency_ms = round((time.perf_counter() - fb_t0) * 1000.0, 2)
                                    freshness_cls, _ = classify_backup_freshness(backup_url)
                                    self.catalog_repo.save_failover_event(
                                        FailoverEvent(
                                            source_id=config.source_id,
                                            run_id=run_id,
                                            failed_url=config.entrypoint,
                                            resolved_url=backup_url if fb_extraction.success else None,
                                            failure_code=(
                                                primary_failure
                                                if fb_extraction.success
                                                else (fb_extraction.failure_code or primary_failure)
                                            ),
                                            http_status=200 if fb_extraction.success else None,
                                            latency_ms=fb_latency_ms,
                                            succeeded=bool(fb_extraction.success),
                                            auto_discovered=was_auto_disc,
                                            freshness_class=freshness_cls,
                                            detail=(
                                                f"Conmutación exitosa hacia {backup_url}"
                                                if fb_extraction.success
                                                else f"Intento fallido hacia {backup_url}"
                                            ),
                                        )
                                    )
                                    if fb_extraction.success:
                                        extraction = fb_extraction
                                        used_contingency_url = backup_url
                                        self.run_control.publish(
                                            run_id,
                                            "failover_activated",
                                            {
                                                "source_id": config.source_id,
                                                "failed_url": config.entrypoint,
                                                "resolved_url": backup_url,
                                                "latency_ms": fb_latency_ms,
                                                "auto_discovered": was_auto_disc,
                                            },
                                        )
                                        break

                            # Capturar historial previo antes de persistir la nueva observación para evaluar VOLUME_DROP
                            prior_observations = self.catalog_repo.get_source_observations(
                                config.source_id, limit=15
                            )
                            historical_counts = [
                                o.coverage.resources_found
                                for o in prior_observations
                                if o.run_id != run_id
                                and o.execution_status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS)
                            ]

                            try:
                                obs = self.catalog_service.reconcile_and_checkpoint(
                                    config=config,
                                    run_id=run_id,
                                    extraction_result=extraction,
                                )
                                src_after = self.catalog_repo.get_source(config.source_id)
                                if src_after is not None:
                                    src_after.active_contingency_url = used_contingency_url
                                    self.catalog_repo.save_source(src_after)
                                    if extraction.success:
                                        vol_alert = evaluate_volume_drop(
                                            src_after,
                                            len(extraction.resources),
                                            historical_counts,
                                        )
                                        if vol_alert is not None:
                                            self.alert_service.record_and_dispatch(
                                                vol_alert, source=src_after
                                            )
                            except OSError:
                                # HTTP success is not checkpoint success. Keep a
                                # failed, retryable observation/report even when
                                # writing one of the source artifacts fails.
                                logger.exception("Error de persistencia para [%s]", config.source_id)
                                obs = SourceRunObservation(
                                    source_id=config.source_id, run_id=run_id, workflow=config.workflow,
                                    execution_status=ExecutionStatus.FAILED,
                                    content_status=ContentStatus.NOT_EVALUATED,
                                    failure_code="SOURCE_PERSISTENCE_ERROR", coverage=extraction.coverage,
                                )
                                try:
                                    self.catalog_repo.save_source_observation(obs)
                                except OSError:
                                    logger.exception("No se pudo persistir observación de [%s]", config.source_id)
                                aggregator.add_observation(obs)
                                self.run_control.publish(
                                    run_id,
                                    "source_failed",
                                    {
                                        "source_id": config.source_id,
                                        "failure_code": obs.failure_code,
                                    },
                                )
                                return
                            if (extraction.success and extraction.metadata.get("discovery_cycle_id")
                                    and self.catalog_repo.supports_discovery_checkpoints):
                                progress = self.catalog_repo.get_discovery_checkpoint(config.source_id)
                                if progress is not None and progress.get("last_run_id") == run_id:
                                    progress["materialized_run_id"] = run_id
                                    self.catalog_repo.save_discovery_checkpoint(config.source_id, progress)
                            self.cadence_evaluator.evaluate_and_update(config, obs)
                            aggregator.add_observation(obs)
                            self.run_control.publish(
                                run_id,
                                "source_completed" if obs.execution_status != ExecutionStatus.FAILED else "source_failed",
                                {
                                    "source_id": config.source_id,
                                    "execution_status": obs.execution_status.value,
                                    "resources_found": obs.coverage.resources_found,
                                    "pages_visited": obs.coverage.pages_visited,
                                },
                            )
                tasks = [asyncio.create_task(process_source(cfg)) for cfg in eligible]

                async def _watch_cancellation() -> None:
                    while tasks and not all(t.done() for t in tasks):
                        if _is_cancelled():
                            for pending in tasks:
                                if not pending.done():
                                    pending.cancel()
                            return
                        await asyncio.sleep(0.05)

                watcher = asyncio.create_task(_watch_cancellation())
                try:
                    if _is_cancelled():
                        for task in tasks:
                            task.cancel()
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, BaseException) and not isinstance(res, asyncio.CancelledError):
                            raise res
                        if isinstance(res, asyncio.CancelledError) and not _is_cancelled():
                            raise res
                except BaseException:
                    # Ningún worker debe continuar escribiendo después de
                    # que el orquestador declare fallida/interrumpida la corrida.
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
            finally:
                if self.worker_pool is None:
                    pool.shutdown(wait=True)
                await runtime.aclose()

            finished_at = datetime.now(timezone.utc)
            was_cancelled = effective_cancel.is_set() or control_state.cancel_event.is_set()
            report = RunReportBuilder.build(
                run_id=run_id, started_at=started_at,
                finished_at=finished_at,
                total_selected=len(target_configs), aggregator=aggregator,
            )
            self.report_repo.save_report(report)
            if was_cancelled:
                run.status = RunStatus.CANCELLED
                run.failure_code = "CANCELLED_BY_OPERATOR"
            else:
                run.status = (RunStatus.PARTIAL if report.sources_failed or any(
                    obs.execution_status == ExecutionStatus.PARTIAL_SUCCESS
                    for obs in report.source_results
                ) else RunStatus.COMPLETED)
                if report.sources_failed and not report.sources_processed:
                    run.status = RunStatus.FAILED
            run.finished_at = finished_at
            run.sources_processed = report.sources_processed
            run.sources_failed = report.sources_failed
            run.sources_skipped = report.sources_skipped
            self.report_repo.save_run(run)
            self.run_control.publish(
                run_id,
                "run_cancelled" if was_cancelled else "run_completed",
                {
                    "status": run.status.value,
                    "sources_processed": run.sources_processed,
                    "sources_failed": run.sources_failed,
                    "sources_skipped": run.sources_skipped,
                },
            )
            return report
        except BaseException as exc:
            if run.status == RunStatus.RUNNING:
                run.status = (RunStatus.INTERRUPTED if isinstance(
                    exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
                ) else RunStatus.FAILED)
                run.finished_at = datetime.now(timezone.utc)
                run.sources_processed = aggregator.total_processed
                run.sources_failed = aggregator.total_failed
                run.sources_skipped = aggregator.total_skipped
                run.failure_code = type(exc).__name__
                try:
                    self.report_repo.save_run(run)
                except Exception:
                    logger.exception("No se pudo persistir estado final de %s", run_id)
                self.run_control.publish(
                    run_id,
                    "run_failed",
                    {"status": run.status.value, "failure_code": run.failure_code},
                )
            raise

    def run_batch(
        self,
        source_id_filter: Optional[str] = None,
        force: bool = False,
        resume_discovery: bool = False,
        pipeline_run_id: Optional[str] = None,
    ) -> RunReport:
        """Wrapper sync de compatibilidad. El CLI usa run_batch_async directamente."""
        return asyncio.run(
            self.run_batch_async(
                source_id_filter=source_id_filter,
                force=force,
                resume_discovery=resume_discovery,
                pipeline_run_id=pipeline_run_id,
            )
        )
