"""HU-22: corte real del proceso durante la tercera fuente.

No accede a red, no usa configuraciones reales y no escribe en output/.
El proceso hijo genera dos checkpoints reales y queda bloqueado en la tercera
fuente; el padre fuerza su terminación y abre un nuevo repositorio/API para
verificar que el trabajo confirmado sobrevivió al corte.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.domain.observations import RunStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]


_CHILD_PROGRAM = textwrap.dedent("""
    import asyncio
    import sys
    from pathlib import Path

    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    from prospector_externo.application.orchestrator import RunOrchestrator
    from prospector_externo.domain.models import ResourceCandidate, DiscoveredUrl
    from prospector_externo.domain.observations import CoverageStats
    from prospector_externo.kernel.contracts import ExtractionResult

    root = Path(sys.argv[1])
    repo = LocalJsonRepositoryAdapter(root / "isolated_output")
    runner = RunOrchestrator(repo, repo, root / "sources.yaml", max_source_concurrency=1)

    async def dispatch(config):
        if config.source_id == "source_three":
            (root / "third_started.marker").write_text("third_dispatch_entered", encoding="utf-8")
            await asyncio.Event().wait()  # Solo termina si el padre mata este proceso.
        index = config.source_id.rsplit("_", 1)[-1]
        return ExtractionResult(
            source_id=config.source_id,
            success=True,
            resources=[ResourceCandidate(
                source_id=config.source_id,
                resource_key=f"{config.source_id}_pdf",
                url=f"https://example.org/{index}/document.pdf",
                title="Documento de prueba",
                file_extension=".pdf",
            )],
            discovered_urls=[DiscoveredUrl(
                source_id=config.source_id,
                normalized_url=f"https://example.org/{index}/listing",
                raw_url=f"/{index}/listing",
                parent_url=f"https://example.org/{index}",
            )],
            coverage=CoverageStats(
                stop_reason="QUEUE_EXHAUSTED", resources_found=1,
                pages_visited=1, urls_discovered=1,
            ),
        )

    runner.dispatcher.dispatch = dispatch
    asyncio.run(runner.run_batch_async(force=True))
""")


def test_hu22_forced_kill_preserves_two_completed_sources_and_api(tmp_path):
    """Mata el proceso hijo realmente, sin que este pueda ejecutar finally."""
    sources = ["source_one", "source_two", "source_three"]
    (tmp_path / "sources.yaml").write_text(
        yaml.safe_dump({"sources": [
            {"source_id": source, "entrypoint": f"https://example.org/{source}",
             "workflow": "html", "rate_limit_seconds": 0}
            for source in sources
        ]}), encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + str(PROJECT_ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD_PROGRAM, str(tmp_path)],
        cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    marker = tmp_path / "third_started.marker"
    try:
        deadline = time.monotonic() + 25
        while not marker.exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=3)
                raise AssertionError(
                    f"El hijo terminó antes de entrar en la tercera fuente "
                    f"(rc={child.returncode}): {stdout[-1500:]} {stderr[-1500:]}"
                )
            if time.monotonic() > deadline:
                raise AssertionError("El proceso hijo no alcanzó la tercera fuente")
            time.sleep(0.05)

        assert child.poll() is None, "Se esperaba una corrida activa en la tercera fuente"
        child.kill()  # SIGKILL en POSIX; TerminateProcess en Windows.
        child.communicate(timeout=10)
        assert child.returncode != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)

    # Nuevo adaptador y API, como después de reiniciar la aplicación.
    repository = LocalJsonRepositoryAdapter(tmp_path / "isolated_output")
    runs = repository.list_runs()
    assert len(runs) == 1
    run_id = runs[0].run_id
    # Un kill forzoso no permite al hijo actualizar su estado en memoria.
    # No se inventa INTERUPTED ni un reporte inexistente.
    assert runs[0].status == RunStatus.RUNNING
    assert repository.get_report(run_id) is None

    app = FastAPI()
    app.include_router(create_catalog_router(repository, repository))
    client = TestClient(app)
    run_response = client.get(f"/runs/{run_id}")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "RUNNING"
    assert client.get(f"/runs/{run_id}/report").status_code == 404

    for source in sources[:2]:
        snapshot = repository.get_latest_snapshot(source)
        assert snapshot is not None
        assert snapshot.run_id == run_id
        assert [r.resource_key for r in snapshot.resources] == [f"{source}_pdf"]
        assert len(snapshot.discovered_urls) == 1
        assert repository.get_resource_catalog(source) is not None
        assert [r.resource_key for r in repository.list_resources(source)] == [f"{source}_pdf"]
        obs = repository.get_source_observations(source)
        assert len(obs) == 1 and obs[0].run_id == run_id
        assert obs[0].execution_status.value == "SUCCESS"
        assert client.get(f"/catalog/resources?source_id={source}").status_code == 200
        assert len(client.get(f"/catalog/resources?source_id={source}").json()) == 1
        api_observations = client.get(f"/sources/{source}/observations")
        assert api_observations.status_code == 200
        assert len(api_observations.json()) == 1

    assert repository.get_latest_snapshot(sources[2]) is None
    assert repository.get_source_observations(sources[2]) == []
    assert client.get(f"/catalog/resources?source_id={sources[2]}").json() == []

    # Batch 10B: el proceso ya no existe; el nuevo orquestador puede reconciliar
    # sin borrar evidencias ni transformar una fuente parcial en completa.
    from prospector_externo.application.orchestrator import RunOrchestrator
    recovered = RunOrchestrator(repository, repository).recover_abandoned_runs()
    assert recovered == [run_id]
    state = LocalJsonRepositoryAdapter(tmp_path / "isolated_output").get_run(run_id)
    assert state.status == RunStatus.INTERRUPTED
    assert state.failure_code == "OWNER_PROCESS_TERMINATED"
    assert state.sources_processed == 2
    assert state.sources_failed == 0
    assert repository.get_report(run_id) is None
    assert client.get(f"/runs/{run_id}").json()["status"] == "INTERRUPTED"
    assert repository.get_latest_snapshot(sources[0]).run_id == run_id
    assert repository.get_latest_snapshot(sources[1]).run_id == run_id
    assert repository.get_latest_snapshot(sources[2]) is None
