"""Adaptadores enchufables para exploración automatizada en web abierta (Objetivo 1)."""

from __future__ import annotations

import os
import re
from urllib.parse import quote_plus, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx

from prospector_externo.ports.open_search_provider import (
    OpenSearchHit,
    OpenSearchProviderPort,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; DATAX-OpenDiscovery/2.0; +https://datax.com.bo)"
)


class CkanOpenDataSearchProvider(OpenSearchProviderPort):
    """Consulta catálogos públicos CKAN (por defecto datos.gob.bo) vía API JSON."""

    provider_name = "ckan_open_data"

    def __init__(
        self,
        api_url: str | None = None,
        timeout: float = 7.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = (
            api_url
            or os.environ.get(
                "PROSPECTOR_CKAN_API_URL",
                "https://datos.gob.bo/api/3/action/package_search",
            )
        ).strip()
        self.timeout = timeout
        self.transport = transport

    def search(
        self,
        query: str,
        *,
        sector: str = "TODOS",
        max_results: int = 10,
    ) -> list[OpenSearchHit]:
        if not self.api_url:
            return []
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                transport=self.transport,
            ) as client:
                resp = client.get(
                    self.api_url,
                    params={"q": query.strip(), "rows": max(1, min(max_results, 20))},
                )
                if resp.status_code != 200:
                    return []
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        results = (payload.get("result") or {}).get("results") or []
        parsed_base = urlsplit(self.api_url)
        portal_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        hits: list[OpenSearchHit] = []
        seen_urls: set[str] = set()

        for pkg in results[:max_results]:
            if not isinstance(pkg, dict):
                continue
            pkg_name = str(pkg.get("name") or "").strip()
            pkg_title = str(pkg.get("title") or pkg_name or "Dataset CKAN").strip()
            pkg_url = str(pkg.get("url") or "").strip()
            if not pkg_url.startswith(("http://", "https://")) and pkg_name:
                pkg_url = f"{portal_origin}/dataset/{pkg_name}"
            if not pkg_url.startswith(("http://", "https://")) or pkg_url in seen_urls:
                continue

            sample_urls: list[str] = []
            for res in pkg.get("resources") or []:
                if isinstance(res, dict):
                    res_url = str(res.get("url") or "").strip()
                    if res_url.startswith(("http://", "https://")) and res_url not in sample_urls:
                        sample_urls.append(res_url)
                        if len(sample_urls) >= 6:
                            break

            seen_urls.add(pkg_url)
            hits.append(
                OpenSearchHit(
                    title=pkg_title[:160],
                    url=pkg_url,
                    sector=sector if sector != "TODOS" else "DATOS_ABIERTOS",
                    provider=self.provider_name,
                    snippet=str(pkg.get("notes") or "")[:240],
                    extra_sample_urls=sample_urls,
                )
            )
        return hits


