"""Servicio de Descubrimiento, Evaluación y Cobertura de Contingencias (Objetivo 3).

Localiza, verifica por HTTP + TLS X.509 real y clasifica URLs de respaldo en:
- `ESPEJO_VIVO`: espejos institucionales, redirecciones permanentes 301/308,
  sitemaps y catálogos CKAN aptos para conmutación automática vigente.
- `HISTORICO_SOLO_LECTURA`: capturas congeladas (p. ej. Wayback Machine CDX)
  que NUNCA reemplazan automáticamente a una fuente activa (DAILY/WEEKLY/MONTHLY).
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import List, Literal, Optional, Sequence
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from prospector_externo.domain.models import FreshnessClass, Source
from prospector_externo.infrastructure.tls_inspector import TlsX509Inspector
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


BackupOriginType = Literal[
    "CONFIGURADA",
    "REDIRECCION_PERMANENTE",
    "SITEMAP_ALTERNO",
    "ESPEJO_INSTITUCIONAL",
    "RUTA_ESTADISTICA_ALTERNA",
    "DATOS_ABIERTOS",
    "WAYBACK_CDX",
]

ACTIVE_UPDATE_CATEGORIES = {"DAILY", "WEEKLY", "MONTHLY"}
HISTORICAL_ARCHIVE_HOSTS = (
    "web.archive.org",
    "archive.is",
    "archive.today",
    "archive.ph",
    "perma.cc",
)
WAYBACK_TS_RE = re.compile(r"/web/(\d{14})/")
DOC_EXT_PATTERN = re.compile(
    r"\.(pdf|xlsx|xls|csv|zip|docx|parquet|json|xml|sav|dta|rar|tar\.gz)(?:$|\?|#)",
    re.IGNORECASE,
)


def is_historical_archive_url(url: str) -> bool:
    """Determina si una URL corresponde a un archivo web histórico congelado."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in HISTORICAL_ARCHIVE_HOSTS)


def extract_wayback_captured_at(url: str) -> Optional[str]:
    """Extrae la fecha ISO de captura desde una URL de Wayback Machine."""
    match = WAYBACK_TS_RE.search(url)
    if not match:
        return None
    raw = match.group(1)
    try:
        dt = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def classify_backup_freshness(
    url: str,
    origin_type: Optional[BackupOriginType] = None,
) -> tuple[FreshnessClass, Optional[str]]:
    """Distingue estrictamente espejos vivos de capturas históricas de solo lectura."""
    if origin_type == "WAYBACK_CDX" or is_historical_archive_url(url):
        return "HISTORICO_SOLO_LECTURA", extract_wayback_captured_at(url)
    return "ESPEJO_VIVO", None


class ContingencyCandidate(BaseModel):
    """Resultado técnico de evaluación de una URL alternativa de respaldo."""

    url: str
    origin_type: BackupOriginType
    freshness_class: FreshnessClass = "ESPEJO_VIVO"
    snapshot_captured_at: Optional[str] = None
    eligible_for_live_failover: bool = True
    reachable: bool
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    tls_valid: bool = False
    tls_error_code: Optional[str] = None
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
    historical_only_urls: List[str] = Field(default_factory=list)


class SourceResilienceEntry(BaseModel):
    """Estado de resiliencia de una fuente individual dentro de la matriz de cobertura."""

    source_id: str
    name: str
    entrypoint: str
    update_category: str
    health_status: str
    live_backup_count: int
    historical_backup_count: int
    active_contingency_url: Optional[str] = None
    risk_level: Literal["PROTEGIDA", "SOLO_HISTORICO", "PUNTO_UNICO_DE_FALLO"]
    contingency_urls: List[str] = Field(default_factory=list)


class ResilienceCoverageReport(BaseModel):
    """Resumen ejecutivo de cobertura de resiliencia del catálogo."""

    total_sources: int
    protected_sources: int
    historical_only_sources: int
    zero_backup_sources: int
    coverage_pct: float
    single_points_of_failure: List[SourceResilienceEntry] = Field(default_factory=list)
    entries: List[SourceResilienceEntry] = Field(default_factory=list)


