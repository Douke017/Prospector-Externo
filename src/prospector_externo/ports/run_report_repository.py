"""
Puerto abstracto para la persistencia de Reportes de Corrida (RunReport).
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from prospector_externo.domain.observations import Run, RunReport


class RunReportRepositoryPort(ABC):
    """Puerto para el ciclo de vida de corridas y sus reportes finales."""

    @abstractmethod
    def save_run(self, run: Run) -> None:
        """Crea/actualiza el estado de la corrida independientemente del reporte."""

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[Run]:
        """Consulta una corrida activa, finalizada o histórica."""

    @abstractmethod
    def list_runs(self, limit: int = 50) -> List[Run]:
        """Lista registros de corridas ordenados por inicio reciente."""

    @abstractmethod
    def save_report(self, report: RunReport) -> None:
        """Persiste el reporte estructurado de una corrida."""
        pass

    @abstractmethod
    def get_report(self, run_id: str) -> Optional[RunReport]:
        """Recupera el reporte correspondiente a un run_id."""
        pass

    @abstractmethod
    def list_reports(self, limit: int = 50) -> List[RunReport]:
        """Lista los últimos reportes de corrida generados."""
        pass