class WebMetaSearchProvider(OpenSearchProviderPort):
    """Consulta un metabuscador web configurable (SearXNG JSON o DuckDuckGo HTML)."""

    provider_name = "web_metasearch"

    def __init__(
        self,
        searxng_url: str | None = None,
        timeout: float = 7.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.searxng_url = (
            searxng_url
            if searxng_url is not None
            else os.environ.get("PROSPECTOR_SEARXNG_URL", "")
        ).strip()
        self.timeout = timeout
        self.transport = transport

    def search(
        self,
        query: str,
        *,
        sector: str = "TODOS",
        max_results: int = 10,
    ) -> list[OpenSearchHit]:
        if self.searxng_url:
            return self._search_searxng(query, sector=sector, max_results=max_results)
        return self._search_duckduckgo_html(query, sector=sector, max_results=max_results)

    def _search_searxng(
        self,
        query: str,
        *,
        sector: str,
        max_results: int,
    ) -> list[OpenSearchHit]:
        endpoint = self.searxng_url.rstrip("/")
        if not endpoint.endswith("/search"):
            endpoint = f"{endpoint}/search"
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                transport=self.transport,
            ) as client:
                resp = client.get(
                    endpoint,
                    params={"q": query.strip(), "format": "json", "language": "es"},
                )
                if resp.status_code != 200:
                    return []
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            return []

        hits: list[OpenSearchHit] = []
        seen: set[str] = set()
        for item in (payload.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            hits.append(
                OpenSearchHit(
                    title=str(item.get("title") or urlsplit(url).netloc)[:160],
                    url=url,
                    sector=sector if sector != "TODOS" else "WEB_ABIERTA",
                    provider="searxng",
                    snippet=str(item.get("content") or "")[:240],
                )
            )
        return hits

    def _search_duckduckgo_html(
        self,
        query: str,
        *,
        sector: str,
        max_results: int,
    ) -> list[OpenSearchHit]:
        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query.strip())}"
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                transport=self.transport,
            ) as client:
                resp = client.get(search_url)
                if resp.status_code != 200:
                    return []
                html = resp.text[:300_000]
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(html, "html.parser")
        hits: list[OpenSearchHit] = []
        seen: set[str] = set()
        for anchor in soup.select("a.result__a"):
            raw_href = str(anchor.get("href") or "").strip()
            url = self._unwrap_ddg_url(raw_href)
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            hits.append(
                OpenSearchHit(
                    title=anchor.get_text(" ", strip=True)[:160] or urlsplit(url).netloc,
                    url=url,
                    sector=sector if sector != "TODOS" else "WEB_ABIERTA",
                    provider="duckduckgo_html",
                )
            )
            if len(hits) >= max_results:
                break
        return hits

    @staticmethod
    def _unwrap_ddg_url(href: str) -> str:
        match = re.search(r"[?&]uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
        return href


class BoundedBfsLinkProvider(OpenSearchProviderPort):
    """Explora enlaces salientes (1 salto acotado) desde semillas institucionales."""

    provider_name = "bounded_bfs"

    def __init__(
        self,
        seed_urls: list[str] | None = None,
        timeout: float = 6.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.seed_urls = list(seed_urls or [])
        self.timeout = timeout
        self.transport = transport

    def search(
        self,
        query: str,
        *,
        sector: str = "TODOS",
        max_results: int = 8,
    ) -> list[OpenSearchHit]:
        if not self.seed_urls:
            return []
        keywords = [
            tok.lower()
            for tok in re.split(r"[\s,;]+", query.strip())
            if len(tok.strip()) >= 3
        ]
        hits: list[OpenSearchHit] = []
        seen: set[str] = {u.strip() for u in self.seed_urls}
        data_hints = (
            "datos", "estadistic", "boletin", "series", "indicador",
            "informe", "descarga", "catalogo", "repositorio", "dataset",
        )

        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            transport=self.transport,
        ) as client:
            for seed in self.seed_urls[:5]:
                try:
                    resp = client.get(seed)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.text[:250_000], "html.parser")
                except httpx.HTTPError:
                    continue

                for anchor in soup.find_all("a", href=True):
                    raw_href = str(anchor.get("href") or "").strip()
                    if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:")):
                        continue
                    abs_url = urljoin(str(resp.url), raw_href)
                    parsed = urlsplit(abs_url)
                    if parsed.scheme not in {"http", "https"} or abs_url in seen:
                        continue
                    text = anchor.get_text(" ", strip=True)
                    haystack = f"{text} {abs_url}".lower()
                    if not any(tok in haystack for tok in (*keywords, *data_hints)):
                        continue
                    seen.add(abs_url)
                    hits.append(
                        OpenSearchHit(
                            title=(text or parsed.netloc)[:160],
                            url=abs_url,
                            sector=sector if sector != "TODOS" else "BFS_DESCUBIERTO",
                            provider=self.provider_name,
                        )
                    )
                    if len(hits) >= max_results:
                        return hits
        return hits
