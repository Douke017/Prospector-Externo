"""Sistema de Monitoreo Estructural y Alertas Tempranas de Mantenimiento (Objetivo 5).

Calcula firmas estructurales determinísticas sobre árboles DOM (selectores CSS
y rutas tipo XPath de contenedores documentales) y esquemas de APIs JSON,
calcula diffs explícitos (`added_selectors`, `removed_selectors`, `changed_fields`),
detecta caídas anómalas de volumen (`VOLUME_DROP`) incluso con HTTP 200 y genera
Informes Técnicos de Mantenimiento listos para Taiga y canales push.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import ssl
from statistics import median
from typing import Any, List, Literal, Optional, Sequence, Tuple
import uuid

from bs4 import BeautifulSoup
import httpx
from pydantic import BaseModel, Field

from prospector_externo.domain.models import Source


DOCUMENT_EXT_RE = re.compile(
    r"\.(pdf|xlsx|xls|csv|zip|docx|rar|parquet|json|xml|sav|dta|tar\.gz)(?:$|\?|#)",
    re.IGNORECASE,
)
VOLATILE_TOKEN_RE = re.compile(r"\b\d{2,}\b")


AlertSeverity = Literal["CRITICA", "ALTA", "MEDIA"]
AlertStatus = Literal["ABIERTA", "RECONOCIDA", "EN_MANTENIMIENTO", "RESUELTA", "SILENCIADA"]
AlertCode = Literal[
    "DOM_CHANGED",
    "API_CHANGED",
    "VOLUME_DROP",
    "TLS_HOLD",
    "BOT_BLOCKED",
    "HTTP_5XX",
    "BUDGET_LIMIT",
    "ACCESS_TIMEOUT",
]


class StructuralDiff(BaseModel):
    """Diff estructural explícito entre línea base y observación vigente."""

    added_selectors: List[str] = Field(default_factory=list)
    removed_selectors: List[str] = Field(default_factory=list)
    changed_fields: List[str] = Field(default_factory=list)
    evidence_snippet: Optional[str] = None


class AlertDeliveryLog(BaseModel):
    """Registro del resultado de despacho de una alerta hacia un canal push."""

    channel_id: str
    channel_type: str
    channel_name: str
    delivered: bool
    http_status: Optional[int] = None
    detail: Optional[str] = None
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StructuralAlert(BaseModel):
    """Alerta temprana generada ante mutación estructural, caída de volumen o bloqueo."""

    alert_id: str = Field(default_factory=lambda: f"alrt_{uuid.uuid4().hex[:10]}")
    source_id: str
    source_name: str
    entrypoint: str
    alert_code: str
    severity: AlertSeverity
    status: AlertStatus = "ABIERTA"
    previous_signature: Optional[str] = None
    current_signature: Optional[str] = None
    affected_selectors: List[str] = Field(default_factory=list)
    structural_diff: Optional[StructuralDiff] = None
    diagnostic_summary: str
    contingency_active: Optional[str] = None
    occurrences: int = Field(default=1, ge=1)
    dedup_key: Optional[str] = None
    assignee: Optional[str] = None
    ticket_url: Optional[str] = None
    muted_until: Optional[datetime] = None
    resolution_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
    delivery_logs: List[AlertDeliveryLog] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved: bool = False

    def compute_dedup_key(self) -> str:
        sig = (self.current_signature or "NO_SIG").strip()
        return f"{self.source_id.lower()}|{self.alert_code.upper()}|{sig}"


class MaintenanceTechnicalReport(BaseModel):
    """Informe técnico estructurado listo para gestión de mantenimiento en Taiga."""

    report_id: str
    generated_at: str
    source_id: str
    source_name: str
    entrypoint: str
    workflow: str
    health_status: str
    alert_code: str
    severity: AlertSeverity
    diagnostic_summary: str
    previous_signature: Optional[str] = None
    current_signature: Optional[str] = None
    affected_selectors: List[str] = Field(default_factory=list)
    structural_diff: Optional[StructuralDiff] = None
    contingency_urls: List[str] = Field(default_factory=list)
    active_contingency_url: Optional[str] = None
    remediation_steps: List[str] = Field(default_factory=list)
    taiga_markdown: str


def _normalize_class_token(cls_name: str) -> str:
    cleaned = VOLATILE_TOKEN_RE.sub("N", cls_name.strip())
    return cleaned[:40]


def compute_dom_skeleton_hash(html: str) -> Tuple[str, List[str]]:
    """Extrae la huella estructural del árbol DOM ignorando textos dinámicos."""
    soup = BeautifulSoup(html or "", "html.parser")
    structural_paths: list[str] = []

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        is_doc = bool(DOCUMENT_EXT_RE.search(href)) or any(
            kw in href.lower()
            for kw in ("download", "descarga", "boletin", "estadistic", "archivo", "dataset")
        )
        if not is_doc:
            continue
        chain: list[str] = []
        node: Any = anchor
        depth = 0
        while node is not None and getattr(node, "name", None) and depth < 5:
            tag = str(node.name).lower()
            if tag in ("[document]", "html", "body"):
                break
            classes = node.get("class") or []
            norm_classes = sorted(
                _normalize_class_token(c) for c in classes if isinstance(c, str) and c.strip()
            )[:2]
            cls_suffix = f".{'.'.join(norm_classes)}" if norm_classes else ""
            chain.append(f"{tag}{cls_suffix}")
            node = node.parent
            depth += 1
        if chain:
            xpath_like = "/" + "/".join(reversed(chain))
            structural_paths.append(f"DOC:{xpath_like}")

    for container in soup.find_all(["table", "form", "nav", "main", "section", "iframe"], limit=80):
        tag = str(container.name).lower()
        elem_id = str(container.get("id") or "").strip()
        norm_id = f"#{VOLATILE_TOKEN_RE.sub('N', elem_id)[:32]}" if elem_id else ""
        classes = container.get("class") or []
        norm_classes = sorted(
            _normalize_class_token(c) for c in classes if isinstance(c, str) and c.strip()
        )[:2]
        cls_suffix = f".{'.'.join(norm_classes)}" if norm_classes else ""
        structural_paths.append(f"BOX:{tag}{norm_id}{cls_suffix}")

    unique_sorted = sorted(set(structural_paths))
    canonical_payload = "\n".join(unique_sorted) if unique_sorted else "EMPTY_DOM_SKELETON"
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    return digest, unique_sorted[:100]


def _extract_json_schema_paths(value: Any, prefix: str = "$", depth: int = 0) -> List[str]:
    if depth > 6:
        return [f"{prefix}:depth_limit"]
    if value is None:
        return [f"{prefix}:null"]
    if isinstance(value, bool):
        return [f"{prefix}:bool"]
    if isinstance(value, (int, float)):
        return [f"{prefix}:number"]
    if isinstance(value, str):
        return [f"{prefix}:string"]
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]:empty"]
        return _extract_json_schema_paths(value[0], f"{prefix}[]", depth + 1)
    if isinstance(value, dict):
        paths: list[str] = []
        for key in sorted(value.keys()):
            paths.extend(_extract_json_schema_paths(value[key], f"{prefix}.{key}", depth + 1))
        return paths
    return [f"{prefix}:unknown"]


def compute_api_schema_hash(payload_text: str) -> Tuple[str, List[str]]:
    """Calcula una huella determinística del contrato JSON de una API."""
    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError:
        return hashlib.sha256(b"INVALID_JSON_PAYLOAD").hexdigest(), ["$:invalid_json"]

    paths = sorted(set(_extract_json_schema_paths(parsed)))
    joined = "\n".join(paths) if paths else "$:empty_object"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest(), paths[:100]


def compute_structural_diff(
    previous_paths: Sequence[str],
    current_paths: Sequence[str],
    *,
    is_json_schema: bool = False,
    evidence_snippet: Optional[str] = None,
) -> StructuralDiff:
    """Calcula el diff explícito entre la línea base previa y la observación actual."""
    prev_set = set(previous_paths)
    curr_set = set(current_paths)

    if is_json_schema:
        prev_map: dict[str, set[str]] = {}
        curr_map: dict[str, set[str]] = {}
        for item in prev_set:
            field_path, _, ftype = item.rpartition(":")
            prev_map.setdefault(field_path or item, set()).add(ftype)
        for item in curr_set:
            field_path, _, ftype = item.rpartition(":")
            curr_map.setdefault(field_path or item, set()).add(ftype)

        changed_fields: list[str] = []
        added: list[str] = []
        removed: list[str] = []

        for fpath in sorted(set(prev_map) | set(curr_map)):
            old_t = prev_map.get(fpath)
            new_t = curr_map.get(fpath)
            if old_t is None and new_t is not None:
                added.append(f"{fpath}:{','.join(sorted(new_t))}")
            elif new_t is None and old_t is not None:
                removed.append(f"{fpath}:{','.join(sorted(old_t))}")
            elif old_t != new_t:
                changed_fields.append(
                    f"{fpath} ({','.join(sorted(old_t))} -> {','.join(sorted(new_t))})"
                )

        return StructuralDiff(
            added_selectors=added[:50],
            removed_selectors=removed[:50],
            changed_fields=changed_fields[:50],
            evidence_snippet=evidence_snippet[:400] if evidence_snippet else None,
        )

    added = sorted(curr_set - prev_set)
    removed = sorted(prev_set - curr_set)
    return StructuralDiff(
        added_selectors=added[:50],
        removed_selectors=removed[:50],
        changed_fields=[],
        evidence_snippet=evidence_snippet[:400] if evidence_snippet else None,
    )


def evaluate_volume_drop(
    source: Source,
    current_resources_count: int,
    historical_counts: Sequence[int],
    *,
    drop_ratio_threshold: float = 0.5,
    min_baseline_count: int = 3,
) -> Optional[StructuralAlert]:
    """Detecta caídas abruptas de volumen (VOLUME_DROP) aun cuando la corrida retorna HTTP 200."""
    positive_history = [int(c) for c in historical_counts if int(c) > 0]
    if not positive_history:
        return None
    baseline_med = float(median(positive_history))
    if baseline_med < min_baseline_count:
        return None

    drop_ratio = (baseline_med - current_resources_count) / baseline_med
    if drop_ratio < drop_ratio_threshold:
        return None

    severity: AlertSeverity = "CRITICA" if current_resources_count == 0 else "ALTA"
    pct_drop = round(drop_ratio * 100.0, 1)
    diag = (
        f"Caída anómala de volumen detectada en {source.name} con HTTP 200: "
        f"se hallaron {current_resources_count} recursos frente a una mediana histórica "
        f"de {baseline_med:.1f} (-{pct_drop}%)."
    )
    diff = StructuralDiff(
        changed_fields=[
            f"resources_found: {baseline_med:.1f} (mediana histórica) -> {current_resources_count} (-{pct_drop}%)"
        ],
        evidence_snippet=(
            f"Historial reciente evaluado: {list(positive_history[:10])}; "
            f"umbral configurado: >= {int(drop_ratio_threshold * 100)}% de caída."
        ),
    )
    alert = StructuralAlert(
        source_id=source.source_id,
        source_name=source.name,
        entrypoint=source.entrypoint,
        alert_code="VOLUME_DROP",
        severity=severity,
        previous_signature=f"median:{baseline_med:.1f}",
        current_signature=f"count:{current_resources_count}",
        affected_selectors=list(source.baseline_selectors[:10]),
        structural_diff=diff,
        diagnostic_summary=diag,
        contingency_active=source.active_contingency_url,
    )
    alert.dedup_key = alert.compute_dedup_key()
    return alert


def map_audit_severity(alert_code: str) -> AlertSeverity:
    code = (alert_code or "").upper()
    if code in {"DOM_CHANGED", "API_CHANGED", "BOT_BLOCKED", "HTTP_5XX"}:
        return "CRITICA"
    if code in {"VOLUME_DROP", "TLS_HOLD", "ACCESS_TIMEOUT"}:
        return "ALTA"
    return "MEDIA"


def build_remediation_steps(alert_code: str, source: Source) -> List[str]:
    code = (alert_code or "DOM_CHANGED").upper()
    if code == "DOM_CHANGED":
        return [
            f"Inspeccionar el marcado HTML vigente en {source.entrypoint} y contrastar los contenedores eliminados en el diff estructural.",
            "Actualizar las URLs semilla (`seeds`) o el flujo de extracción (`html` / `javascript`) en Parámetros de Rastreo.",
            "Ejecutar una prueba `Dry-Run` para confirmar detección de archivos antes de guardar la política.",
            "Disparar una corrida dirigida de validación y confirmar recuperación del conteo base de recursos.",
        ]
    if code == "API_CHANGED":
        return [
            f"Revisar la respuesta actual del endpoint público de {source.name} frente a los campos modificados en el diff JSON.",
            "Ajustar las rutas de paginación o parámetros de consulta en la configuración de semillas de API.",
            "Validar con `Dry-Run` que los enlaces o registros devueltos conserven integridad antes de promover a custodia.",
        ]
    if code == "VOLUME_DROP":
        return [
            f"Verificar manualmente si {source.entrypoint} cambió la paginación, filtró series por defecto o exige interacción JS.",
            "Comparar la corrida actual frente a la mediana histórica y ejecutar inspección estructural en vivo.",
            "Activar un espejo vivo de contingencia si el portal principal está sirviendo una página reducida.",
        ]
    if code == "TLS_HOLD":
        return [
            f"Verificar cadena de certificados X.509 del dominio institucional ({source.entrypoint}).",
            "Conmutar temporalmente a una URL espejo viva en Rutas de Contingencia.",
            "Mantener aislamiento preventivo hasta la renovación del certificado por la entidad emisora.",
        ]
    if code == "BOT_BLOCKED":
        return [
            "Incrementar el intervalo entre peticiones (`rate_limit_seconds >= 3.0`) para reducir presión sobre el firewall perimetral.",
            "Activar URL alternativa de datos abiertos o espejo institucional verificado.",
            "Evaluar cambio controlado a flujo de navegador (`javascript`) si el portal exige desafío cliente.",
        ]
    if code == "BUDGET_LIMIT":
        return [
            "Acotar `allowed_hosts` y añadir palabras clave de exclusión (`excluded_path_keywords`) para evitar calendarios o paginación infinita.",
            "Aumentar gradualmente `max_urls` solo sobre las secciones estrictamente documentales.",
        ]
    return [
        f"Comprobar disponibilidad operativa del servidor remoto en {source.entrypoint}.",
        "Verificar activación automática de las URLs de contingencia registradas.",
        "Reintentar extracción dirigida una vez estabilizado el portal institucional.",
    ]


def build_maintenance_report(
    source: Source,
    alert: Optional[StructuralAlert] = None,
) -> MaintenanceTechnicalReport:
    """Construye el Informe Técnico de Mantenimiento en JSON y Markdown para Taiga."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    code = (alert.alert_code if alert else (source.audit_code or "SIN_INCIDENCIA")).upper()
    severity = alert.severity if alert else map_audit_severity(code)
    summary = (
        alert.diagnostic_summary
        if alert
        else (
            source.audit_note
            or "La estructura del portal coincide con la línea base verificada."
        )
    )
    prev_sig = alert.previous_signature if alert else (source.dom_structural_hash or source.api_schema_hash)
    curr_sig = alert.current_signature if alert else (source.dom_structural_hash or source.api_schema_hash)
    selectors = alert.affected_selectors if alert else source.baseline_selectors
    sdiff = alert.structural_diff if alert else None
    steps = build_remediation_steps(code, source)

    contingency_block = (
        "\n".join(f"  - `{u}`" for u in source.contingency_urls)
        if source.contingency_urls
        else "  - *Sin rutas de contingencia registradas (se recomienda ejecutar descubrimiento automático)*"
    )
    selectors_block = (
        "\n".join(f"  - `{s}`" for s in selectors[:10])
        if selectors
        else "  - *Sin selectores documentales observados en la última captura*"
    )
    diff_lines: list[str] = []
    if sdiff:
        if sdiff.removed_selectors:
            diff_lines.append(
                "- **Selectores / Rutas Eliminadas:** "
                + ", ".join(f"`{x}`" for x in sdiff.removed_selectors[:8])
            )
        if sdiff.added_selectors:
            diff_lines.append(
                "- **Selectores / Rutas Agregadas:** "
                + ", ".join(f"`{x}`" for x in sdiff.added_selectors[:8])
            )
        if sdiff.changed_fields:
            diff_lines.append(
                "- **Campos / Métricas Modificadas:** "
                + ", ".join(f"`{x}`" for x in sdiff.changed_fields[:8])
            )
    diff_block = "\n".join(diff_lines) if diff_lines else "- *Sin variaciones estructurales detalladas*"
    steps_block = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))

    taiga_md = (
        f"## [MANTENIMIENTO PROSPECTOR] [{code}] {source.name} (`{source.source_id}`)\n\n"
        f"- **Fecha de Diagnóstico:** {now_iso}\n"
        f"- **Severidad Operativa:** {severity}\n"
        f"- **Estado en Catálogo:** `{source.health_status}`\n"
        f"- **URL Principal:** {source.entrypoint}\n"
        f"- **Estrategia de Extracción:** `{source.workflow}`\n"
        f"- **Contingencia Activa:** `{source.active_contingency_url or 'No requerida / En espera'}`\n\n"
        f"### 1. Diagnóstico Técnico\n"
        f"{summary}\n\n"
        f"### 2. Evidencia de Huella y Diff Estructural\n"
        f"- **Firma Base Anterior:** `{prev_sig or 'N/D'}`\n"
        f"- **Firma Observada Actual:** `{curr_sig or 'N/D'}`\n"
        f"{diff_block}\n"
        f"- **Rutas DOM / Esquema Observadas:**\n{selectors_block}\n\n"
        f"### 3. Rutas de Contingencia Configuradas\n"
        f"{contingency_block}\n\n"
        f"### 4. Plan de Remediación Técnica (Criterios de Cierre)\n"
        f"{steps_block}\n"
    )

    return MaintenanceTechnicalReport(
        report_id=f"rep_maint_{source.source_id}_{uuid.uuid4().hex[:6]}",
        generated_at=now_iso,
        source_id=source.source_id,
        source_name=source.name,
        entrypoint=source.entrypoint,
        workflow=source.workflow,
        health_status=source.health_status,
        alert_code=code,
        severity=severity,
        diagnostic_summary=summary,
        previous_signature=prev_sig,
        current_signature=curr_sig,
        affected_selectors=selectors,
        structural_diff=sdiff,
        contingency_urls=source.contingency_urls,
        active_contingency_url=source.active_contingency_url,
        remediation_steps=steps,
        taiga_markdown=taiga_md,
    )


