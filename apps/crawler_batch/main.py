"""CLI batch del Prospector Externo."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.domain.observations import ExecutionStatus
import prospector_externo.workflows  # noqa: F401 - autoregistro


def setup_logging(verbose: bool = False) -> None:
    # Evita que un mensaje de excepción con bytes/caracteres extraños genere
    # un segundo UnicodeEncodeError en consolas Windows.
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def report_exit_code(report) -> int:
    """Un lote incompleto debe quedar como candidato de reintento."""
    if int(getattr(report, "sources_failed", 0) or 0) > 0:
        return 2
    if any(
        row.execution_status == ExecutionStatus.PARTIAL_SUCCESS
        for row in getattr(report, "source_results", [])
    ):
        return 3
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prospector Externo Batch")
    parser.add_argument("--config", default=str(project_root / "config" / "sources.yaml"))
    parser.add_argument("--source", default=None)
    parser.add_argument("--output-dir", default=str(project_root / "output"))
    parser.add_argument("--catalog-dir", default=None,
                        help="Directorio compartido de state/ y reports/ para la API.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume-discovery", action="store_true",
                        help="Retoma la frontera HTML/HTML comentado compatible, sin relajar límites.")
    parser.add_argument("--pipeline-run-id", default=None,
                        help="ID de lote B9B/B13; distinto del run_id de esta corrida.")
    parser.add_argument("--backend", choices=["json", "mongo"], default="json")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--recover-abandoned", action="store_true",
                        help="Solo reconcilia corridas abandonadas; NO ejecuta crawling.")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("prospector.batch.main")

    if args.backend == "mongo":
        from prospector_externo.adapters.persistence.mongo_adapter import MongoPersistenceAdapter
        repo = MongoPersistenceAdapter(connection_uri=args.mongo_uri)
    else:
        repo = LocalJsonRepositoryAdapter(
            base_output_dir=Path(args.output_dir),
            catalog_dir=Path(args.catalog_dir) if args.catalog_dir else None,
        )

    orchestrator = RunOrchestrator(
        catalog_repo=repo,
        report_repo=repo,
        config_path=Path(args.config),
    )

    if args.recover_abandoned:
        recovered = orchestrator.recover_abandoned_runs()
        logger.info("Reconciliación terminada: %d corridas recuperadas.", len(recovered))
        for run_id in recovered:
            logger.info("RECOVERED_RUN_ID: %s", run_id)
        return

    try:
        report = asyncio.run(
            orchestrator.run_batch_async(
                source_id_filter=args.source,
                force=args.force,
                resume_discovery=args.resume_discovery,
                pipeline_run_id=args.pipeline_run_id,
            )
        )
        logger.info("Proceso concluido. Run ID: %s", report.run_id)

        exit_code = report_exit_code(report)
        if exit_code:
            logger.error(
                "La corrida terminó con fuentes FAILED o cobertura parcial (%d FAILED); "
                "se propaga código de salida %d.",
                report.sources_failed,
                exit_code,
            )
            raise SystemExit(exit_code)

    except Exception as exc:
        logger.critical("Fallo durante la corrida batch: %s", ascii(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
