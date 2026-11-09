"""Servicio de Exploración Automatizada y Preselección en Web Abierta (Objetivo 1).

Identifica y preselecciona fuentes públicas de datos alineadas con criterios
temáticos, sectoriales y de formatos requeridos, calculando un Índice de
Confianza y Relevancia de Origen (0–100) auditable para promoción al catálogo.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Dict, List, Literal, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from prospector_externo.domain.models import Source
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


CandidateStatus = Literal["PRESELECCIONADA", "APROBADA", "DESCARTADA"]

CURATED_OPEN_WEB_DIRECTORY: list[dict[str, str]] = [
    {
        "title": "Banco Central de Bolivia — Estadísticas Semanales y Monetarias",
        "url": "https://www.bcb.gob.bo/?q=estadisticas/semanales",
        "sector": "MONETARIO_FINANCIERO",
    },
    {
        "title": "Bolsa Boliviana de Valores — Boletines Estadísticos y Ruedo",
        "url": "https://www.bbv.com.bo/estadisticas",
        "sector": "MERCADO_VALORES",
    },
    {
        "title": "FINRURAL — Desempeño Financiero de IFDs Asociadas",
        "url": "https://www.finrural.org.bo",
        "sector": "MICROFINANZAS_IFD",
    },
    {
        "title": "ASFI — Boletines Estadísticos del Sistema de Intermediación Financiera",
        "url": "https://www.asfi.gob.bo/index.php/mv-estadisticas/mv-boletines-estadisticos.html",
        "sector": "SUPERVISION_FINANCIERA",
    },
    {
        "title": "Instituto Nacional de Estadística (INE) — Estadísticas Económicas",
        "url": "https://www.ine.gob.bo/index.php/estadisticas-economicas/",
        "sector": "MACROECONOMIA",
    },
    {
        "title": "Autoridad de Fiscalización y Control de Pensiones y Seguros (APS)",
        "url": "https://www.aps.gob.bo/estadisticas",
        "sector": "PENSIONES_SEGUROS",
    },
    {
        "title": "ASOFIN — Estadísticas de Entidades Especializadas en Microfinanzas",
        "url": "https://www.asofinbolivia.com",
        "sector": "MICROFINANZAS_IFD",
    },
    {
        "title": "CEPALSTAT — Portal de Datos y Publicaciones Económicas Regionales",
        "url": "https://www.cepal.org/es/datos-y-estadisticas",
        "sector": "ORGANISMOS_MULTILATERALES",
    },
    {
        "title": "Ministerio de Economía y Finanzas Públicas — Dossier Fiscal",
        "url": "https://www.economiayfinanzas.gob.bo",
        "sector": "FISCAL_TESORO",
    },
]


INSTITUTIONAL_SUFFIX_SCORES: list[tuple[str, int]] = [
    (".gob.bo", 35),
    (".org.bo", 32),
    (".edu.bo", 28),
    (".com.bo", 24),
    (".int", 30),
    (".org", 24),
    (".gov", 30),
]


class TrustRelevanceBreakdown(BaseModel):
    """Desglose auditable del puntaje de confianza y relevancia (0–100)."""

    domain_trust_pts: int = Field(ge=0, le=35)
    protocol_health_pts: int = Field(ge=0, le=20)
    format_availability_pts: int = Field(ge=0, le=30)
    thematic_relevance_pts: int = Field(ge=0, le=15)


class OpenDiscoveryCandidate(BaseModel):
    """Fuente identificada y preseleccionada durante exploración de web abierta."""

    candidate_id: str
    domain: str
    entrypoint: str
    title: str
    sector: str
    trust_score: int = Field(ge=0, le=100)
    relevance_score: int = Field(ge=0, le=100)
    combined_score: int = Field(ge=0, le=100)
    breakdown: TrustRelevanceBreakdown
    tls_valid: bool = False
    http_status: Optional[int] = None
    detected_extensions: List[str] = Field(default_factory=list)
    detected_files_count: int = 0
    sample_resource_urls: List[str] = Field(default_factory=list)
    matched_keywords: List[str] = Field(default_factory=list)
    status: CandidateStatus = "PRESELECCIONADA"
    promoted_source_id: Optional[str] = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OpenDiscoverySearchRequest(BaseModel):
    """Criterios de búsqueda y filtrado general para exploración abierta."""

    query: str = Field(
        default="estadísticas cartera tasas boletín financiero",
        min_length=2,
        max_length=200,
    )
    sector: str = Field(default="TODOS")
    target_extensions: List[str] = Field(
        default_factory=lambda: [".xlsx", ".xls", ".csv", ".pdf", ".zip"]
    )
    seed_urls: List[str] = Field(default_factory=list)
    trusted_domains_only: bool = False
    min_combined_score: int = Field(default=45, ge=0, le=95)


class PromoteCandidateRequest(BaseModel):
    """Datos para promover una fuente preseleccionada al catálogo oficial."""

    source_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=2, max_length=160)
    update_category: Literal["DAILY", "WEEKLY", "MONTHLY"] = "MONTHLY"
    workflow: Literal["html", "javascript", "api"] = "html"


class OpenDiscoveryService:
    """Ejecuta exploración acotada, evaluación de confianza/relevancia y promoción."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        http_timeout: float = 7.0,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.http_timeout = http_timeout

    @staticmethod
    def score_domain_trust(host: str) -> int:
        host_l = host.lower().strip()
        for suffix, pts in INSTITUTIONAL_SUFFIX_SCORES:
            if host_l.endswith(suffix):
                return pts
        return 12

    @staticmethod
    def is_trusted_institutional_host(host: str) -> bool:
        host_l = host.lower()
        return any(
            host_l.endswith(sfx)
            for sfx in (".gob.bo", ".org.bo", ".edu.bo", ".com.bo", ".org", ".int", ".gov")
        )

    def search_and_preselect(
        self,
        criteria: OpenDiscoverySearchRequest,
        live_probe: bool = True,
    ) -> List[OpenDiscoveryCandidate]:
        """Explora semillas institucionales y web abierta aplicando filtros de confianza."""
        keywords = [
            tok.lower()
            for tok in re.split(r"[\s,;]+", criteria.query.strip())
            if len(tok.strip()) >= 3
        ]
        target_exts = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (criteria.target_extensions or [".xlsx", ".pdf", ".csv"])
        ]

        targets: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for custom_url in criteria.seed_urls:
            clean = custom_url.strip()
            if clean.startswith(("http://", "https://")) and clean not in seen_urls:
                seen_urls.add(clean)
                targets.append({
                    "title": f"Portal Explorado ({urlsplit(clean).netloc})",
                    "url": clean,
                    "sector": criteria.sector if criteria.sector != "TODOS" else "PERSONALIZADO",
                })

        sector_filter = (criteria.sector or "TODOS").strip().upper()
        for item in CURATED_OPEN_WEB_DIRECTORY:
            if sector_filter not in ("TODOS", "ALL", "*", ""):
                item_sec = item["sector"].upper()
                is_fin_alias = sector_filter == "FINANCIERO" and any(
                    tok in item_sec for tok in ("FINANC", "MICROFINANZ", "VALORES", "FISCAL")
                )
                if item_sec != sector_filter and sector_filter not in item_sec and not is_fin_alias:
                    continue
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                targets.append(item)

        existing_entrypoints = {
            UrlNormalizer.normalize(s.entrypoint): s.source_id
            for s in self.catalog_repo.list_sources()
        }

        candidates: list[OpenDiscoveryCandidate] = []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DATAX-OpenDiscovery/1.0; +https://datax.com.bo)"
            ),
        }

        with httpx.Client(
            timeout=self.http_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for entry in targets:
                url = entry["url"]
                parsed = urlsplit(url)
                host = parsed.netloc.lower()
                if criteria.trusted_domains_only and not self.is_trusted_institutional_host(host):
                    continue

                domain_pts = self.score_domain_trust(host)
                tls_valid = parsed.scheme.lower() == "https"
                http_status: Optional[int] = None
                page_title = entry["title"]
                detected_exts: set[str] = set()
                sample_urls: list[str] = []
                matched_kw: set[str] = set()

                for kw in keywords:
                    if kw in entry["title"].lower() or kw in url.lower():
                        matched_kw.add(kw)

                if live_probe:
                    try:
                        resp = client.get(url)
                        http_status = resp.status_code
                        if resp.status_code == 200:
                            soup = BeautifulSoup(resp.text[:350_000], "html.parser")
                            if soup.title and soup.title.string:
                                extracted_title = soup.title.string.strip()
                                if len(extracted_title) >= 4:
                                    page_title = extracted_title[:140]
                            text_sample = soup.get_text(" ", strip=True)[:25_000].lower()
                            for kw in keywords:
                                if kw in text_sample:
                                    matched_kw.add(kw)
                            for a_tag in soup.find_all("a", href=True):
                                raw_href = str(a_tag.get("href", "")).strip()
                                if not raw_href or raw_href.startswith(("javascript:", "mailto:", "#")):
                                    continue
                                abs_href = urljoin(str(resp.url), raw_href)
                                href_path = urlsplit(abs_href).path.lower()
                                for ext in target_exts:
                                    if href_path.endswith(ext):
                                        detected_exts.add(ext)
                                        if abs_href not in sample_urls and len(sample_urls) < 8:
                                            sample_urls.append(abs_href)
                    except httpx.HTTPError:
                        http_status = None

                protocol_pts = 0
                if tls_valid:
                    protocol_pts += 10
                if http_status == 200:
                    protocol_pts += 10
                elif http_status and 200 < http_status < 400:
                    protocol_pts += 6
                elif not live_probe and tls_valid:
                    protocol_pts += 6

                if len(sample_urls) >= 3:
                    format_pts = 30
                elif len(sample_urls) >= 1:
                    format_pts = 22
                elif http_status == 200:
                    format_pts = 10
                elif not live_probe and self.is_trusted_institutional_host(host):
                    format_pts = 18
                else:
                    format_pts = 4

                if keywords:
                    ratio = min(1.0, len(matched_kw) / max(1, min(len(keywords), 3)))
                    relevance_pts = int(round(ratio * 15))
                else:
                    relevance_pts = 10

                combined = min(100, domain_pts + protocol_pts + format_pts + relevance_pts)
                if combined < criteria.min_combined_score:
                    continue

                trust_pct = min(100, int(round((domain_pts + protocol_pts) / 55.0 * 100)))
                rel_pct = min(100, int(round((format_pts + relevance_pts) / 45.0 * 100)))
                norm_entry = UrlNormalizer.normalize(url)
                cand_id = "cand_" + hashlib.sha256(norm_entry.encode("utf-8")).hexdigest()[:12]
                already_src = existing_entrypoints.get(norm_entry)

                candidate = OpenDiscoveryCandidate(
                    candidate_id=cand_id,
                    domain=host,
                    entrypoint=url,
                    title=page_title,
                    sector=entry["sector"],
                    trust_score=trust_pct,
                    relevance_score=rel_pct,
                    combined_score=combined,
                    breakdown=TrustRelevanceBreakdown(
                        domain_trust_pts=domain_pts,
                        protocol_health_pts=protocol_pts,
                        format_availability_pts=format_pts,
                        thematic_relevance_pts=relevance_pts,
                    ),
                    tls_valid=tls_valid,
                    http_status=http_status,
                    detected_extensions=sorted(detected_exts),
                    detected_files_count=len(sample_urls),
                    sample_resource_urls=sample_urls,
                    matched_keywords=sorted(matched_kw),
                    status="APROBADA" if already_src else "PRESELECCIONADA",
                    promoted_source_id=already_src,
                )
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.combined_score, reverse=True)
        self._save_candidates(candidates)
        return candidates

    def list_candidates(self) -> List[OpenDiscoveryCandidate]:
        if hasattr(self.catalog_repo, "list_discovery_candidates"):
            return self.catalog_repo.list_discovery_candidates()
        return []

    def promote_candidate(
        self,
        candidate_id: str,
        payload: PromoteCandidateRequest,
    ) -> Source:
        candidates = self.list_candidates()
        target = next((c for c in candidates if c.candidate_id == candidate_id), None)
        if target is None:
            raise KeyError(f"Candidato '{candidate_id}' no encontrado en preselección")

        existing = self.catalog_repo.get_source(payload.source_id)
        host = urlsplit(target.entrypoint).netloc.lower()
        now = datetime.now(timezone.utc)

        source = existing or Source(
            source_id=payload.source_id.lower(),
            name=payload.name,
            entrypoint=target.entrypoint,
            workflow=payload.workflow,
            health_status="ACTIVE",
            update_category=payload.update_category,
            policy_customized=True,
            seeds=[target.entrypoint],
            allowed_extensions=target.detected_extensions or [".pdf", ".xlsx", ".xls", ".csv", ".zip"],
            allowed_hosts=[host] if host else [],
            created_at=now,
            updated_at=now,
        )
        source.policy_customized = True
        source.updated_at = now
        self.catalog_repo.save_source(source)

        target.status = "APROBADA"
        target.promoted_source_id = source.source_id
        self._save_candidates([target])
        return source

    def _save_candidates(self, items: List[OpenDiscoveryCandidate]) -> None:
        if hasattr(self.catalog_repo, "save_discovery_candidates"):
            self.catalog_repo.save_discovery_candidates(items)
