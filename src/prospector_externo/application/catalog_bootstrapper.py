"""Inicializador idempotente del catálogo de 52 fuentes oficiales de DATAX.

Cuando la fachada API arranca en entorno operativo (`PROSPECTOR_SEED_ON_BOOT=1`),
garantiza que las 52 fuentes declaradas en `config/sources.yaml` estén presentes
en el almacén activo enriquecidas con la evidencia de validación de sitio
(`FINAL_MATRIX_52.json`), sin sobrescribir fuentes ya personalizadas por el analista.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from prospector_externo.domain.models import Source, SourceConfig
from prospector_externo.ports.catalog_repository import CatalogRepositoryPort


def _classify_audit_row(row: Dict[str, Any]) -> Tuple[str, str | None, str | None]:
    classification = str(row.get("audit_classification") or "").upper()
    prior_delivery = str(row.get("prior_delivery_status") or "").upper()
    note = str(row.get("note") or "").strip()

    if "TLS" in classification:
        return (
            "WARNING",
            "TLS_HOLD",
            f"Fallo verificado de negociación de certificado TLS/SSL en el servidor institucional ({note}).",
        )
    if "5XX" in classification:
        return (
            "WARNING",
            "HTTP_5XX",
            f"Respuesta de falla interna del servidor remoto confirmada en auditoría ({note}).",
        )
    if "ACCESS_REVIEW" in classification:
        return (
            "WARNING",
            "BOT_BLOCKED",
            f"Restricción perimetral o control de acceso detectado sobre el portal público ({note}).",
        )
    if prior_delivery == "STATUS_ONLY":
        return (
            "SUSPENDED",
            "DOM_CHANGED",
            f"Portal sin exposición directa de descargas tabulares; requiere flujo especializado ({note}).",
        )
    if row.get("coverage_ratio") is not None and float(row.get("coverage_ratio") or 1.0) < 0.35:
        return (
            "WARNING",
            "BUDGET_LIMIT",
            f"Cobertura parcial ({float(row['coverage_ratio']) * 100:.1f}%) por presupuesto de exploración acotado.",
        )
    return ("ACTIVE", None, None)


def bootstrap_official_catalog_if_needed(
    catalog_repo: CatalogRepositoryPort,
    project_root: Path,
) -> int:
    """Sincroniza de forma idempotente las 52 fuentes oficiales en el almacén activo."""
    yaml_path = project_root / "config" / "sources.yaml"
    if not yaml_path.is_file():
        return 0

    raw_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    configs = [SourceConfig.model_validate(item) for item in raw_yaml.get("sources", [])]

    matrix_by_id: Dict[str, Dict[str, Any]] = {}
    matrix_path = project_root / "docs" / "site_validation_evidence" / "FINAL_MATRIX_52.json"
    if matrix_path.is_file():
        try:
            matrix_payload = json.loads(matrix_path.read_text(encoding="utf-8"))
            for row in matrix_payload.get("rows", []):
                sid = str(row.get("source_id") or "").strip().lower()
                if sid:
                    matrix_by_id[sid] = row
        except (OSError, ValueError):
            matrix_by_id = {}

    existing = {s.source_id.lower(): s for s in catalog_repo.list_sources()}
    now = datetime.now(timezone.utc)
    seeded = 0

    for cfg in configs:
        key = cfg.source_id.lower()
        current = existing.get(key)
        if current is not None and getattr(current, "policy_customized", False):
            continue

        audit_row = matrix_by_id.get(key, {})
        health, audit_code, audit_note = _classify_audit_row(audit_row)
        existing_res_count = len(catalog_repo.list_resources(source_id=cfg.source_id))
        baseline_count = max(int(audit_row.get("baseline_raw_records") or 0), existing_res_count)

        if current is None:
            source = Source(
                source_id=cfg.source_id,
                name=cfg.name or audit_row.get("name") or cfg.source_id.upper(),
                entrypoint=cfg.entrypoint,
                workflow=cfg.workflow,
                health_status=health,
                update_category=cfg.update_category,
                audit_code=audit_code,
                audit_note=audit_note,
                baseline_resource_count=baseline_count,
                seeds=list(cfg.seeds),
                allowed_extensions=list(cfg.allowed_extensions),
                rate_limit_seconds=cfg.rate_limit_seconds,
                max_urls=cfg.max_urls,
                max_resources=cfg.max_resources,
                ignore_robots_txt=cfg.ignore_robots_txt,
                contingency_url=cfg.contingency_url,
                contingency_urls=list(cfg.contingency_urls),
                excluded_path_keywords=list(cfg.excluded_path_keywords),
                allowed_hosts=list(cfg.allowed_hosts),
                created_at=now,
                updated_at=now,
            )
            catalog_repo.save_source(source)
            seeded += 1
        elif (not current.seeds and cfg.seeds) or (current.baseline_resource_count == 0 and baseline_count > 0):
            if not current.seeds and cfg.seeds:
                current.seeds = list(cfg.seeds)
                current.allowed_extensions = list(cfg.allowed_extensions)
                current.allowed_hosts = list(cfg.allowed_hosts)
                current.excluded_path_keywords = list(cfg.excluded_path_keywords)
            if current.baseline_resource_count == 0 and baseline_count > 0:
                current.baseline_resource_count = baseline_count
            if current.audit_code is None and audit_code is not None:
                current.health_status = health
                current.audit_code = audit_code
                current.audit_note = audit_note
            catalog_repo.save_source(current)
            seeded += 1

    return seeded
