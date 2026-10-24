"""Sincroniza evidencias B13 hacia Mongo sin realizar crawling.

Por defecto --check es 100 % offline y no abre Mongo. --commit escribe
únicamente las corridas validadas y admite reejecución idempotente.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))

from prospector_externo.application.catalog_sync import collect_evidence, read_evidence, sync_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B13 JSON a MongoDB (sin red a las fuentes)")
    source_mode = parser.add_mutually_exclusive_group(required=True)
    source_mode.add_argument("--pipeline-run-id", help="Run ID del pipeline B13")
    source_mode.add_argument("--evidence-dir", help="Carpeta aislada ya ejecutada (p. ej. FAM de 12F)")
    parser.add_argument("--output-root", default="output/checkpointed")
    parser.add_argument("--source", default=None, help="ID lógico opcional, para sincronizar una fuente")
    parser.add_argument("--mongo-uri", default=os.environ.get("PROSPECTOR_MONGO_URI", "mongodb://127.0.0.1:27018"))
    parser.add_argument("--mongo-database", default="prospector_externo")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Solo valida archivos; no escribe ni conecta")
    mode.add_argument("--commit", action="store_true", help="Publica en MongoDB")
    args = parser.parse_args(argv)

    try:
        if args.evidence_dir:
            folder = Path(args.evidence_dir)
            evidence = read_evidence(folder, args.source or folder.name)
            if evidence is None:
                raise ValueError(f"No se encontró reporte en {folder}")
            rows = [evidence]
        else:
            rows = collect_evidence(Path(args.output_root), args.pipeline_run_id, source_id=args.source)
        summary = {
            "mode": "COMMIT" if args.commit else "CHECK_SIN_RED",
            "sources_checked": len(rows),
            "resources_checked": sum(len(row.resources or ()) for row in rows),
            "physical_sources": len({row.source.source_id for row in rows}),
        }
        if args.commit:
            from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
            repo = MongoPersistenceAdapter(connection_uri=args.mongo_uri, database_name=args.mongo_database)
            try:
                repo.check_health()
                summary.update(sync_evidence(repo, rows))
            finally:
                repo.client.close()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"CATALOG_SYNC_ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
