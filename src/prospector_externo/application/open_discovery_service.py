"""Servicio de Exploración Automatizada y Preselección en Web Abierta (Objetivo 1).

Combina proveedores enchufables de búsqueda externa (metabuscador web, catálogos
CKAN y exploración BFS acotada), validación criptográfica real TLS X.509,
puntuación auditable de confianza/relevancia (0–100), vista previa de muestras y
gestión persistente de candidatos (PRESELECCIONADA, APROBADA, DESCARTADA).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from prospector_externo.adapters.discovery.search_providers import (
    BoundedBfsLinkProvider,
    CkanOpenDataSearchProvider,
    WebMetaSearchProvider,
)
from prospector_externo.domain.models import Source
from prospector_externo.domain.normalizer import UrlNormalizer
from prospector_externo.infrastructure.tls_inspector import (
    TlsInspectionResult,
    TlsX509Inspector,
)
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.open_search_provider import (
    OpenSearchHit,
    OpenSearchProviderPort,
)

CandidateStatus = Literal["PRESELECCIONADA", "APROBADA", "DESCARTADA"]

SCIENTIFIC_DEFAULT_EXTENSIONS: list[str] = [
    ".xlsx",
    ".xls",
    ".csv",
    ".pdf",
    ".zip",
    ".parquet",
    ".json",
    ".xml",
    ".dat",
    ".txt",
    ".sav",
    ".dta",
    ".rar",
    ".tar.gz",
]

# Directorio semilla opcional (nunca única fuente cuando hay proveedores externos).
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
    (".gov", 30),
    (".org", 24),
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
    discovery_provider: str = "open_web"
    trust_score: int = Field(ge=0, le=100)
    relevance_score: int = Field(ge=0, le=100)
    combined_score: int = Field(ge=0, le=100)
    breakdown: TrustRelevanceBreakdown
    tls_valid: bool = False
    tls_protocol: str | None = None
    tls_issuer: str | None = None
    tls_expires_at: str | None = None
    tls_error_code: str | None = None
    tls_error_detail: str | None = None
    http_status: int | None = None
    detected_extensions: list[str] = Field(default_factory=list)
    detected_files_count: int = 0
    sample_resource_urls: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    status: CandidateStatus = "PRESELECCIONADA"
    promoted_source_id: str | None = None
    discard_reason: str | None = None
    discarded_at: datetime | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OpenDiscoverySearchRequest(BaseModel):
    """Criterios de búsqueda y filtrado general para exploración abierta."""

    query: str = Field(
        default="estadísticas series datos abiertos",
        min_length=2,
        max_length=200,
    )
    sector: str = Field(default="TODOS")
    target_extensions: list[str] = Field(
        default_factory=lambda: [".xlsx", ".xls", ".csv", ".pdf", ".zip", ".parquet", ".json"]
    )
    seed_urls: list[str] = Field(default_factory=list)
    trusted_domains_only: bool = False
    excluded_domains: list[str] = Field(default_factory=list)
    include_discarded: bool = False
    include_curated_baseline: bool = True
    enable_external_providers: bool = True
    enable_bfs_expansion: bool = True
    min_combined_score: int = Field(default=45, ge=0, le=95)
    max_results: int = Field(default=25, ge=1, le=100)


class PromoteCandidateRequest(BaseModel):
    """Datos para promover una fuente preseleccionada al catálogo oficial."""

    source_id: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
    )
    name: str = Field(min_length=2, max_length=160)
    update_category: Literal["DAILY", "WEEKLY", "MONTHLY"] = "MONTHLY"
    workflow: Literal["html", "javascript", "api"] = "html"


class DiscardCandidateRequest(BaseModel):
    """Motivo obligatorio para descartar permanentemente un candidato."""

    reason: str = Field(min_length=3, max_length=400)


class OpenDiscoveryService:
    """Ejecuta exploración abierta, validación TLS X.509 real, descarte y promoción."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        http_timeout: float = 7.0,
        search_providers: list[OpenSearchProviderPort] | None = None,
        tls_inspector: TlsX509Inspector | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.http_timeout = http_timeout
        self.http_transport = http_transport
        self.tls_inspector = tls_inspector or TlsX509Inspector(timeout=min(5.0, http_timeout))
        if search_providers is not None:
            self.search_providers = list(search_providers)
        else:
            self.search_providers = [
                CkanOpenDataSearchProvider(timeout=http_timeout, transport=http_transport),
                WebMetaSearchProvider(timeout=http_timeout, transport=http_transport),
            ]

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

    @staticmethod
    def _candidate_id_for_url(url: str) -> str:
        norm = UrlNormalizer.normalize(url)
        return "cand_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]

    def _collect_targets(
        self,
        criteria: OpenDiscoverySearchRequest,
        *,
        live_probe: bool,
    ) -> list[OpenSearchHit]:
        targets: list[OpenSearchHit] = []
        seen_norm: set[str] = set()

        def register(hit: OpenSearchHit) -> None:
            clean = hit.url.strip()
            if not clean.startswith(("http://", "https://")):
                return
            norm = UrlNormalizer.normalize(clean)
            if norm in seen_norm:
                return
            seen_norm.add(norm)
            targets.append(hit.model_copy(update={"url": clean}))

        # 1. Semillas directas ingresadas por el usuario
        for custom_url in criteria.seed_urls:
            clean = custom_url.strip()
            if clean.startswith(("http://", "https://")):
                register(
                    OpenSearchHit(
                        title=f"Portal Explorado ({urlsplit(clean).netloc})",
                        url=clean,
                        sector=criteria.sector if criteria.sector != "TODOS" else "PERSONALIZADO",
                        provider="seed_input",
                    )
                )

        # 2. Proveedores externos de búsqueda abierta (metabuscador, CKAN, custom DI)
        if criteria.enable_external_providers and (live_probe or self._has_custom_providers()):
            for provider in self.search_providers:
                try:
                    hits = provider.search(
                        criteria.query,
                        sector=criteria.sector,
                        max_results=max(5, criteria.max_results // 2),
                    )
                except Exception:
                    continue
                for hit in hits:
                    register(hit)

        # 3. Expansión BFS acotada (1 salto) desde semillas explícitas
        if criteria.enable_bfs_expansion and criteria.seed_urls and live_probe:
            bfs = BoundedBfsLinkProvider(
                seed_urls=criteria.seed_urls,
                timeout=min(self.http_timeout, 6.0),
                transport=self.http_transport,
            )
            for hit in bfs.search(criteria.query, sector=criteria.sector, max_results=6):
                register(hit)

        # 4. Directorio semilla opcional (nunca único origen si hay hits externos)
        if criteria.include_curated_baseline:
            sector_filter = (criteria.sector or "TODOS").strip().upper()
            for item in CURATED_OPEN_WEB_DIRECTORY:
                if sector_filter not in ("TODOS", "ALL", "*", ""):
                    item_sec = item["sector"].upper()
                    is_fin_alias = sector_filter == "FINANCIERO" and any(
                        tok in item_sec for tok in ("FINANC", "MICROFINANZ", "VALORES", "FISCAL")
                    )
                    if item_sec != sector_filter and sector_filter not in item_sec and not is_fin_alias:
                        continue
                register(
                    OpenSearchHit(
                        title=item["title"],
                        url=item["url"],
                        sector=item["sector"],
                        provider="curated_baseline",
                    )
                )

        return targets[: criteria.max_results]

    def _has_custom_providers(self) -> bool:
        default_types = (CkanOpenDataSearchProvider, WebMetaSearchProvider)
        return any(not isinstance(p, default_types) for p in self.search_providers)

    def search_and_preselect(
        self,
        criteria: OpenDiscoverySearchRequest,
        live_probe: bool = True,
    ) -> list[OpenDiscoveryCandidate]:
        """Explora proveedores externos y semillas aplicando confianza, TLS real y descartes."""
        keywords = [
            tok.lower()
            for tok in re.split(r"[\s,;]+", criteria.query.strip())
            if len(tok.strip()) >= 3
        ]
        target_exts = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (criteria.target_extensions or SCIENTIFIC_DEFAULT_EXTENSIONS)
        ]
        excluded_domains = {d.strip().lower() for d in criteria.excluded_domains if d.strip()}

        persisted_by_id = {c.candidate_id: c for c in self.list_candidates()}
        existing_entrypoints = {
            UrlNormalizer.normalize(s.entrypoint): s.source_id
            for s in self.catalog_repo.list_sources()
        }

        targets = self._collect_targets(criteria, live_probe=live_probe)
        candidates: list[OpenDiscoveryCandidate] = []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DATAX-OpenDiscovery/2.0; +https://datax.com.bo)"
            ),
        }

        with httpx.Client(
            timeout=self.http_timeout,
            follow_redirects=True,
            headers=headers,
            transport=self.http_transport,
        ) as client:
            for hit in targets:
                url = hit.url
                parsed = urlsplit(url)
                host = parsed.netloc.lower()
                if any(host == ex or host.endswith(f".{ex}") for ex in excluded_domains):
                    continue
                if criteria.trusted_domains_only and not self.is_trusted_institutional_host(host):
                    continue

                cand_id = self._candidate_id_for_url(url)
                prior = persisted_by_id.get(cand_id)
                if prior is not None and prior.status == "DESCARTADA" and not criteria.include_discarded:
                    continue

                domain_pts = self.score_domain_trust(host)
                http_status: int | None = None
                page_title = hit.title
                detected_exts: set[str] = set()
                sample_urls: list[str] = list(hit.extra_sample_urls[:8])
                matched_kw: set[str] = set()

                for s_url in sample_urls:
                    s_path = urlsplit(s_url).path.lower()
                    for ext in target_exts:
                        if s_path.endswith(ext):
                            detected_exts.add(ext)

                haystack_meta = f"{hit.title} {url} {hit.snippet}".lower()
                for kw in keywords:
                    if kw in haystack_meta:
                        matched_kw.add(kw)

                # Validación criptográfica real de certificado TLS X.509
                if live_probe:
                    tls_res = self.tls_inspector.inspect(url)
                else:
                    is_https = parsed.scheme.lower() == "https"
                    tls_res = TlsInspectionResult(
                        tls_valid=False,
                        error_code=None if is_https else "NOT_HTTPS",
                        error_detail=(
                            "No verificado en modo sin sonda en vivo."
                            if is_https
                            else "El enlace no utiliza protocolo HTTPS."
                        ),
                    )

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
                if tls_res.tls_valid:
                    protocol_pts += 10
                elif not live_probe and parsed.scheme.lower() == "https":
                    protocol_pts += 5
                if http_status == 200:
                    protocol_pts += 10
                elif http_status and 200 < http_status < 400:
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
                already_src = existing_entrypoints.get(norm_entry)

                status_val: CandidateStatus = "PRESELECCIONADA"
                discard_reason: str | None = None
                discarded_at: datetime | None = None
                if already_src:
                    status_val = "APROBADA"
                elif prior is not None and prior.status == "DESCARTADA":
                    status_val = "DESCARTADA"
                    discard_reason = prior.discard_reason
                    discarded_at = prior.discarded_at

                candidate = OpenDiscoveryCandidate(
                    candidate_id=cand_id,
                    domain=host,
                    entrypoint=url,
                    title=page_title,
                    sector=hit.sector,
                    discovery_provider=hit.provider,
                    trust_score=trust_pct,
                    relevance_score=rel_pct,
                    combined_score=combined,
                    breakdown=TrustRelevanceBreakdown(
                        domain_trust_pts=domain_pts,
                        protocol_health_pts=protocol_pts,
                        format_availability_pts=format_pts,
                        thematic_relevance_pts=relevance_pts,
                    ),
                    tls_valid=tls_res.tls_valid,
                    tls_protocol=tls_res.protocol,
                    tls_issuer=tls_res.issuer,
                    tls_expires_at=tls_res.not_after,
                    tls_error_code=tls_res.error_code,
                    tls_error_detail=tls_res.error_detail,
                    http_status=http_status,
                    detected_extensions=sorted(detected_exts),
                    detected_files_count=len(sample_urls),
                    sample_resource_urls=sample_urls,
                    matched_keywords=sorted(matched_kw),
                    status=status_val,
                    promoted_source_id=already_src,
                    discard_reason=discard_reason,
                    discarded_at=discarded_at,
                )
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.combined_score, reverse=True)
        self._save_candidates(candidates)
        return candidates

    def list_candidates(
        self,
        *,
        include_discarded: bool = True,
        status: CandidateStatus | None = None,
    ) -> list[OpenDiscoveryCandidate]:
        items: list[OpenDiscoveryCandidate] = []
        if hasattr(self.catalog_repo, "list_discovery_candidates"):
            items = list(self.catalog_repo.list_discovery_candidates())
        if status is not None:
            items = [c for c in items if c.status == status]
        elif not include_discarded:
            items = [c for c in items if c.status != "DESCARTADA"]
        return items

    def discard_candidate(
        self,
        candidate_id: str,
        payload: DiscardCandidateRequest,
    ) -> OpenDiscoveryCandidate:
        """Marca un candidato como descartado con motivo persistido."""
        target = next((c for c in self.list_candidates() if c.candidate_id == candidate_id), None)
        if target is None:
            raise KeyError(f"Candidato '{candidate_id}' no encontrado en preselección")
        target.status = "DESCARTADA"
        target.discard_reason = payload.reason.strip()
        target.discarded_at = datetime.now(timezone.utc)
        self._save_candidates([target])
        return target

    def reactivate_candidate(self, candidate_id: str) -> OpenDiscoveryCandidate:
        """Reactiva explícitamente un candidato previamente descartado."""
        target = next((c for c in self.list_candidates() if c.candidate_id == candidate_id), None)
        if target is None:
            raise KeyError(f"Candidato '{candidate_id}' no encontrado en preselección")
        target.status = "PRESELECCIONADA"
        target.discard_reason = None
        target.discarded_at = None
        self._save_candidates([target])
        return target

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
            allowed_extensions=target.detected_extensions or [
                ".pdf", ".xlsx", ".xls", ".csv", ".zip", ".parquet", ".json"
            ],
            allowed_hosts=[host] if host else [],
            created_at=now,
            updated_at=now,
        )
        source.policy_customized = True
        source.updated_at = now
        self.catalog_repo.save_source(source)

        target.status = "APROBADA"
        target.promoted_source_id = source.source_id
        target.discard_reason = None
        target.discarded_at = None
        self._save_candidates([target])
        return source

    def _save_candidates(self, items: list[OpenDiscoveryCandidate]) -> None:
        if hasattr(self.catalog_repo, "save_discovery_candidates"):
            self.catalog_repo.save_discovery_candidates(items)
