"""RunOrchestrator async con runtime HTTP compartido por corrida."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import socket
import uuid

import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from prospector_externo.application.aggregator import ResultAggregator
from prospector_externo.application.cadence_evaluator import CadencePolicyEvaluator
from prospector_externo.application.catalog_service import CatalogApplicationService
from prospector_externo.application.dispatcher import SourceDispatcher
from prospector_externo.application.report_builder import RunReportBuilder
from prospector_externo.application.status_gate import SourceStatusGate
from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.observations import (
    ContentStatus,
    ExecutionStatus,
    Run,
    RunStatus,
    RunReport,
    SourceRunObservation,
)
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.infrastructure.concurrency import AsyncWorkerPool
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort

logger = logging.getLogger("prospector.application.orchestrator")


class RunOrchestrator:
    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        report_repo: RunReportRepositoryPort,
        config_path: Optional[Path] = None,
        worker_pool: Optional[AsyncWorkerPool] = None,
        http_runtime_factory: Optional[Callable[[], AsyncHttpRuntime]] = None,
        max_source_concurrency: int = 4,
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
    ) -> RunReport:
        # Las corridas nuevas no deben presentar como activas las que ya
        # terminaron por kill en este mismo host. Se omiten las no verificables.
        self.recover_abandoned_runs()
        started_at = datetime.now(timezone.utc)
        run_id = f"run_{started_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        all_configs = self.load_configs()

        if source_id_filter:
            target_configs = [
                cfg for cfg in all_configs
                if cfg.source_id.lower() == source_id_filter.lower()
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

                async def process_source(config: SourceConfig) -> None:
                    from urllib.parse import urlsplit

                    async with pool.async_source_slot(config):
                        async with semaphore:
                            dispatched = self.dispatcher.dispatch(config)
                            extraction = (await dispatched if inspect.isawaitable(dispatched)
                                          else dispatched)
                            used_contingency_url: Optional[str] = None

                            # Failover automático hacia URLs de contingencia (Objetivo 3)
                            if not extraction.success and config.contingency_urls:
                                for backup_url in config.contingency_urls:
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
                                    fb_dispatched = self.dispatcher.dispatch(fallback_cfg)
                                    fb_extraction = (
                                        await fb_dispatched
                                        if inspect.isawaitable(fb_dispatched)
                                        else fb_dispatched
                                    )
                                    if fb_extraction.success:
                                        extraction = fb_extraction
                                        used_contingency_url = backup_url
                                        break

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
                                return
                            if (extraction.success and extraction.metadata.get("discovery_cycle_id")
                                    and self.catalog_repo.supports_discovery_checkpoints):
                                progress = self.catalog_repo.get_discovery_checkpoint(config.source_id)
                                if progress is not None and progress.get("last_run_id") == run_id:
                                    progress["materialized_run_id"] = run_id
                                    self.catalog_repo.save_discovery_checkpoint(config.source_id, progress)
                            self.cadence_evaluator.evaluate_and_update(config, obs)
                            aggregator.add_observation(obs)
                tasks = [asyncio.create_task(process_source(cfg)) for cfg in eligible]
                try:
                    await asyncio.gather(*tasks)
                except BaseException:
                    # Ningún worker debe continuar escribiendo después de
                    # que el orquestador declare fallida/interrumpida la corrida.
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            finally:
                if self.worker_pool is None:
                    pool.shutdown(wait=True)
                await runtime.aclose()

            finished_at = datetime.now(timezone.utc)
            report = RunReportBuilder.build(
                run_id=run_id, started_at=started_at,
                finished_at=finished_at,
                total_selected=len(target_configs), aggregator=aggregator,
            )
            self.report_repo.save_report(report)
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
