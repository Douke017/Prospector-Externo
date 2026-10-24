"""Publica evidencia JSON de una corrida en MongoDB sin volver a visitar fuentes.

Lee exclusivamente snapshots/checkpoints aislados por fuente. Rechaza
proyecciones obsoletas y no inventa recursos a partir de snapshots RAW.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prospector_externo.domain.models import ResourceCandidate, Snapshot, Source
from prospector_externo.domain.observations import Run, RunReport, SourceRunObservation, ExecutionStatus, RunStatus

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")


@dataclass(frozen=True)
class Evidence:
    logical_source_id: str
    source: Source
    run: Run
    report: RunReport
    observation: SourceRunObservation
    snapshot: Snapshot | None
    resources: tuple[ResourceCandidate, ...] | None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _only_source_id(path: Path) -> tuple[str, Source]:
    data = _read_json(path)
    if not isinstance(data, dict) or len(data) != 1:
        raise ValueError(f"El archivo debe contener una sola fuente: {path}")
    key, value = next(iter(data.items()))
    source = Source.model_validate(value)
    if key != source.source_id:
        raise ValueError(f"Identidad de fuente incoherente: {path}")
    return key, source


def read_evidence(directory: Path, logical_source_id: str) -> Evidence | None:
    """Prevalida toda la evidencia ANTES de acceder a MongoDB."""
    reports_dir = directory / "reports"
    if not reports_dir.is_dir():
        return None  # fuente aún sin ejecutar: reanudable
    report_files = list(reports_dir.glob("run_*.json"))
    if not report_files:
        return None
    physical_id, source = _only_source_id(directory / "state" / "sources.json")
    # Un resume deja varios intentos en la misma carpeta. Elegir por la fecha
    # del reporte, nunca por nombre/mtime ni descartando un intento fallido.
    reports = [RunReport.model_validate(_read_json(path)) for path in report_files]
    newest = max(item.started_at for item in reports)
    candidates = [item for item in reports if item.started_at == newest]
    if source.last_run_id is not None:
        candidates = [item for item in candidates if item.run_id == source.last_run_id]
    if len(candidates) != 1:
        raise ValueError(f"Fuente y último reporte no corresponden o son ambiguos en {directory}")
    report = candidates[0]
    if len(report.source_results) != 1:
        raise ValueError(f"Reporte sin una sola observación: {directory}")
    if not RUN_ID_PATTERN.fullmatch(report.run_id):
        raise ValueError(f"run_id inseguro: {report.run_id!r}")

    observation = report.source_results[0]
    if observation.source_id != physical_id or observation.run_id != report.run_id:
        raise ValueError(f"Observación y fuente no corresponden en {directory}")

    run_path = directory / "state" / "runs" / f"run_{report.run_id}.json"
    run = Run.model_validate(_read_json(run_path))
    if run.run_id != report.run_id:
        raise ValueError(f"Corrida incompatible: {run_path}")
    if (run.status not in {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED}
            or run.selected_source_ids != [physical_id]):
        raise ValueError(f"Corrida no finalizada o asociada a otra fuente: {run_path}")
    obs_path = directory / "state" / "observations" / f"{physical_id}_{report.run_id}.json"
    saved_observation = SourceRunObservation.model_validate(_read_json(obs_path))
    if saved_observation != observation:
        raise ValueError(f"Observación distinta del reporte: {directory}")

    snapshot_file = directory / "state" / "snapshots" / f"{physical_id}_{report.run_id}.json"
    snapshot = Snapshot.model_validate(_read_json(snapshot_file)) if snapshot_file.exists() else None
    resources: tuple[ResourceCandidate, ...] | None = None
    if observation.execution_status in (ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS):
        if snapshot is None or snapshot.source_id != physical_id or snapshot.run_id != report.run_id:
            raise ValueError(f"Snapshot ausente o incorrecto: {snapshot_file}")
        catalog_path = directory / "state" / "resource_catalog" / f"{physical_id}.json"
        if not catalog_path.is_file():
            raise ValueError(
                f"Falta catálogo reconciliado aislado: {catalog_path}; "
                "no se debe sustituir por el snapshot RAW ni tomar un catálogo global potencialmente obsoleto"
            )
        catalog = _read_json(catalog_path)
        if not isinstance(catalog, dict) or catalog.get("source_id") != physical_id or catalog.get("run_id") != report.run_id:
            raise ValueError(f"Catálogo no pertenece a esta corrida: {catalog_path}")
        if not isinstance(catalog.get("resources"), list):
            raise ValueError(f"Catálogo sin lista de recursos: {catalog_path}")
        resources = tuple(ResourceCandidate.model_validate(row) for row in catalog["resources"])
        if any(row.source_id != physical_id for row in resources):
            raise ValueError(f"Recursos asociados a otra fuente: {catalog_path}")
        if len({row.resource_key for row in resources}) != len(resources):
            raise ValueError(f"Recursos duplicados: {catalog_path}")
    elif snapshot is not None and (snapshot.source_id != physical_id or snapshot.run_id != report.run_id):
        raise ValueError(f"Snapshot incompatible: {snapshot_file}")

    return Evidence(logical_source_id, source, run, report, saved_observation, snapshot, resources)


def collect_evidence(output_root: Path, run_id: str, *, source_id: str | None = None) -> list[Evidence]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("--pipeline-run-id solo admite letras, números, guion y guion bajo")
    base = output_root / run_id
    if not base.is_dir():
        raise FileNotFoundError(f"No existe evidencia B13: {base}")
    if source_id is not None:
        if not RUN_ID_PATTERN.fullmatch(source_id):
            raise ValueError("--source invalido")
        paths = [base / source_id]
    else:
        paths = sorted(p for p in base.iterdir() if p.is_dir())
    rows: list[Evidence] = []
    for directory in paths:
        if not directory.is_dir():
            if source_id is not None:
                raise FileNotFoundError(f"No existe evidencia de fuente: {directory}")
            continue
        evidence = read_evidence(directory, directory.name)
        if evidence is not None:
            rows.append(evidence)
    if not rows:
        raise ValueError("No hay evidencias ejecutadas para sincronizar")
    # Un mismo source físico puede representar varios códigos lógicos.
    # Aplicar checkpoints del más antiguo al más nuevo evita retrocesos.
    return sorted(rows, key=lambda row: (row.run.started_at, row.logical_source_id))


def sync_evidence(repo: Any, rows: list[Evidence]) -> dict[str, Any]:
    """Importación idempotente de hechos y proyección vigente por source_id.

    Varias identidades lógicas pueden usar un mismo source físico. Cada corrida
    mantiene su historia propia, pero SOLO la última actualiza el catálogo vivo.
    Así la importación y su reintento nunca retroceden el catálogo compartido.
    """
    latest: dict[str, Evidence] = {}
    for row in rows:
        key = row.source.source_id
        current = latest.get(key)
        if current is None or (row.source.last_run_at or row.run.started_at) >= (
            current.source.last_run_at or current.run.started_at
        ):
            latest[key] = row

    # Rechazar un histórico antiguo ANTES de escribir un solo registro.
    for physical_id, item in latest.items():
        existing = repo.get_source(physical_id)
        if existing and existing.last_run_at and item.source.last_run_at:
            if existing.last_run_at > item.source.last_run_at:
                raise ValueError(
                    f"STALE_EVIDENCE {physical_id}: Mongo contiene una corrida posterior; "
                    "no se sobrescribe su catálogo"
                )

    results: list[dict[str, Any]] = []
    for item in rows:
        is_latest = latest[item.source.source_id] is item
        if is_latest:
            # La exportación JSON del snapshot necesita la procedencia real.
            repo.save_source(item.source)
        repo.save_run(item.run)
        if item.snapshot is not None:
            repo.save_snapshot(item.snapshot)
        repo.save_source_observation(item.observation)
        repo.save_report(item.report)
        if is_latest:
            if item.resources is not None:
                repo.save_resource_catalog(
                    item.source.source_id, item.run.run_id, list(item.resources)
                )
        results.append({
            "logical_source_id": item.logical_source_id,
            "physical_source_id": item.source.source_id,
            "run_id": item.run.run_id,
            "resources": len(item.resources) if item.resources is not None else None,
            "status": item.observation.execution_status.value,
            "catalog_current": is_latest,
        })
    return {"sources_imported": len(results), "results": results}
