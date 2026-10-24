from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
import uuid
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from prospector_externo.domain.observations import CoverageStats


ROOT = Path(".")
PLAN_PATH = Path("config/source_operational_plan.yaml")
SOURCES_CONFIG_PATH = Path("config/sources.yaml")
EXECUTION_MAP_PATH = Path("config/source_execution_map.yaml")
KNOWN_EXTERNAL_BLOCKERS = {"mhe", "sigma"}

LEGACY_FIELDS = {
    "descripcion",
    "url_descarga",
    "fecha_actualizacion",
    "tipo_archivo",
    "url_origen",
    "metodo_deteccion",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_plan_rows(repo_root: Path) -> list[dict[str, Any]]:
    """Carga y valida las identidades del plan durable de fuentes."""
    import yaml

    plan_path = repo_root / PLAN_PATH
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8-sig"))
    rows = plan.get("sources") if isinstance(plan, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plan operacional debe contener sources:list no vacÃ­a")

    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Fuente #{index} del plan debe ser un objeto")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"Fuente #{index} del plan no tiene source_id vÃ¡lido")
        if source_id != source_id.strip():
            raise ValueError(f"source_id contiene espacios externos: {source_id!r}")
        if source_id in seen:
            raise ValueError(f"Plan operacional contiene source_id duplicado: {source_id}")
        seen.add(source_id)
        if row.get("next_phase") not in {"OPERATIONAL_CONFIG", "B10_STATUS"}:
            raise ValueError(
                f"Fuente {source_id}: next_phase no reconocido: {row.get('next_phase')!r}"
            )
    return rows


def inventory_summary(repo_root: Path) -> dict[str, int]:
    """Deriva los conteos del plan; no depende de totales histÃ³ricos escritos a mano."""
    rows = source_plan_rows(repo_root)
    operational = sum(row["next_phase"] == "OPERATIONAL_CONFIG" for row in rows)
    return {
        "total": len(rows),
        "operational": operational,
        "status_only": len(rows) - operational,
    }


def operational_roster(repo_root: Path) -> list[str]:
    """Deriva el roster desde configuraciÃ³n durable/versionada, no runtime B11."""
    return sorted(
        row["source_id"]
        for row in source_plan_rows(repo_root)
        if row["next_phase"] == "OPERATIONAL_CONFIG"
    )


def selected_roster(repo_root: Path, source_ids: list[str] | None) -> list[str]:
    roster = operational_roster(repo_root)
    if source_ids is None:
        return roster
    ids = list(dict.fromkeys(s.strip() for s in source_ids if s.strip()))
    unknown = set(ids) - set(roster)
    if unknown or not ids:
        raise ValueError(f"--source-ids debe seleccionar identidades operativas válidas: {sorted(unknown)}")
    return ids


def selection_plan(repo_root: Path, catalog_dir: Path, source_ids: list[str] | None,
                   mode: str) -> dict[str, Any]:
    """Plan sin crear directorios, sockets, corridas ni adaptadores con efectos."""
    from src.execution.checkpointed_batch import load_yaml, match_source_entry, execution_map_index
    from prospector_externo.application.status_gate import SourceStatusGate
    from prospector_externo.domain.models import Source, SourceConfig
    cfg = load_yaml(repo_root / SOURCES_CONFIG_PATH)
    mapping = load_yaml(repo_root / EXECUTION_MAP_PATH)
    mapped = execution_map_index(mapping)
    path = catalog_dir / "state/sources.json"
    states = {sid: Source.model_validate(row) for sid, row in load_json(path).items()} if path.exists() else {}
    roster = selected_roster(repo_root, source_ids)
    rows = []
    for row in source_plan_rows(repo_root):
        sid = row["source_id"]
        if row["next_phase"] != "OPERATIONAL_CONFIG":
            rows.append({"logical_source_id": sid, "selection_status": "STATUS_ONLY",
                         "reason": row.get("operational_status")})
            continue
        _, entry = match_source_entry(row, cfg, mapping)
        if sid not in roster:
            status, reason = "NOT_SELECTED", None
        elif mapped.get(sid, {}).get("acquisition_hold"):
            status, reason = "HELD", mapped[sid]["acquisition_hold"]
        else:
            allowed, reason = SourceStatusGate.evaluate_eligibility(SourceConfig.model_validate(entry),
                states.get(entry["source_id"]), force=mode == "initial")
            status = "ELIGIBLE" if allowed else "SKIPPED"
        rows.append({"logical_source_id": sid, "physical_source_id": entry["source_id"],
                     "workflow": entry.get("workflow"), "selection_status": status, "reason": reason,
                     "acquisition_hold": mapped.get(sid, {}).get("acquisition_hold"),
                     "health_status": states[entry["source_id"]].health_status if entry["source_id"] in states else None})
    return {"inventory": inventory_summary(repo_root), "mode": mode, "catalog_dir": str(catalog_dir),
            "selected_logical": len(roster),
            "physical_configurations": len({r["physical_source_id"] for r in rows if r.get("physical_source_id")}),
            "held_total": sum(bool(r.get("acquisition_hold")) for r in rows),
            "selected_physical": len({r["physical_source_id"] for r in rows if r.get("logical_source_id") in roster}),
            "eligible_physical": len({r["physical_source_id"] for r in rows if r["selection_status"] == "ELIGIBLE"}),
            "counts": dict(Counter(r["selection_status"] for r in rows)), "sources": rows}


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def legacy_records(document: Any) -> int:
    if not isinstance(document, dict):
        return 0

    stats = document.get("ESTADISTICAS")
    if not isinstance(stats, dict):
        return 0

    count = 0
    invalid = 0

    for row in walk_dicts(stats):
        keys = set(row)
        if not keys.intersection(LEGACY_FIELDS):
            continue

        count += 1
        if not LEGACY_FIELDS.issubset(keys):
            invalid += 1

    if invalid:
        raise ValueError(
            f"Legacy JSON contiene {invalid} registros incompletos"
        )

    return count


def latest_json(directory: Path) -> Path | None:
    if not directory.exists():
        return None

    candidates = [
        path for path in directory.glob("*.json")
        if path.is_file()
    ]
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda path: (path.name, path.stat().st_mtime_ns),
    )


def latest_report(evidence_root: Path) -> tuple[Path | None, dict[str, Any]]:
    paths = list((evidence_root / "reports").glob("run_*.json"))
    if not paths:
        return None, {}
    def order(item):
        path, raw = item
        value = raw.get("started_at") if isinstance(raw, dict) else None
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() if value else path.stat().st_mtime
        return timestamp, path.name
    path, raw = max(((p, load_json(p)) for p in paths), key=order)
    source_path = evidence_root / "state" / "sources.json"
    if source_path.is_file():
        states = load_json(source_path)
        if isinstance(states, dict) and len(states) == 1:
            expected = next(iter(states.values())).get("last_run_id")
            if expected and raw.get("run_id") != expected:
                return None, {}  # Corte antes del reporte: no resucitar un éxito anterior.
    if not isinstance(raw, dict):
        return path, {}

    rows = raw.get("source_results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return path, row

    return path, raw


def latest_snapshot(evidence_root: Path, expected_run_id: str | None = None) -> tuple[Path | None, dict[str, Any]]:
    paths = list((evidence_root / "state" / "snapshots").glob("*.json"))
    if not paths:
        return None, {}
    def order(item):
        path, raw = item
        value = raw.get("captured_at") if isinstance(raw, dict) else None
        return (datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                if value else path.stat().st_mtime), path.name
    path, raw = max(((p, load_json(p)) for p in paths), key=order)
    if expected_run_id is not None and isinstance(raw, dict) and raw.get("run_id") != expected_run_id:
        return None, {}  # La proyección offline también consume el último snapshot.
    return path, raw if isinstance(raw, dict) else {}


def physical_source_id(evidence_root: Path) -> str | None:
    sources_path = evidence_root / "state" / "sources.json"
    if sources_path.exists():
        raw = load_json(sources_path)
        if isinstance(raw, dict) and len(raw) == 1:
            key = next(iter(raw))
            if isinstance(key, str) and key:
                return key

    _, snapshot = latest_snapshot(evidence_root)
    value = snapshot.get("source_id")
    if isinstance(value, str) and value:
        return value

    return None


def grouping_contract(
    repo_root: Path,
    logical_id: str,
    physical_id: str,
) -> Path | None:
    candidates = [
        repo_root / "config" / "grouping" / f"{logical_id}.yaml",
        repo_root / "config" / "grouping" / f"{logical_id}.yml",
    ]

    if physical_id != logical_id:
        candidates.extend(
            [
                repo_root / "config" / "grouping" / f"{physical_id}.yaml",
                repo_root / "config" / "grouping" / f"{physical_id}.yml",
            ]
        )

    for path in candidates:
        if path.exists():
            return path

    return None


def canonical_legacy_file(
    evidence_root: Path,
    physical_id: str,
) -> tuple[Path | None, int]:
    preferred = (
        evidence_root
        / physical_id
        / "downstream"
        / "legacy_estadisticas.json"
    )

    candidates: list[Path] = []
    if preferred.exists():
        candidates.append(preferred)

    for path in evidence_root.rglob("legacy_estadisticas.json"):
        if path not in candidates:
            candidates.append(path)

    viable: list[tuple[Path, int]] = []

    for path in candidates:
        try:
            records = legacy_records(load_json(path))
        except Exception:
            continue

        if records > 0:
            viable.append((path, records))

    if preferred.exists():
        for path, records in viable:
            if path == preferred:
                return path, records

    if len(viable) == 1:
        return viable[0]

    return None, 0


def run_command(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        parts = [
            "$ " + subprocess.list2cmdline(command),
            "",
        ]
        if cp.stdout:
            parts.append(cp.stdout.rstrip())
        if cp.stderr:
            parts.append(cp.stderr.rstrip())
        parts.append("")
        parts.append(f"RETURN_CODE={cp.returncode}")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(parts) + "\n")

    return cp


def build_transtats_manifest(
    evidence_root: Path,
    physical_id: str,
) -> tuple[Path | None, str | None]:
    snapshot_path, snapshot = latest_snapshot(evidence_root)
    resources = snapshot.get("resources")

    if not isinstance(resources, list):
        return None, "SNAPSHOT_RESOURCES_MISSING"

    matches = [
        row
        for row in resources
        if isinstance(row, dict)
        and row.get("discovery_method")
        == "custom_form_acquisition_job"
    ]

    if len(matches) != 1:
        return None, (
            "EXPECTED_ONE_ACQUISITION_JOB_"
            f"FOUND_{len(matches)}"
        )

    resource = matches[0]
    url = resource.get("url")

    if not isinstance(url, str) or "DL_SelectFields" not in url:
        return None, "TRANSTATS_ACQUISITION_URL_UNEXPECTED"

    output = (
        evidence_root
        / physical_id
        / "downstream"
        / "acquisition_job.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "logical_source_id": "transtats",
        "source_id": physical_id,
        "decision": "ACQUISITION_JOB_READY",
        "kind": "download_form_acquisition_job",
        "title": (
            resource.get("title")
            or "US Bureau of Transportation Statistics"
        ),
        "url": url,
        "content_type": resource.get("content_type"),
        "discovery_method": resource.get("discovery_method"),
        "execution_policy": {
            "allowed_methods": ["GET", "HEAD"],
            "submission_policy": "metadata_only_no_post",
        },
        "legacy_projection": {
            "status": "NOT_APPLICABLE_YET",
            "reason": (
                "The public evidence is an acquisition job/form, not a "
                "materialized downloadable file."
            ),
        },
        "source_snapshot": (
            str(snapshot_path) if snapshot_path else None
        ),
    }

    dump_json(output, payload)
    return output, None


def project_success(
    *,
    repo_root: Path,
    logical_id: str,
    evidence_root: Path,
    physical_id: str,
    logs_dir: Path,
) -> dict[str, Any]:
    if logical_id == "transtats":
        manifest, error = build_transtats_manifest(
            evidence_root,
            physical_id,
        )
        if error:
            return {
                "classification": "SPECIALIZATION_FAILED",
                "legacy_records": 0,
                "artifact": None,
                "projection_error": error,
            }

        return {
            "classification": "ACQUISITION_JOB_READY",
            "legacy_records": 0,
            "artifact": str(manifest),
            "projection_error": None,
        }

    contract = grouping_contract(
        repo_root,
        logical_id,
        physical_id,
    )

    command = [
        sys.executable,
        "-m",
        "apps.datax_projection.main",
        "--output-dir",
        str(evidence_root),
        "--source",
        physical_id,
    ]

    if contract is not None:
        command.extend(
            [
                "--grouping-contract",
                str(contract),
            ]
        )

    cp = run_command(
        command,
        cwd=repo_root,
        log_path=logs_dir / f"{logical_id}_projection.log",
    )

    if cp.returncode != 0:
        return {
            "classification": "PROJECTION_FAILED",
            "legacy_records": 0,
            "artifact": None,
            "projection_error": (
                f"datax_projection rc={cp.returncode}"
            ),
        }

    legacy_path, records = canonical_legacy_file(
        evidence_root,
        physical_id,
    )

    if legacy_path is None or records <= 0:
        return {
            "classification": "PROJECTION_EMPTY",
            "legacy_records": 0,
            "artifact": None,
            "projection_error": None,
        }

    return {
        "classification": "DATAX_READY",
        "legacy_records": records,
        "artifact": str(legacy_path),
        "projection_error": None,
    }


def inspect_and_project(
    *,
    repo_root: Path,
    run_id: str,
    roster: list[str],
) -> list[dict[str, Any]]:
    output_root = (
        repo_root / "output" / "checkpointed" / run_id
    )
    logs_dir = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "logs"
    )

    rows: list[dict[str, Any]] = []

    from src.execution.checkpointed_batch import load_execution_map, execution_map_index
    mappings = execution_map_index(load_execution_map(repo_root / EXECUTION_MAP_PATH))
    checkpoint_path = repo_root / ".runtime/b13_pipeline" / run_id / "checkpoint.json"
    checkpoint_sources = load_json(checkpoint_path).get("sources", {}) if checkpoint_path.is_file() else {}
    for logical_id in roster:
        evidence_root = output_root / logical_id
        mapped = mappings.get(logical_id, {})
        if mapped.get("acquisition_hold"):
            rows.append({"logical_source_id": logical_id,
                         "physical_source_id": mapped.get("config_source_id"),
                         "execution_status": "SKIPPED", "failure_code": mapped["acquisition_hold"],
                         "classification": "EXTERNAL_BLOCKER", "raw_resource_count": None,
                         "legacy_records": 0, "artifact": None, "coverage_complete": None,
                         "evidence_root": str(evidence_root)})
            continue

        report_path, source_result = latest_report(
            evidence_root
        )

        if report_path is None:
            checkpoint_source = checkpoint_sources.get(logical_id, {})
            failed = checkpoint_source.get("state") == "FAILED"
            rows.append(
                {
                    "logical_source_id": logical_id,
                    "physical_source_id": checkpoint_source.get("metadata", {}).get("config_source_id") if failed else None,
                    "execution_status": "FAILED" if failed else None,
                    "failure_code": checkpoint_source.get("last_error") if failed else None,
                    "raw_resource_count": None,
                    "classification": "EXECUTION_FAILED" if failed else "PENDING",
                    "legacy_records": 0,
                    "artifact": None,
                    "evidence_root": str(evidence_root),
                }
            )
            continue

        execution_status = source_result.get(
            "execution_status"
        )
        failure_code = source_result.get("failure_code")
        coverage = source_result.get("coverage")
        if not isinstance(coverage, dict):
            coverage = {}
        coverage_stats = CoverageStats.model_validate(coverage)
        stop_reason = coverage_stats.stop_reason
        partial_coverage = (
            execution_status == "PARTIAL_SUCCESS"
            or (
                execution_status == "SUCCESS"
                and coverage_stats.is_partial_discovery()
            )
        )

        report_run_id = load_json(report_path).get("run_id")
        snapshot_path, snapshot = latest_snapshot(evidence_root, report_run_id)
        raw_count = snapshot.get("total_resources")
        if not isinstance(raw_count, int):
            resources = snapshot.get("resources")
            raw_count = (
                len(resources)
                if isinstance(resources, list)
                else None
            )

        physical_id = physical_source_id(
            evidence_root
        )

        base = {
            "logical_source_id": logical_id,
            "physical_source_id": physical_id,
            "execution_status": execution_status,
            "failure_code": failure_code,
            "stop_reason": stop_reason,
            "crawl_run_id": report_run_id,
            "pages_visited": coverage.get("pages_visited"),
            "requests_total": coverage.get("requests_total"),
            "urls_pending": coverage.get("urls_pending"),
            "urls_failed": coverage.get("urls_failed"),
            "coverage_complete": (
                not partial_coverage
                if execution_status in {"SUCCESS", "PARTIAL_SUCCESS"} and stop_reason is not None
                else None
            ),
            "raw_resource_count": raw_count,
            "evidence_root": str(evidence_root),
            "report_path": str(report_path),
            "snapshot_path": (
                str(snapshot_path)
                if snapshot_path is not None
                else None
            ),
        }

        if (report_run_id and snapshot_path is None
                and execution_status in {"SUCCESS", "PARTIAL_SUCCESS"}):
            rows.append({**base, "classification": "SNAPSHOT_MISSING_OR_STALE",
                         "legacy_records": 0, "artifact": None})
            continue

        if partial_coverage and (snapshot_path is None or not raw_count):
            rows.append({
                **base,
                "classification": "PARTIAL_COVERAGE",
                "legacy_records": 0,
                "artifact": None,
            })
            continue

        if execution_status not in {"SUCCESS", "PARTIAL_SUCCESS"}:
            if (
                logical_id in KNOWN_EXTERNAL_BLOCKERS
                and failure_code == "ROBOTS_UNREACHABLE"
            ):
                rows.append(
                    {
                        **base,
                        "classification": "EXTERNAL_BLOCKER",
                        "legacy_records": 0,
                        "artifact": None,
                    }
                )
            else:
                rows.append(
                    {
                        **base,
                        "classification": "EXECUTION_FAILED",
                        "legacy_records": 0,
                        "artifact": None,
                    }
                )
            continue

        if not physical_id:
            rows.append(
                {
                    **base,
                    "classification": "PHYSICAL_SOURCE_UNRESOLVED",
                    "legacy_records": 0,
                    "artifact": None,
                }
            )
            continue

        if execution_status == "SUCCESS" and raw_count == 0:
            rows.append({**base, "classification": "NO_RESOURCES", "legacy_records": 0, "artifact": None})
            continue

        projection = project_success(
            repo_root=repo_root,
            logical_id=logical_id,
            evidence_root=evidence_root,
            physical_id=physical_id,
            logs_dir=logs_dir,
        )

        rows.append(
            {
                **base,
                **projection,
            }
        )

    return rows


def package_ready(
    rows: list[dict[str, Any]],
    *,
    expected_source_ids: Iterable[str] | None = None,
) -> bool:
    allowed = {"DATAX_READY", "ACQUISITION_JOB_READY", "EXTERNAL_BLOCKER", "SKIPPED", "NO_RESOURCES", "EXECUTION_FAILED"}
    if not rows or any(row.get("classification") not in allowed for row in rows):
        return False

    source_ids = [row.get("logical_source_id") for row in rows]
    if any(not isinstance(source_id, str) or not source_id.strip() for source_id in source_ids):
        return False
    if len(set(source_ids)) != len(source_ids):
        return False

    if expected_source_ids is not None:
        expected = list(expected_source_ids)
        return (
            len(set(expected)) == len(expected)
            and len(source_ids) == len(expected)
            and set(source_ids) == set(expected)
        )

    # Compatibilidad con callers que solo consultan el tamaÃ±o del lote.
    # La ejecuciÃ³n productiva pasa siempre el roster explÃ­cito.
    return len(rows) == inventory_summary(ROOT.resolve())["operational"]


def safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def write_checksums(package_dir: Path) -> None:
    targets = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file()
        and path.name != "checksums.sha256"
    )

    lines = [
        f"{sha256_file(path)}  "
        f"{path.relative_to(package_dir).as_posix()}"
        for path in targets
    ]

    (package_dir / "checksums.sha256").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_zip(
    package_dir: Path,
    zip_path: Path,
) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    if zip_path.exists():
        raise FileExistsError(f"Se conserva el paquete existente: {zip_path}")

    with zipfile.ZipFile(
        zip_path,
        "x",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    arcname=path.relative_to(
                        package_dir
                    ).as_posix(),
                )