def evaluate_structure_payload(
    source: Source,
    body_text: str,
    *,
    content_type: str = "text/html",
    target_url: Optional[str] = None,
) -> Tuple[Source, Optional[StructuralAlert], MaintenanceTechnicalReport]:
    """Evalúa un cuerpo HTML o JSON contra la línea base de una fuente con diff explícito."""
    url_ref = target_url or source.entrypoint
    ctype = (content_type or "").lower()
    alert: Optional[StructuralAlert] = None
    snippet = re.sub(r"\s+", " ", (body_text or "")[:600]).strip()

    if "json" in ctype or source.workflow == "api":
        new_hash, schema_paths = compute_api_schema_hash(body_text)
        old_hash = source.api_schema_hash
        prev_paths = source.baseline_json_paths or source.baseline_selectors
        if old_hash and old_hash != new_hash:
            diff = compute_structural_diff(
                prev_paths,
                schema_paths,
                is_json_schema=True,
                evidence_snippet=snippet,
            )
            affected = (
                diff.removed_selectors + diff.changed_fields + diff.added_selectors
            )[:15] or schema_paths[:15]
            diag = (
                f"Cambio detectado en el contrato JSON de la API ({url_ref}). "
                f"Eliminados: {len(diff.removed_selectors)}, "
                f"Modificados: {len(diff.changed_fields)}, "
                f"Agregados: {len(diff.added_selectors)}."
            )
            source.health_status = "WARNING"
            source.audit_code = "API_CHANGED"
            source.audit_note = diag
            alert = StructuralAlert(
                source_id=source.source_id,
                source_name=source.name,
                entrypoint=source.entrypoint,
                alert_code="API_CHANGED",
                severity="CRITICA",
                previous_signature=old_hash,
                current_signature=new_hash,
                affected_selectors=affected,
                structural_diff=diff,
                diagnostic_summary=diag,
                contingency_active=source.active_contingency_url,
            )
            alert.dedup_key = alert.compute_dedup_key()
        else:
            source.api_schema_hash = new_hash
            source.baseline_json_paths = schema_paths
            source.baseline_selectors = schema_paths
            if source.audit_code in {"API_CHANGED", "HTTP_5XX", "ACCESS_TIMEOUT"}:
                source.audit_code = None
                source.audit_note = "Esquema JSON verificado sin alteraciones estructurales."
                source.health_status = "ACTIVE"
    else:
        new_hash, dom_paths = compute_dom_skeleton_hash(body_text)
        old_hash = source.dom_structural_hash
        if old_hash and old_hash != new_hash:
            diff = compute_structural_diff(
                source.baseline_selectors,
                dom_paths,
                is_json_schema=False,
                evidence_snippet=snippet,
            )
            affected = (diff.removed_selectors + diff.added_selectors)[:15] or dom_paths[:15]
            diag = (
                f"Alteración estructural del árbol DOM detectada en {url_ref}. "
                f"Selectores eliminados: {len(diff.removed_selectors)}, "
                f"agregados: {len(diff.added_selectors)}."
            )
            source.health_status = "WARNING"
            source.audit_code = "DOM_CHANGED"
            source.audit_note = diag
            alert = StructuralAlert(
                source_id=source.source_id,
                source_name=source.name,
                entrypoint=source.entrypoint,
                alert_code="DOM_CHANGED",
                severity="CRITICA",
                previous_signature=old_hash,
                current_signature=new_hash,
                affected_selectors=affected,
                structural_diff=diff,
                diagnostic_summary=diag,
                contingency_active=source.active_contingency_url,
            )
            alert.dedup_key = alert.compute_dedup_key()
        else:
            source.dom_structural_hash = new_hash
            source.baseline_selectors = dom_paths
            if source.audit_code in {"DOM_CHANGED", "HTTP_5XX", "ACCESS_TIMEOUT"}:
                source.audit_code = None
                source.audit_note = (
                    f"Estructura DOM verificada ({len(dom_paths)} contenedores documentales estables)."
                )
                source.health_status = "ACTIVE"

    source.updated_at = datetime.now(timezone.utc)
    return source, alert, build_maintenance_report(source, alert)


