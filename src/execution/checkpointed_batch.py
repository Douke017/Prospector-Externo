from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import yaml

from src.persistence.model import (
    FAILED,
    PARTIAL,
    RUNNING,
    PENDING,
    SKIPPED_STATUS,
    SUCCEEDED,
    assert_plan_compatible,
    new_checkpoint,
    recover_interrupted,
    resume_candidates,
    transition,
    stable_hash,
)


IDENTITY_KEYS = (
    "source_id",
    "logical_code",
    "code",
    "id",
    "name",
    "nombre",
)
URL_KEYS = (
    "url",
    "base_url",
    "start_url",
    "entrypoint",
    "site_url",
    "source_url",
)


def load_yaml(path: str | Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_plan(path: str | Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise ValueError("Plan operacional inválido")
    rows = value.get("sources")
    if not isinstance(rows, list) or len(rows) != 52:
        raise ValueError("Plan operacional debe contener 52 fuentes")
    return value


def load_execution_map(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.exists():
        return None
    value = load_yaml(candidate)
    if not isinstance(value, dict):
        raise ValueError("Execution map inválido")
    return value


def execution_map_index(
    execution_map: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not execution_map:
        return {}
    rows = execution_map.get("sources")
    if not isinstance(rows, list):
        raise ValueError("Execution map sin sources")
    return {
        str(row["logical_source_id"]): row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("logical_source_id"), str)
        and row.get("logical_source_id")
    }


def _container(config: Any) -> tuple[str, Any]:
    if isinstance(config, list):
        return "top_list", config

    if not isinstance(config, dict):
        raise ValueError("Config de fuentes debe ser mapping o list")

    sources = config.get("sources")
    if isinstance(sources, list):
        return "sources_list", sources
    if isinstance(sources, dict):
        return "sources_dict", sources

    raise ValueError(
        "No se encontró contenedor de fuentes compatible "
        "(list, sources:list o sources:mapping)"
    )


def iter_entries(config: Any) -> list[tuple[str | None, dict[str, Any]]]:
    kind, container = _container(config)

    if kind in {"top_list", "sources_list"}:
        return [
            (None, item)
            for item in container
            if isinstance(item, dict)
        ]

    return [
        (str(key), item)
        for key, item in container.items()
        if isinstance(item, dict)
    ]


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _identity_values(
    key: str | None,
    entry: dict[str, Any],
) -> set[str]:
    values = set()
    if key:
        values.add(_norm(key))
    for field in IDENTITY_KEYS:
        value = entry.get(field)
        if value is not None:
            values.add(_norm(value))
    return {value for value in values if value}


def _url_values(entry: dict[str, Any]) -> set[str]:
    values = set()
    for field in URL_KEYS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            values.add(value.strip().rstrip("/").casefold())
    return values


def find_config_source_id(
    config_source_id: str,
    config: Any,
) -> tuple[str | None, dict[str, Any]]:
    target = _norm(config_source_id)
    matches = [
        (key, entry)
        for key, entry in iter_entries(config)
        if target in _identity_values(key, entry)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(
            f"config_source_id {config_source_id!r} no existe en sources config"
        )
    raise ValueError(
        f"config_source_id {config_source_id!r} tiene {len(matches)} matches"
    )


def match_source_entry(
    plan_source: dict[str, Any],
    config: Any,
    execution_map: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    map_index = execution_map_index(execution_map)
    source_id = str(plan_source.get("source_id") or "")

    mapped = map_index.get(source_id)
    if mapped is not None:
        config_source_id = mapped.get("config_source_id")
        if not isinstance(config_source_id, str) or not config_source_id:
            raise ValueError(
                f"{source_id}: execution map sin config_source_id"
            )
        return find_config_source_id(config_source_id, config)

    identity_targets = {
        _norm(plan_source.get("source_id")),
        _norm(plan_source.get("logical_code")),
        _norm(plan_source.get("name")),
    }
    identity_targets.discard("")

    entries = iter_entries(config)

    identity_matches = [
        (key, entry)
        for key, entry in entries
        if identity_targets & _identity_values(key, entry)
    ]

    if len(identity_matches) == 1:
        return identity_matches[0]
    if len(identity_matches) > 1:
        exact = [
            item
            for item in identity_matches
            if _norm(plan_source.get("source_id"))
            in _identity_values(item[0], item[1])
        ]
        if len(exact) == 1:
            return exact[0]
        raise ValueError(
            f"{source_id}: match ambiguo por identidad"
        )

    url_targets = set()
    for field in ("effective_entrypoint", "historical_entrypoint"):
        value = plan_source.get(field)
        if isinstance(value, str) and value.strip():
            url_targets.add(
                value.strip().rstrip("/").casefold()
            )

    url_matches = [
        (key, entry)
        for key, entry in entries
        if url_targets & _url_values(entry)
    ]

    if len(url_matches) == 1:
        return url_matches[0]
    if len(url_matches) > 1:
        raise ValueError(
            f"{source_id}: match ambiguo por URL"
        )

    raise KeyError(
        f"{source_id}: no existe en config de fuentes"
    )


def build_single_source_config(
    config: Any,
    matched_key: str | None,
    matched_entry: dict[str, Any],
) -> Any:
    kind, _ = _container(config)
    entry = deepcopy(matched_entry)

    if kind == "top_list":
        return [entry]

    result = deepcopy(config)
    if kind == "sources_list":
        result["sources"] = [entry]
        return result

    if matched_key is None:
        raise ValueError("sources:mapping requiere key")
    result["sources"] = {matched_key: entry}
    return result


def operational_sources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in plan.get("sources", [])
        if isinstance(row, dict)
        and row.get("next_phase") == "OPERATIONAL_CONFIG"
    ]


def validate_source_mapping(
    plan: dict[str, Any],
    source_config: Any,
    execution_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = operational_sources(plan)
    matched = []
    failures = []

    for row in rows:
        source_id = str(row.get("source_id"))
        try:
            key, entry = match_source_entry(
                row,
                source_config,
                execution_map,
            )
            matched.append(
                {
                    "logical_source_id": source_id,
                    "config_source_id": entry.get("source_id"),
                    "mapping_key": key,
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "source_id": source_id,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    return {
        "operational_sources": len(rows),
        "matched_sources": len(matched),
        "unmatched_sources": len(failures),
        "matches": matched,
        "failures": failures,
    }


def default_executor(
    *,
    python_executable: str,
    single_config_path: Path,
    output_dir: Path,
    catalog_dir: Path | None = None,
    force: bool = False,
    pipeline_run_id: str | None = None,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        "-m",
        "apps.crawler_batch.main",
        "--config",
        str(single_config_path),
        "--output-dir",
        str(output_dir),
        "--resume-discovery",
    ]
    if pipeline_run_id is not None:
        command.extend(["--pipeline-run-id", pipeline_run_id])
    if catalog_dir is not None:
        command.extend(["--catalog-dir", str(catalog_dir)])
    if force:
        command.append("--force")
    print("> " + subprocess.list2cmdline(command), flush=True)
    import psutil
    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    peak = None
    cpu_by_pid = {}
    samples = 0
    process = subprocess.Popen(command)
    try:
        while process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                tree = [parent] + parent.children(recursive=True)
                rss = 0
                for child in tree:
                    try:
                        rss += child.memory_info().rss
                        ticks = child.cpu_times()
                        cpu_by_pid[(child.pid, child.create_time())] = ticks.user + ticks.system
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                peak = max(peak or 0, rss)
                samples += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    finally:
        # La cancelación externa no deja un hijo escribiendo después del supervisor.
        if process.poll() is None:
            process.terminate()
            process.wait()
        payload = {
            "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - clock,
            "cpu_seconds_sampled": sum(cpu_by_pid.values()) if cpu_by_pid else None,
            "rss_peak_sampled_bytes": peak, "samples": samples,
            "measurement": "sampled_process_tree" if samples else "NO_MEDIDO",
        }
        directory = output_dir / "telemetry"
        directory.mkdir(exist_ok=True)
        (directory / f"{uuid.uuid4().hex}.json").write_text(json.dumps(payload), encoding="utf-8")
    return int(process.returncode)


def _partial_crawl_evidence(
    *,
    output_dir: Path,
    known_reports: set[Path],
    expected_source_id: str,
) -> dict[str, Any] | None:
    """Acknowledge RC=3 only when a NEW report proves partial success.

    Old reports in a resumed source directory must never turn a crashed
    subprocess into an apparent partial success.
    """
    report_dir = output_dir / "reports"
    new_reports = sorted(
        (p for p in report_dir.glob("run_*.json") if p not in known_reports),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
        reverse=True,
    ) if report_dir.is_dir() else []
    for path in new_reports:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
            continue
        results = value.get("source_results")
        if not isinstance(results, list) or len(results) != 1:
            continue
        result = results[0]
        if (
            not isinstance(result, dict)
            or result.get("source_id") != expected_source_id
            or result.get("execution_status") != "PARTIAL_SUCCESS"
        ):
            continue
        coverage = result.get("coverage")
        if not isinstance(coverage, dict):
            coverage = {}
        return {
            "crawler_run_id": value["run_id"],
            "report_path": str(path),
            "coverage_status": "PARTIAL_SUCCESS",
            "stop_reason": coverage.get("stop_reason"),
            "urls_pending": coverage.get("urls_pending"),
            "urls_failed": coverage.get("urls_failed"),
            "resources_found": coverage.get("resources_found"),
        }
    return None


def execution_config_fingerprint(single_config: Any) -> str:
    """La identidad del trabajo incluye el perfil efectivo, no el presupuesto renovable."""
    rows = []
    for key, entry in iter_entries(single_config):
        if "entrypoint" in entry:
            from prospector_externo.domain.discovery import DiscoveryFrontier
            from prospector_externo.domain.models import SourceConfig
            rows.append(DiscoveryFrontier.profile_fingerprint(SourceConfig.model_validate(entry)))
        else:
            rows.append((key, entry))  # Compatibilidad con configuraciones históricas.
    return stable_hash(rows)


def _acquisition_evidence(directory: Path, logical_id: str):
    from prospector_externo.application.catalog_sync import read_evidence
    try:
        item = read_evidence(directory, logical_id)
        if item is not None and item.observation.execution_status.value in {"SUCCESS", "PARTIAL_SUCCESS"}:
            return item
    except (OSError, ValueError):
        pass  # No reutilizar evidencia incompleta ni reconstruir resultados supuestos.
    return None


def run_checkpointed_sources(
    *,
    plan: dict[str, Any],
    source_config: Any,
    checkpoint: dict[str, Any],
    store: Any,
    work_dir: Path,
    output_root: Path,
    catalog_dir: Path | None = None,
    force: bool = False,
    execution_map: dict[str, Any] | None = None,
    max_sources: int | None = None,
    fail_fast: bool = False,
    executor: Callable[..., int] = default_executor,
    python_executable: str = sys.executable,
    selected_source_ids: list[str] | None = None,
    respect_eligibility: bool = False,
    resume: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assert_plan_compatible(checkpoint, plan)

    checkpoint, recovered = recover_interrupted(checkpoint)
    if recovered:
        store.save(checkpoint)

    plan_index = {
        row["source_id"]: row
        for row in operational_sources(plan)
    }
    map_index = execution_map_index(execution_map)
    holds = {sid: row["acquisition_hold"] for sid, row in map_index.items()
             if sid in plan_index and row.get("acquisition_hold")}
    for source_id, reason in holds.items():
        if checkpoint["sources"][source_id]["state"] == PENDING:
            checkpoint = transition(checkpoint, source_id, SKIPPED_STATUS,
                                    metadata={"skip_reason": reason, "policy_hold": True})
            store.save(checkpoint)
    prepared = {}
    legacy_unverified = []
    for source_id, row in plan_index.items():
        key, entry = match_source_entry(row, source_config, execution_map)
        single = build_single_source_config(source_config, key, entry)
        fingerprint = execution_config_fingerprint(single)
        prepared[source_id] = (entry, single, fingerprint)
        previous = checkpoint["sources"][source_id]
        if previous["state"] == SUCCEEDED:
            metadata = previous.get("metadata", {})
            old_fingerprint = metadata.get("config_fingerprint")
            legacy_path = metadata.get("single_config")
            if old_fingerprint is None and legacy_path and Path(legacy_path).is_file():
                old_fingerprint = execution_config_fingerprint(load_yaml(legacy_path))
            if old_fingerprint is not None and old_fingerprint != fingerprint:
                raise ValueError(f"SOURCE_CONFIG_CHANGED {source_id}: SUCCEEDED pertenece a otro perfil; use un run-id nuevo")
            if old_fingerprint is None:
                legacy_unverified.append(source_id)

    recovered_eligibility_skips = []
    if resume:
        # Repair only the legacy false terminal skip: a failed acquisition was
        # marked NOT_DUE on resume. Never reopen status-only, held or completed
        # sources, nor a source whose acquisition configuration has changed.
        for sid, (_, _, fingerprint) in prepared.items():
            previous = checkpoint["sources"][sid]
            metadata = previous.get("metadata", {})
            if (sid not in holds and previous["state"] == SKIPPED_STATUS and previous.get("attempts", 0) > 0
                    and previous.get("last_error", "") and not metadata.get("policy_hold")
                    and str(metadata.get("skip_reason", "")).startswith("NOT_DUE:")
                    and metadata.get("exit_code") not in {None, 0, 3}
                    and metadata.get("config_fingerprint") == fingerprint
                    and (selected_source_ids is None or sid in selected_source_ids)):
                checkpoint = deepcopy(checkpoint)
                previous = checkpoint["sources"][sid]
                previous["state"] = FAILED
                previous["metadata"]["recovered_eligibility_skip"] = {
                    "skip_reason": previous["metadata"].pop("skip_reason"),
                    "skip_report_run_id": metadata.get("skip_report_run_id"),
                    "last_error": previous["last_error"],
                }
                checkpoint["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
                recovered_eligibility_skips.append(sid)
        if recovered_eligibility_skips:
            store.save(checkpoint)

    candidates = [
        source_id
        for source_id in resume_candidates(
            checkpoint,
            include_failed=True,
        )
        if source_id in plan_index and source_id not in holds
        and (selected_source_ids is None or source_id in selected_source_ids)
    ]

    skipped = {}
    if respect_eligibility and not force and catalog_dir is not None:
        from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
        from prospector_externo.application.status_gate import SourceStatusGate
        from prospector_externo.domain.models import SourceConfig
        catalog = LocalJsonRepositoryAdapter(catalog_dir)
        catalog.check_health()
        for sid in list(candidates):
            entry = prepared[sid][0]
            allowed, reason = SourceStatusGate.evaluate_eligibility(
                SourceConfig.model_validate(entry), catalog.get_source(entry["source_id"]),
                recovering_pipeline=bool(resume and checkpoint["sources"][sid].get("attempts", 0) > 0
                    and checkpoint["sources"][sid].get("metadata", {}).get("config_fingerprint") == prepared[sid][2]),
            )
            if not allowed:
                skipped[sid] = {"physical_source_id": entry["source_id"], "reason": reason}
                checkpoint = transition(checkpoint, sid, SKIPPED_STATUS,
                                        metadata={"skip_reason": reason, "config_source_id": entry["source_id"],
                                                  "config_fingerprint": prepared[sid][2]})
                store.save(checkpoint)
                candidates.remove(sid)
        if skipped:
            # Una omisión es observable por REST, sin modificar Source, cadencia o snapshots.
            from prospector_externo.domain.observations import Run, RunReport, SourceRunObservation
            now = datetime.now(timezone.utc)
            rid = "run_" + now.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
            unique = {row["physical_source_id"]: (sid, row) for sid, row in skipped.items()}
            observations = [SourceRunObservation(source_id=pid, run_id=rid,
                workflow=prepared[sid][0].get("workflow", "html"), execution_status="SKIPPED",
                content_status="NOT_EVALUATED", skip_reason=row["reason"])
                for pid, (sid, row) in unique.items()]
            catalog.save_run(Run(run_id=rid, pipeline_run_id=checkpoint["run_id"], started_at=now,
                finished_at=now, status="COMPLETED", selected_source_ids=list(unique),
                sources_selected=len(unique), sources_skipped=len(unique)))
            for observation in observations:
                catalog.save_source_observation(observation)
            catalog.save_report(RunReport(run_id=rid, started_at=now, sources_selected=len(unique),
                sources_skipped=len(unique), source_results=observations))
            for sid in skipped:
                checkpoint["sources"][sid]["metadata"]["skip_report_run_id"] = rid
            store.save(checkpoint)

    # El store ordena claves al serializar. Tras un restart, un alias PENDING
    # puede adelantarse a su adquisición PARTIAL: retomar primero esa frontera
    # evita repetir el prefijo físico. Se conserva el límite de fuentes del lote.
    map_index = execution_map_index(execution_map)
    for alias in list(candidates):
        if not map_index.get(alias, {}).get("reuse_acquisition"):
            continue
        donor = next((sid for sid in candidates
                      if prepared[sid][2] == prepared[alias][2]
                      and checkpoint["sources"][sid]["state"] == PARTIAL
                      and not map_index.get(sid, {}).get("reuse_acquisition")), None)
        if donor is not None and candidates.index(donor) > candidates.index(alias):
            candidates.remove(donor)
            candidates.insert(candidates.index(alias), donor)

    if max_sources is not None:
        if max_sources <= 0:
            raise ValueError("max_sources debe ser > 0")
        candidates = candidates[:max_sources]

    work_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = work_dir / "source_configs"
    configs_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    partial = 0
    failed = 0
    executed = []
    acquisitions_executed = 0
    reused = []
    acquisitions = {}
    # Solo evidencia completa puede reutilizarse entre invocaciones del mismo lote.
    if any(row.get("reuse_acquisition") for row in map_index.values()):
        for source_id, previous in checkpoint["sources"].items():
            if (source_id in prepared and previous["state"] == SUCCEEDED
                    and previous.get("metadata", {}).get("config_fingerprint") == prepared[source_id][2]):
                item = _acquisition_evidence(output_root / source_id, source_id)
                if (item is not None and item.observation.execution_status.value == "SUCCESS"
                        and item.run.pipeline_run_id == checkpoint["run_id"]):
                    acquisitions[prepared[source_id][2]] = (source_id, 0, item)

    for index, source_id in enumerate(candidates, 1):
        source = plan_index[source_id]
        matched_entry, single_config, fingerprint = prepared[source_id]

        config_path = configs_dir / f"{source_id}.yaml"
        config_path.write_text(
            yaml.safe_dump(
                single_config,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            ),
            encoding="utf-8",
        )

        source_output = output_root / source_id

        print(
            f"[{index:02d}/{len(candidates):02d}] "
            f"{source.get('logical_code') or source_id} "
            f"({source_id})",
            flush=True,
        )

        checkpoint = transition(
            checkpoint,
            source_id,
            RUNNING,
            metadata={
                "single_config": str(config_path),
                "output_dir": str(source_output),
                "config_source_id": matched_entry.get("source_id"),
                "config_fingerprint": fingerprint,
            },
        )
        store.save(checkpoint)

        # If this attempt produces no new report, stale previous evidence
        # must not be used to classify a non-zero exit code.
        reports_dir = source_output / "reports"
        known_reports = set(reports_dir.glob("run_*.json")) if reports_dir.is_dir() else set()
        known_telemetry = set((source_output / "telemetry").glob("*.json"))

        execution_options = dict(
            python_executable=python_executable,
            single_config_path=config_path,
            output_dir=source_output,
            catalog_dir=catalog_dir,
            force=force,
        )
        if executor is default_executor:
            execution_options["pipeline_run_id"] = checkpoint["run_id"]
        cached = acquisitions.get(fingerprint) if map_index.get(source_id, {}).get("reuse_acquisition") else None
        if cached is not None and cached[0] != source_id:
            from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
            from prospector_externo.application.catalog_sync import sync_evidence
            original, exit_code, evidence = cached
            target = LocalJsonRepositoryAdapter(source_output, catalog_dir=catalog_dir)
            sync_evidence(target, [evidence])
            reuse = {"logical_source_id": source_id, "acquisition_reused_from": original,
                     "crawler_run_id": evidence.run.run_id, "physical_source_id": evidence.source.source_id}
            checkpoint["sources"][source_id]["metadata"].update(reuse)
            reused.append(reuse)
        else:
            acquisitions_executed += 1
            exit_code = executor(**execution_options)
            if exit_code in {0, 3} and any(row.get("reuse_acquisition") for row in map_index.values()):
                evidence = _acquisition_evidence(source_output, source_id)
                if (evidence is not None and evidence.run.pipeline_run_id == checkpoint["run_id"]
                        and reports_dir / f"run_{evidence.run.run_id}.json" not in known_reports):
                    acquisitions[fingerprint] = (source_id, exit_code, evidence)

        executed.append(source_id)
        fresh_telemetry = set((source_output / "telemetry").glob("*.json")) - known_telemetry
        if fresh_telemetry:
            checkpoint["sources"][source_id]["metadata"]["telemetry"] = json.loads(
                max(fresh_telemetry, key=lambda p: p.stat().st_mtime_ns).read_text(encoding="utf-8"))

        if exit_code == 0:
            checkpoint = transition(
                checkpoint,
                source_id,
                SUCCEEDED,
                metadata={"exit_code": 0},
            )
            succeeded += 1
        elif exit_code == 3 and (partial_evidence := _partial_crawl_evidence(
            output_dir=source_output,
            known_reports=known_reports,
            expected_source_id=str(matched_entry.get("source_id") or source_id),
        )) is not None:
            checkpoint = transition(
                checkpoint,
                source_id,
                PARTIAL,
                resource_count=partial_evidence["resources_found"],
                metadata={"exit_code": 3, **partial_evidence},
            )
            partial += 1
        else:
            checkpoint = transition(
                checkpoint,
                source_id,
                FAILED,
                error=f"crawler_batch_exit_code_{exit_code}",
                metadata={"exit_code": exit_code},
            )
            failed += 1

        store.save(checkpoint)

        if exit_code != 0 and fail_fast:
            break

    report = {
        "run_id": checkpoint["run_id"],
        "recovered_interrupted": recovered,
        "recovered_eligibility_skips": recovered_eligibility_skips,
        "candidate_count": len(candidates),
        "executed_source_ids": executed,
        "succeeded": succeeded,
        "partial": partial,
        "failed": failed,
        "acquisitions_executed": acquisitions_executed,
        "acquisitions_reused": reused,
        "legacy_config_unverified": legacy_unverified,
        "acquisition_holds": holds,
        "skipped_eligibility": skipped,
        "remaining_resume_candidates": [sid for sid in resume_candidates(
            checkpoint,
            include_failed=True,
        ) if sid not in holds and (selected_source_ids is None or sid in selected_source_ids)],
    }

    return checkpoint, report


def initialize_or_resume(
    *,
    plan: dict[str, Any],
    store: Any,
    run_id: str | None,
    resume: bool,
) -> tuple[dict[str, Any], bool]:
    if resume:
        checkpoint = store.load(run_id)
        assert_plan_compatible(checkpoint, plan)
        return checkpoint, False

    checkpoint = new_checkpoint(
        plan,
        run_id=run_id,
    )
    store.save(checkpoint)
    return checkpoint, True


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
