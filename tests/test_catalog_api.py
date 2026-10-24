"""Pruebas para la fachada de consulta REST Catalog API Facade."""

import tempfile
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
from prospector_externo.domain.observations import Run, RunReport
from datetime import datetime, timezone


def test_installed_api_entrypoint_is_callable_without_starting_real_server(tmp_path, monkeypatch):
    import importlib
    import uvicorn

    monkeypatch.setenv("PROSPECTOR_BACKEND", "json")
    monkeypatch.setenv("PROSPECTOR_OUTPUT_DIR", str(tmp_path))
    module = importlib.import_module("apps.catalog_api.main")
    module = importlib.reload(module)
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    module.main([])
    assert calls == [(module.app, {"host": "127.0.0.1", "port": 8000})]


def test_catalog_api_routes():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = LocalJsonRepositoryAdapter(base_output_dir=Path(tmpdir))

        # Registrar datos de prueba
        test_source = Source(
            source_id="asfi",
            name="ASFI",
            entrypoint="https://www.asfi.gob.bo",
            workflow="javascript",
            health_status="ACTIVE"
        )
        repo.save_source(test_source)

        test_snap = Snapshot(
            source_id="asfi",
            run_id="run_123",
            resources_hash="abc",
            total_resources=1,
            resources=[
                ResourceCandidate(
                    resource_key="k1",
                    url="https://www.asfi.gob.bo/doc.pdf",
                    source_id="asfi",
                    title="Doc ASFI",
                    file_extension=".pdf"
                )
            ]
        )
        repo.save_snapshot(test_snap)

        app = FastAPI()
        router = create_catalog_router(catalog_repo=repo, report_repo=repo)
        app.include_router(router)

        client = TestClient(app)

        # GET /catalog/sources
        resp_sources = client.get("/catalog/sources")
        assert resp_sources.status_code == 200
        sources_data = resp_sources.json()
        assert len(sources_data) == 1
        assert sources_data[0]["source_id"] == "asfi"

        # GET /catalog/sources/asfi
        resp_detail = client.get("/catalog/sources/asfi")
        assert resp_detail.status_code == 200
        assert resp_detail.json()["workflow"] == "javascript"

        # GET /catalog/resources
        resp_res = client.get("/catalog/resources?source_id=asfi")
        assert resp_res.status_code == 200
        res_data = resp_res.json()
        assert len(res_data) == 1
        assert res_data[0]["resource_key"] == "k1"

        # GET /catalog/sources/inexistente -> 404
        resp_404 = client.get("/catalog/sources/inexistente")
        assert resp_404.status_code == 404