def exact_duplicate_groups(
    manifest_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in manifest_rows:
        digest = row.get("sha256")
        if isinstance(digest, str) and digest:
            groups[digest].append(row)

    result = []

    for digest, group in sorted(groups.items()):
        if len(group) < 2:
            continue

        physical = sorted(
            {
                str(row.get("physical_source_id"))
                for row in group
            }
        )

        result.append(
            {
                "sha256": digest,
                "logical_source_ids": sorted(
                    str(row["logical_source_id"])
                    for row in group
                ),
                "physical_source_ids": physical,
                "shared_physical_source": (
                    len(physical) == 1
                ),
            }
        )

    return result


def build_package(
    *,
    repo_root: Path,
    run_id: str,
    rows: list[dict[str, Any]],
    expected_source_ids: Iterable[str] | None = None,
    offline_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inventory = inventory_summary(repo_root)
    if not package_ready(rows, expected_source_ids=(expected_source_ids if expected_source_ids is not None else operational_roster(repo_root))):
        raise ValueError("El paquete requiere todas las fuentes operacionales del plan, sin duplicados")

    package_root = (
        repo_root
        / "output"
        / "b13-pipeline"
        / run_id
        / "datax-package"
    )
    package_dir = package_root / "latest"
    zip_path = package_root / "datax_package.zip"

    if package_dir.exists() or zip_path.exists():
        # Un resume conserva todos los paquetes anteriores, incluso un latest
        # incompleto tras corte. state.json apunta al paquete recién producido.
        revision = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12]
        package_dir = package_root / "revisions" / revision
        zip_path = package_root / f"datax_package_{revision}.zip"

    package_dir.mkdir(parents=True, exist_ok=False)
    sources_dir = package_dir / "sources"
    special_dir = (
        package_dir / "special" / "transtats"
    )
    sources_dir.mkdir(parents=True, exist_ok=True)
    special_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    legacy_total = 0

    for row in sorted(
        rows,
        key=lambda item: str(item["logical_source_id"]),
    ):
        logical_id = str(row["logical_source_id"])
        classification = str(row["classification"])

        manifest_row = {
            "logical_source_id": logical_id,
            "physical_source_id": row.get(
                "physical_source_id"
            ),
            "classification": classification,
            "execution_status": row.get(
                "execution_status"
            ),
            "failure_code": row.get("failure_code"),
            "stop_reason": row.get("stop_reason"),
            "urls_pending": row.get("urls_pending"),
            "urls_failed": row.get("urls_failed"),
            "coverage_complete": row.get("coverage_complete"),
            "raw_resource_count": row.get(
                "raw_resource_count"
            ),
            "legacy_records": int(
                row.get("legacy_records") or 0
            ),
            "artifact": None,
            "sha256": None,
            "crawl_run_id": row.get("crawl_run_id"),
            "selection_status": row.get("selection_status"),
            "skip_reason": row.get("skip_reason"),
            "artifact_reused": row.get("artifact_reused", False),
        }

        if classification == "DATAX_READY":
            src = Path(str(row["artifact"]))
            destination = (
                sources_dir / f"{logical_id}.json"
            )
            safe_copy(src, destination)

            records = legacy_records(
                load_json(destination)
            )
            if records <= 0:
                raise RuntimeError(
                    f"{logical_id}: legacy vacÃ­o durante packaging"
                )

            legacy_total += records
            manifest_row["legacy_records"] = records
            manifest_row["artifact"] = (
                f"sources/{logical_id}.json"
            )
            manifest_row["sha256"] = sha256_file(
                destination
            )
            if row.get("projection_path"):
                projection_target = package_dir / "projections" / f"{logical_id}.json"
                export_target = package_dir / "provenance" / logical_id / "export_manifest.json"
                safe_copy(Path(row["projection_path"]), projection_target)
                safe_copy(Path(row["export_manifest_path"]), export_target)
                if (sha256_file(projection_target) != row["projection_sha256"]
                        or sha256_file(export_target) != row["export_manifest_sha256"]
                        or manifest_row["sha256"] != row["legacy_sha256"]):
                    raise ValueError(f"{logical_id}: artefactos cambiaron durante empaquetado")
                manifest_row.update({
                    "projection_artifact": projection_target.relative_to(package_dir).as_posix(),
                    "projection_sha256": row["projection_sha256"],
                    "export_manifest_artifact": export_target.relative_to(package_dir).as_posix(),
                    "export_manifest_sha256": row["export_manifest_sha256"],
                    "input_resources_hash": row["input_resources_hash"],
                    "snapshot_reference": row["snapshot_reference"],
                    "projection_origin": row["projection_origin"],
                })

        elif classification == "ACQUISITION_JOB_READY":
            src = Path(str(row["artifact"]))
            destination = (
                special_dir / "acquisition_job.json"
            )
            safe_copy(src, destination)
            manifest_row["artifact"] = (
                "special/transtats/acquisition_job.json"
            )
            manifest_row["sha256"] = sha256_file(
                destination
            )

        elif classification in {"EXTERNAL_BLOCKER", "SKIPPED", "NO_RESOURCES", "EXECUTION_FAILED"}:
            blockers.append(
                {
                    "logical_source_id": logical_id,
                    "physical_source_id": row.get(
                        "physical_source_id"
                    ),
                    "failure_code": row.get(
                        "failure_code"
                    ),
                    "execution_status": row.get(
                        "execution_status"
                    ),
                }
            )

        else:
            raise RuntimeError(
                f"No se puede empaquetar {logical_id}: "
                f"{classification}"
            )

        manifest_rows.append(manifest_row)

    duplicate_groups = exact_duplicate_groups(
        manifest_rows
    )
    cross_physical = [
        group
        for group in duplicate_groups
        if not group["shared_physical_source"]
    ]

    manifest = {
        "schema_version": "1.0.0",
        "package_type": "b13_reproducible_datax_delivery",
        "pipeline_run_id": run_id,
        "selected_source_ids": [row["logical_source_id"] for row in rows],
        "failed_sources": [row["logical_source_id"] for row in rows if row.get("classification") == "EXECUTION_FAILED"],
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "inventory": inventory,
        "counts": dict(
            Counter(
                row["classification"]
                for row in rows
            )
        ),
        "legacy_records": legacy_total,
        "coverage_partial_sources": sorted(
            row["logical_source_id"]
            for row in rows
            if row.get("coverage_complete") is False
        ),
        "sources": manifest_rows,
        "duplicate_groups": duplicate_groups,
        "cross_physical_duplicate_groups": (
            cross_physical
        ),
        "consumer": {
            "entrypoint": "manifest.json",
            "selection_key": "logical_source_id",
            "legacy_pattern": (
                "sources/{logical_source_id}.json"
            ),
        },
    }
    if offline_provenance is not None:
        manifest["revision"] = {**offline_provenance, "revision_id": package_dir.name}
        origin = offline_provenance["origin_manifest"]
        origin_target = package_dir / "provenance/origin_manifest.json"
        safe_copy(repo_root / origin["path"], origin_target)
        if sha256_file(origin_target) != origin["sha256"]:
            raise ValueError("Manifest de origen cambió durante empaquetado")
    dump_json(package_dir / "manifest.json", manifest)

    dump_json(
        package_dir / "external_blockers.json",
        {
            "count": len(blockers),
            "sources": blockers,
        },
    )

    with (package_dir / "sources.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        fields = [
            "logical_source_id",
            "physical_source_id",
            "classification",
            "execution_status",
            "failure_code",
            "stop_reason",
            "urls_pending",
            "urls_failed",
            "coverage_complete",
            "raw_resource_count",
            "legacy_records",
            "artifact",
            "sha256",
        ]
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
        )
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(
                {
                    key: row.get(key)
                    for key in fields
                }
            )

    readme = f"""# B13 reproducible DATAX package

Pipeline run: `{run_id}`

- {len(rows)} selected logical sources are accounted for (inventory: {inventory["operational"]} operational).
- Legacy DATAX artifacts remain separated by logical source.
- TRANSTATS remains an acquisition-job manifest until materialization exists.
- External blockers remain explicit and are never converted to fake success.
- Projection readiness does not imply complete crawl coverage; inspect
  `coverage_partial_sources` and per-source coverage fields in `manifest.json`.
- `manifest.json` is the package entry point.
- Total legacy records in this run: {legacy_total}.
- References are not downloaded documents; remote availability is not verified.
"""
    if offline_provenance is not None:
        readme += """
This revision packages existing corrected projections, with no HTTP acquisition.
`projections/*.json` contains the selected references, decisions and original URLs.
`provenance/` preserves input export manifests and the original package manifest.
Their relative paths describe the original input layout; package artifact paths
and hashes are indexed separately in the new manifest. Snapshot and original ZIP
references retain their source identities, hashes and partial coverage status.
"""
    (package_dir / "README.md").write_text(
        readme,
        encoding="utf-8",
        newline="\n",
    )

    write_checksums(package_dir)
    build_zip(package_dir, zip_path)

    return {
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "zip_sha256": sha256_file(zip_path),
        "legacy_records": legacy_total,
        "duplicate_groups": len(duplicate_groups),
        "cross_physical_duplicate_groups": len(
            cross_physical
        ),
        "package_ready": not cross_physical,
    }


