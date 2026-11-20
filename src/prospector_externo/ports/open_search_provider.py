"""Puerto de proveedores de descubrimiento en web abierta (Objetivo 1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pydantic import BaseModel, Field


class OpenSearchHit(BaseModel):
    """Resultado crudo devuelto por un proveedor externo de búsqueda abierta."""

    title: str
    url: str
    sector: str = "GENERAL"
    provider: str
    snippet: str = ""
    extra_sample_urls: list[str] = Field(default_factory=list)


class OpenSearchProviderPort(ABC):
    """Puerto saliente para consultar motores de búsqueda o catálogos abiertos."""

    provider_name: str = "generic"

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        sector: str = "TODOS",
        max_results: int = 10,
    ) -> list[OpenSearchHit]:
        """Devuelve candidatos de portales o repositorios públicos para los criterios."""
