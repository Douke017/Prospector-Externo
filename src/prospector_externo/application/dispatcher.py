"""Despachador async de workflows por fuente."""

import inspect
import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from prospector_externo.domain.models import SourceConfig
if TYPE_CHECKING:
    from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.kernel.registry import WorkflowRegistry
import prospector_externo.workflows  # noqa: F401  # garantiza autoregistro sin depender del orden de imports

logger = logging.getLogger("prospector.application.dispatcher")


class SourceDispatcher:
    def __init__(self, http_runtime: Optional["AsyncHttpRuntime"] = None) -> None:
        self.http_runtime = http_runtime
        self.progress_repo = None

    def set_http_runtime(self, runtime: "AsyncHttpRuntime") -> None:
        self.http_runtime = runtime

    def set_discovery_progress(self, repo, *, run_id, resume=False, pipeline_run_id=None):
        self.progress_repo = repo
        self.progress_run_id = run_id
        self.resume_discovery = resume
        self.pipeline_run_id = pipeline_run_id

    async def dispatch(self, config: SourceConfig) -> ExtractionResult:
        if self.http_runtime is None:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="HTTP_RUNTIME_NOT_CONFIGURED",
            )

        try:
            workflow = WorkflowRegistry.resolve(config.workflow)
        except ValueError as exc:
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code="WORKFLOW_NOT_FOUND",
                error_message=str(exc),
            )

        workflow.bind_http_session(self.http_runtime.session_for(config))

        try:
            if (getattr(workflow, "supports_discovery_resume", False) and self.progress_repo is not None
                    and self.progress_repo.supports_discovery_checkpoints):
                previous = (self.progress_repo.get_discovery_checkpoint(config.source_id)
                            if self.resume_discovery else None)
                workflow.bind_discovery_progress(
                    previous, lambda state: self.progress_repo.save_discovery_checkpoint(config.source_id, state),
                    run_id=self.progress_run_id, pipeline_run_id=self.pipeline_run_id,
                )
            result = workflow.run(config)
            if inspect.isawaitable(result):
                try:
                    result = await asyncio.wait_for(result, timeout=config.max_runtime_seconds)
                except asyncio.TimeoutError:
                    if hasattr(workflow, "on_timeout"):
                        return workflow.on_timeout(config)
                    return ExtractionResult(
                        source_id=config.source_id, success=False, failure_code="MAX_RUNTIME",
                        coverage={"stop_reason": "MAX_RUNTIME", "timeouts": 1},
                    )
            return result
        except Exception as exc:
            safe_error = ascii(exc)
            logger.error(
                "Fallo no controlado en workflow %s: %s: %s",
                config.workflow,
                type(exc).__name__,
                safe_error,
                exc_info=True,
            )
            return ExtractionResult(
                source_id=config.source_id,
                success=False,
                failure_code=("DISCOVERY_CHECKPOINT_INVALID"
                              if "DISCOVERY_" in str(exc)
                              else "UNHANDLED_WORKFLOW_EXCEPTION"),
                error_message=f"{type(exc).__name__}: {safe_error}",
            )