def test_checkpointed_batch_publishes_shared_catalog_to_http_api(tmp_path):
    """Prueba integrada, sin red: checkpoint -> evidencia B13 -> catálogo -> API."""
    import yaml

    from src.execution.checkpointed_batch import run_checkpointed_sources
    from src.persistence.model import new_checkpoint
    from src.persistence.store import JsonCheckpointStore
    from prospector_externo.domain.observations import (
        ContentStatus, ExecutionStatus, RunReport, SourceRunObservation,
    )

    plan = {"sources": [
        {
            "source_id": f"s{i}",
            "logical_code": f"S{i}",
            "name": f"Fuente {i}",
            "next_phase": "OPERATIONAL_CONFIG" if i < 2 else "B10_STATUS",
        }
        for i in range(52)
    ]}
    sources = {"sources": [
        {"source_id": "s0", "entrypoint": "https://example.org/0"},
        {"source_id": "s1", "entrypoint": "https://example.org/1"},
    ]}
    catalog_dir = tmp_path / "output"
    evidence_dir = catalog_dir / "checkpointed" / "b13-integration"
    checkpoint = new_checkpoint(plan, run_id="b13-integration")
    store = JsonCheckpointStore(tmp_path / "checkpoint.json")
    store.save(checkpoint)

    def offline_executor(**kwargs):
        assert kwargs["catalog_dir"] == catalog_dir
        source_id = yaml.safe_load(kwargs["single_config_path"].read_text(
            encoding="utf-8",
        ))["sources"][0]["source_id"]
        writer = LocalJsonRepositoryAdapter(
            base_output_dir=kwargs["output_dir"],
            catalog_dir=kwargs["catalog_dir"],
        )
        writer.save_source(Source(
            source_id=source_id, name=source_id.upper(),
            entrypoint=f"https://example.org/{source_id}", workflow="html",
        ))
        writer.save_snapshot(Snapshot(
            source_id=source_id, run_id=f"crawl-{source_id}",
            resources_hash=source_id, total_resources=1,
            resources=[ResourceCandidate(
                source_id=source_id, resource_key=f"key-{source_id}",
                url=f"https://example.org/{source_id}/informe.pdf",
                file_extension=".pdf",
            )],
        ))
        obs = SourceRunObservation(
            source_id=source_id, run_id=f"crawl-{source_id}",
            workflow="html", execution_status=ExecutionStatus.SUCCESS,
            content_status=ContentStatus.CHANGED,
        )
        writer.save_source_observation(obs)
        writer.save_report(RunReport(
            run_id=f"crawl-{source_id}",
            started_at=obs.observed_at,
            source_results=[obs],
        ))
        return 0

    _, report = run_checkpointed_sources(
        plan=plan, source_config=sources,
        checkpoint=checkpoint, store=store,
        work_dir=tmp_path / "work", output_root=evidence_dir,
        catalog_dir=catalog_dir, max_sources=2, executor=offline_executor,
    )
    assert report["succeeded"] == 2
    assert report["failed"] == 0

    api_repo = LocalJsonRepositoryAdapter(base_output_dir=catalog_dir)
    app = FastAPI()
    app.include_router(create_catalog_router(api_repo, api_repo))
    client = TestClient(app)

    assert {x["source_id"] for x in client.get("/catalog/sources").json()} == {"s0", "s1"}
    assert {x["resource_key"] for x in client.get("/catalog/resources").json()} == {
        "key-s0", "key-s1",
    }
    assert [x["resource_key"] for x in client.get(
        "/catalog/resources", params={"source_id": "s0"},
    ).json()] == ["key-s0"]
    assert len(client.get("/sources/s1/observations").json()) == 1
    assert client.get("/runs/crawl-s0/report").status_code == 200
    assert (evidence_dir / "s0" / "state" / "sources.json").is_file()
    assert (evidence_dir / "s1" / "s1" / "mapa_s1.json").is_file()


