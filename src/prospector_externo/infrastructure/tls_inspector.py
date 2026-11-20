"""Inspector criptográfico real de certificados TLS X.509 (Objetivo 1 y 5)."""

from __future__ import annotations

from datetime import datetime, timezone
import socket
import ssl
from urllib.parse import urlsplit

from pydantic import BaseModel


class TlsInspectionResult(BaseModel):
    """Resultado verificable de la inspección real de un certificado TLS X.509."""

    tls_valid: bool
    protocol: str | None = None
    issuer: str | None = None
    subject_cn: str | None = None
    not_after: str | None = None
    days_remaining: int | None = None
    error_code: str | None = None
    error_detail: str | None = None


def _extract_rdn_field(rdn_seq: tuple, key: str) -> str | None:
    for rdn in rdn_seq or ():
        for attr_key, attr_val in rdn:
            if attr_key == key and isinstance(attr_val, str):
                return attr_val
    return None


def _classify_ssl_error(exc: ssl.SSLError) -> str:
    msg = str(exc).lower()
    if "expired" in msg or "has expired" in msg:
        return "CERT_EXPIRED"
    if "self-signed" in msg or "self signed" in msg:
        return "SELF_SIGNED_CERT"
    if "hostname" in msg or "ip address mismatch" in msg or "doesn't match" in msg:
        return "HOSTNAME_MISMATCH"
    if "unable to get local issuer" in msg or "verify failed" in msg:
        return "UNTRUSTED_CERT_CHAIN"
    return "TLS_HANDSHAKE_ERROR"


class TlsX509Inspector:
    """Valida criptográficamente el certificado X.509 y la cadena TLS de un host."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    def inspect(self, url: str) -> TlsInspectionResult:
        """Ejecuta un handshake TLS real verificando cadena, hostname y vigencia."""
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() != "https":
            return TlsInspectionResult(
                tls_valid=False,
                error_code="NOT_HTTPS",
                error_detail="El enlace no utiliza protocolo HTTPS.",
            )

        host = parsed.hostname
        port = parsed.port or 443
        if not host:
            return TlsInspectionResult(
                tls_valid=False,
                error_code="INVALID_HOST",
                error_detail="No se pudo resolver el nombre de host del enlace.",
            )

        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED

        try:
            with socket.create_connection((host, port), timeout=self.timeout) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert() or {}
                    protocol = tls_sock.version()
        except ssl.SSLCertVerificationError as exc:
            return TlsInspectionResult(
                tls_valid=False,
                error_code=_classify_ssl_error(exc),
                error_detail=str(exc),
            )
        except ssl.SSLError as exc:
            return TlsInspectionResult(
                tls_valid=False,
                error_code=_classify_ssl_error(exc),
                error_detail=str(exc),
            )
        except OSError as exc:
            return TlsInspectionResult(
                tls_valid=False,
                error_code="TLS_CONNECTION_FAILED",
                error_detail=str(exc),
            )

        not_after_raw = cert.get("notAfter")
        not_after_iso: str | None = None
        days_remaining: int | None = None
        if isinstance(not_after_raw, str) and not_after_raw:
            expiry_ts = ssl.cert_time_to_seconds(not_after_raw)
            expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
            not_after_iso = expiry_dt.isoformat()
            days_remaining = int((expiry_dt - datetime.now(timezone.utc)).total_seconds() // 86400)
            if days_remaining < 0:
                return TlsInspectionResult(
                    tls_valid=False,
                    protocol=protocol,
                    issuer=_extract_rdn_field(cert.get("issuer", ()), "organizationName"),
                    subject_cn=_extract_rdn_field(cert.get("subject", ()), "commonName"),
                    not_after=not_after_iso,
                    days_remaining=days_remaining,
                    error_code="CERT_EXPIRED",
                    error_detail=f"Certificado X.509 vencido el {not_after_iso}.",
                )

        return TlsInspectionResult(
            tls_valid=True,
            protocol=protocol,
            issuer=_extract_rdn_field(cert.get("issuer", ()), "organizationName")
            or _extract_rdn_field(cert.get("issuer", ()), "commonName"),
            subject_cn=_extract_rdn_field(cert.get("subject", ()), "commonName"),
            not_after=not_after_iso,
            days_remaining=days_remaining,
        )
