"""Frontera de discovery acotada, determinista y consciente de source_id."""

from __future__ import annotations

import time
import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Deque, Dict, Iterable, Optional, Set, Tuple
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlparse

from prospector_externo.domain.discovery_intelligence import SpiderTrapDetector
from prospector_externo.domain.models import SourceConfig
from prospector_externo.domain.normalizer import UrlNormalizer


class StopReason(str, Enum):
    QUEUE_EXHAUSTED = "QUEUE_EXHAUSTED"
    MAX_URLS = "MAX_URLS"
    MAX_RUNTIME = "MAX_RUNTIME"
    REQUEST_BUDGET = "REQUEST_BUDGET_REACHED"
    MAX_CONSECUTIVE_ERRORS = "MAX_CONSECUTIVE_ERRORS"


@dataclass(frozen=True)
class DiscoveryItem:
    normalized_url: str
    raw_url: str
    depth: int
    parent_url: Optional[str] = None


class DiscoveryFrontier:
    """Cola BFS con canonicalización, scope, dedupe y límites por fuente."""

    # GETs con estos parámetros pueden ejecutar acciones (carrito, cupón, etc.).
    # No deben entrar a la cola, aunque el servidor las exponga como enlaces.
    MUTATING_QUERY_KEYS = frozenset({
        "add-to-cart", "add_to_cart", "remove_item", "remove-from-cart",
        "apply_coupon", "wc-ajax", "add-to-wishlist",
    })

    NON_NAVIGABLE_IMAGE_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".avif", ".bmp", ".ico", ".svg",
    }

    def __init__(self, config: SourceConfig, monotonic=time.monotonic) -> None:
        self.config = config
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._queue: Deque[DiscoveryItem] = deque()
        self._seen: Set[str] = set()
        self._resource_seen: Set[str] = set()
        self._visited: Set[str] = set()
        self._items: Dict[str, DiscoveryItem] = {}
        self._active: Optional[DiscoveryItem] = None
        self._deferred: Deque[DiscoveryItem] = deque()
        self._rejected = 0
        self._rejection_reasons: Counter[str] = Counter()
        self._query_variants: Dict[Tuple[str, str, Tuple[str, ...]], Set[str]] = defaultdict(set)
        self._spider_detector = SpiderTrapDetector(
            max_calendar_variants=config.max_calendar_variants,
            max_url_length=config.max_url_length,
            max_query_keys=config.max_query_keys,
        )
        self.stop_reason: Optional[StopReason] = None

        hosts = set()
        for candidate in [config.entrypoint, *config.seeds]:
            host = (urlparse(candidate).hostname or "").lower().rstrip(".")
            if host:
                hosts.add(host)
        for host in config.allowed_hosts:
            clean = host.strip().lower().rstrip(".")
            if clean:
                hosts.add(clean)
        self._allowed_hosts = hosts

    @property
    def rejected_count(self) -> int:
        return self._rejected

    @property
    def pending_count(self) -> int:
        return len(self._queue) + len(self._deferred) + int(self._active is not None)

    @staticmethod
    def profile_fingerprint(config: SourceConfig) -> str:
        """Identifica alcance y estrategia; el presupuesto temporal puede renovarse."""
        profile = config.model_dump(mode="json", exclude={
            "name", "update_category", "grouping_contract", "max_requests", "max_runtime_seconds",
        })
        return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def dump_state(self) -> dict:
        """El elemento en vuelo vuelve a pendientes: checkpoint con semántica at-least-once."""
        pending = ([self._active] if self._active else []) + list(self._queue) + list(self._deferred)
        return {
            "schema_version": "discovery-frontier-1.0",
            "source_id": self.config.source_id,
            "profile_fingerprint": self.profile_fingerprint(self.config),
            "items": [asdict(item) for item in self._items.values()],
            "pending": [item.normalized_url for item in pending],
            "visited": sorted(self._visited),
            "resource_seen": sorted(self._resource_seen),
            "rejections": dict(self._rejection_reasons),
        }

    @classmethod
    def restore(cls, config: SourceConfig, state: dict) -> "DiscoveryFrontier":
        if (not isinstance(state, dict) or state.get("schema_version") != "discovery-frontier-1.0"
                or state.get("source_id") != config.source_id
                or state.get("profile_fingerprint") != cls.profile_fingerprint(config)):
            raise ValueError("DISCOVERY_FRONTIER_INCOMPATIBLE")
        frontier = cls(config)
        items, pending = state.get("items"), state.get("pending")
        visited, resource_seen = state.get("visited"), state.get("resource_seen")
        if not all(isinstance(v, list) for v in (items, pending, visited, resource_seen)):
            raise ValueError("DISCOVERY_FRONTIER_INVALID")
        if (any(not isinstance(v, str) for v in [*pending, *visited, *resource_seen])
                or any(not isinstance(row, dict) or set(row) != {
                    "normalized_url", "raw_url", "depth", "parent_url"} for row in items)):
            raise ValueError("DISCOVERY_FRONTIER_INVALID_ITEM")
        for row in items:
            item = DiscoveryItem(**row)
            if (type(item.depth) is not int or item.depth < 0
                    or not isinstance(item.normalized_url, str) or not isinstance(item.raw_url, str)
                    or (item.parent_url is not None and not isinstance(item.parent_url, str))
                    or UrlNormalizer.normalize(item.normalized_url) != item.normalized_url
                    or not frontier.enqueue(item.normalized_url, depth=item.depth,
                                            parent_url=item.parent_url)):
                raise ValueError("DISCOVERY_FRONTIER_UNSAFE_ITEM")
            frontier._items[f"{config.source_id}|{item.normalized_url}"] = item
        keys = [f"{config.source_id}|{url}" for url in pending]
        if (len(set(keys)) != len(keys) or len(set(visited)) != len(visited)
                or set(keys) & set(visited) or set(keys) | set(visited) != frontier._seen):
            raise ValueError("DISCOVERY_FRONTIER_INCONSISTENT_QUEUE")
        frontier._queue = deque(frontier._items[key] for key in keys)
        frontier._visited = set(visited)
        if (len(set(resource_seen)) != len(resource_seen)
                or len(resource_seen) > config.max_resources
                or any(not isinstance(key, str) or not key.startswith(f"{config.source_id}|")
                       for key in resource_seen)):
            raise ValueError("DISCOVERY_FRONTIER_INVALID_RESOURCES")
        frontier._resource_seen = set(resource_seen)
        rejections = state.get("rejections", {})
        if not isinstance(rejections, dict) or any(type(v) is not int or v < 0 for v in rejections.values()):
            raise ValueError("DISCOVERY_FRONTIER_INVALID_COUNTERS")
        frontier._rejection_reasons = Counter(rejections)
        frontier._rejected = sum(rejections.values())
        # Un límite de admisión alcanzado no desaparece al reanudar el proceso.
        frontier.stop_reason = StopReason.MAX_URLS if rejections.get("MAX_URLS") else None
        frontier._started_at = frontier._monotonic()
        return frontier

    @property
    def discovered_count(self) -> int:
        return len(self._seen | self._resource_seen)

    @property
    def visited_count(self) -> int:
        return len(self._visited)

    @property
    def spider_traps_blocked(self) -> int:
        return sum(
            count
            for reason, count in self._rejection_reasons.items()
            if reason.startswith("SPIDER_TRAP:")
        )

    @property
    def query_variants_blocked(self) -> int:
        return self._rejection_reasons["QUERY_VARIANT_LIMIT"]

    def rejection_count(self, reason: str) -> int:
        return self._rejection_reasons[reason]

    def _runtime_exceeded(self) -> bool:
        return (self._monotonic() - self._started_at) >= self.config.max_runtime_seconds

    def reject(self, reason: str) -> bool:
        self._rejected += 1
        self._rejection_reasons[reason] += 1
        return False

    def _query_family(self, parsed) -> Tuple[str, str, Tuple[str, ...]]:
        query = parse_qsl(parsed.query, keep_blank_values=True)
        keys = tuple(sorted({key.lower() for key, _ in query}))
        return (
            parsed.hostname.lower().rstrip("."),
            parsed.path or "/",
            keys,
        )

    def enqueue(
        self,
        raw_url: str,
        *,
        base_url: Optional[str] = None,
        depth: int = 0,
        parent_url: Optional[str] = None,
    ) -> bool:
        if depth > self.config.max_depth:
            return self.reject("MAX_DEPTH")

        if self._runtime_exceeded():
            self.stop_reason = StopReason.MAX_RUNTIME
            return self.reject("MAX_RUNTIME")

        normalized = UrlNormalizer.normalize(raw_url, base_url=base_url)
        parsed = urlparse(normalized)

        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return self.reject("INVALID_OR_UNSUPPORTED_URL")

        host = parsed.hostname.lower().rstrip(".")
        if self._allowed_hosts and host not in self._allowed_hosts:
            return self.reject("OUT_OF_SCOPE_HOST")

        # Bloqueo preventivo de rutas con posibles efectos secundarios.
        # No se usan GET para agregar compras, quitar ítems ni modificar sesiones.
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if query_keys & self.MUTATING_QUERY_KEYS:
            return self.reject("MUTATING_NAVIGATION_QUERY")

        # Restricciones de navegación declaradas por fuente: antes no se aplicaban.
        # Se revisa el PATH, nunca la URL de un ResourceCandidate: los documentos
        # encontrados siguen catalogándose por register_resource() sin este filtro.
        decoded_path = unquote(parsed.path).casefold()
        for raw_keyword in self.config.excluded_path_keywords:
            keyword = unquote(raw_keyword).strip().casefold()
            if not keyword:
                continue
            if decoded_path == keyword.rstrip("/") or keyword in decoded_path:
                return self.reject("EXCLUDED_NAVIGATION_PATH")

        # Las imágenes enlazadas por HTML o sitemap no son páginas de
        # exploración. No deben consumir requests ni disparar errores seguidos.
        if PurePosixPath(unquote(parsed.path)).suffix.lower() in self.NON_NAVIGABLE_IMAGE_EXTENSIONS:
            return self.reject("NON_NAVIGABLE_IMAGE")

        trap_reason = self._spider_detector.inspect(normalized)
        if trap_reason:
            return self.reject(f"SPIDER_TRAP:{trap_reason}")

        identity = f"{self.config.source_id}|{normalized}"
        if identity in self._seen:
            return False

        if len(self._seen) >= self.config.max_urls:
            self.stop_reason = StopReason.MAX_URLS
            return self.reject("MAX_URLS")

        query_value = parsed.query or ""
        family = self._query_family(parsed)
        variants = self._query_variants[family]
        if query_value not in variants and len(variants) >= self.config.max_query_variants:
            return self.reject("QUERY_VARIANT_LIMIT")
        variants.add(query_value)

        self._seen.add(identity)
        item = DiscoveryItem(
                normalized_url=normalized,
                raw_url=raw_url,
                depth=depth,
                parent_url=parent_url,
        )
        self._items[identity] = item
        self._queue.append(item)
        return True

    def register_resource(self, normalized_url: str) -> bool:
        """Registra un recurso sin consumir el presupuesto de navegación.

        `max_urls` acota la frontera de páginas. Los recursos descubiertos
        tienen un límite independiente (`max_resources`) para evitar que un
        sitemap lleno de páginas deje cero espacio para PDF/XLS/CSV/API.
        """
        identity = f"{self.config.source_id}|{normalized_url}"
        if identity in self._resource_seen:
            return False
        if len(self._resource_seen) >= self.config.max_resources:
            return self.reject("MAX_RESOURCES")
        self._resource_seen.add(identity)
        return True

    def seed(self, urls: Iterable[str]) -> None:
        for url in urls:
            self.enqueue(url, depth=0, parent_url=None)

    def pop(self) -> Optional[DiscoveryItem]:
        if self._runtime_exceeded():
            self.stop_reason = StopReason.MAX_RUNTIME
            return None
        if not self._queue:
            if self.stop_reason is None:
                self.stop_reason = StopReason.QUEUE_EXHAUSTED
            return None
        self._active = self._queue.popleft()
        return self._active

    def mark_visited(self, item: DiscoveryItem) -> None:
        self._visited.add(f"{self.config.source_id}|{item.normalized_url}")
        self._active = None

    def defer(self, item: DiscoveryItem) -> None:
        """Reintenta fallos en la próxima invocación, nunca en un bucle inmediato."""
        self._visited.discard(f"{self.config.source_id}|{item.normalized_url}")
        self._active = None
        self._deferred.append(item)
