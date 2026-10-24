from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.b13_pipeline.main import (
    build_package,
    exact_duplicate_groups,
    inspect_and_project,
    legacy_records,
    package_ready,
    save_state,
    command_run,
    prepare_checkpoint_config,
)


def _record():
    return {
        "descripcion": "Dataset",
        "url_descarga": "https://example.test/a.csv",
        "fecha_actualizacion": "2026-09",
        "tipo_archivo": "CSV",
        "url_origen": "https://example.test/",
        "metodo_deteccion": "html_link",
    }


def _checkpoint_plan():
    return {"sources": [{"source_id": f"s{i}", "next_phase": "OPERATIONAL_CONFIG"}
                        for i in range(52)]}


def test_b13_checkpoints_are_isolated_between_run_ids(tmp_path):
    from src.persistence.model import new_checkpoint
    from src.persistence.store import JsonCheckpointStore

    config_one = prepare_checkpoint_config(tmp_path, run_id="first", resume=False)
    path_one = Path(json.loads(config_one.read_text(encoding="utf-8"))["json"]["path"])
    JsonCheckpointStore(path_one).save(new_checkpoint(_checkpoint_plan(), run_id="first"))
    before = path_one.read_bytes()
    config_two = prepare_checkpoint_config(tmp_path, run_id="second", resume=False)
    path_two = Path(json.loads(config_two.read_text(encoding="utf-8"))["json"]["path"])
    JsonCheckpointStore(path_two).save(new_checkpoint(_checkpoint_plan(), run_id="second"))
    assert path_one != path_two
    assert path_one.read_bytes() == before
    assert prepare_checkpoint_config(tmp_path, run_id="first", resume=True) == config_one
    with pytest.raises(FileExistsError, match="--resume"):
        prepare_checkpoint_config(tmp_path, run_id="first", resume=False)
    assert path_one.read_bytes() == before


def test_b13_resume_copies_only_matching_legacy_checkpoint(tmp_path):
    from src.persistence.model import new_checkpoint
    from src.persistence.store import JsonCheckpointStore

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "checkpointing.yaml").write_text(
        'json:\n  path: .runtime/checkpoints/latest.json\n', encoding="utf-8",
    )
    legacy_path = tmp_path / ".runtime/checkpoints/latest.json"
    JsonCheckpointStore(legacy_path).save(new_checkpoint(_checkpoint_plan(), run_id="old"))
    before = legacy_path.read_bytes()
    with pytest.raises(ValueError, match="no coincide"):
        prepare_checkpoint_config(tmp_path, run_id="different", resume=True)
    assert not (tmp_path / ".runtime/b13_pipeline/different").exists()
    config = prepare_checkpoint_config(tmp_path, run_id="old", resume=True)
    copied = Path(json.loads(config.read_text(encoding="utf-8"))["json"]["path"])
    assert JsonCheckpointStore(copied).load("old")["run_id"] == "old"
    assert legacy_path.read_bytes() == before


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "", "x" * 97])
def test_b13_invalid_run_id_is_rejected_before_any_io(tmp_path, run_id):
    with pytest.raises(ValueError, match="--run-id"):
        command_run(tmp_path, run_id=run_id, resume=False, max_sources=1, sync_mongo=True)
    assert list(tmp_path.iterdir()) == []


def test_b13_uses_run_scoped_checkpoint_in_child_command(tmp_path):
    import yaml

    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": [{"source_id": "a", "next_phase": "OPERATIONAL_CONFIG"}]}),
        encoding="utf-8",
    )
    with patch("apps.b13_pipeline.main.run_command", return_value=subprocess.CompletedProcess([], 0)) as run:
        assert command_run(tmp_path, run_id="isolated", resume=False, max_sources=1) == 3
    cmd = run.call_args.args[0]
    config_path = Path(cmd[cmd.index("--checkpoint-config") + 1])
    assert config_path.parent == tmp_path / ".runtime/b13_pipeline/isolated"
    assert Path(json.loads(config_path.read_text(encoding="utf-8"))["json"]["path"]).name == "checkpoint.json"


@pytest.mark.parametrize("returncode", [1, 2])
def test_b13_propagates_child_failure_without_evidence(tmp_path, returncode):
    import yaml

    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": [{"source_id": "a", "next_phase": "OPERATIONAL_CONFIG"}]}),
        encoding="utf-8",
    )
    with patch("apps.b13_pipeline.main.run_command", return_value=subprocess.CompletedProcess([], returncode)):
        assert command_run(tmp_path, run_id="failed", resume=False, max_sources=1) == 2


