"""Recuperación durable JS con renderer determinista y HTTP/persistencia reales."""
import threading
from urllib.parse import urlsplit

import pytest

from prospector_externo.workflows import javascript_workflow
from test_discovery_checkpoint_resume import attempt, source_config


class Renderer:
    def __init__(self, calls, *, fail_once=None):
        self.calls, self.fail_once = calls, fail_once
        self.closed = 0
        self.thread_ids = []

    def is_available(self):
        return True

    def fetch_dynamic_dom(self, url):
        self.thread_ids.append(threading.get_ident())
        path = urlsplit(url).path
        self.calls.append(path)
        if self.fail_once == path:
            self.fail_once = None
            raise RuntimeError("renderer transient failure")
        links = ''.join(f'<a href="/{i}">Page</a>' for i in range(1, 4)) if path == "/root" else ""
        return f'<a href="/files{path}.pdf">JS document</a>{links}', None

    def close(self):
        self.thread_ids.append(threading.get_ident())
        self.closed += 1


def test_javascript_resume_advances_without_repeating_confirmed_dom(tmp_path, monkeypatch):
    http_calls, renders, drivers = [], [], []
    def driver():
        instance = Renderer(renders)
        drivers.append(instance)
        return instance
    original_init = javascript_workflow.JavascriptWorkflow.__init__
    def initialize(self):
        original_init(self)
        self.browser_driver = driver()
    monkeypatch.setattr(javascript_workflow.JavascriptWorkflow, "__init__", initialize)
    cfg = source_config("javascript")
    observations = []
    for _ in range(4):
        repo, report = attempt(tmp_path, cfg, http_calls)
        observations.extend(report.source_results)
    assert renders == ["/root", "/1", "/2", "/3"]
    assert [o.coverage.urls_pending for o in observations] == [3, 2, 1, 0]
    assert [o.execution_status.value for o in observations] == ["PARTIAL_SUCCESS"] * 3 + ["SUCCESS"]
    assert observations[-1].resume_status == "RESTORED"
    assert len(repo.get_latest_snapshot("resume").resources) == 4
    assert all(r.discovery_method == "javascript" for r in repo.list_resources("resume"))
    assert all(d.closed == 1 and len(set(d.thread_ids)) == 1 for d in drivers)
    assert repo.get_discovery_checkpoint("resume")["materialized_run_id"] == report.run_id


def test_render_failure_is_replayed_and_does_not_mask_failure_with_prior_raw(tmp_path, monkeypatch):
    calls, renders = [], []
    renderer = Renderer(renders)
    original_init = javascript_workflow.JavascriptWorkflow.__init__
    def initialize(self):
        original_init(self)
        self.browser_driver = renderer
    monkeypatch.setattr(javascript_workflow.JavascriptWorkflow, "__init__", initialize)
    cfg = source_config("javascript")
    repo, _ = attempt(tmp_path, cfg, calls)
    previous = repo.get_latest_snapshot("resume")
    renderer.fail_once = "/1"
    repo, failed = attempt(tmp_path, cfg, calls)
    assert failed.source_results[0].execution_status.value == "FAILED"
    assert repo.get_latest_snapshot("resume") == previous
    assert "https://example.test/1" in repo.get_discovery_checkpoint("resume")["frontier"]["pending"]
    for _ in range(3):
        repo, report = attempt(tmp_path, cfg, calls)
    assert renders.count("/root") == 1
    assert renders.count("/1") == 2
    assert len(repo.list_resources("resume")) == 4
    assert report.source_results[0].execution_status.value == "SUCCESS"


@pytest.mark.parametrize("change,expected", [({"max_depth": 1}, "PROFILE_CHANGED_RESTART")])
def test_js_scope_change_restarts_explicitly(tmp_path, monkeypatch, change, expected):
    renders = []
    original_init = javascript_workflow.JavascriptWorkflow.__init__
    def initialize(self):
        original_init(self)
        self.browser_driver = Renderer(renders)
    monkeypatch.setattr(javascript_workflow.JavascriptWorkflow, "__init__", initialize)
    cfg = source_config("javascript")
    attempt(tmp_path, cfg, [])
    _, report = attempt(tmp_path, cfg.model_copy(update=change), [])
    assert report.source_results[0].resume_status == expected
    assert renders == ["/root", "/root"]
