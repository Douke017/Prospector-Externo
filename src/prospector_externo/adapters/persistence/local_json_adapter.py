"""
Adaptador de persistencia en archivos JSON locales.
Mantiene compatibilidad con contratos de salida (Standard, Tree, Compact)
y almacena reportes de corrida y estado histórico en disco.
"""

import json
import os
import shutil
import tempfile
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from prospector_externo.ports.catalog_repository import CatalogRepositoryPort
from prospector_externo.ports.run_report_repository import RunReportRepositoryPort
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import (
    Run, RunReport, SourceRunObservation,
)


class LocalJsonRepositoryAdapter(CatalogRepositoryPort, RunReportRepositoryPort):
    """Implementa persistencia local en archivos JSON con particionamiento por fuente."""

    supports_discovery_checkpoints = True

    def _discovery_checkpoint_path(self, source_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", source_id):
            raise ValueError("DISCOVERY_CHECKPOINT_UNSAFE_SOURCE_ID")
        # Cursor aislado por salida B9B, nunca en el catálogo global compartido.
        return self.base_dir / "state" / "discovery_progress" / f"{source_id}.json"

    def get_discovery_checkpoint(self, source_id: str) -> Optional[dict]:
        path = self._discovery_checkpoint_path(source_id)
        if not path.exists():
            return None
        if path.stat().st_size > 32_000_000:
            raise ValueError("DISCOVERY_CHECKPOINT_TOO_LARGE")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("DISCOVERY_CHECKPOINT_INVALID_JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("DISCOVERY_CHECKPOINT_INVALID")
        return value

    def save_discovery_checkpoint(self, source_id: str, checkpoint: dict) -> None:
        if checkpoint.get("source_id") != source_id:
            raise ValueError("DISCOVERY_CHECKPOINT_SOURCE_MISMATCH")
        path = self._discovery_checkpoint_path(source_id)
        run_id = checkpoint.get("last_run_id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
            raise ValueError("DISCOVERY_CHECKPOINT_UNSAFE_RUN_ID")
        content = json.dumps(checkpoint, ensure_ascii=False)
        # Conservar evidencia del intento anterior además del cursor vigente.
        archive = path.parent / source_id / f"{run_id}.json"
        self._atomic_write_text(archive, content)
        self._atomic_write_text(path, content)

    def __init__(self, base_output_dir: Path, catalog_dir: Optional[Path] = None):
        self.base_dir = Path(base_output_dir)
        catalog_root = Path(catalog_dir) if catalog_dir is not None else self.base_dir
        self.state_dir = catalog_root / "state"
        self.reports_dir = catalog_root / "reports"

        # Crear estructura de carpetas
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "snapshots").mkdir(exist_ok=True)
        (self.state_dir / "observations").mkdir(exist_ok=True)
        (self.state_dir / "runs").mkdir(exist_ok=True)

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        """Sustituye un JSON completo sin dejar un archivo truncado ante un fallo.

        No constituye una transacción de varias escrituras ni un lock multiproceso.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".tmp-", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _mirror_evidence(self, path: Path, relative_path: Path) -> None:
        """Conserva la evidencia aislada que consume B13 al compartir el catálogo."""
        if self.base_dir == self.state_dir.parent:
            return
        target = self.base_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent,
                prefix=".tmp-", suffix=".tmp", delete=False,
            ) as handle, path.open("rb") as source:
                temporary = Path(handle.name)
                shutil.copyfileobj(source, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_snapshot(self, snapshot: Snapshot) -> None:
        """Guarda snapshot en carpeta de estado y exporta los 3 formatos JSON en output/<source_id>/."""
        # 1. Guardar copia histórica de snapshot
        snap_path = self.state_dir / "snapshots" / f"{snapshot.source_id}_{snapshot.run_id}.json"
        self._atomic_write_text(snap_path, snapshot.model_dump_json(indent=2))
        self._mirror_evidence(snap_path, Path("state/snapshots") / snap_path.name)

        # 2. Exportar en la carpeta dedicada de la fuente
        source_dir = self.base_dir / snapshot.source_id
        source_dir.mkdir(parents=True, exist_ok=True)

        # 2.1 Contrato Estándar
        source_state = self.get_source(snapshot.source_id)
        source_name = source_state.name if source_state else snapshot.source_id.upper()
        source_entrypoint = source_state.entrypoint if source_state else ""
        standard_contract = {
            "version": "1.0.0",
            "source": {
                "id": snapshot.source_id,
                "name": source_name,
                "entrypoint": source_entrypoint
            },
            "run": {
                "run_id": snapshot.run_id,
                "timestamp": snapshot.captured_at.isoformat(),
                "status": snapshot.execution_status or "SUCCESS"
            },
            "datasets": [
                {
                    "dataset_id": f"{snapshot.source_id}_dataset",
                    "title": f"Recursos Públicos — {snapshot.source_id.upper()}",
                    "resources": [r.model_dump() for r in snapshot.resources]
                }
            ],
            "metadata": {
                "generated_by": "prospector-externo",
                "total_resources": snapshot.total_resources,
                "resources_hash": snapshot.resources_hash
            }
        }
        self._atomic_write_text(
            source_dir / f"mapa_{snapshot.source_id}.json",
            json.dumps(standard_contract, indent=2, ensure_ascii=False, default=str),
        )

        # 2.2 Formato Compacto IA
        compact = {
            "source_id": snapshot.source_id,
            "run_id": snapshot.run_id,
            "total": snapshot.total_resources,
            "resources": [
                {
                    "key": r.resource_key,
                    "url": r.url,
                    "title": r.title,
                    "ext": r.file_extension,
                    "status": r.change_status,
                    "extracted_from": r.extracted_from_archive,
                    "resource_type": r.resource_type,
                    "api": r.api.model_dump(mode="json") if r.api is not None else None,
                }
                for r in snapshot.resources
            ]
        }
        self._atomic_write_text(
            source_dir / f"mapa_{snapshot.source_id}_compact.json",
            json.dumps(compact, indent=2, ensure_ascii=False, default=str),
        )

        # 2.3 Formato Árbol Jerárquico
        tree = {
            "name": f"Raíz {snapshot.source_id.upper()}",
            "type": "source",
            "children": [
                {
                    "name": r.title or r.resource_key,
                    "type": "resource",
                    "url": r.url,
                    "extension": r.file_extension,
                    "status": r.change_status,
                    "resource_type": r.resource_type,
                    "api": r.api.model_dump(mode="json") if r.api is not None else None,
                }
                for r in snapshot.resources
            ]
        }
        self._atomic_write_text(
            source_dir / f"mapa_{snapshot.source_id}_tree.json",
            json.dumps(tree, indent=2, ensure_ascii=False, default=str),
        )

    def get_latest_snapshot(self, source_id: str) -> Optional[Snapshot]:
        """Busca el snapshot más reciente para una fuente."""
        snapshots_dir = self.state_dir / "snapshots"
        matching = snapshots_dir.glob(f"{source_id}_*.json")
        snapshots = []
        for path in matching:
            try:
                snap = Snapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
                if snap.source_id == source_id:
                    snapshots.append(snap)
            except (OSError, ValueError):
                continue
        return max(snapshots, key=lambda snap: snap.captured_at, default=None)

    def save_source_observation(self, observation: SourceRunObservation) -> None:
        """Guarda observación de la corrida para la fuente."""
        obs_file = self.state_dir / "observations" / f"{observation.source_id}_{observation.run_id}.json"
        self._atomic_write_text(obs_file, observation.model_dump_json(indent=2))
        self._mirror_evidence(obs_file, Path("state/observations") / obs_file.name)

    def get_source_observations(self, source_id: str, limit: int = 20) -> List[SourceRunObservation]:
        """Recupera observaciones históricas de una fuente."""
        obs_dir = self.state_dir / "observations"
        results = []
        for file in obs_dir.glob(f"{source_id}_*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    observation = SourceRunObservation.model_validate(json.load(f))
                    if observation.source_id == source_id:
                        results.append(observation)
            except Exception:
                pass
        return sorted(results, key=lambda obs: obs.observed_at, reverse=True)[:limit]

    def save_source(self, source: Source) -> None:
        """Persiste o actualiza la fuente en el archivo maestro sources.json."""
        sources_file = self.state_dir / "sources.json"
        sources_dict = {}
        if sources_file.exists():
            try:
                with open(sources_file, "r", encoding="utf-8") as f:
                    sources_dict = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"No se puede actualizar un catálogo de fuentes inválido: {sources_file}") from exc
            if not isinstance(sources_dict, dict):
                raise ValueError(f"El catálogo de fuentes debe ser un objeto JSON: {sources_file}")

        sources_dict[source.source_id] = source.model_dump(mode="json")
        self._atomic_write_text(
            sources_file, json.dumps(sources_dict, indent=2, ensure_ascii=False),
        )
        if self.base_dir != self.state_dir.parent:
            local_sources = self.base_dir / "state" / "sources.json"
            local_sources.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write_text(local_sources,
                json.dumps({source.source_id: sources_dict[source.source_id]},
                           indent=2, ensure_ascii=False),
            )

    def get_source(self, source_id: str) -> Optional[Source]:
        """Obtiene una fuente registrada."""
        sources_file = self.state_dir / "sources.json"
        if not sources_file.exists():
            return None
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if source_id in data:
                    return Source.model_validate(data[source_id])
        except Exception:
            pass
        return None

    def list_sources(self) -> List[Source]:
        """Lista todas las fuentes registradas."""
        sources_file = self.state_dir / "sources.json"
        if not sources_file.exists():
            return []
        try:
            with open(sources_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [Source.model_validate(item) for item in data.values()]
        except Exception:
            return []

    def check_health(self) -> None:
        """Valida la legibilidad del catálogo sin alterar sus archivos.

        list_sources conserva su respuesta legacy vacía ante errores de lectura;
        el healthcheck NO debe confundir un JSON corrupto con un catálogo vacío.
        """
        sources_file = self.state_dir / "sources.json"
        if sources_file.exists():
            with sources_file.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, dict):
                raise ValueError("Formato inválido de sources.json")
            for item in payload.values():
                Source.model_validate(item)

    def get_resource_catalog(self, source_id: str) -> Optional[List[ResourceCandidate]]:
        """Catálogo materializado por fuente; None = no migrado desde snapshots legacy."""
        path = self.state_dir / "resource_catalog" / f"{source_id}.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["source_id"] != source_id:
            raise ValueError(f"Catálogo con identidad incorrecta: {path}")
        return [ResourceCandidate.model_validate(r) for r in payload["resources"]]

    def save_resource_catalog(
        self, source_id: str, run_id: str, resources: List[ResourceCandidate]
    ) -> None:
        """Un checkpoint pequeño por fuente, escrito atómicamente, sin tocar RAW."""
        if any(r.source_id != source_id for r in resources):
            raise ValueError("El catálogo contiene recursos de otra fuente")
        directory = self.state_dir / "resource_catalog"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{source_id}.json"
        payload = json.dumps({
            "source_id": source_id, "run_id": run_id,
            "resources": [r.model_dump(mode="json") for r in resources],
        }, ensure_ascii=False, indent=2)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix=f".{source_id}.", suffix=".tmp", delete=False,
            ) as temp:
                tmp_path = Path(temp.name)
                temp.write(payload)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(tmp_path, path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        # B13 corre con catálogo compartido. Se conserva además una copia
        # de esta reconciliación en la evidencia aislada de la fuente, para
        # sincronizar después a Mongo sin recrawlear ni usar un global obsoleto.
        self._mirror_evidence(path, Path("state/resource_catalog") / path.name)
        source = self.get_source(source_id)
        if source:
            source.catalog_last_run_id = run_id
            self.save_source(source)

    def list_resources(self, source_id: Optional[str] = None) -> List[ResourceCandidate]:
        """Recursos no removidos del catálogo; fallback a snapshots legacy."""
        if source_id:
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
        """Checkpoint atómico del estado del proceso, no del reporte de negocio."""
        directory = self.state_dir / "runs"
        directory.mkdir(exist_ok=True)
        path = directory / f"run_{run.run_id}.json"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix=".run_", suffix=".tmp", delete=False,
            ) as file:
                tmp_path = Path(file.name)
                file.write(run.model_dump_json(indent=2))
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_path, path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        self._mirror_evidence(path, Path("state/runs") / path.name)

    def get_run(self, run_id: str) -> Optional[Run]:
        path = self.state_dir / "runs" / f"run_{run_id}.json"
        if path.is_file():
            run = Run.model_validate_json(path.read_text(encoding="utf-8"))
            if run.run_id != run_id:
                raise ValueError("Archivo de corrida con identidad incorrecta")
            return run
        report = self.get_report(run_id)
        return Run.from_legacy_report(report) if report else None

    def list_runs(self, limit: int = 50) -> List[Run]:
        states = {}
        for file in (self.state_dir / "runs").glob("run_*.json"):
            run = Run.model_validate_json(file.read_text(encoding="utf-8"))
            states[run.run_id] = run
        # Los reportes anteriores a esta versión también son consultables.
        for report_file in self.reports_dir.glob("run_*.json"):
            report = RunReport.model_validate_json(report_file.read_text(encoding="utf-8"))
            states.setdefault(report.run_id, Run.from_legacy_report(report))
        return sorted(states.values(), key=lambda run: run.started_at, reverse=True)[:limit]

    def save_report(self, report: RunReport) -> None:
        """Persiste el RunReport de una corrida."""
        report_file = self.reports_dir / f"run_{report.run_id}.json"
        self._atomic_write_text(report_file, report.model_dump_json(indent=2))
        self._mirror_evidence(report_file, Path("reports") / report_file.name)

    def get_report(self, run_id: str) -> Optional[RunReport]:
        """Recupera un reporte de corrida por su ID."""
        report_file = self.reports_dir / f"run_{run_id}.json"
        if not report_file.exists():
            return None
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                return RunReport.model_validate(json.load(f))
        except Exception:
            return None

    def list_reports(self, limit: int = 50) -> List[RunReport]:
        """Lista los reportes de corrida más recientes."""
        reports = []
        for f in self.reports_dir.glob("run_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fl:
                    reports.append(RunReport.model_validate(json.load(fl)))
            except Exception:
                pass
        return sorted(reports, key=lambda item: item.started_at, reverse=True)[:limit]
