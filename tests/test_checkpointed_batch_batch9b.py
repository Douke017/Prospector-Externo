from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from src.execution.checkpointed_batch import (
    build_single_source_config,
    default_executor,
    initialize_or_resume,
    match_source_entry,
    run_checkpointed_sources,
    validate_source_mapping,
)
from src.persistence.model import (
    FAILED,
    PENDING,
    SUCCEEDED,
    new_checkpoint,
)
from src.persistence.store import JsonCheckpointStore


def plan_fixture():
    rows = []
    for i in range(52):
        operational = i < 41
        rows.append(
            {
                "source_id": f"s{i}",
                "logical_code": f"S{i}",
                "name": f"Source {i}",
                "effective_entrypoint": f"https://example.org/{i}",
                "historical_entrypoint": f"https://old.example.org/{i}",
                "operational_status": (
                    "OPERATIONAL_HTTP_HTML"
                    if operational
                    else "NO_PUBLIC_DATA_EVIDENCE"
                ),
                "workflow_strategy": "html" if operational else None,
                "operational_config": None,
                "next_phase": (
                    "OPERATIONAL_CONFIG"
                    if operational
                    else "B10_STATUS"
                ),
            }
        )
    return {"sources": rows}


def source_config_fixture():
    return {
        "schema_version": "fixture",
        "sources": [
            {
                "source_id": f"s{i}",
                "logical_code": f"S{i}",
                "url": f"https://example.org/{i}",
            }
            for i in range(52)
        ],
    }


def test_mapping_matches_all_41_operational():
    report = validate_source_mapping(
        plan_fixture(),
        source_config_fixture(),
    )
    assert report["operational_sources"] == 41
    assert report["matched_sources"] == 41
    assert report["unmatched_sources"] == 0


def test_match_by_logical_code():
    plan_source = {
        "source_id": "different-id",
        "logical_code": "ABC",
        "name": "Something",
    }
    config = {
        "sources": [
            {
                "logical_code": "ABC",
                "url": "https://example.org",
            }
        ]
    }
    key, entry = match_source_entry(plan_source, config)
    assert key is None
    assert entry["logical_code"] == "ABC"


def test_single_source_config_preserves_envelope():
    config = {
        "schema_version": "fixture",
        "settings": {"budget": 5},
        "sources": [
            {"source_id": "a"},
            {"source_id": "b"},
        ],
    }
    single = build_single_source_config(
        config,
        None,
        {"source_id": "b"},
    )
    assert single["settings"] == {"budget": 5}
    assert single["sources"] == [{"source_id": "b"}]


def test_successful_execution_marks_succeeded(tmp_path: Path):
    plan = plan_fixture()
    config = source_config_fixture()
    checkpoint = new_checkpoint(plan, run_id="run-1")
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)

    def executor(**kwargs):
        assert kwargs["catalog_dir"] == tmp_path / "catalog"
        assert kwargs["force"] is True
        return 0

    updated, report = run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=checkpoint,
        store=store,
        work_dir=tmp_path / "work",
        output_root=tmp_path / "out",
        catalog_dir=tmp_path / "catalog",
        force=True,
        max_sources=1,
        executor=executor,
    )

    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert updated["sources"]["s0"]["state"] == SUCCEEDED


def test_failed_execution_is_resume_candidate(tmp_path: Path):
    plan = plan_fixture()
    config = source_config_fixture()
    checkpoint = new_checkpoint(plan, run_id="run-1")
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)

    def executor(**kwargs):
        return 7

    updated, report = run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=checkpoint,
        store=store,
        work_dir=tmp_path / "work",
        output_root=tmp_path / "out",
        max_sources=1,
        executor=executor,
    )

    assert report["failed"] == 1
    assert updated["sources"]["s0"]["state"] == FAILED
    assert "s0" in report["remaining_resume_candidates"]

    resumed, resumed_report = run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=updated,
        store=store,
        work_dir=tmp_path / "resume_work",
        output_root=tmp_path / "resume_out",
        max_sources=1,
        executor=lambda **kwargs: 0,
    )
    assert resumed_report["executed_source_ids"] == ["s1"]
    assert resumed["sources"]["s0"]["attempts"] == 1
    assert resumed["sources"]["s1"]["state"] == SUCCEEDED


def test_resume_does_not_reexecute_succeeded(tmp_path: Path):
    plan = plan_fixture()
    config = source_config_fixture()
    checkpoint = new_checkpoint(plan, run_id="run-1")
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)

    calls = []

    def executor(**kwargs):
        calls.append(kwargs["single_config_path"])
        return 0

    first, _ = run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=checkpoint,
        store=store,
        work_dir=tmp_path / "work1",
        output_root=tmp_path / "out1",
        max_sources=1,
        executor=executor,
    )

    second, _ = run_checkpointed_sources(
        plan=plan,
        source_config=config,
        checkpoint=first,
        store=store,
        work_dir=tmp_path / "work2",
        output_root=tmp_path / "out2",
        max_sources=1,
        executor=executor,
    )

    assert len(calls) == 2
    assert first["sources"]["s0"]["state"] == SUCCEEDED
    assert second["sources"]["s0"]["state"] == SUCCEEDED
    assert second["sources"]["s1"]["state"] == SUCCEEDED


def test_initialize_or_resume_roundtrip(tmp_path: Path):
    plan = plan_fixture()
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")

    created, is_new = initialize_or_resume(
        plan=plan,
        store=store,
        run_id="run-1",
        resume=False,
    )
    assert is_new is True
    assert created["sources"]["s0"]["state"] == PENDING

    resumed, is_new = initialize_or_resume(
        plan=plan,
        store=store,
        run_id="run-1",
        resume=True,
    )
    assert is_new is False
    assert resumed["run_id"] == "run-1"


def test_default_executor_passes_shared_catalog_and_force(tmp_path: Path):
    with patch("src.execution.checkpointed_batch.subprocess.Popen") as run:
        run.return_value.poll.return_value = 0
        run.return_value.returncode = 0
        assert default_executor(
            python_executable="python", single_config_path=tmp_path / "single.yaml",
            output_dir=tmp_path / "evidence", catalog_dir=tmp_path / "catalog",
            force=True,
        ) == 0
    command = run.call_args.args[0]
    assert command[command.index("--output-dir") + 1] == str(tmp_path / "evidence")
    assert command[command.index("--catalog-dir") + 1] == str(tmp_path / "catalog")
    assert "--force" in command