def package_offline(
    repo_root: Path, *, run_id: str, projection_dir: Path,
    catalog_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate existing exports, then use the same versioned B13 packager.

    No acquisition, projection writes, checkpoint updates or Mongo sync. A
    mismatch fails before package creation; existing history is never replaced.
    """
    from prospector_externo.application.legacy_stats_exporter import (
        LegacyStatsContractValidator, LegacyStatsJsonExporter,
    )
    from prospector_externo.application.projection_export_pipeline import ProjectionExportManifest
    from prospector_externo.application.projection_service import DataxProjectionService
    from prospector_externo.domain.models import Snapshot, Source
    from prospector_externo.domain.projection import DataxProjection

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("--run-id inválido")
    repo_root = repo_root.resolve()
    projection_dir = projection_dir.resolve()
    origin_dir = repo_root / "output/b13-pipeline" / run_id / "datax-package"
    origin_path = origin_dir / "latest/manifest.json"
    origin_zip = origin_dir / "datax_package.zip"
    origin = load_json(origin_path)
    rows = origin.get("sources", [])
    roster = origin.get("selected_source_ids", [])
    if (origin.get("pipeline_run_id") != run_id or not roster
            or not package_ready(rows, expected_source_ids=roster)
            or any(row["classification"] != "DATAX_READY" for row in rows)):
        raise ValueError("Revisión offline requiere un paquete de origen DATAX_READY coherente")
    # Verify the preserved origin itself, including its checksum inventory and ZIP.
    old_checksums = origin_dir / "latest/checksums.sha256"
    for line in old_checksums.read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        target = (origin_dir / "latest" / relative).resolve()
        if (origin_dir / "latest").resolve() not in target.parents or sha256_file(target) != digest:
            raise ValueError("Checksum del paquete de origen inválido")
    with zipfile.ZipFile(origin_zip) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP de origen corrupto")
        for path in (origin_dir / "latest").rglob("*"):
            if path.is_file() and archive.read(path.relative_to(origin_dir / "latest").as_posix()) != path.read_bytes():
                raise ValueError("ZIP y directorio del paquete de origen difieren")

    def reference(path):
        try:
            return path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return str(path.resolve())

    catalog_repo = None
    if catalog_dir is not None:
        from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
        if not (catalog_dir / "state/sources.json").is_file():
            raise ValueError("--catalog-dir sin state/sources.json")
        catalog_repo = LocalJsonRepositoryAdapter(catalog_dir)
    service = DataxProjectionService()
    validated = []
    old_total = 0
    for row in rows:
        logical, physical = row["logical_source_id"], row["physical_source_id"]
        if any(not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", sid)
               for sid in (logical, physical)):
            raise ValueError("Identidad de fuente inválida")
        downstream = projection_dir / physical / "downstream"
        projection_path = downstream / "datax_projection.json"
        legacy_path = downstream / "legacy_estadisticas.json"
        export_path = downstream / "export_manifest.json"
        projection = DataxProjection.model_validate(load_json(projection_path))
        export = ProjectionExportManifest.model_validate(load_json(export_path))
        legacy = load_json(legacy_path)
        records = LegacyStatsContractValidator.validate(legacy)
        if (export.source_id != physical or projection.source_id != physical
                or export.run_id != row["crawl_run_id"] or projection.run_id != export.run_id
                or export.input_resources_hash != projection.resources_hash
                or export.projection_sha256 != sha256_file(projection_path)
                or export.legacy_sha256 != sha256_file(legacy_path)
                or export.legacy_records != records or export.selected_resources != records
                or export.raw_resources != projection.total_raw_resources
                or export.families != projection.total_families
                or export.projection_relpath != f"{physical}/downstream/datax_projection.json"
                or export.legacy_relpath != f"{physical}/downstream/legacy_estadisticas.json"
                or LegacyStatsJsonExporter.build(projection).document != legacy):
            raise ValueError(f"{logical}: export, proyección y legacy incoherentes")
        evidence_root = repo_root / "output/checkpointed" / run_id / physical
        snapshot_path, raw_snapshot = latest_snapshot(evidence_root, expected_run_id=export.run_id)
        if snapshot_path is None:
            raise ValueError(f"{logical}: snapshot de origen ausente o incompatible")
        snapshot = Snapshot.model_validate(raw_snapshot)
        source = Source.model_validate(load_json(evidence_root / "state/sources.json")[physical])
        if (service.project(source, snapshot) != projection
                or row["raw_resource_count"] != projection.total_raw_resources):
            raise ValueError(f"{logical}: proyección no coincide con snapshot/política actual")
        if catalog_repo is not None:
            live_source = catalog_repo.get_source(physical)
            live_snapshot = catalog_repo.get_latest_snapshot(physical)
            if (live_source is None or live_snapshot is None
                    or service.project(live_source, live_snapshot) != projection):
                raise ValueError(f"{logical}: proyección no coincide con el catálogo/API actual")
        original_artifact = origin_dir / "latest" / row["artifact"]
        original_records = legacy_records(load_json(original_artifact))
        if original_records != row["legacy_records"] or sha256_file(original_artifact) != row["sha256"]:
            raise ValueError(f"{logical}: legacy del paquete de origen incoherente")
        old_total += original_records
        validated.append({**row, "artifact": str(legacy_path), "legacy_records": records,
            "artifact_reused": True, "projection_path": str(projection_path),
            "export_manifest_path": str(export_path), "projection_sha256": sha256_file(projection_path),
            "export_manifest_sha256": sha256_file(export_path), "legacy_sha256": sha256_file(legacy_path),
            "input_resources_hash": projection.resources_hash,
            "projection_origin": reference(projection_path),
            "snapshot_reference": {"path": reference(snapshot_path), "sha256": sha256_file(snapshot_path),
                                   "snapshot_id": snapshot.snapshot_id, "run_id": snapshot.run_id}})
    if old_total != origin["legacy_records"]:
        raise ValueError("Total del paquete de origen incoherente")
    provenance = {"mode": "offline_existing_projection", "http_acquisitions": 0,
        "remote_availability_verified": False, "projection_grouping": "default_technical",
        "origin_pipeline_run_id": run_id, "input_projection_dir": reference(projection_dir),
        "catalog_reference": reference(catalog_dir) if catalog_dir else None,
        "catalog_projection_matches": catalog_repo is not None,
        "origin_manifest": {"path": reference(origin_path), "sha256": sha256_file(origin_path),
                            "artifact": "provenance/origin_manifest.json"},
        "origin_zip": {"path": reference(origin_zip), "sha256": sha256_file(origin_zip),
                       "legacy_records": old_total}}
    result = build_package(repo_root=repo_root, run_id=run_id, rows=validated,
                           expected_source_ids=roster, offline_provenance=provenance)
    return {**result, "manifest_path": str(Path(result["package_dir"]) / "manifest.json"),
            "source_counts": {row["logical_source_id"]: row["legacy_records"] for row in validated},
            "mode": provenance["mode"], "origin_zip_sha256": provenance["origin_zip"]["sha256"]}


def save_state(
    *,
    repo_root: Path,
    run_id: str,
    checkpoint_returncode: int | None,
    rows: list[dict[str, Any]],
    package: dict[str, Any] | None,
    mongo_sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = Counter(
        str(row.get("classification"))
        for row in rows
    )

    terminal_good = {
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "EXTERNAL_BLOCKER",
        "SKIPPED",
        "NO_RESOURCES",
    }

    pending = [
        row["logical_source_id"]
        for row in rows
        if row.get("classification") == "PENDING"
    ]
    unresolved = [
        row["logical_source_id"]
        for row in rows
        if row.get("classification")
        not in terminal_good | {"PENDING"}
    ]
    coverage_partial_sources = sorted(
        row["logical_source_id"]
        for row in rows
        if row.get("coverage_complete") is False
    )

    if pending:
        status = "PARTIAL"
    elif unresolved:
        status = "FAILED"
    elif package is None:
        status = "PACKAGE_NOT_BUILT"
    elif not package.get("package_ready"):
        status = "PACKAGE_REVIEW"
    else:
        status = "READY"

    if mongo_sync is not None and mongo_sync.get("status") == "FAILED":
        status = "MONGO_SYNC_FAILED"

    payload = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "pipeline_run_id": run_id,
        "status": status,
        "checkpoint_returncode": checkpoint_returncode,
        "inventory": inventory_summary(repo_root),
        "counts": dict(sorted(counts.items())),
        "pending": pending,
        "unresolved": unresolved,
        "coverage_partial_sources": coverage_partial_sources,
        "package": package,
        "mongo_sync": mongo_sync,
        "sources": rows,
    }

    state_path = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "state.json"
    )
    dump_json(state_path, payload)

    dump_json(
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / "latest.json",
        payload,
    )

    return payload


def print_summary(state: dict[str, Any]) -> None:
    print("=" * 78)
    print("B13 â€” REPRODUCIBLE END-TO-END PIPELINE")
    print("=" * 78)
    print(f"Run ID:                 {state['pipeline_run_id']}")
    print(f"Status:                 {state['status']}")
    inventory = state["inventory"]
    print(f"Inventory:              {inventory['total']}")
    print(f"Operational:            {inventory['operational']}")
    print(f"Status-only:            {inventory['status_only']}")

    print()
    print("Classification:")
    for key in (
        "DATAX_READY",
        "ACQUISITION_JOB_READY",
        "EXTERNAL_BLOCKER",
        "SKIPPED",
        "NO_RESOURCES",
        "PENDING",
        "PARTIAL_COVERAGE",
        "EXECUTION_FAILED",
        "PROJECTION_FAILED",
        "PROJECTION_EMPTY",
        "SPECIALIZATION_FAILED",
        "PHYSICAL_SOURCE_UNRESOLVED",
    ):
        print(
            f"  {key:<28} "
            f"{state['counts'].get(key, 0)}"
        )

    print()
    print(
        "Pending:                "
        + (
            ", ".join(state["pending"])
            if state["pending"]
            else "(none)"
        )
    )
    print(
        "Unresolved:             "
        + (
            ", ".join(state["unresolved"])
            if state["unresolved"]
            else "(none)"
        )
    )
    print(
        "Partial crawl coverage: "
        + (
            ", ".join(state["coverage_partial_sources"])
            if state["coverage_partial_sources"]
            else "(none)"
        )
    )

    package = state.get("package")
    if isinstance(package, dict):
        print()
        print(
            f"Legacy records:         "
            f"{package['legacy_records']}"
        )
        print(
            f"Duplicate groups:       "
            f"{package['duplicate_groups']}"
        )
        print(
            "Cross-physical dupes:   "
            f"{package['cross_physical_duplicate_groups']}"
        )
        print(
            f"Package:                "
            f"{package['package_dir']}"
        )
        print(
            f"ZIP:                    "
            f"{package['zip_path']}"
        )
        print(
            f"ZIP SHA256:             "
            f"{package['zip_sha256']}"
        )


def command_plan(repo_root: Path, *, catalog_dir: Path | None = None,
                 source_ids: list[str] | None = None, mode: str = "incremental",
                 json_output: bool = False) -> int:
    diagnostic = selection_plan(repo_root, Path(catalog_dir or repo_root / "output").resolve(), source_ids, mode)
    if json_output:
        print(json.dumps(diagnostic, indent=2, ensure_ascii=False))
        return 0
    required = [
        PLAN_PATH,
        SOURCES_CONFIG_PATH,
        EXECUTION_MAP_PATH,
    ]

    missing = [
        str(path)
        for path in required
        if not (repo_root / path).exists()
    ]
    if missing:
        raise FileNotFoundError(
            "Faltan prerequisitos: "
            + ", ".join(missing)
        )

    roster = operational_roster(repo_root)
    inventory = inventory_summary(repo_root)

    print("=" * 78)
    print("B13 â€” PIPELINE PLAN")
    print("=" * 78)
    print(f"Inventory:          {inventory['total']}")
    print(f"Operational:        {len(roster)}")
    print(f"Status-only:        {inventory['status_only']}")
    print(f"Selected logical:   {diagnostic['selected_logical']}")
    print(f"Selected physical:  {diagnostic['selected_physical']}")
    print(f"Eligible physical:  {diagnostic['eligible_physical']}")
    print(f"Eligibility:        {diagnostic['counts']}")
    for item in diagnostic["sources"]:
        if item.get("reason"):
            print(f"  {item['logical_source_id']}: {item['selection_status']} {item['reason']}")
    from src.execution.checkpointed_batch import load_yaml, match_source_entry

    config = load_yaml(repo_root / SOURCES_CONFIG_PATH)
    execution_map = load_yaml(repo_root / EXECUTION_MAP_PATH)
    print()
    print("Logical source -> physical config -> runtime workflow:")
    for row in source_plan_rows(repo_root):
        if row["next_phase"] != "OPERATIONAL_CONFIG":
            print(f"  {row['source_id']} -> STATUS_ONLY ({row.get('operational_status', 'unknown')})")
            continue
        _, physical = match_source_entry(row, config, execution_map)
        actual = physical.get("workflow", "html")
        planned = row.get("workflow_strategy")
        note = f" [historical plan: {planned}]" if planned and planned != actual else ""
        print(f"  {row['source_id']} -> {physical['source_id']} -> {actual}{note}")
    print("Checkpoint runner:  apps.checkpointed_batch.main")
    print("Projection:         apps.datax_projection.main")
    print("Packaging:          B13 dynamic package")
    print("Resume:             YES")
    print("Network now:        NO")
    print("Live run network:   YES")
    print()
    print("Run command:")
    print(
        "  python -m apps.b13_pipeline.main run "
        "--run-id cierre-52-AAAAMMDD-01 --max-sources 3"
    )
    print("Resume command:")
    print(
        "  python -m apps.b13_pipeline.main run "
        "--run-id cierre-52-AAAAMMDD-01 --resume"
    )

    return 0


def prepare_checkpoint_config(repo_root: Path, *, run_id: str, resume: bool) -> Path:
    """Aísla B9 por corrida y conserva el checkpoint global de versiones previas."""
    from src.persistence.factory import load_config
    from src.persistence.store import JsonCheckpointStore

    runtime = repo_root / ".runtime" / "b13_pipeline" / run_id
    checkpoint_path = runtime / "checkpoint.json"
    config_path = runtime / "checkpointing.json"
    if not resume and (runtime.exists() or (repo_root / "output" / "checkpointed" / run_id).exists()):
        raise FileExistsError(f"Ya existe evidencia de {run_id}; use --resume o un --run-id nuevo")
    if resume:
        if checkpoint_path.exists():
            JsonCheckpointStore(checkpoint_path).load(run_id)
        else:
            # Migrar solo un checkpoint que demuestre pertenecer a esta corrida.
            # Nunca reconstruir SUCCEEDED a partir de la mera presencia de JSON.
            legacy_config = load_config(repo_root / "config" / "checkpointing.yaml")
            legacy_path = Path(legacy_config.get("json", {}).get("path", ".runtime/checkpoints/latest.json"))
            if not legacy_path.is_absolute():
                legacy_path = repo_root / legacy_path
            legacy = JsonCheckpointStore(legacy_path).load(run_id)
            JsonCheckpointStore(checkpoint_path).save(legacy)
    dump_json(config_path, {"default_backend": "json", "json": {"path": str(checkpoint_path)}})
    return config_path


def command_run(
    repo_root: Path,
    *,
    run_id: str,
    resume: bool,
    max_sources: int | None,
    sync_mongo: bool = False,
    mongo_uri: str = "mongodb://127.0.0.1:27018",
    catalog_dir: Path | None = None,
    mode: str | None = None,
    source_ids: list[str] | None = None,
    mongo_database: str = "prospector_externo",
) -> int:
    pipeline_clock = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("--run-id solo admite letras, números, guion y guion bajo (1-96 caracteres)")
    if max_sources is not None and max_sources <= 0:
        raise ValueError("--max-sources debe ser > 0")
    runtime_dir = repo_root / ".runtime/b13_pipeline" / run_id
    request_path = runtime_dir / "request.json"
    previous_request = load_json(request_path) if resume and request_path.exists() else {}
    mode = mode or previous_request.get("mode", "initial")
    if mode not in {"initial", "incremental"}:
        raise ValueError("mode debe ser initial o incremental")
    if source_ids is None and previous_request:
        source_ids = previous_request["source_ids"]
    roster = selected_roster(repo_root, source_ids)
    catalog_dir = Path(catalog_dir or previous_request.get("catalog_dir") or repo_root / "output").resolve()
    request = {"mode": mode, "source_ids": roster, "catalog_dir": str(catalog_dir)}
    if previous_request and previous_request != request:
        raise ValueError("PIPELINE_REQUEST_CHANGED: reanudar exige mismo modo, selección y catálogo")

    if sync_mongo:
        # Igual que las CLI del batch, admitir ejecución desde checkout sin pip -e.
        sys.path.insert(0, str(repo_root / "src"))
        # Comprobar conectividad ANTES de lanzar el primer rastreo real.
        from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
        repo = MongoPersistenceAdapter(connection_uri=mongo_uri, database_name=mongo_database)
        try:
            repo.check_health()
        finally:
            repo.client.close()

    checkpoint_config = prepare_checkpoint_config(repo_root, run_id=run_id, resume=resume)
    if not request_path.exists():
        dump_json(request_path, request)
    attempt_dir = runtime_dir / "attempts" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_") + uuid.uuid4().hex[:12])
    attempt_dir.mkdir(parents=True)
    checkpoint_path = runtime_dir / "checkpoint.json"
    if checkpoint_path.exists():
        shutil.copyfile(checkpoint_path, attempt_dir / "checkpoint_before.json")
    batch_report = attempt_dir / "batch_report.json"
    checkpoint_command = [
        sys.executable,
        "-m",
        "apps.checkpointed_batch.main",
        "--plan",
        str(PLAN_PATH),
        "--sources-config",
        str(SOURCES_CONFIG_PATH),
        "--execution-map",
        str(EXECUTION_MAP_PATH),
        "--backend",
        "json",
        "--checkpoint-config",
        str(checkpoint_config),
        "run",
        "--catalog-dir",
        str(catalog_dir),
        "--run-id",
        run_id,
        "--source-ids", ",".join(roster),
        "--report", str(batch_report),
    ]

    if mode == "initial":
        checkpoint_command.append("--force")
    else:
        checkpoint_command.append("--eligible-only")

    if resume:
        checkpoint_command.append("--resume")

    if max_sources is not None:
        checkpoint_command.extend(
            [
                "--max-sources",
                str(max_sources),
            ]
        )

    print("=" * 78)
    print("B13 â€” CHECKPOINTED LIVE EXECUTION")
    print("=" * 78)
    print(f"Run ID:       {run_id}")
    print(f"Resume:       {'YES' if resume else 'NO'}")
    print(f"Mode:         {mode}")
    print(f"Catalog:      {catalog_dir}")
    print(f"Selected:     {', '.join(roster)}")
    print(
        f"Max sources:  "
        f"{max_sources if max_sources is not None else 'ALL'}"
    )

    checkpoint_log = (
        repo_root
        / ".runtime"
        / "b13_pipeline"
        / run_id
        / "checkpointed_batch.log"
    )

    phase_clock = time.perf_counter()
    cp = run_command(
        checkpoint_command,
        cwd=repo_root,
        log_path=checkpoint_log,
    )
    crawl_seconds = time.perf_counter() - phase_clock

    print(
        "Checkpoint runner RC: "
        f"{cp.returncode}"
    )
    print(
        "Nota: RC no-cero no aborta automÃ¡ticamente; "
        "el pipeline inspecciona cada evidencia."
    )

    print()
    print("ProyecciÃ³n/inspecciÃ³n offline de evidencias disponibles...")

    batch = load_json(batch_report) if batch_report.exists() else {}
    skipped = batch.get("skipped_eligibility", {})
    checkpoint = load_json(checkpoint_path) if checkpoint_path.exists() else {"sources": {}}
    for sid in roster:
        entry = checkpoint["sources"].get(sid, {})
        metadata = entry.get("metadata", {})
        if entry.get("state") == "SKIPPED_STATUS" and metadata.get("skip_reason") and not metadata.get("policy_hold"):
            skipped.setdefault(sid, {"physical_source_id": metadata["config_source_id"],
                                     "reason": metadata["skip_reason"]})
    phase_clock = time.perf_counter()
    rows = inspect_and_project(
        repo_root=repo_root,
        run_id=run_id,
        roster=[sid for sid in roster if sid not in skipped],
    )
    delivery_path = catalog_dir / "state/deliveries.json"
    deliveries = load_json(delivery_path) if delivery_path.exists() else {}
    for sid, info in skipped.items():
        saved = deliveries.get(sid)
        state_path = catalog_dir / "state/sources.json"
        states = load_json(state_path) if state_path.exists() else {}
        source = states.get(info["physical_source_id"], {})
        metadata = checkpoint["sources"].get(sid, {}).get("metadata", {})
        reusable = (saved and saved.get("config_fingerprint") == metadata.get("config_fingerprint")
                    and saved.get("crawl_run_id") == source.get("last_run_id")
                    and saved["row"].get("physical_source_id") == info["physical_source_id"]
                    and saved["row"].get("artifact") and Path(saved["row"]["artifact"]).is_file()
                    and sha256_file(Path(saved["row"]["artifact"])) == saved.get("artifact_sha256"))
        row = dict(saved["row"]) if reusable else {
            "logical_source_id": sid, "physical_source_id": info["physical_source_id"],
            "classification": "SKIPPED", "artifact": None, "legacy_records": 0,
            "raw_resource_count": None, "coverage_complete": None,
        }
        row.update(execution_status="SKIPPED", selection_status="SKIPPED", skip_reason=info["reason"],
                   artifact_reused=bool(reusable), telemetry={"measurement": "NO_MEDIDO"})
        if not reusable:
            row["artifact_status"] = "NO_VERIFIED_PREVIOUS_DELIVERY"
        rows.append(row)
    for row in rows:
        sid = row["logical_source_id"]
        metadata = checkpoint["sources"].get(sid, {}).get("metadata", {})
        row.setdefault("selection_status", "HELD" if row.get("classification") == "EXTERNAL_BLOCKER" else "SELECTED")
        row.setdefault("telemetry", metadata.get("telemetry", {"measurement": "NO_MEDIDO"}))
        row["acquisition_reused_from"] = metadata.get("acquisition_reused_from")
        acquired_now = sid in batch.get("executed_source_ids", []) and not row["acquisition_reused_from"]
        row["requests_this_attempt"] = row.get("requests_total") if acquired_now else 0
        if not acquired_now:
            row["telemetry"] = {"measurement": "NO_MEDIDO"}
            if row.get("selection_status") == "SELECTED":
                row["selection_status"] = "ALREADY_COMPLETED"
        if row.get("artifact") and row.get("execution_status") != "SKIPPED":
            deliveries[sid] = {"row": row, "config_fingerprint": metadata.get("config_fingerprint"),
                "crawl_run_id": row.get("crawl_run_id"), "artifact_sha256": sha256_file(Path(row["artifact"]))}
    projection_seconds = time.perf_counter() - phase_clock
    # Índice operativo mutable; los artefactos y los paquetes anteriores no se sobrescriben.
    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    LocalJsonRepositoryAdapter._atomic_write_text(delivery_path, json.dumps(deliveries, ensure_ascii=False))

    mongo_sync = None
    phase_clock = time.perf_counter()
    if sync_mongo:
        # No se visita de nuevo ninguna web: importación de evidencias JSON
        # aisladas. Los checkpoints de corridas parciales pueden retomarse.
        from prospector_externo.application.catalog_sync import collect_evidence, sync_evidence
        try:
            # Solo adquisiciones actuales; las omisiones no inventan un snapshot nuevo.
            from prospector_externo.application.catalog_sync import read_evidence
            evidence_rows = [item for row in rows if row.get("execution_status") not in {None, "SKIPPED"}
                             and (item := read_evidence(Path(row["evidence_root"]), row["logical_source_id"])) is not None]
            repo = MongoPersistenceAdapter(connection_uri=mongo_uri, database_name=mongo_database)
            try:
                repo.check_health()
                result = sync_evidence(repo, evidence_rows)
                local = LocalJsonRepositoryAdapter(catalog_dir)
                skipped_runs = {checkpoint["sources"].get(sid, {}).get("metadata", {}).get("skip_report_run_id")
                                for sid in skipped} - {None}
                for skipped_run_id in skipped_runs:
                    omitted_run = local.get_run(skipped_run_id)
                    omitted_report = local.get_report(skipped_run_id)
                    if omitted_run is None or omitted_report is None:
                        raise ValueError("SKIP_REPORT_MISSING: no se fabrica evidencia de omisiones")
                    repo.save_run(omitted_run)
                    for observation in omitted_report.source_results:
                        repo.save_source_observation(observation)
                    repo.save_report(omitted_report)
                result["skipped_runs_imported"] = len(skipped_runs)
            finally:
                repo.client.close()
            mongo_sync = {"status": "OK", **result}
            print(f"Mongo sync: OK ({result['sources_imported']} fuentes físicas/logical runs)")
        except Exception as exc:
            mongo_sync = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
            print(f"Mongo sync: FAILED: {mongo_sync['error']}")
    sync_seconds = time.perf_counter() - phase_clock if sync_mongo else None

    package = None
    phase_clock = time.perf_counter()
    if package_ready(rows, expected_source_ids=roster):
        print(
            f"{len(roster)}/{len(roster)} operacionales terminales; "
            "construyendo paquete reproducible..."
        )
        package = build_package(
            repo_root=repo_root,
            run_id=run_id,
            rows=rows,
            expected_source_ids=roster,
        )
    else:
        print(
            "Corrida parcial o con pendientes; "
            "el paquete final todavÃ­a no se construye."
        )
    package_seconds = time.perf_counter() - phase_clock

    state = save_state(
        repo_root=repo_root,
        run_id=run_id,
        checkpoint_returncode=cp.returncode,
        rows=rows,
        package=package,
        mongo_sync=mongo_sync,
    )
    state.update(mode=mode, catalog_dir=str(catalog_dir), selected_source_ids=roster,
        started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
        attempt_id=attempt_dir.name, batch_report=batch, timings={"crawl_seconds": crawl_seconds,
        "projection_seconds": projection_seconds, "package_seconds": package_seconds,
        "mongo_sync_seconds": sync_seconds, "total_seconds": time.perf_counter() - pipeline_clock},
        paths={"state": str(runtime_dir / "state.json"), "attempt": str(attempt_dir),
               "catalog": str(catalog_dir), "log": str(checkpoint_log)})
    dump_json(attempt_dir / "state.json", state)
    dump_json(runtime_dir / "state.json", state)
    dump_json(repo_root / ".runtime/b13_pipeline/latest.json", state)
    for row in rows:
        print(f"SOURCE {row['logical_source_id']} physical={row.get('physical_source_id')} "
              f"selection={row.get('selection_status')} status={row.get('execution_status')} "
              f"RAW={row.get('raw_resource_count')} DATAX={row.get('legacy_records')} "
              f"pages={row.get('pages_visited')} pending={row.get('urls_pending')} "
              f"failed={row.get('urls_failed')} stop={row.get('stop_reason')} "
              f"requests_this_attempt={row.get('requests_this_attempt')} "
              f"skip={row.get('skip_reason')} evidence={row.get('evidence_root')}")
    print(f"Summary: {runtime_dir / 'state.json'}")
    print(f"Elapsed: {state['timings']['total_seconds']:.3f}s; acquisitions={batch.get('acquisitions_executed', 'NO_MEDIDO')}")
    if package and package.get("package_dir"):
        print(f"Manifest: {Path(package['package_dir']) / 'manifest.json'}")

    print_summary(state)

    if state["status"] in {"FAILED", "MONGO_SYNC_FAILED"} or state["unresolved"]:
        return 2
    if cp.returncode not in {0, 2, 3}:
        return 2
    if cp.returncode and not any(row.get("execution_status") for row in rows):
        return 2  # No convertir un hijo que falló sin evidencia en éxito aparente.
    if (state["status"] == "PACKAGE_REVIEW" or state["pending"]
            or state["coverage_partial_sources"]):
        return 3

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "B13: pipeline reproducible crawl -> projection -> "
            "package, con checkpoint/resume."
        )
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    plan_parser = sub.add_parser(
        "plan",
        help="Valida y muestra el plan sin red.",
    )
    parser.add_argument("--project-dir", type=Path, default=ROOT,
                        help="Raíz de configuración/output/runtime; por defecto directorio actual")

    run_parser = sub.add_parser(
        "run",
        help="Ejecuta o reanuda el pipeline live.",
    )
    run_parser.add_argument(
        "--run-id",
        required=True,
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
    )
    run_parser.add_argument(
        "--max-sources",
        type=int,
        default=None,
    )
    run_parser.add_argument(
        "--catalog-dir", type=Path, default=None,
        help="Catálogo JSON consolidado aislado; por defecto output/ (no cambia evidencias del lote)",
    )
    run_parser.add_argument(
        "--sync-mongo", action="store_true",
        help="Sincroniza los checkpoints JSON a Mongo, sin segundo rastreo",
    )
    run_parser.add_argument(
        "--mongo-uri",
        default="mongodb://127.0.0.1:27018",
        help="MongoDB accesible desde el host (Compose: localhost:27018)",
    )
    run_parser.add_argument("--mongo-database", default="prospector_externo")
    for entry in (plan_parser, run_parser):
        entry.add_argument("--source-ids", default=None, help="IDs lógicos separados por coma; omitir selecciona todas")
        entry.add_argument("--mode", choices=["initial", "incremental"], default=None)
    plan_parser.add_argument("--catalog-dir", type=Path, default=None)
    plan_parser.add_argument("--json", action="store_true", help="Diagnóstico sin escrituras ni red")

    offline_parser = sub.add_parser("package-offline", help="Empaqueta proyecciones existentes en una revisión nueva, sin HTTP")
    offline_parser.add_argument("--run-id", required=True, help="Corrida de origen con paquete preservado")
    offline_parser.add_argument("--projection-dir", type=Path, required=True, help="Raíz de exports corregidos por fuente")
    offline_parser.add_argument("--catalog-dir", type=Path, default=None, help="Verifica además igualdad con la proyección del catálogo/API JSON actual")

    args = parser.parse_args()
    repo_root = args.project_dir.resolve()

    if args.command == "package-offline":
        result = package_offline(repo_root, run_id=args.run_id, projection_dir=args.projection_dir,
                                 catalog_dir=args.catalog_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["package_ready"] else 3

    if args.command == "plan":
        return command_plan(repo_root, catalog_dir=args.catalog_dir, mode=args.mode or "incremental",
                            source_ids=args.source_ids.split(",") if args.source_ids else None,
                            json_output=args.json)

    return command_run(
        repo_root,
        run_id=args.run_id,
        resume=args.resume,
        max_sources=args.max_sources,
        sync_mongo=args.sync_mongo,
        mongo_uri=args.mongo_uri,
        catalog_dir=args.catalog_dir,
        mode=args.mode,
        source_ids=args.source_ids.split(",") if args.source_ids else None,
        mongo_database=args.mongo_database,
    )


if __name__ == "__main__":
    raise SystemExit(main())
