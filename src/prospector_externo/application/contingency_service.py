"""Servicio de Descubrimiento y Evaluación Automática de Contingencias (Objetivo 3).

Busca, verifica técnicamente y registra URLs alternativas de confianza
(espejos institucionales, repositorios de datos abiertos y capturas históricas
verificadas en Wayback Machine CDX) para garantizar resiliencia ante caídas
o descontinuación de los enlaces principales.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import List, Literal, Optional
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from prospector_externo.domain.models import Source
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


BackupOriginType = Literal[
    "CONFIGURADA",
    "ESPEJO_INSTITUCIONAL",
    "RUTA_ESTADISTICA_ALTERNA",
    "DATOS_ABIERTOS",
    "WAYBACK_CDX",
]

DOC_EXT_PATTERN = re.compile(r"\.(pdf|xlsx|xls|csv|zip|docx)(?:$|\?|#)", re.IGNORECASE)


class ContingencyCandidate(BaseModel):
    """Resultado técnico de evaluación de una URL alternativa de respaldo."""

    url: str
    origin_type: BackupOriginType
    reachable: bool
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    content_type: Optional[str] = None
    valid_structure: bool = False
    detected_resources_count: int = 0
    confidence_score: int = Field(ge=0, le=100)
    recommendation: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContingencyEvaluationResponse(BaseModel):
    """Respuesta consolidada del buscador y evaluador de contingencias."""

    source_id: str
    source_name: str
    primary_entrypoint: str
    auto_registered_count: int = 0
    candidates: List[ContingencyCandidate] = Field(default_factory=list)
    updated_contingency_urls: List[str] = Field(default_factory=list)


class ContingencyDiscoveryService:
    """Localiza, evalúa en vivo y registra fuentes de respaldo confiables."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        http_timeout: float = 7.0,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.http_timeout = http_timeout

    def discover_and_evaluate(
        self,
        source: Source,
        auto_register: bool = True,
    ) -> ContingencyEvaluationResponse:
        """Genera candidatos espejo/históricos, los prueba por HTTP y registra los aptos."""
        raw_candidates = self._collect_candidate_urls(source)
        evaluated: list[ContingencyCandidate] = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DATAX-Resilience-Probe/1.0; +https://datax.com.bo)"
            ),
        }

        with httpx.Client(
            timeout=self.http_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            # 1. Consultar Wayback CDX para capturas HTTP 200 verificadas
            wayback_urls = self._query_wayback_cdx(client, source.entrypoint)
            for wb_url in wayback_urls:
                if all(wb_url != existing_u for existing_u, _ in raw_candidates):
                    raw_candidates.append((wb_url, "WAYBACK_CDX"))

            # 2. Consultar datos.gob.bo CKAN por nombre/sigla institucional
            ckan_urls = self._query_datos_gob_bo(client, source)
            for ck_url in ckan_urls:
                if all(ck_url != existing_u for existing_u, _ in raw_candidates):
                    raw_candidates.append((ck_url, "DATOS_ABIERTOS"))

            # 3. Evaluar cada URL candidata en vivo
            for cand_url, origin_type in raw_candidates[:10]:
                evaluated.append(self._probe_candidate(client, cand_url, origin_type))

        evaluated.sort(
            key=lambda item: (item.reachable, item.confidence_score, -(item.latency_ms or 99999.0)),
            reverse=True,
        )

        auto_added = 0
        current_backups = list(source.contingency_urls or [])
        if source.contingency_url and source.contingency_url not in current_backups:
            current_backups.insert(0, source.contingency_url)

        if auto_register:
            for item in evaluated:
                if item.reachable and item.confidence_score >= 65 and item.url not in current_backups:
                    current_backups.append(item.url)
                    auto_added += 1
            if auto_added > 0 or current_backups != source.contingency_urls:
                source.contingency_urls = current_backups
                source.contingency_url = current_backups[0] if current_backups else None
                source.policy_customized = True
                source.updated_at = datetime.now(timezone.utc)
                self.catalog_repo.save_source(source)

        return ContingencyEvaluationResponse(
            source_id=source.source_id,
            source_name=source.name,
            primary_entrypoint=source.entrypoint,
            auto_registered_count=auto_added,
            candidates=evaluated,
            updated_contingency_urls=current_backups,
        )

    def _collect_candidate_urls(self, source: Source) -> List[tuple[str, BackupOriginType]]:
        pairs: list[tuple[str, BackupOriginType]] = []
        seen: set[str] = {source.entrypoint.rstrip("/")}

        def add(url: str, kind: BackupOriginType) -> None:
            clean = url.strip()
            if not clean.startswith(("http://", "https://")):
                return
            norm = clean.rstrip("/")
            if norm not in seen:
                seen.add(norm)
                pairs.append((clean, kind))

        for cfg_url in source.contingency_urls:
            add(cfg_url, "CONFIGURADA")
        if source.contingency_url:
            add(source.contingency_url, "CONFIGURADA")
        for seed in source.seeds:
            add(seed, "RUTA_ESTADISTICA_ALTERNA")

        parsed = urlsplit(source.entrypoint)
        host = parsed.netloc.lower()
        scheme = parsed.scheme.lower() or "https"
        if host:
            alt_host = host[4:] if host.startswith("www.") else f"www.{host}"
            add(urlunsplit((scheme, alt_host, parsed.path or "/", parsed.query, "")), "ESPEJO_INSTITUCIONAL")

            root_base = urlunsplit((scheme, host, "/", "", ""))
            for common_path in ("/estadisticas", "/publicaciones", "/boletines"):
                if parsed.path.rstrip("/").lower() != common_path:
                    add(urljoin(root_base, common_path), "RUTA_ESTADISTICA_ALTERNA")

        return pairs

    @staticmethod
    def _query_wayback_cdx(client: httpx.Client, target_url: str) -> List[str]:
        """Consulta Wayback Machine CDX API para obtener snapshots HTTP 200 recientes."""
        cdx_endpoint = (
            "https://web.archive.org/cdx/search/cdx"
            f"?url={quote_plus(target_url)}&output=json&fl=timestamp,original,statuscode,mimetype"
            "&filter=statuscode:200&collapse=digest&limit=3&sort=reverse"
        )
        try:
            resp = client.get(cdx_endpoint, timeout=5.0)
            if resp.status_code != 200:
                return []
            rows = resp.json()
            if not isinstance(rows, list) or len(rows) <= 1:
                return []
            urls: list[str] = []
            for row in rows[1:]:
                if isinstance(row, list) and len(row) >= 2:
                    ts, orig = str(row[0]), str(row[1])
                    urls.append(f"https://web.archive.org/web/{ts}/{orig}")
            return urls
        except (httpx.HTTPError, ValueError):
            return []

    @staticmethod
    def _query_datos_gob_bo(client: httpx.Client, source: Source) -> List[str]:
        """Consulta el catálogo nacional de Datos Abiertos por sigla de la entidad."""
        query = quote_plus(source.source_id.replace("_", " "))
        api_url = f"https://datos.gob.bo/api/3/action/package_search?q={query}&rows=2"
        try:
            resp = client.get(api_url, timeout=4.5)
            if resp.status_code != 200:
                return []
            payload = resp.json()
            results = (payload.get("result") or {}).get("results") or []
            found: list[str] = []
            for pkg in results:
                for res in pkg.get("resources") or []:
                    res_url = str(res.get("url") or "").strip()
                    if res_url.startswith(("http://", "https://")):
                        found.append(res_url)
                        if len(found) >= 2:
                            return found
            return found
        except (httpx.HTTPError, ValueError, AttributeError):
            return []

    @staticmethod
    def _probe_candidate(
        client: httpx.Client,
        url: str,
        origin_type: BackupOriginType,
    ) -> ContingencyCandidate:
        t0 = time.perf_counter()
        try:
            resp = client.get(url)
            latency = round((time.perf_counter() - t0) * 1000.0, 1)
        except httpx.HTTPError as exc:
            return ContingencyCandidate(
                url=url,
                origin_type=origin_type,
                reachable=False,
                http_status=None,
                latency_ms=None,
                valid_structure=False,
                detected_resources_count=0,
                confidence_score=0,
                recommendation=f"Descartada: inaccesible ({type(exc).__name__}).",
            )

        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200:
            return ContingencyCandidate(
                url=url,
                origin_type=origin_type,
                reachable=False,
                http_status=resp.status_code,
                latency_ms=latency,
                content_type=ctype or None,
                valid_structure=False,
                detected_resources_count=0,
                confidence_score=15 if resp.status_code < 500 else 0,
                recommendation=f"No apta: respondió HTTP {resp.status_code}.",
            )

        raw_bytes = resp.content[:16]
        is_direct_binary = (
            raw_bytes.startswith(b"%PDF-")
            or raw_bytes.startswith(b"PK\x03\x04")
            or raw_bytes.startswith(b"\xd0\xcf\x11\xe0")
            or ctype in {"text/csv", "application/json", "application/pdf"}
        )

        doc_links = 0
        if not is_direct_binary and ("html" in ctype or b"<html" in resp.content[:512].lower()):
            soup = BeautifulSoup(resp.text[:250_000], "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = str(a_tag.get("href", ""))
                if DOC_EXT_PATTERN.search(href):
                    doc_links += 1

        valid_struct = is_direct_binary or doc_links > 0 or ("json" in ctype)
        base_score = 55
        if url.startswith("https://"):
            base_score += 10
        if is_direct_binary:
            base_score += 25
            doc_links = max(doc_links, 1)
        elif doc_links >= 5:
            base_score += 28
        elif doc_links >= 1:
            base_score += 18
        if latency < 1500:
            base_score += 7

        score = min(100, base_score)
        if score >= 75:
            rec = "Óptima como fuente de respaldo verificada (alta disponibilidad y enlaces válidos)."
        elif score >= 65:
            rec = "Apta como contingencia secundaria verificada."
        else:
            rec = "Accesible por HTTP 200, pero con baja densidad de archivos descargables directos."

        return ContingencyCandidate(
            url=url,
            origin_type=origin_type,
            reachable=True,
            http_status=resp.status_code,
            latency_ms=latency,
            content_type=ctype or None,
            valid_structure=valid_struct,
            detected_resources_count=doc_links,
            confidence_score=score,
            recommendation=rec,
        )