class ContingencyDiscoveryService:
    """Localiza, evalúa en vivo y audita fuentes de respaldo confiables."""

    def __init__(
        self,
        catalog_repo: CatalogRepositoryPort,
        http_timeout: float = 7.0,
        *,
        tls_inspector: Optional[TlsX509Inspector] = None,
        http_transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.catalog_repo = catalog_repo
        self.http_timeout = http_timeout
        self.tls_inspector = tls_inspector or TlsX509Inspector(timeout=min(http_timeout, 5.0))
        self.http_transport = http_transport

    def probe_single_url(
        self,
        url: str,
        *,
        origin_type: BackupOriginType = "CONFIGURADA",
        update_category: str = "DAILY",
    ) -> ContingencyCandidate:
        """Verifica en vivo una URL de respaldo individual (HTTP, latencia, TLS, archivos)."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DATAX-Resilience-Probe/2.0; +https://datax.com.bo)"
            ),
        }
        with httpx.Client(
            timeout=self.http_timeout,
            follow_redirects=True,
            headers=headers,
            transport=self.http_transport,
        ) as client:
            return self._probe_candidate(
                client,
                url.strip(),
                origin_type,
                update_category=update_category,
            )

    def discover_and_evaluate(
        self,
        source: Source,
        auto_register: bool = True,
    ) -> ContingencyEvaluationResponse:
        """Genera candidatos espejo/históricos, los prueba y registra solo espejos vivos aptos."""
        raw_candidates = self._collect_candidate_urls(source)
        evaluated: list[ContingencyCandidate] = []

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; DATAX-Resilience-Probe/2.0; +https://datax.com.bo)"
            ),
        }

        with httpx.Client(
            timeout=self.http_timeout,
            follow_redirects=True,
            headers=headers,
            transport=self.http_transport,
        ) as client:
            # 0. Inspeccionar redirecciones permanentes 301/308 del entrypoint
            redirect_url = self._probe_permanent_redirect(source.entrypoint)
            if redirect_url and all(redirect_url != u for u, _ in raw_candidates):
                raw_candidates.insert(0, (redirect_url, "REDIRECCION_PERMANENTE"))

            # 1. Consultar datos.gob.bo CKAN por nombre/sigla institucional (espejos vivos)
            ckan_urls = self._query_datos_gob_bo(client, source)
            for ck_url in ckan_urls:
                if all(ck_url != existing_u for existing_u, _ in raw_candidates):
                    raw_candidates.append((ck_url, "DATOS_ABIERTOS"))

            # 2. Consultar Wayback CDX (siempre marcado como HISTORICO_SOLO_LECTURA)
            wayback_urls = self._query_wayback_cdx(client, source.entrypoint)
            for wb_url in wayback_urls:
                if all(wb_url != existing_u for existing_u, _ in raw_candidates):
                    raw_candidates.append((wb_url, "WAYBACK_CDX"))

            # 3. Evaluar cada URL candidata en vivo
            for cand_url, origin_type in raw_candidates[:12]:
                evaluated.append(
                    self._probe_candidate(
                        client,
                        cand_url,
                        origin_type,
                        update_category=source.update_category,
                    )
                )

        evaluated.sort(
            key=lambda item: (
                item.eligible_for_live_failover,
                item.reachable,
                item.confidence_score,
                -(item.latency_ms or 99999.0),
            ),
            reverse=True,
        )

        auto_added = 0
        current_backups = list(source.contingency_urls or [])
        if source.contingency_url and source.contingency_url not in current_backups:
            current_backups.insert(0, source.contingency_url)

        historical_urls = [
            item.url for item in evaluated if item.freshness_class == "HISTORICO_SOLO_LECTURA"
        ]

        if auto_register:
            for item in evaluated:
                # Regla estricta: capturas históricas NUNCA reemplazan fuentes vigentes activas
                if not item.eligible_for_live_failover:
                    continue
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
            historical_only_urls=historical_urls,
        )

    @staticmethod
    def compute_resilience_coverage(sources: Sequence[Source]) -> ResilienceCoverageReport:
        """Audita la cobertura de resiliencia del catálogo e identifica puntos únicos de fallo."""
        entries: list[SourceResilienceEntry] = []
        protected = 0
        historical_only = 0
        zero_backup = 0

        for src in sorted(sources, key=lambda s: s.source_id):
            backups = list(dict.fromkeys(
                [u for u in (src.contingency_urls or []) if u.strip()]
                + ([src.contingency_url] if src.contingency_url else [])
            ))
            live_count = sum(1 for u in backups if not is_historical_archive_url(u))
            hist_count = sum(1 for u in backups if is_historical_archive_url(u))

            if live_count > 0:
                risk: Literal["PROTEGIDA", "SOLO_HISTORICO", "PUNTO_UNICO_DE_FALLO"] = "PROTEGIDA"
                protected += 1
            elif hist_count > 0:
                risk = "SOLO_HISTORICO"
                historical_only += 1
            else:
                risk = "PUNTO_UNICO_DE_FALLO"
                zero_backup += 1

            entries.append(
                SourceResilienceEntry(
                    source_id=src.source_id,
                    name=src.name,
                    entrypoint=src.entrypoint,
                    update_category=src.update_category,
                    health_status=src.health_status,
                    live_backup_count=live_count,
                    historical_backup_count=hist_count,
                    active_contingency_url=src.active_contingency_url,
                    risk_level=risk,
                    contingency_urls=backups,
                )
            )

        total = len(entries)
        coverage_pct = round((protected / total) * 100.0, 2) if total > 0 else 0.0
        spofs = [e for e in entries if e.risk_level != "PROTEGIDA"]
        return ResilienceCoverageReport(
            total_sources=total,
            protected_sources=protected,
            historical_only_sources=historical_only,
            zero_backup_sources=zero_backup,
            coverage_pct=coverage_pct,
            single_points_of_failure=spofs,
            entries=entries,
        )

    def _probe_permanent_redirect(self, entrypoint: str) -> Optional[str]:
        try:
            with httpx.Client(
                timeout=min(self.http_timeout, 5.0),
                follow_redirects=False,
                transport=self.http_transport,
            ) as client:
                resp = client.head(entrypoint)
                if resp.status_code in (301, 308):
                    location = resp.headers.get("Location")
                    if location:
                        resolved = urljoin(entrypoint, location.strip())
                        if resolved.rstrip("/") != entrypoint.rstrip("/"):
                            return resolved
        except httpx.HTTPError:
            return None
        return None

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
            add(
                urlunsplit((scheme, alt_host, parsed.path or "/", parsed.query, "")),
                "ESPEJO_INSTITUCIONAL",
            )
            root_base = urlunsplit((scheme, host, "/", "", ""))
            for common_path in ("/estadisticas", "/publicaciones", "/boletines"):
                if parsed.path.rstrip("/").lower() != common_path:
                    add(urljoin(root_base, common_path), "RUTA_ESTADISTICA_ALTERNA")

        return pairs

    @staticmethod
    def _query_wayback_cdx(client: httpx.Client, target_url: str) -> List[str]:
        """Consulta Wayback Machine CDX API para capturas históricas de referencia."""
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

    def _probe_candidate(
        self,
        client: httpx.Client,
        url: str,
        origin_type: BackupOriginType,
        *,
        update_category: str = "DAILY",
    ) -> ContingencyCandidate:
        freshness, captured_at = classify_backup_freshness(url, origin_type)
        is_active_source = (update_category or "DAILY").upper() in ACTIVE_UPDATE_CATEGORIES
        eligible_live = not (freshness == "HISTORICO_SOLO_LECTURA" and is_active_source)

        tls_res = self.tls_inspector.inspect(url)
        t0 = time.perf_counter()
        try:
            resp = client.get(url)
            latency = round((time.perf_counter() - t0) * 1000.0, 1)
        except httpx.HTTPError as exc:
            return ContingencyCandidate(
                url=url,
                origin_type=origin_type,
                freshness_class=freshness,
                snapshot_captured_at=captured_at,
                eligible_for_live_failover=False,
                reachable=False,
                http_status=None,
                latency_ms=None,
                tls_valid=tls_res.tls_valid,
                tls_error_code=tls_res.error_code,
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
                freshness_class=freshness,
                snapshot_captured_at=captured_at,
                eligible_for_live_failover=False,
                reachable=False,
                http_status=resp.status_code,
                latency_ms=latency,
                tls_valid=tls_res.tls_valid,
                tls_error_code=tls_res.error_code,
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
            or raw_bytes.startswith(b"PAR1")
            or ctype in {"text/csv", "application/json", "application/pdf", "application/octet-stream"}
        )

        doc_links = 0
        if not is_direct_binary and ("html" in ctype or b"<html" in resp.content[:512].lower()):
            soup = BeautifulSoup(resp.text[:250_000], "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = str(a_tag.get("href", ""))
                if DOC_EXT_PATTERN.search(href):
                    doc_links += 1

        valid_struct = is_direct_binary or doc_links > 0 or ("json" in ctype)
        base_score = 52
        if tls_res.tls_valid:
            base_score += 13
        elif url.startswith("https://"):
            base_score += 4
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
        if freshness == "HISTORICO_SOLO_LECTURA":
            rec = (
                "Captura histórica de solo lectura (Wayback/Archivo). "
                "Útil para recuperación forense; bloqueada para reemplazo automático de fuentes vigentes."
            )
        elif score >= 75:
            rec = "Espejo vivo óptimo (alta disponibilidad, TLS y archivos objetivo verificados)."
        elif score >= 65:
            rec = "Espejo vivo apto como contingencia secundaria."
        else:
            rec = "Accesible por HTTP 200, pero con baja densidad de archivos descargables directos."

        return ContingencyCandidate(
            url=url,
            origin_type=origin_type,
            freshness_class=freshness,
            snapshot_captured_at=captured_at,
            eligible_for_live_failover=eligible_live and valid_struct,
            reachable=True,
            http_status=resp.status_code,
            latency_ms=latency,
            tls_valid=tls_res.tls_valid,
            tls_error_code=tls_res.error_code,
            content_type=ctype or None,
            valid_structure=valid_struct,
            detected_resources_count=doc_links,
            confidence_score=score,
            recommendation=rec,
        )
