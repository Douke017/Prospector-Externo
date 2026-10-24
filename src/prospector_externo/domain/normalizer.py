"""Normalización canónica e identidad estable de URLs."""

import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin, urlsplit, quote, unquote
from typing import Optional


class UrlNormalizer:
    """Normaliza URLs sin destruir parámetros potencialmente semánticos de datasets."""

    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "sessionid", "phpsessid", "jsessionid",
    }

    @staticmethod
    def encode_download_url(url: str) -> Optional[str]:
        """Encode only a trustworthy absolute HTTP path; never repair authority/query.

        Preserve escapes (including encoded separators), query bytes/order and
        fragments. A syntactically safe reference is not proof of availability.
        Reject ambiguous input before urllib can silently strip control bytes.
        """
        if (not url or any(ord(c) < 32 or ord(c) == 127 for c in url)
                or "\\" in url or re.search(r"%(?![0-9a-fA-F]{2})", url)):
            return None
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or not url.lower().startswith(parsed.scheme + "://")
                    or "%" in parsed.netloc or parsed.username is not None
                    or parsed.password is not None):
                return None
            # Accessing port validates malformed and out-of-range ports.
            parsed.port
            if any(c.isspace() for c in parsed.netloc + parsed.query + parsed.fragment):
                return None
            if any(c.isspace() and c != " " for c in parsed.path):
                return None
            # OpenAPI placeholders are documentation, not concrete URLs. Leave
            # them intact in discovery so required-parameter policy can see them.
            if "{" in parsed.path or "}" in parsed.path:
                return None
            decoded = unquote(url)
            if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
                return None
            # Do not turn encoded traversal into a trusted download reference.
            if any(segment in {".", ".."} for segment in unquote(parsed.path).split("/")):
                return None
            path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
            start = url.index("://") + 3 + len(parsed.netloc)
            return url[:start] + path + url[start + len(parsed.path):]
        except (ValueError, UnicodeError):
            return None

    @classmethod
    def normalize(cls, raw_url: str, base_url: Optional[str] = None) -> str:
        if not raw_url:
            return ""

        # Do not let urljoin/urlparse erase dangerous literal control bytes.
        if any(ord(c) < 32 or ord(c) == 127 for c in raw_url) or "\\" in raw_url:
            return raw_url
        url = raw_url.strip()
        if base_url:
            url = urljoin(base_url, url)

        try:
            parsed = urlparse(url)
        except ValueError:
            return url
        if parsed.scheme.lower() not in ("http", "https"):
            return url

        encoded = cls.encode_download_url(url)
        if encoded is None:
            return url
        parsed = urlparse(encoded)

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        if netloc.endswith(":80") and scheme == "http":
            netloc = netloc[:-3]
        elif netloc.endswith(":443") and scheme == "https":
            netloc = netloc[:-4]

        path = parsed.path or "/"

        filtered_query = []
        if parsed.query:
            for k, v in sorted(parse_qsl(parsed.query, keep_blank_values=True)):
                if k.lower() not in cls.TRACKING_PARAMS:
                    filtered_query.append((k, v))

        return urlunparse(
            (scheme, netloc, path, parsed.params, urlencode(filtered_query), "")
        )

    @classmethod
    def compute_url_hash(cls, normalized_url: str) -> str:
        return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()

    @classmethod
    def compute_resource_key(cls, source_id: str, normalized_url: str) -> str:
        """Identidad de catálogo: la misma URL puede pertenecer a dos source_id lógicos."""
        value = f"{source_id}|{normalized_url}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