def test_b13_returns_partial_exit_code_even_when_package_is_consumable(tmp_path):
    import yaml

    (tmp_path / "config").mkdir()
    (tmp_path / "config/source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": [{"source_id": "a", "next_phase": "OPERATIONAL_CONFIG"}]}),
        encoding="utf-8",
    )
    rows = [{"logical_source_id": "a", "classification": "DATAX_READY",
             "execution_status": "PARTIAL_SUCCESS", "coverage_complete": False}]
    with patch("apps.b13_pipeline.main.run_command", return_value=subprocess.CompletedProcess([], 2)), \
            patch("apps.b13_pipeline.main.inspect_and_project", return_value=rows), \
            patch("apps.b13_pipeline.main.build_package", return_value={"package_ready": True}), \
            patch("apps.b13_pipeline.main.print_summary"):
        assert command_run(tmp_path, run_id="partial", resume=False, max_sources=1) == 3


def test_legacy_records_accepts_historical_contract():
    document = {
        "ESTADISTICAS": {
            "CAT": {
                "RAMA": {
                    "a.csv": _record(),
                }
            }
        }
    }

    assert legacy_records(document) == 1


def test_package_ready_accepts_terminal_operational_states():
    rows = []

    for index in range(42):
        rows.append(
            {
                "logical_source_id": f"ready_{index}",
                "classification": "DATAX_READY",
            }
        )

    rows.extend(
        [
            {
                "logical_source_id": "transtats",
                "classification": "ACQUISITION_JOB_READY",
            },
            {
                "logical_source_id": "mhe",
                "classification": "EXTERNAL_BLOCKER",
            },
            {
                "logical_source_id": "sigma",
                "classification": "EXTERNAL_BLOCKER",
            },
        ]
    )

    assert package_ready(rows) is True


def test_package_ready_rejects_partial_run():
    rows = [
        {
            "logical_source_id": f"s{i}",
            "classification": "DATAX_READY",
        }
        for i in range(40)
    ]
    rows.append(
        {
            "logical_source_id": "pending",
            "classification": "PENDING",
        }
    )

    assert package_ready(rows) is False


def test_historical_success_with_incomplete_coverage_is_not_packaged(tmp_path: Path):
    evidence = tmp_path / "output" / "checkpointed" / "smoke" / "anapo"
    reports = evidence / "reports"
    reports.mkdir(parents=True)
    (reports / "run_old.json").write_text(json.dumps({
        "source_results": [{
            "source_id": "anapo", "execution_status": "SUCCESS",
            "coverage": {"stop_reason": "MAX_CONSECUTIVE_ERRORS", "urls_pending": 77},
        }],
    }), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="smoke", roster=["anapo"])
    assert rows[0]["classification"] == "PARTIAL_COVERAGE"
    assert not package_ready(rows)

    (reports / "run_old.json").write_text(json.dumps({
        "source_results": [{
            "source_id": "anapo", "execution_status": "FAILED",
            "coverage": {"stop_reason": "MAX_CONSECUTIVE_ERRORS"},
        }],
    }), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="smoke", roster=["anapo"])
    assert rows[0]["classification"] == "EXECUTION_FAILED"


@pytest.mark.parametrize("metrics", [
    {"urls_pending": 2}, {"urls_failed": 1}, {"archive_inspection_deferred": 1},
    {"api_pagination_stopped": 1}, {"http_403": 1}, {"resources_limit_reached": 1},
])
def test_b13_detects_incomplete_legacy_success_from_all_domain_metrics(tmp_path, metrics):
    reports = tmp_path / "output/checkpointed/legacy/s1/reports"
    reports.mkdir(parents=True)
    (reports / "run_old.json").write_text(json.dumps({"source_results": [{
        "source_id": "s1", "execution_status": "SUCCESS",
        "coverage": {"stop_reason": "QUEUE_EXHAUSTED", **metrics},
    }]}), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="legacy", roster=["s1"])
    assert rows[0]["coverage_complete"] is False
    assert rows[0]["classification"] == "PARTIAL_COVERAGE"


def test_b13_does_not_invent_complete_coverage_without_metrics(tmp_path):
    reports = tmp_path / "output/checkpointed/legacy/s1/reports"
    reports.mkdir(parents=True)
    (reports / "run_old.json").write_text(json.dumps({"source_results": [{
        "source_id": "s1", "execution_status": "SUCCESS",
    }]}), encoding="utf-8")
    rows = inspect_and_project(repo_root=tmp_path, run_id="legacy", roster=["s1"])
    assert rows[0]["coverage_complete"] is None


