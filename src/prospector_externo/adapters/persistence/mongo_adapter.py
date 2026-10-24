"""
Adaptador de persistencia MongoDB para el Prospector Externo.
Implementa el modelo de 7 colecciones definido en la Sección 16 del documento de arquitectura.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import (
    Run, RunReport, SourceRunObservation,
)

logger = logging.getLogger("prospector.adapters.mongo")

try:
    import pymongo
    from pymongo import MongoClient
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False


class MongoPersistenceAdapter(CatalogRepositoryPort, RunReportRepositoryPort):
    """Adaptador de persistencia sobre MongoDB."""

    supports_discovery_checkpoints = True

    def get_discovery_checkpoint(self, source_id: str) -> Optional[dict]:
        doc = self.sources_col.find_one({"source_id": source_id})
        return doc.get("_discovery_progress") if doc else None

    def save_discovery_checkpoint(self, source_id: str, checkpoint: dict) -> None:
        if checkpoint.get("source_id") != source_id:
            raise ValueError("DISCOVERY_CHECKPOINT_SOURCE_MISMATCH")
        # Campo privado: Source/Pydantic no lo publica mediante la API.
        source = Source(source_id=source_id, name=checkpoint["source_name"],
                        entrypoint=checkpoint["entrypoint"], workflow=checkpoint["workflow"])
        self.sources_col.update_one(
            {"source_id": source_id},
            {"$setOnInsert": source.model_dump(mode="json"),
             "$set": {"_discovery_progress": checkpoint}}, upsert=True,
        )

    def check_health(self) -> None:
        """Confirma conectividad de la base, no solo que FastAPI esté activo."""
        self.client.admin.command("ping")

    def __init__(self, connection_uri: str = "mongodb://localhost:27017", database_name: str = "prospector_externo"):
        if not HAS_PYMONGO:
            raise RuntimeError("pymongo no está instalado. Ejecuta: pip install pymongo")

        self.client = MongoClient(connection_uri)
        self.db = self.client[database_name]

        # Colecciones según Sección 16 del documento de arquitectura
        self.sources_col = self.db["sources"]
        self.runs_col = self.db["runs"]
        self.discovered_urls_col = self.db["discovered_urls"]
        self.resource_candidates_col = self.db["resource_candidates"]
        self.snapshots_col = self.db["snapshots"]
        self.source_observations_col = self.db["source_observations"]
        self.run_reports_col = self.db["run_reports"]

        try:
            self._ensure_indexes()
        except Exception:
            # No operar sobre una base sin garantías de identidad. Los índices
            # de Mongo son parte del contrato; una advertencia no basta.
            self.client.close()
            raise

    def _ensure_indexes(self) -> None:
        """Garantiza identidad y rutas de consulta; falla si el esquema no cumple."""
        try:
            self.sources_col.create_index("source_id", unique=True)
            self.sources_col.create_index([("health_status", 1), ("next_eligible_at", 1)])
            self.runs_col.create_index("run_id", unique=True)
            self.runs_col.create_index([("started_at", -1), ("status", 1)])
            self.discovered_urls_col.create_index(
                [("source_id", 1), ("normalized_url", 1)], unique=True
            )
            self.discovered_urls_col.create_index([("source_id", 1), ("last_seen_at", -1)])
            self.resource_candidates_col.create_index(
                [("source_id", 1), ("resource_key", 1)], unique=True
            )
            self.resource_candidates_col.create_index([("source_id", 1), ("catalog_current", 1)])
            self.resource_candidates_col.create_index([
                ("catalog_current", 1), ("source_id", 1), ("resource_key", 1), ("url", 1),
            ])
            self.snapshots_col.create_index([("source_id", 1), ("run_id", 1)], unique=True)
            self.snapshots_col.create_index([("source_id", 1), ("captured_at", -1)])
            self.source_observations_col.create_index([("source_id", 1), ("run_id", 1)], unique=True)
            self.source_observations_col.create_index([("source_id", 1), ("observed_at", -1)])
            self.run_reports_col.create_index("run_id", unique=True)
            self.run_reports_col.create_index("finished_at")
        except Exception as exc:
            # Índices heredados incompatibles/duplicados deben repararse con
            # migración supervisada, nunca borrarse de manera automática.
            logger.error("No fue posible validar índices obligatorios del catálogo Mongo (%s)",
                         type(exc).__name__)
            raise RuntimeError(
                "No se pudieron asegurar los índices MongoDB del catálogo. "
                "Revise índices heredados o registros duplicados antes de arrancar."
            ) from exc

    def save_snapshot(self, snapshot: Snapshot) -> None:
        if any(found.source_id != snapshot.source_id for found in snapshot.discovered_urls):
            raise ValueError("URL descubierta asociada a otra fuente")
        doc = snapshot.model_dump(mode="json")
        self.snapshots_col.update_one(
            {"source_id": snapshot.source_id, "run_id": snapshot.run_id},
            {"$set": doc},
            upsert=True
        )
        # Una fila consolidada por URL/fuente conserva primera y ultima vista.
        # El snapshot anterior mantiene las evidencias exactas por corrida.
        for found in snapshot.discovered_urls:
            payload = found.model_dump(mode="json")
            self.discovered_urls_col.update_one(
                {"source_id": found.source_id, "normalized_url": found.normalized_url},
                {
                    "$setOnInsert": {
                        "first_seen_at": payload["discovered_at"],
                        "first_seen_run_id": snapshot.run_id,
                    },
                    "$set": {
                        **payload,
                        "last_seen_at": payload["discovered_at"],
                        "last_seen_run_id": snapshot.run_id,
                    },
                },
                upsert=True,
            )
        # La proyección viva se actualiza exclusivamente con save_resource_catalog.
        # No sobrescribir el estado consolidado con un snapshot RAW parcial.

    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        doc = self.snapshots_col.find_one({"source_id": source_id}, sort=[("captured_at", -1)])
        if doc:
            doc.pop("_id", None)
            return Snapshot.model_validate(doc)
        return None

    def save_source_observation(self, observation: SourceRunObservation) -> None:
        doc = observation.model_dump(mode="json")
        self.source_observations_col.update_one(
            {"source_id": observation.source_id, "run_id": observation.run_id},
            {"$set": doc},
            upsert=True
        )

    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        cursor = self.source_observations_col.find({"source_id": source_id}).sort("observed_at", -1).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(SourceRunObservation.model_validate(doc))
        return results

    def save_source(self, source: Source) -> None:
        doc = source.model_dump(mode="json")
        self.sources_col.update_one(
            {"source_id": source.source_id},
            {"$set": doc},
            upsert=True
        )

    def get_source(self, source_id: str) -> Optional[Source]:
        doc = self.sources_col.find_one({"source_id": source_id})
        if doc:
            doc.pop("_id", None)
            return Source.model_validate(doc)
        return None

    def list_sources(self) -> List[Source]:
        cursor = self.sources_col.find()
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(Source.model_validate(doc))
        return results

    def query_sources(self, *, limit: Optional[int] = None, offset: int = 0) -> List[Source]:
        cursor = self.sources_col.find().sort("source_id", 1).skip(offset)
        if limit is not None:
            cursor = cursor.limit(limit)
        return [Source.model_validate(doc) for doc in cursor]

    def query_resources(self, source_id: Optional[str] = None, *,
                        limit: Optional[int] = None, offset: int = 0) -> List[ResourceCandidate]:
        if source_id is not None:
            source = self.get_source(source_id)
            legacy = source is not None and source.catalog_last_run_id is None
        else:
            # null coincide también con fuentes antiguas sin este campo.
            legacy = self.sources_col.find_one({"catalog_last_run_id": None}) is not None
        if legacy:
            return super().query_resources(source_id, limit=limit, offset=offset)
        query = {"catalog_current": True, "change_status": {"$ne": "REMOVED"}}
        if source_id is not None:
            query["source_id"] = source_id
        cursor = self.resource_candidates_col.find(query).sort(
            [("source_id", 1), ("resource_key", 1), ("url", 1)]).skip(offset)
        if limit is not None:
            cursor = cursor.limit(limit)
        return [ResourceCandidate.model_validate(doc) for doc in cursor]

    def get_resource_catalog(self, source_id: str) -> Optional[List[ResourceCandidate]]:
        source = self.get_source(source_id)
        if not source or source.catalog_last_run_id is None:
            return None
        cursor = self.resource_candidates_col.find({
            "source_id": source_id, "catalog_current": True,
        })
        return [ResourceCandidate.model_validate({k: v for k, v in doc.items()
                                                   if k != "_id"}) for doc in cursor]

    def save_resource_catalog(
        self, source_id: str, run_id: str, resources: List[ResourceCandidate]
    ) -> None:
        if any(r.source_id != source_id for r in resources):
            raise ValueError("El catálogo contiene recursos de otra fuente")
        keys = [r.resource_key for r in resources]
        if len(set(keys)) != len(keys):
            raise ValueError("Claves duplicadas en catálogo")
        # La colección existente conserva el estado de vida y contadores.
        # Mongo real se certificará con transacciones/caída de proceso en HU-14.
        for r in resources:
            self.resource_candidates_col.update_one(
                {"source_id": source_id, "resource_key": r.resource_key},
                {"$set": {**r.model_dump(mode="json"), "catalog_current": True}},
                upsert=True,
            )
        self.resource_candidates_col.update_many(
            {"source_id": source_id, "resource_key": {"$nin": keys}},
            {"$set": {"catalog_current": False}},
        )
        self.sources_col.update_one(
            {"source_id": source_id},
            {"$set": {"catalog_last_run_id": run_id}},
        )

    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        if source_id is not None:
            state = self.get_resource_catalog(source_id)
            if state is None:
                snap = self.get_latest_snapshot(source_id)
                state = snap.resources if snap else []
            return [r for r in state if r.change_status != "REMOVED"]
        resources = []
        for src in self.list_sources():
            resources.extend(self.list_resources(src.source_id))
        return resources

    def save_run(self, run: Run) -> None:
        self.runs_col.update_one(
            {"run_id": run.run_id},
            {"$set": run.model_dump(mode="json")}, upsert=True,
        )

    def get_run(self, run_id: str) -> Optional[Run]:
        doc = self.runs_col.find_one({"run_id": run_id})
        if doc:
            doc.pop("_id", None)
            return Run.model_validate(doc)
        report = self.get_report(run_id)
        if not report:
            return None
        return Run.from_legacy_report(report)

    def list_runs(self, limit: int = 50) -> List[Run]:
        states = {}
        # Solo necesitamos los últimos N de cada colección; la unión final
        # incluye registros antiguos que todavía viven solo en run_reports.
        for doc in self.runs_col.find().sort("started_at", -1).limit(limit):
            doc.pop("_id", None)
            run = Run.model_validate(doc)
            states[run.run_id] = run
        for doc in self.run_reports_col.find().sort("started_at", -1).limit(limit):
            doc.pop("_id", None)
            report = RunReport.model_validate(doc)
            states.setdefault(report.run_id, Run.from_legacy_report(report))
        return sorted(states.values(), key=lambda run: run.started_at, reverse=True)[:limit]

    def save_report(self, report: RunReport) -> None:
        doc = report.model_dump(mode="json")
        self.run_reports_col.update_one(
            {"run_id": report.run_id},
            {"$set": doc},
            upsert=True
        )

    def get_report(self, run_id: str) -> Optional[RunReport]:
        doc = self.run_reports_col.find_one({"run_id": run_id})
        if doc:
            doc.pop("_id", None)
            return RunReport.model_validate(doc)
        return None

    def list_reports(self, limit: int = 50) -> List[RunReport]:
        cursor = self.run_reports_col.find().sort("finished_at", -1).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(RunReport.model_validate(doc))
        return results