def test_run_detail_works_while_running_and_legacy_reports_remain_readable(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    client = TestClient(app)
    started = datetime.now(timezone.utc)
    repo.save_run(Run(run_id="active", started_at=started,
                      selected_source_ids=["a"], sources_selected=1))
    running = client.get("/runs/active")
    assert running.status_code == 200
    assert running.json()["status"] == "RUNNING"
    assert running.json()["finished_at"] is None
    assert client.get("/runs/active/report").status_code == 404
    assert client.get("/runs/unknown").status_code == 404

    repo.save_report(RunReport(run_id="legacy", started_at=started))
    legacy = client.get("/runs/legacy")
    assert legacy.status_code == 200
    assert legacy.json()["status"] == "COMPLETED"
    assert legacy.json()["legacy_inferred"] is True
    assert client.get("/runs/legacy/report").status_code == 200
    assert len(client.get("/runs").json()) == 1  # No cambia contrato previo.


def test_resources_optional_pagination_preserves_legacy_response(tmp_path):
    """Un cliente antiguo recibe la lista; un cliente nuevo puede paginar sin huecos."""
    repo = LocalJsonRepositoryAdapter(tmp_path)
    for source_id in ("beta", "alpha"):
        repo.save_source(Source(
            source_id=source_id, name=source_id.upper(),
            entrypoint=f"https://{source_id}.example.org", workflow="html",
        ))
        repo.save_snapshot(Snapshot(
            source_id=source_id, run_id=f"run-{source_id}",
            resources_hash=source_id, total_resources=3,
            resources=[ResourceCandidate(
                resource_key=f"{source_id}-{index}", source_id=source_id,
                url=f"https://{source_id}.example.org/{index}.pdf",
                file_extension=".pdf",
            ) for index in (3, 1, 2)],
        ))
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    client = TestClient(app)

    legacy = client.get("/catalog/resources")
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 6

    pages = [client.get("/catalog/resources", params={"limit": 2, "offset": n})
             for n in (0, 2, 4, 6)]
    assert all(response.status_code == 200 for response in pages)
    assert pages[-1].json() == []
    paginated = [item for page in pages[:3] for item in page.json()]
    assert [(item["source_id"], item["resource_key"]) for item in paginated] == [
        (sid, f"{sid}-{i}") for sid in ("alpha", "beta") for i in (1, 2, 3)
    ]
    assert {item["resource_key"] for item in paginated} == {
        item["resource_key"] for item in legacy.json()
    }


def test_resources_pagination_filters_before_slicing_and_validates_bounds(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    for sid in ("alpha", "beta"):
        repo.save_source(Source(source_id=sid, name=sid,
                                entrypoint=f"https://{sid}.example.org", workflow="html"))
        repo.save_snapshot(Snapshot(source_id=sid, run_id=f"run-{sid}",
                                   resources_hash=sid, total_resources=2, resources=[
            ResourceCandidate(source_id=sid, resource_key=f"{sid}-{i}",
                              url=f"https://{sid}.example.org/{i}.pdf") for i in (1, 2)
        ]))
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    client = TestClient(app)
    response = client.get("/catalog/resources", params={
        "source_id": "beta", "offset": 1, "limit": 1,
    })
    assert response.status_code == 200
    assert [resource["resource_key"] for resource in response.json()] == ["beta-2"]
    for params in ({"limit": 0}, {"limit": -1}, {"limit": 501}, {"offset": -1}):
        assert client.get("/catalog/resources", params=params).status_code == 422
    assert client.post("/catalog/resources").status_code == 405


def test_source_pagination_is_stable_and_legacy_list_remains(tmp_path):
    repo = LocalJsonRepositoryAdapter(tmp_path)
    for sid in ("z", "a", "m"):
        repo.save_source(Source(source_id=sid, name=sid,
                                entrypoint=f"https://{sid}.example.org", workflow="html"))
    app = FastAPI()
    app.include_router(create_catalog_router(repo, repo))
    client = TestClient(app)
    assert {source["source_id"] for source in client.get("/catalog/sources").json()} == {"z", "a", "m"}
    assert [source["source_id"] for source in client.get("/catalog/sources", params={
        "offset": 1, "limit": 2,
    }).json()] == ["m", "z"]
    assert client.get("/catalog/sources", params={"offset": -1}).status_code == 422
    assert client.get("/catalog/sources", params={"limit": 501}).status_code == 422


def test_health_reports_catalog_corruption_without_leaking_paths(tmp_path, monkeypatch):
    """Un JSON dañado responde 503 en vez de fingir que la API está saludable."""
    import importlib

    module = importlib.import_module("apps.catalog_api.main")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    monkeypatch.setattr(module, "repo", repo)
    client = TestClient(module.app)
    assert client.get("/health").status_code == 200  # Aún sin corrida inicial.
    sources_file = repo.state_dir / "sources.json"
    sources_file.write_text('{"alpha":', encoding="utf-8")
    broken = client.get("/health")
    assert broken.status_code == 503
    assert broken.json() == {"detail": "Catálogo no disponible"}
    assert str(tmp_path) not in broken.text
    sources_file.write_text("{}", encoding="utf-8")
    assert client.get("/health").status_code == 200


def test_health_rejects_invalid_source_records(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("apps.catalog_api.main")
    repo = LocalJsonRepositoryAdapter(tmp_path)
    monkeypatch.setattr(module, "repo", repo)
    path = repo.state_dir / "sources.json"
    path.write_text('{"bad_source": {"name": "Sin identidad"}}', encoding="utf-8")
    assert TestClient(module.app).get("/health").status_code == 503


def test_hu20_inspect_url_normalization_hash_and_openapi():
    """La respuesta es una huella de URL, no un hash inventado del archivo."""
    import hashlib
    from prospector_externo.domain.normalizer import UrlNormalizer

    app = FastAPI()
    app.include_router(create_catalog_router(object(), object()))
    client = TestClient(app)

    result = client.post("/mapping/inspect", json={
        "url": "HTTPS://EXAMPLE.ORG:443/reports/tabla.csv?utm_source=test&year=2025#detalle",
    })
    assert result.status_code == 200
    data = result.json()
    canonical = "https://example.org/reports/tabla.csv?year=2025"
    assert data == {
        "normalized_url": canonical,
        "file_extension": ".csv",
        "resource_type": "file",
        "url_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "hash_basis": "normalized_url",
        "inspection_mode": "offline_url_only",
    }
    assert data["url_sha256"] == UrlNormalizer.compute_url_hash(canonical)
    contract = client.get("/openapi.json").json()
    endpoint = contract["paths"]["/mapping/inspect"]["post"]
    assert endpoint["requestBody"]["content"]["application/json"]["schema"]
    assert endpoint["responses"]["200"]["content"]["application/json"]["schema"]
    assert "MappingInspectRequest" in contract["components"]["schemas"]
    assert "MappingInspectResponse" in contract["components"]["schemas"]


def test_hu20_accepts_wrapped_resource_and_query_heuristics():
    app = FastAPI()
    app.include_router(create_catalog_router(object(), object()))
    client = TestClient(app)
    wrapped = client.post("/mapping/inspect", json={
        "resource": {"url": "https://example.org/recursos/DATOS.XLSX?utm_medium=ad"}
    })
    assert wrapped.status_code == 200
    assert wrapped.json()["file_extension"] == ".xlsx"
    assert wrapped.json()["resource_type"] == "file"
    assert wrapped.json()["normalized_url"] == "https://example.org/recursos/DATOS.XLSX"

    no_extension = client.post("/mapping/inspect", json={"url": "https://example.org/datos"})
    assert no_extension.status_code == 200
    assert no_extension.json()["file_extension"] == ""
    assert no_extension.json()["resource_type"] == "undetermined"


def test_hu20_rejects_invalid_requests_as_422():
    app = FastAPI()
    app.include_router(create_catalog_router(object(), object()))
    client = TestClient(app)
    bad_payloads = [
        {}, {"resource": {}},
        {"url": "not-a-url"}, {"url": "ftp://example.org/file.csv"},
        {"url": "javascript:alert(1)"}, {"url": "https:///missing-host.pdf"},
        {"url": "https://user:secret@example.org/file.pdf"},
        {"url": "https://example.org/" + "a" * 2100},
        {"url": "https://one.example.org/a.pdf",
         "resource": {"url": "https://two.example.org/b.pdf"}},
    ]
    for payload in bad_payloads:
        result = client.post("/mapping/inspect", json=payload)
        assert result.status_code == 422, (payload, result.status_code, result.text)


def test_hu20_matching_dual_url_and_no_catalog_mutation():
    """No requiere conexión a catálogo ni ejecuta solicitudes de crawling."""
    app = FastAPI()
    app.include_router(create_catalog_router(object(), object()))
    client = TestClient(app)
    resp = client.post("/mapping/inspect", json={
        "url": "https://example.org/data.zip?utm_campaign=campaign",
        "resource": {"url": "https://example.org/data.zip"},
    })
    assert resp.status_code == 200
    assert resp.json()["normalized_url"] == "https://example.org/data.zip"
    assert resp.json()["file_extension"] == ".zip"
