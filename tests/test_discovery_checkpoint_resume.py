"""Recuperación BFS real de workflows con HTTP simulado y almacenamiento JSON real."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.discovery import DiscoveryFrontier
from prospector_externo.domain.models import SourceConfig
from prospector_externo.infrastructure.http_policy import RetryPolicy
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


ROOT = Path(__file__).resolve().parents[1]


def source_config(workflow="html", **changes):
    return SourceConfig(source_id="resume", entrypoint="https://example.test/",
                        seeds=["https://example.test/root"], workflow=workflow,
                        max_requests=2, max_urls=20, max_resources=50,
                        discover_sitemaps=False, discover_apis=False, inspect_archives=False,
                        rate_limit_seconds=0, **changes)


def handler_for(calls, workflow="html"):
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        links = ''.join(f'<a href="/{i}">Página {i}</a>' for i in range(1, 4)) if request.url.path == "/root" else ""
        doc = f'<a href="/files{request.url.path}.pdf">Documento</a>'
        if workflow == "commented_html":
            doc = f'<!-- {doc} -->'
        return httpx.Response(200, text=doc + links, headers={"content-type": "text/html"})
    return handler


def attempt(folder, config, calls, *, resume=True, handler=None):
    path = folder / "sources.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"sources": [config.model_dump(mode="json")]}), encoding="utf-8")
    repo = LocalJsonRepositoryAdapter(folder / "evidence")
    runner = RunOrchestrator(repo, repo, path, http_runtime_factory=lambda: AsyncHttpRuntime(
        transport=httpx.MockTransport(handler or handler_for(calls, config.workflow)),
        retry_policy=RetryPolicy(max_attempts=1),
    ))
    report = runner.run_batch(force=True, resume_discovery=resume, pipeline_run_id="pipeline_test")
    return repo, report


@pytest.mark.parametrize("workflow", ["html", "commented_html"])
def test_resume_advances_queue_with_same_budget_and_keeps_all_raw(tmp_path, workflow):
    config = source_config(workflow)
    calls = []
    observations = []
    for _ in range(4):
        repo, report = attempt(tmp_path, config, calls)
        observations.extend(report.source_results)
        assert repo.get_run(report.run_id).pipeline_run_id == "pipeline_test"
    assert [path for path in calls if path != "/robots.txt"] == ["/root", "/1", "/2", "/3"]
    assert [obs.coverage.urls_pending for obs in observations] == [3, 2, 1, 0]
    assert [obs.coverage.resources_found for obs in observations] == [1, 2, 3, 4]
    assert [obs.execution_status.value for obs in observations] == ["PARTIAL_SUCCESS"] * 3 + ["SUCCESS"]
    assert observations[-1].coverage.urls_visited_before_resume == 3
    assert not observations[-1].coverage.confirms_resource_absence()
    assert len({obs.discovery_cycle_id for obs in observations}) == 1
    assert observations[-1].resume_status == "RESTORED"
    snapshot = repo.get_latest_snapshot("resume")
    assert len(snapshot.resources) == 4
    assert len(snapshot.discovered_urls) == 4
    assert len(repo.list_resources("resume")) == 4
    if workflow == "commented_html":
        assert all(row.discovery_method == "commented_html_link" for row in snapshot.resources)
    assert len(list((tmp_path / "evidence/state/discovery_progress/resume").glob("*.json"))) == 4

    # Un ciclo ya materializado inicia un recorrido nuevo, no un éxito vacío.
    calls.clear()
    _, new_report = attempt(tmp_path, config, calls)
    assert "/root" in calls
    assert new_report.source_results[0].discovery_cycle_id != observations[-1].discovery_cycle_id


def test_baseline_restart_repeats_prefix_where_resume_makes_progress(tmp_path):
    calls = []
    for _ in range(4):
        repo, _ = attempt(tmp_path, source_config(), calls, resume=False)
    assert calls.count("/root") == 4
    assert len(repo.list_resources("resume")) == 1


def test_profile_change_restarts_explicitly_and_budget_change_preserves_frontier(tmp_path):
    cfg = source_config()
    calls = []
    attempt(tmp_path, cfg, calls)
    _, report = attempt(tmp_path, cfg.model_copy(update={"max_requests": 3}), calls)
    assert report.source_results[0].resume_status == "RESTORED"
    calls.clear()
    _, report = attempt(tmp_path, cfg.model_copy(update={"max_depth": 1}), calls)
    assert report.source_results[0].resume_status == "PROFILE_CHANGED_RESTART"
    assert "/root" in calls


def test_failed_request_is_deferred_until_next_attempt(tmp_path):
    cfg = source_config().model_copy(update={"max_requests": 3})
    calls = []
    normal = handler_for(calls)
    failed = False
    def handler(request):
        nonlocal failed
        if request.url.path == "/1" and not failed:
            calls.append("/1")
            failed = True
            return httpx.Response(503)
        return normal(request)
    repo, first = attempt(tmp_path, cfg, calls, handler=handler)
    assert first.source_results[0].coverage.urls_failed == 1
    state = repo.get_discovery_checkpoint("resume")
    assert "/1" in [httpx.URL(url).path for url in state["frontier"]["pending"]]
    for _ in range(2):
        repo, last = attempt(tmp_path, cfg, calls)
    assert len(repo.list_resources("resume")) == 4
    assert calls.count("/root") == 1
    assert calls.count("/1") == 2
    assert last.source_results[0].execution_status.value == "SUCCESS"


@pytest.mark.parametrize("workflow", ["html", "commented_html"])
def test_recovered_raw_does_not_mask_total_failure_of_new_attempt(tmp_path, workflow):
    cfg = source_config(workflow)
    repo, initial = attempt(tmp_path, cfg, [])
    snapshot = repo.get_latest_snapshot("resume")
    def unavailable(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(503)
    repo, failed = attempt(tmp_path, cfg, [], handler=unavailable)
    assert failed.source_results[0].execution_status.value == "FAILED"
    assert failed.source_results[0].failure_code == "HTTP_503"
    assert failed.source_results[0].coverage.resources_found == 1
    assert repo.get_latest_snapshot("resume") == snapshot
    assert len(repo.list_resources("resume")) == 1
    assert repo.get_source("resume").consecutive_failures == 1
    assert len(repo.get_discovery_checkpoint("resume")["resources"]) == 1
    calls = []
    _, recovered = attempt(tmp_path, cfg.model_copy(update={"max_requests": 10}), calls)
    assert "/root" not in calls
    assert recovered.source_results[0].execution_status.value == "SUCCESS"


def test_unmaterialized_complete_progress_is_recovered_without_recrawl(tmp_path):
    cfg = source_config().model_copy(update={"max_requests": 10})
    calls = []
    repo, report = attempt(tmp_path, cfg, calls)
    state = repo.get_discovery_checkpoint("resume")
    assert state["traversal_complete"]
    state["materialized_run_id"] = None  # Corte entre extracción y checkpoint de catálogo.
    repo.save_discovery_checkpoint("resume", state)
    calls.clear()
    repo, recovered = attempt(tmp_path, cfg, calls)
    assert calls == []
    assert len(repo.get_latest_snapshot("resume").resources) == 4
    assert recovered.source_results[0].resume_status == "RESTORED"
    assert repo.get_discovery_checkpoint("resume")["materialized_run_id"] == recovered.run_id


@pytest.mark.parametrize("corruption", ["source", "scope", "depth", "duplicate", "inventory", "shape", "envelope", "raw_type"])
def test_invalid_checkpoint_is_rejected_before_network(tmp_path, corruption):
    cfg = source_config()
    calls = []
    repo, _ = attempt(tmp_path, cfg, calls)
    state = repo.get_discovery_checkpoint("resume")
    if corruption == "source":
        state["source_id"] = "another"
    elif corruption == "scope":
        state["frontier"]["items"][0]["normalized_url"] = "https://unauthorized.test/"
    elif corruption == "depth":
        state["frontier"]["items"][0]["depth"] = 100
    elif corruption == "duplicate":
        state["frontier"]["pending"] *= 2
    elif corruption == "shape":
        state["frontier"]["items"][0] = "not an item"
    elif corruption == "envelope":
        state["traversal_complete"] = "false"
    elif corruption == "raw_type":
        state["frontier"]["items"][0]["raw_url"] = {"invalid": "type"}
    else:
        state["resources"][0]["source_id"] = "another"
    path = tmp_path / "evidence/state/discovery_progress/resume.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    calls.clear()
    _, failed = attempt(tmp_path, cfg, calls)
    assert calls == []
    assert failed.sources_failed == 1
    assert failed.source_results[0].failure_code == "DISCOVERY_CHECKPOINT_INVALID"
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_checkpoint_atomic_write_keeps_previous_version_on_replace_failure(tmp_path, monkeypatch):
    repo, _ = attempt(tmp_path, source_config(), [])
    path = tmp_path / "evidence/state/discovery_progress/resume.json"
    before = path.read_bytes()
    from prospector_externo.adapters.persistence import local_json_adapter
    def fail(*args):
        raise OSError("simulated interruption")
    monkeypatch.setattr(local_json_adapter.os, "replace", fail)
    with pytest.raises(OSError):
        repo.save_discovery_checkpoint("resume", repo.get_discovery_checkpoint("resume"))
    assert path.read_bytes() == before


def test_frontier_inflight_is_requeued_and_limits_are_not_relaxed():
    cfg = source_config()
    frontier = DiscoveryFrontier(cfg)
    frontier.seed(cfg.seeds)
    item = frontier.pop()
    restored = DiscoveryFrontier.restore(cfg, json.loads(json.dumps(frontier.dump_state())))
    assert restored.pending_count == 1
    assert restored.pop() == item
    assert restored.visited_count == 0


def test_progress_is_isolated_from_shared_catalog(tmp_path):
    shared = tmp_path / "shared"
    one = LocalJsonRepositoryAdapter(tmp_path / "one", catalog_dir=shared)
    two = LocalJsonRepositoryAdapter(tmp_path / "two", catalog_dir=shared)
    one.save_discovery_checkpoint("resume", {"source_id": "resume", "last_run_id": "attempt_one"})
    assert one.get_discovery_checkpoint("resume") is not None
    assert two.get_discovery_checkpoint("resume") is None
    assert not (shared / "state/discovery_progress").exists()


def test_kill_during_page_fetch_preserves_frontier_and_prior_raw(tmp_path):
    cfg = source_config().model_copy(update={"max_requests": 10})
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(yaml.safe_dump({"sources": [cfg.model_dump(mode="json")]}), encoding="utf-8")
    code = '''
import asyncio,sys,httpx
from pathlib import Path
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime
root=Path(sys.argv[1])
async def handler(request):
    if request.url.path=='/robots.txt': return httpx.Response(200,text='User-agent: *\\nAllow: /')
    if request.url.path=='/root': return httpx.Response(200,text='<a href="/a.pdf">A</a><a href="/1">Next</a>')
    (root/'inflight.marker').write_text('ready')
    await asyncio.Event().wait()
repo=LocalJsonRepositoryAdapter(root/'evidence')
runner=RunOrchestrator(repo,repo,root/'sources.yaml',http_runtime_factory=lambda:AsyncHttpRuntime(transport=httpx.MockTransport(handler)))
asyncio.run(runner.run_batch_async(force=True,resume_discovery=True,pipeline_run_id="pipeline_test"))
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
    child = subprocess.Popen([sys.executable, "-B", "-c", code, str(tmp_path)], cwd=ROOT,
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "inflight.marker").exists():
            if child.poll() is not None:
                raise AssertionError(child.communicate(timeout=3))
            assert time.monotonic() < deadline
            time.sleep(.05)
        child.kill()
        child.communicate(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)
    calls = []
    repo, report = attempt(tmp_path, cfg, calls)
    assert "/root" not in calls
    assert "/1" in calls
    assert len(repo.get_latest_snapshot("resume").resources) == 2
    assert report.source_results[0].coverage.urls_visited_before_resume == 1
