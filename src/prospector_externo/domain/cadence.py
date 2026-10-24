"""
Modelo de cadencia y planificación de ejecución para fuentes del Prospector Externo.
"""

from enum import Enum
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, Field


class UpdateCategory(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CadenceState(BaseModel):
    """Estado de cadencia de actualización y elegibilidad temporal."""
    category: UpdateCategory = UpdateCategory.DAILY
    next_eligible_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    consecutive_no_change_count: int = 0
    weekly_no_change_count: int = 0
    weekly_no_change_since_at: Optional[datetime] = None
    last_evaluated_at: Optional[datetime] = None

    def calculate_next_run(self, now: Optional[datetime] = None) -> datetime:
        """Calcula la próxima fecha de elegibilidad según la categoría actual."""
        base_time = now or datetime.now(timezone.utc)
        if self.category == UpdateCategory.DAILY:
            self.next_eligible_at = base_time + timedelta(days=1)
        elif self.category == UpdateCategory.WEEKLY:
            self.next_eligible_at = base_time + timedelta(days=7)
        elif self.category == UpdateCategory.MONTHLY:
            self.next_eligible_at = base_time + timedelta(days=30)
        self.last_evaluated_at = base_time
        return self.next_eligible_at

    def register_change(
        self, base_category: UpdateCategory, *, now: Optional[datetime] = None
    ) -> None:
        """Restablece la cadencia base y cancela cualquier período semanal sin cambios."""
        self.consecutive_no_change_count = 0
        self.weekly_no_change_count = 0
        self.weekly_no_change_since_at = None
        self.category = base_category
        self.calculate_next_run(now=now)

    def register_no_change(self, *, now: Optional[datetime] = None) -> None:
        """Reduce frecuencia solo con observaciones sin cambios y cuatro semanas reales.

        Cinco observaciones DAILY -> WEEKLY. Para pasar a MONTHLY se requieren
        cuatro observaciones nuevas en WEEKLY Y al menos 28 días transcurridos
        desde su inicio; contar llamadas inmediatas no equivale a cuatro semanas.
        """
        evaluated_at = now or datetime.now(timezone.utc)
        self.consecutive_no_change_count += 1

        if self.category == UpdateCategory.DAILY:
            if self.consecutive_no_change_count >= 5:
                self.category = UpdateCategory.WEEKLY
                self.weekly_no_change_count = 0
                self.weekly_no_change_since_at = evaluated_at
        elif self.category == UpdateCategory.WEEKLY:
            # Una fuente configurada de origen como WEEKLY no tiene transición
            # previa; iniciamos un intervalo conservador en la primera observación.
            if self.weekly_no_change_since_at is None:
                self.weekly_no_change_since_at = evaluated_at
            self.weekly_no_change_count += 1
            if (
                self.weekly_no_change_count >= 4
                and evaluated_at - self.weekly_no_change_since_at >= timedelta(weeks=4)
            ):
                self.category = UpdateCategory.MONTHLY

        self.calculate_next_run(now=evaluated_at)
