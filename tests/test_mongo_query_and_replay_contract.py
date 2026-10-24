"""Contrato del adaptador con servidor simulado; no certifica Mongo real."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from prospector_externo.adapters.persistence import mongo_adapter
from prospector_externo.domain.models import Source, ResourceCandidate, Snapshot, DiscoveredUrl
from prospector_externo.domain.observations import Run, RunReport, RunStatus, SourceRunObservation
from test_mongo_persistence_adapter import FakeClient, FakeCollection


def matches(document, query):
    for key, expected in (query or {}).items():
        value = document.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and value == expected["$ne"]: return False
            if "$nin" in expected and value in expected["$nin"]: return False
        elif value != expected:
            return False
    return True


class Cursor(list):
    def sort(self, key, direction=None):
        fields = key if isinstance(key, list) else [(key, direction)]
        for field, direction in reversed(fields):
            super().sort(key=lambda row: row.get(field, ""), reverse=direction == -1)
        return self
    def skip(self, offset): return Cursor(self[offset:])
    def limit(self, count): return Cursor(self[:count])


@pytest.fixture
def repository(monkeypatch):
    client = FakeClient()
    def find(self, query=None):
        return Cursor([deepcopy(row) for row in self.docs.values() if matches(row, query)])
    def one(self, query, sort=None):
        rows = find(self, query)
        if sort: rows.sort(sort)
        return rows[0] if rows else None
    def many(self, query, update):
        for row in self.docs.values():
            if matches(row, query): row.update(deepcopy(update["$set"]))
    monkeypatch.setattr(FakeCollection, "find", find, raising=False)
    monkeypatch.setattr(FakeCollection, "find_one", one)
    monkeypatch.setattr(FakeCollection, "update_many", many, raising=False)
    monkeypatch.setattr(mongo_adapter, "HAS_PYMONGO", True)
    monkeypatch.setattr(mongo_adapter, "MongoClient", lambda uri: client)
    return mongo_adapter.MongoPersistenceAdapter("mongodb://unused")


def test_server_pagination_filters_removed_and_preserves_catalog_consistency(repository):
    for sid in ("z", "a"):
        repository.save_source(Source(source_id=sid, name=sid, entrypoint="https://example.test", workflow="html"))
        resources = [ResourceCandidate(source_id=sid, resource_key=f"r{i}", url=f"https://example.test/{i}.pdf",
                                       change_status="REMOVED" if i == 1 else "NEW") for i in (2, 1, 0)]
        repository.save_resource_catalog(sid, "current", resources)
    assert [s.source_id for s in repository.query_sources(limit=1, offset=1)] == ["z"]
    assert [(r.source_id, r.resource_key) for r in repository.query_resources(limit=2, offset=1)] == [("a", "r2"), ("z", "r0")]
    assert [r.resource_key for r in repository.query_resources("z", offset=1)] == ["r2"]
    assert repository.query_resources("absent", limit=1) == []
    assert len(repository.list_resources()) == 4
    repository.save_resource_catalog("a", "empty", [])
    assert repository.list_resources("a") == []
    assert [r.source_id for r in repository.query_resources(limit=10)] == ["z", "z"]
    with pytest.raises(ValueError, match="otra fuente"):
        repository.save_resource_catalog("wrong", "bad", resources)
    with pytest.raises(ValueError, match="duplicadas"):
        repository.save_resource_catalog("a", "bad", [resources[0], resources[0]])


def test_legacy_snapshots_fallback_and_discovered_timestamp_replay(repository):
    start = datetime(2026, 10, 22, tzinfo=timezone.utc)
    repository.save_source(Source(source_id="legacy", name="Legacy", entrypoint="https://example.test", workflow="html"))
    resource = ResourceCandidate(source_id="legacy", resource_key="doc", url="https://example.test/a.pdf")
    for index in range(2):
        at = start + timedelta(hours=index)
        repository.save_snapshot(Snapshot(source_id="legacy", run_id=f"run_{index}", captured_at=at,
            resources_hash="hash", total_resources=1, resources=[resource],
            discovered_urls=[DiscoveredUrl(source_id="legacy", normalized_url="https://example.test/listing", raw_url="/listing",
                                          discovered_at=at)]))
    assert repository.get_latest_snapshot("legacy").run_id == "run_1"
    assert repository.list_resources("legacy") == [resource]
    assert repository.query_resources("legacy", limit=1) == [resource]
    assert repository.query_resources(limit=1) == [resource]
    assert len(repository.discovered_urls_col.docs) == 1
    row = next(iter(repository.discovered_urls_col.docs.values()))
    assert datetime.fromisoformat(row["first_seen_at"]) == start
    assert datetime.fromisoformat(row["last_seen_at"]) == start + timedelta(hours=1)


def test_runs_observations_reports_replay_and_legacy_union(repository):
    at = datetime(2026, 10, 22, tzinfo=timezone.utc)
    for index in range(3):
        obs = SourceRunObservation(source_id="source", run_id=f"r{index}", workflow="html",
            observed_at=at + timedelta(minutes=index), execution_status="SUCCESS", content_status="NO_CHANGE")
        report = RunReport(run_id=obs.run_id, started_at=obs.observed_at,
                           finished_at=obs.observed_at + timedelta(seconds=1), source_results=[obs])
        repository.save_report(report)
        repository.save_report(report)
        repository.save_source_observation(obs)
        repository.save_source_observation(obs)
        if index != 0:
            repository.save_run(Run(run_id=obs.run_id, started_at=obs.observed_at, status=RunStatus.RUNNING))
    assert len(repository.run_reports_col.docs) == len(repository.source_observations_col.docs) == 3
    assert repository.get_run("r0").legacy_inferred is True
    assert repository.get_run("r2").status == RunStatus.RUNNING
    assert [r.run_id for r in repository.list_runs(limit=2)] == ["r2", "r1"]
    assert [r.run_id for r in repository.list_reports(limit=2)] == ["r2", "r1"]
    assert [r.run_id for r in repository.get_source_observations("source", limit=1)] == ["r2"]
    assert repository.get_run("absent") is None
    assert repository.get_report("absent") is None