def test_partial_crawl_with_valid_projection_is_deliverable_and_auditable(tmp_path: Path):
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": [{"source_id": "anapo", "next_phase": "OPERATIONAL_CONFIG"}]}),
        encoding="utf-8",
    )
    evidence = tmp_path / "output" / "checkpointed" / "smoke" / "anapo"
    reports = evidence / "reports"
    snapshots = evidence / "state" / "snapshots"
    reports.mkdir(parents=True)
    snapshots.mkdir(parents=True)
    (reports / "run_partial.json").write_text(json.dumps({
        "source_results": [{"source_id": "anapo", "execution_status": "PARTIAL_SUCCESS",
                            "failure_code": "REQUEST_BUDGET_REACHED",
                            "coverage": {"stop_reason": "REQUEST_BUDGET_REACHED",
                                         "urls_pending": 59, "urls_failed": 0}}],
    }), encoding="utf-8")
    (snapshots / "anapo_run_partial.json").write_text(json.dumps({
        "source_id": "anapo", "total_resources": 111, "resources": [],
    }), encoding="utf-8")
    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text(json.dumps({"ESTADISTICAS": {"NORMATIVA": {"doc": _record()}}}),
                           encoding="utf-8")

    with patch("apps.b13_pipeline.main.project_success", return_value={
        "classification": "DATAX_READY", "legacy_records": 1,
        "artifact": str(legacy_file), "projection_error": None,
    }):
        rows = inspect_and_project(repo_root=tmp_path, run_id="smoke", roster=["anapo"])

    assert package_ready(rows, expected_source_ids=["anapo"])
    assert rows[0]["execution_status"] == "PARTIAL_SUCCESS"
    assert rows[0]["coverage_complete"] is False
    package = build_package(repo_root=tmp_path, run_id="smoke", rows=rows)
    manifest = json.loads((Path(package["package_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["coverage_partial_sources"] == ["anapo"]
    assert manifest["sources"][0]["stop_reason"] == "REQUEST_BUDGET_REACHED"
    assert manifest["sources"][0]["urls_pending"] == 59
    state = save_state(repo_root=tmp_path, run_id="smoke", checkpoint_returncode=3,
                       rows=rows, package=package)
    assert state["status"] == "READY"
    assert state["coverage_partial_sources"] == ["anapo"]


def test_exact_duplicate_groups_classifies_shared_physical():
    rows = [
        {
            "logical_source_id": "a",
            "physical_source_id": "shared",
            "sha256": "hash",
        },
        {
            "logical_source_id": "b",
            "physical_source_id": "shared",
            "sha256": "hash",
        },
        {
            "logical_source_id": "c",
            "physical_source_id": "c",
            "sha256": "other",
        },
    ]

    groups = exact_duplicate_groups(rows)

    assert len(groups) == 1
    assert groups[0]["logical_source_ids"] == ["a", "b"]
    assert groups[0]["shared_physical_source"] is True


def test_exact_duplicate_groups_flags_cross_physical():
    rows = [
        {
            "logical_source_id": "a",
            "physical_source_id": "pa",
            "sha256": "hash",
        },
        {
            "logical_source_id": "b",
            "physical_source_id": "pb",
            "sha256": "hash",
        },
    ]

    groups = exact_duplicate_groups(rows)

    assert len(groups) == 1
    assert groups[0]["shared_physical_source"] is False


def test_operational_roster_uses_durable_plan_not_b11_runtime(tmp_path):
    import yaml
    from apps.b13_pipeline.main import operational_roster

    config = tmp_path / "config"
    config.mkdir()
    rows = [
        {
            "source_id": f"operational_{index}",
            "next_phase": "OPERATIONAL_CONFIG",
        }
        for index in range(41)
    ]
    rows.extend(
        {
            "source_id": f"status_{index}",
            "next_phase": "B10_STATUS",
        }
        for index in range(11)
    )
    (config / "source_operational_plan.yaml").write_text(
        yaml.safe_dump({"sources": rows}, sort_keys=False),
        encoding="utf-8",
    )

    result = operational_roster(tmp_path)
    assert len(result) == 41
    assert "operational_0" in result
    assert not (tmp_path / ".runtime" / "b11_output_audit" / "latest.json").exists()
