"""Pruebas de integración del orquestador hexagonal y checkpointing incremental."""

import asyncio
import tempfile
import yaml
from pathlib import Path
from unittest.mock import MagicMock

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.observations import ExecutionStatus, ContentStatus, RunStatus
from prospector_externo.kernel.contracts import ExtractionResult
from prospector_externo.domain.models import ResourceCandidate, DiscoveredUrl


def test_orchestrator_incremental_runs_and_change_detection(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        output_dir = tmp_path / "output"
        config_file = tmp_path / "sources.yaml"

        config_data = {
            "sources": [
                {
                    "source_id": "test_src",
                    "name": "Fuente de Prueba",
                    "entrypoint": "https://test.gob.bo",
                    "workflow": "html",
                    "update_category": "DAILY"
                }
            ]
        }
        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        repo = LocalJsonRepositoryAdapter(base_output_dir=output_dir)
        orchestrator = RunOrchestrator(catalog_repo=repo, report_repo=repo, config_path=config_file)

        # Mock del workflow dispatch
        sample_resources = [
            ResourceCandidate(
                resource_key="key_1",
                url="https://test.gob.bo/doc1.pdf",
                source_id="test_src",
                title="Doc 1",
                file_extension=".pdf"
            )
        ]

        mock_dispatch = MagicMock()
        mock_dispatch.return_value = ExtractionResult(
            source_id="test_src",
            success=True,
            resources=sample_resources,
            discovered_urls=[DiscoveredUrl(
                normalized_url="https://test.gob.bo/publicaciones",
                raw_url="/publicaciones", source_id="test_src",
                parent_url="https://test.gob.bo", depth=1,
            )],
        )
        monkeypatch.setattr(orchestrator.dispatcher, "dispatch", mock_dispatch)

        # 1. Primera Corrida: Debe ser SUCCESS y CHANGED
        report1 = orchestrator.run_batch(force=True)
        assert report1.sources_processed == 1
        assert report1.sources_failed == 0
        assert report1.sources_with_changes == 1
        obs1 = report1.source_results[0]
        assert obs1.execution_status == ExecutionStatus.SUCCESS
        assert obs1.content_status == ContentStatus.CHANGED

        # Validar que los archivos de salida existen en output/test_src/
        assert (output_dir / "test_src" / "mapa_test_src.json").exists()
        assert (output_dir / "test_src" / "mapa_test_src_compact.json").exists()
        assert (output_dir / "test_src" / "mapa_test_src_tree.json").exists()
        assert (output_dir / "reports" / f"run_{report1.run_id}.json").exists()
        assert repo.get_run(report1.run_id).status == RunStatus.COMPLETED
        assert [entry.raw_url for entry in repo.get_latest_snapshot("test_src").discovered_urls] == [
            "/publicaciones"
        ]

        # 2. Segunda Corrida (sin cambios): Debe detectar NO_CHANGE
        report2 = orchestrator.run_batch(force=True)
        assert report2.sources_processed == 1
        assert report2.sources_with_changes == 0
        assert report2.sources_without_changes == 1
        obs2 = report2.source_results[0]
        assert obs2.content_status == ContentStatus.NO_CHANGE


def test_run_is_running_before_workflow_finishes(tmp_path, monkeypatch):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [{
        "source_id": "a", "entrypoint": "https://example.test/a", "workflow": "html",
    }]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    runner = RunOrchestrator(repo, repo, config_path=cfg)
    observed = []

    def dispatch(config):
        runs = repo.list_runs()
        assert len(runs) == 1
        observed.append(runs[0].status)
        assert repo.get_report(runs[0].run_id) is None
        return ExtractionResult(source_id=config.source_id, success=True, resources=[])

    monkeypatch.setattr(runner.dispatcher, "dispatch", dispatch)
    report = runner.run_batch(force=True)
    assert observed == [RunStatus.RUNNING]
    record = repo.get_run(report.run_id)
    assert record.status == RunStatus.COMPLETED
    assert record.finished_at is not None
    assert record.selected_source_ids == ["a"]
    assert not record.legacy_inferred


def test_run_failure_preserves_checkpoint_of_completed_source(tmp_path, monkeypatch):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [
        {"source_id": "first", "entrypoint": "https://example.test/first"},
        {"source_id": "second", "entrypoint": "https://example.test/second"},
    ]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    runner = RunOrchestrator(repo, repo, config_path=cfg, max_source_concurrency=1)
    calls = []

    def dispatch(config):
        calls.append(config.source_id)
        if config.source_id == "second":
            raise RuntimeError("interrupcion simulada de workflow")
        return ExtractionResult(source_id="first", success=True, resources=[
            ResourceCandidate(source_id="first", resource_key="k", url="https://example.test/a.pdf")
        ])

    monkeypatch.setattr(runner.dispatcher, "dispatch", dispatch)
    import pytest
    with pytest.raises(RuntimeError, match="interrupcion simulada"):
        runner.run_batch(force=True)
    assert calls == ["first", "second"]
    run = repo.list_runs()[0]
    assert run.status == RunStatus.FAILED
    assert run.failure_code == "RuntimeError"
    assert run.finished_at is not None
    assert run.sources_processed == 1
    assert repo.get_latest_snapshot("first").resources[0].resource_key == "k"
    assert repo.get_source_observations("first")[0].run_id == run.run_id
    assert repo.get_report(run.run_id) is None


def test_async_cancel_marks_run_interrupted_without_report(tmp_path, monkeypatch):
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [
        {"source_id": "slow", "entrypoint": "https://example.test/slow"},
    ]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    runner = RunOrchestrator(repo, repo, config_path=cfg)

    async def scenario():
        started = asyncio.Event()
        never = asyncio.Event()

        async def dispatch(_config):
            started.set()
            await never.wait()

        monkeypatch.setattr(runner.dispatcher, "dispatch", dispatch)
        task = asyncio.create_task(runner.run_batch_async(force=True))
        await started.wait()
        run_id = repo.list_runs()[0].run_id
        assert repo.get_run(run_id).status == RunStatus.RUNNING
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("La cancelación debía propagarse")
        run = LocalJsonRepositoryAdapter(tmp_path / "output").get_run(run_id)
        assert run.status == RunStatus.INTERRUPTED
        assert run.failure_code == "CancelledError"
        assert run.finished_at is not None
        assert repo.get_report(run_id) is None

    asyncio.run(scenario())


def test_partial_success_marks_run_partial_without_losing_report(tmp_path, monkeypatch):
    from prospector_externo.domain.observations import CoverageStats

    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [
        {"source_id": "partial", "entrypoint": "https://example.test/partial"},
    ]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    runner = RunOrchestrator(repo, repo, config_path=cfg)
    monkeypatch.setattr(runner.dispatcher, "dispatch", lambda _: ExtractionResult(
        source_id="partial", success=True,
        coverage=CoverageStats(stop_reason="REQUEST_BUDGET_REACHED", urls_pending=2),
        resources=[ResourceCandidate(
            source_id="partial", resource_key="pdf",
            url="https://example.test/informe.pdf")],
    ))
    report = runner.run_batch(force=True)
    assert report.source_results[0].execution_status == ExecutionStatus.PARTIAL_SUCCESS
    restored = LocalJsonRepositoryAdapter(tmp_path / "output")
    assert restored.get_run(report.run_id).status == RunStatus.PARTIAL
    assert restored.get_report(report.run_id) is not None
    assert restored.list_resources("partial")[0].resource_key == "pdf"


def test_run_list_merges_historical_reports_without_mutating_them(tmp_path):
    from datetime import datetime, timedelta, timezone
    from prospector_externo.domain.observations import Run, RunReport

    repo = LocalJsonRepositoryAdapter(tmp_path)
    t0 = datetime(2026, 10, 8, tzinfo=timezone.utc)
    repo.save_report(RunReport(run_id="historical", started_at=t0))
    repo.save_run(Run(run_id="running", started_at=t0 + timedelta(hours=1)))
    repo.save_report(RunReport(run_id="running", started_at=t0 + timedelta(hours=1)))
    states = LocalJsonRepositoryAdapter(tmp_path).list_runs()
    assert [run.run_id for run in states] == ["running", "historical"]
    assert states[0].status == RunStatus.RUNNING  # El reporte no pisa el estado.
    assert states[1].legacy_inferred is True
    assert [run.run_id for run in repo.list_runs(limit=1)] == ["running"]
    assert not (tmp_path / "state" / "runs" / "run_historical.json").exists()


def test_orchestrator_enforces_browser_bulkhead_without_blocking_http(tmp_path, monkeypatch):
    """Dos Chromium máximo; los JS en espera no acaparan los slots HTTP."""
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [
        *[{"source_id": f"js{i}", "entrypoint": "https://example.test", "workflow": "javascript"}
          for i in range(5)],
        {"source_id": "html", "entrypoint": "https://example.test", "workflow": "html"},
    ]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    runner = RunOrchestrator(repo, repo, config_path=cfg, max_source_concurrency=4)

    async def scenario():
        first_browser_started = asyncio.Event()
        http_started = asyncio.Event()
        unblock = asyncio.Event()
        active_js = 0
        active_all = 0
        max_js = 0
        max_all = 0
        started_js = 0

        async def dispatch(config):
            nonlocal active_js, active_all, max_js, max_all, started_js
            active_all += 1
            max_all = max(max_all, active_all)
            if config.workflow == "javascript":
                active_js += 1
                started_js += 1
                max_js = max(max_js, active_js)
                if started_js == 2:
                    first_browser_started.set()
            else:
                http_started.set()
            try:
                if config.workflow == "javascript":
                    await unblock.wait()
                return ExtractionResult(source_id=config.source_id, success=True)
            finally:
                active_all -= 1
                if config.workflow == "javascript":
                    active_js -= 1

        monkeypatch.setattr(runner.dispatcher, "dispatch", dispatch)
        task = asyncio.create_task(runner.run_batch_async(force=True))
        try:
            await asyncio.wait_for(first_browser_started.wait(), timeout=2)
            # JS pendientes ya existen; HTML no puede quedar bloqueado por ellos.
            await asyncio.wait_for(http_started.wait(), timeout=2)
            assert max_js == 2
            assert max_all <= 4
        finally:
            unblock.set()
        report = await asyncio.wait_for(task, timeout=3)
        assert report.sources_processed == 6
        assert max_js == 2
        assert max_all <= 4

    asyncio.run(scenario())


def test_orchestrator_respects_injected_browser_pool_limit(tmp_path, monkeypatch):
    from prospector_externo.infrastructure.concurrency import AsyncWorkerPool

    cfg = tmp_path / "sources.yaml"
    cfg.write_text(yaml.safe_dump({"sources": [
        {"source_id": f"js{i}", "entrypoint": "https://example.test", "workflow": "javascript"}
        for i in range(3)
    ]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(tmp_path / "output")
    pool = AsyncWorkerPool(max_http_workers=4, max_browser_workers=1)
    runner = RunOrchestrator(repo, repo, config_path=cfg, worker_pool=pool)
    active = 0
    max_active = 0

    async def dispatch(config):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.005)
            return ExtractionResult(source_id=config.source_id, success=True)
        finally:
            active -= 1

    monkeypatch.setattr(runner.dispatcher, "dispatch", dispatch)
    report = runner.run_batch(force=True)
    assert report.sources_processed == 3
    assert max_active == 1
    pool.shutdown()


def test_recovery_never_marks_live_owner_as_interrupted(tmp_path):
    import os
    import psutil
    import socket
    from datetime import datetime, timezone
    from prospector_externo.domain.observations import Run

    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    orchestrator = RunOrchestrator(repo, repo)
    repo.save_run(Run(
        run_id="live", started_at=datetime.now(timezone.utc),
        owner_host=socket.gethostname(), owner_pid=os.getpid(),
        owner_process_started_at=psutil.Process().create_time(),
    ))
    assert orchestrator.recover_abandoned_runs() == []
    assert repo.get_run("live").status == RunStatus.RUNNING


def test_recovery_skips_old_runs_and_other_hosts(tmp_path):
    import os
    import psutil
    from datetime import datetime, timezone
    from prospector_externo.domain.observations import Run

    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    orchestrator = RunOrchestrator(repo, repo)
    repo.save_run(Run(run_id="old", started_at=datetime.now(timezone.utc)))
    repo.save_run(Run(
        run_id="remote", started_at=datetime.now(timezone.utc),
        owner_host="other-machine", owner_pid=os.getpid(),
        owner_process_started_at=psutil.Process().create_time() - 86400,
    ))
    assert orchestrator.recover_abandoned_runs() == []
    assert repo.get_run("old").status == RunStatus.RUNNING
    assert repo.get_run("remote").status == RunStatus.RUNNING


def test_recovery_detects_pid_reuse_and_is_idempotent(tmp_path, monkeypatch):
    import os
    import psutil
    import socket
    from datetime import datetime, timezone
    from prospector_externo.domain.observations import Run
    from prospector_externo.application import orchestrator as module

    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    runner = RunOrchestrator(repo, repo)
    repo.save_run(Run(
        run_id="reused", started_at=datetime.now(timezone.utc),
        owner_host=socket.gethostname(), owner_pid=os.getpid(),
        owner_process_started_at=psutil.Process().create_time() - 86400,
        selected_source_ids=["missing"], sources_selected=1,
    ))
    recovered = runner.recover_abandoned_runs()
    assert recovered == ["reused"]
    record = repo.get_run("reused")
    assert record.status == RunStatus.INTERRUPTED
    assert record.failure_code == "OWNER_PROCESS_TERMINATED"
    assert record.finished_at is not None
    assert record.sources_processed == 0
    assert runner.recover_abandoned_runs() == []
    assert repo.get_run("reused").finished_at == record.finished_at


def test_recovery_restores_finished_report_not_interruption(tmp_path):
    import os
    import psutil
    import socket
    from datetime import datetime, timezone
    from prospector_externo.domain.observations import Run, RunReport

    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    runner = RunOrchestrator(repo, repo)
    start = datetime(2026, 10, 8, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 9, tzinfo=timezone.utc)
    repo.save_run(Run(
        run_id="report_saved", started_at=start,
        owner_host=socket.gethostname(), owner_pid=os.getpid(),
        owner_process_started_at=psutil.Process().create_time() - 86400,
    ))
    repo.save_report(RunReport(
        run_id="report_saved", started_at=start, finished_at=finished,
        sources_selected=1, sources_processed=1,
    ))
    assert runner.recover_abandoned_runs() == ["report_saved"]
    run = repo.get_run("report_saved")
    assert run.status == RunStatus.COMPLETED
    assert run.finished_at == finished
    assert run.sources_processed == 1
    assert repo.get_report("report_saved") is not None


def test_recovery_declines_when_owner_cannot_be_inspected(tmp_path, monkeypatch):
    import os
    import socket
    import psutil
    from datetime import datetime, timezone
    from prospector_externo.domain.observations import Run
    from prospector_externo.application import orchestrator as module

    repo = LocalJsonRepositoryAdapter(tmp_path / "out")
    runner = RunOrchestrator(repo, repo)
    repo.save_run(Run(
        run_id="unknown", started_at=datetime.now(timezone.utc),
        owner_host=socket.gethostname(), owner_pid=os.getpid(),
        owner_process_started_at=1.0,
    ))
    def denied(_pid):
        raise psutil.AccessDenied(pid=os.getpid())
    monkeypatch.setattr(module.psutil, "Process", denied)
    assert runner.recover_abandoned_runs() == []
    assert repo.get_run("unknown").status == RunStatus.RUNNING


def test_recovery_cli_runs_without_crawling_or_source_config(tmp_path):
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + str(root)
    result = subprocess.run([
        sys.executable, "-m", "apps.crawler_batch.main", "--recover-abandoned",
        "--config", str(tmp_path / "not-present.yaml"),
        "--output-dir", str(tmp_path / "test-only"),
    ], cwd=root, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "0 corridas recuperadas" in result.stdout
    assert not (tmp_path / "test-only" / "reports").exists() or not list(
        (tmp_path / "test-only" / "reports").glob("*.json"))


def test_api_never_reveals_run_owner_host_and_pid(tmp_path):
    import os
    import socket
    import psutil
    from datetime import datetime, timezone
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from prospector_externo.adapters.api.routes import create_catalog_router
    from prospector_externo.domain.observations import Run

    repo = LocalJsonRepositoryAdapter(tmp_path)
    repo.save_run(Run(
        run_id="private_metadata", started_at=datetime.now(timezone.utc),
        owner_host=socket.gethostname(), owner_pid=os.getpid(),
        owner_process_started_at=psutil.Process().create_time(),
    ))
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    response = TestClient(app).get("/runs/private_metadata")
    assert response.status_code == 200
    public = response.json()
    assert public["status"] == "RUNNING"
    assert "owner_host" not in public
    assert "owner_pid" not in public
    assert "owner_process_started_at" not in public
    assert repo.get_run("private_metadata").owner_pid == os.getpid()
