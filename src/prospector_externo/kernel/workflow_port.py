"""Puerto de workflows del microkernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from prospector_externo.domain.models import SourceConfig
from prospector_externo.kernel.contracts import ExtractionResult
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from prospector_externo.infrastructure.http_runtime import SourceHttpSession


class SourceWorkflow(ABC):
    supports_discovery_resume = False

    def bind_discovery_progress(
        self, checkpoint: Optional[dict], save: Callable[[dict], None], *,
        run_id: str, pipeline_run_id: Optional[str] = None,
    ) -> None:
        self._discovery_checkpoint = checkpoint
        self._save_discovery_callback = save
        self._discovery_run_id = run_id
        self._pipeline_run_id = pipeline_run_id

    def bind_http_session(self, session: SourceHttpSession) -> None:
        """Inyección explícita de infraestructura por fuente."""
        self.http_session = session

    @abstractmethod
    async def run(self, config: SourceConfig) -> ExtractionResult:
        pass