def verify_source_structure_live(
    source: Source,
    timeout_seconds: float = 10.0,
    transport: Optional[httpx.BaseTransport] = None,
) -> Tuple[Source, Optional[StructuralAlert], MaintenanceTechnicalReport]:
    """Inspecciona en vivo el portal o endpoint de una fuente y evalúa deriva estructural."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; DATAX-Prospector-Monitor/2.0; +https://datax.com.bo)"
        ),
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
    }
    target_url = source.seeds[0] if (source.workflow == "api" and source.seeds) else source.entrypoint

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers=headers,
            transport=transport,
        ) as client:
            resp = client.get(target_url)
    except httpx.ConnectError as exc:
        err_text = str(exc)
        if (
            "SSL" in err_text.upper()
            or "CERTIFICATE" in err_text.upper()
            or isinstance(exc.__cause__, ssl.SSLError)
        ):
            code = "TLS_HOLD"
            diag = f"Fallo de validación TLS/SSL al conectar con {target_url}: {err_text[:180]}"
        else:
            code = "HTTP_5XX"
            diag = f"Imposible establecer conexión TCP/HTTP con {target_url}: {err_text[:180]}"
        source.health_status = "WARNING"
        source.audit_code = code
        source.audit_note = diag
        alert = StructuralAlert(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            alert_code=code,
            severity=map_audit_severity(code),
            previous_signature=source.dom_structural_hash or source.api_schema_hash,
            current_signature=None,
            affected_selectors=source.baseline_selectors[:15],
            diagnostic_summary=diag,
            contingency_active=source.active_contingency_url,
        )
        alert.dedup_key = alert.compute_dedup_key()
        return source, alert, build_maintenance_report(source, alert)
    except httpx.TimeoutException:
        code = "ACCESS_TIMEOUT"
        diag = f"Tiempo de espera agotado ({timeout_seconds}s) al verificar estructura en {target_url}."
        source.health_status = "WARNING"
        source.audit_code = code
        source.audit_note = diag
        alert = StructuralAlert(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            alert_code=code,
            severity=map_audit_severity(code),
            previous_signature=source.dom_structural_hash or source.api_schema_hash,
            diagnostic_summary=diag,
            contingency_active=source.active_contingency_url,
        )
        alert.dedup_key = alert.compute_dedup_key()
        return source, alert, build_maintenance_report(source, alert)
    except httpx.HTTPError as exc:
        code = "HTTP_5XX"
        diag = f"Error de transporte HTTP al inspeccionar {target_url}: {str(exc)[:180]}"
        source.health_status = "WARNING"
        source.audit_code = code
        source.audit_note = diag
        alert = StructuralAlert(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            alert_code=code,
            severity=map_audit_severity(code),
            diagnostic_summary=diag,
            contingency_active=source.active_contingency_url,
        )
        alert.dedup_key = alert.compute_dedup_key()
        return source, alert, build_maintenance_report(source, alert)

    if resp.status_code in (401, 403, 429):
        code = "BOT_BLOCKED"
        diag = (
            f"Acceso restringido por control perimetral o WAF (HTTP {resp.status_code}) "
            f"en {target_url}."
        )
        source.health_status = "WARNING"
        source.audit_code = code
        source.audit_note = diag
        alert = StructuralAlert(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            alert_code=code,
            severity="CRITICA",
            previous_signature=source.dom_structural_hash or source.api_schema_hash,
            affected_selectors=source.baseline_selectors[:15],
            diagnostic_summary=diag,
            contingency_active=source.active_contingency_url,
        )
        alert.dedup_key = alert.compute_dedup_key()
        return source, alert, build_maintenance_report(source, alert)

    if resp.status_code >= 500:
        code = "HTTP_5XX"
        diag = f"Indisponibilidad del servidor institucional (HTTP {resp.status_code}) en {target_url}."
        source.health_status = "WARNING"
        source.audit_code = code
        source.audit_note = diag
        alert = StructuralAlert(
            source_id=source.source_id,
            source_name=source.name,
            entrypoint=source.entrypoint,
            alert_code=code,
            severity="CRITICA",
            diagnostic_summary=diag,
            contingency_active=source.active_contingency_url,
        )
        alert.dedup_key = alert.compute_dedup_key()
        return source, alert, build_maintenance_report(source, alert)

    ctype = (resp.headers.get("content-type") or "").lower()
    body_text = resp.text[:500_000]
    return evaluate_structure_payload(
        source,
        body_text,
        content_type=ctype,
        target_url=target_url,
    )
