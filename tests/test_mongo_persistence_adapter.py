"""Contrato offline del catálogo Mongo. HU-14 exige smoke adicional con servidor."""

from datetime import datetime, timezone

import pytest

from prospector_externo.adapters.persistence import mongo_adapter as adapter_module
from prospector_externo.domain.models import DiscoveredUrl, Snapshot


class FakeCollection:
    def __init__(self, name, *, reject_index=False):
        self.name = name
        self.indexes = []
        self.reject_index = reject_index
        self.docs = {}

    def create_index(self, keys, **options):
        if self.reject_index:
            raise ValueError("conflicting index options")
        self.indexes.append((keys, options))
        return f"{self.name}_index_{len(self.indexes)}"

    def update_one(self, query, operation, upsert=False):
        key = tuple(sorted(query.items()))
        existing = self.docs.get(key)
        if existing is None:
            if not upsert:
                return
            existing = dict(query)
            existing.update(operation.get("$setOnInsert", {}))
            self.docs[key] = existing
        existing.update(operation.get("$set", {}))

    def find_one(self, query):
        return self.docs.get(tuple(sorted(query.items())))


class FakeDatabase:
    def __init__(self, *, reject_collection=None):
        self.collections = {}
        self.reject_collection = reject_collection

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection(
                name, reject_index=(name == self.reject_collection),
            )
        return self.collections[name]


class FakeClient:
    def __init__(self, *, reject_collection=None):
        self.db = FakeDatabase(reject_collection=reject_collection)
        self.closed = False

    def __getitem__(self, database):
        return self.db

    def close(self):
        self.closed = True


def make_adapter(monkeypatch, *, reject_collection=None):
    client = FakeClient(reject_collection=reject_collection)
    monkeypatch.setattr(adapter_module, "HAS_PYMONGO", True)
    monkeypatch.setattr(adapter_module, "MongoClient", lambda uri: client, raising=False)
    return client, adapter_module.MongoPersistenceAdapter(connection_uri="mongodb://unused")


def has_index(collection, keys, *, unique=None):
    return any(
        actual == keys and (unique is None or options.get("unique", False) is unique)
        for actual, options in collection.indexes
    )


def test_mongo_catalog_provisions_all_seven_collections_and_identity_indexes(monkeypatch):
    client, _adapter = make_adapter(monkeypatch)
    cols = client.db.collections
    assert set(cols) == {
        "sources", "runs", "discovered_urls", "resource_candidates",
        "snapshots", "source_observations", "run_reports",
    }
    assert has_index(cols["sources"], "source_id", unique=True)
    assert has_index(cols["runs"], "run_id", unique=True)
    assert has_index(cols["discovered_urls"], [("source_id", 1), ("normalized_url", 1)], unique=True)
    assert has_index(cols["resource_candidates"], [("source_id", 1), ("resource_key", 1)], unique=True)
    assert has_index(cols["snapshots"], [("source_id", 1), ("run_id", 1)], unique=True)
    assert has_index(cols["source_observations"], [("source_id", 1), ("run_id", 1)], unique=True)
    assert has_index(cols["run_reports"], "run_id", unique=True)


def test_mongo_private_progress_replay_preserves_source_and_seven_collections(monkeypatch):
    from prospector_externo.domain.models import Source
    client, adapter = make_adapter(monkeypatch)
    source = Source(source_id="resume", name="Official name", entrypoint="https://example.test/", workflow="html",
                    consecutive_failures=2, catalog_scope_fingerprint="scope")
    adapter.save_source(source)
    progress = {"source_id": "resume", "source_name": "Cursor name", "entrypoint": source.entrypoint,
                "workflow": "html", "last_run_id": "attempt_one", "frontier": {"pending": ["https://example.test/a"]}}
    adapter.save_discovery_checkpoint("resume", progress)
    adapter.save_discovery_checkpoint("resume", progress)
    assert adapter.get_discovery_checkpoint("resume") == progress
    assert adapter.get_source("resume") == source
    assert "_discovery_progress" not in adapter.get_source("resume").model_dump()
    assert len(client.db.collections) == 7
    assert len(client.db.collections["sources"].docs) == 1
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        adapter.save_discovery_checkpoint("wrong", progress)
    assert adapter.get_discovery_checkpoint("wrong") is None


def test_mongo_progress_can_precede_first_catalog_write(monkeypatch):
    _, adapter = make_adapter(monkeypatch)
    progress = {"source_id": "resume", "source_name": "Cursor name", "entrypoint": "https://example.test/",
                "workflow": "html", "last_run_id": "attempt_one"}
    adapter.save_discovery_checkpoint("resume", progress)
    assert adapter.get_source("resume").name == "Cursor name"
    assert adapter.get_discovery_checkpoint("resume") == progress


def test_mongo_catalog_provisions_query_indexes(monkeypatch):
    client, _adapter = make_adapter(monkeypatch)
    cols = client.db.collections
    assert has_index(cols["sources"], [("health_status", 1), ("next_eligible_at", 1)])
    assert has_index(cols["runs"], [("started_at", -1), ("status", 1)])
    assert has_index(cols["discovered_urls"], [("source_id", 1), ("last_seen_at", -1)])
    assert has_index(cols["resource_candidates"], [("source_id", 1), ("catalog_current", 1)])
    assert has_index(cols["snapshots"], [("source_id", 1), ("captured_at", -1)])
    assert has_index(cols["source_observations"], [("source_id", 1), ("observed_at", -1)])
    assert has_index(cols["run_reports"], "finished_at")


def test_mongo_catalog_fails_closed_on_incompatible_existing_index(monkeypatch):
    client = FakeClient(reject_collection="resource_candidates")
    monkeypatch.setattr(adapter_module, "HAS_PYMONGO", True)
    monkeypatch.setattr(adapter_module, "MongoClient", lambda uri: client, raising=False)
    with pytest.raises(RuntimeError, match="No se pudieron asegurar los índices"):
        adapter_module.MongoPersistenceAdapter(connection_uri="mongodb://unused")
    assert client.closed is True


def test_mongo_discovered_url_upsert_retains_first_seen_timestamp(monkeypatch):
    client, adapter = make_adapter(monkeypatch)
    timestamp1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    timestamp2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    def save(run_id, timestamp):
        adapter.save_snapshot(Snapshot(
            source_id="test", run_id=run_id, resources_hash="hash",
            total_resources=0,
            discovered_urls=[DiscoveredUrl(
                normalized_url="https://example.org/page", raw_url="https://example.org/page",
                source_id="test", discovered_at=timestamp,
            )],
        ))
    save("r1", timestamp1)
    save("r2", timestamp2)
    docs = list(client.db.collections["discovered_urls"].docs.values())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["first_seen_at"] == timestamp1.isoformat().replace("+00:00", "Z")
    assert doc["first_seen_run_id"] == "r1"
    assert doc["last_seen_at"] == timestamp2.isoformat().replace("+00:00", "Z")
    assert doc["last_seen_run_id"] == "r2"


def test_mongo_catalog_index_collision_does_not_get_silenced(monkeypatch):
    client = FakeClient(reject_collection="sources")
    monkeypatch.setattr(adapter_module, "HAS_PYMONGO", True)
    monkeypatch.setattr(adapter_module, "MongoClient", lambda uri: client, raising=False)
    with pytest.raises(RuntimeError, match="índices MongoDB"):
        adapter_module.MongoPersistenceAdapter(connection_uri="mongodb://unused")
    assert client.closed
