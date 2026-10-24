"""CLI B13 -> subprocess B9B -> crawler -> proyección -> paquete -> REST.

Usa un servidor local de páginas sintéticas; no realiza solicitudes públicas.
"""
import json
import os
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.b13_pipeline.main import command_run
from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter


ROOT = Path(__file__).resolve().parents[1]


def test_local_b13_subprocess_resume_package_and_api(tmp_path, monkeypatch):
    calls = Counter()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            calls[self.path] += 1
            bodies = {"/robots.txt": "User-agent: *\nAllow: /",
                      "/root": '<a href="/a.csv">Monthly report</a><a href="/next">Next</a>',
                      "/next": '<a href="/b.pdf">Annual report</a>',
                      "/DL_SelectFields": '<form method="POST"><input name="fields"></form>'}
            self.send_response(200 if self.path in bodies else 404)
            self.send_header("Content-Type", "text/plain" if self.path=="/robots.txt" else "text/html")
            self.end_headers(); self.wfile.write(bodies.get(self.path, "missing").encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    config = tmp_path / "config"; config.mkdir()
    def write(name, data): (config/name).write_text(yaml.safe_dump(data,sort_keys=False),encoding="utf-8")
    write("sources.yaml", {"sources": [
        {"source_id":"alpha", "entrypoint":base, "workflow":"html", "seeds":[base+"/root"],
         "rate_limit_seconds":0, "max_requests":2, "discover_sitemaps":False, "discover_apis":False, "inspect_archives":False},
        {"source_id":"transtats", "entrypoint":base, "workflow":"custom", "seeds":[base+"/DL_SelectFields"],
         "rate_limit_seconds":0, "max_requests":3, "inspect_archives":False,
         "custom_config":{"custom_kind":"download_form_acquisition_job","allowed_methods":["GET","HEAD"]}},
    ]})
    write("source_operational_plan.yaml", {"sources":[
        {"source_id":sid,"next_phase":"OPERATIONAL_CONFIG"} for sid in ("alpha","fdta_valles","transtats")]+[
        {"source_id":f"status{i}","next_phase":"B10_STATUS"} for i in range(49)]})
    write("source_execution_map.yaml", {"sources":[
        {"logical_source_id":"alpha","config_source_id":"alpha"},
        {"logical_source_id":"transtats","config_source_id":"transtats"},
        {"logical_source_id":"fdta_valles","config_source_id":"alpha","acquisition_hold":"IDENTITY_UNVERIFIED_FDTA_ICCO"}]})
    monkeypatch.setenv("PYTHONPATH", str(ROOT/"src")+os.pathsep+str(ROOT))
    try:
        catalog = tmp_path / "isolated-catalog"
        assert command_run(tmp_path,run_id="local_e2e",resume=False,max_sources=1,catalog_dir=catalog)==3
        state_path=tmp_path/".runtime/b13_pipeline/local_e2e/state.json"
        first=json.loads(state_path.read_text(encoding="utf-8"))
        assert first["pending"]==["transtats"]
        assert first["coverage_partial_sources"]==["alpha"]
        assert command_run(tmp_path,run_id="local_e2e",resume=True,max_sources=None,catalog_dir=catalog)==0
        final=json.loads(state_path.read_text(encoding="utf-8"))
        assert final["status"]=="READY" and not final["coverage_partial_sources"]
        assert final["counts"]=={"ACQUISITION_JOB_READY":1,"DATAX_READY":1,"EXTERNAL_BLOCKER":1}
        assert calls["/root"]==calls["/next"]==calls["/DL_SelectFields"]==1
        assert not calls["/a.csv"] and not calls["/b.pdf"]
        repo=LocalJsonRepositoryAdapter(catalog)
        assert not (tmp_path/"output/state").exists()
        app=FastAPI(); app.include_router(create_catalog_router(repo,repo)); client=TestClient(app)
        assert client.get("/catalog/resources?source_id=alpha&limit=1&offset=1").status_code==200
        resources=client.get("/catalog/resources?source_id=alpha").json(); assert len(resources)==2
        assert {r["file_extension"] for r in resources}=={".csv",".pdf"}
        reports=client.get("/runs").json(); assert len(reports)==3
        crawl_id=next(r["run_id"] for r in reports if r["source_results"][0]["source_id"]=="alpha")
        assert crawl_id!="local_e2e"
        assert client.get(f"/runs/{crawl_id}").json()["pipeline_run_id"]=="local_e2e"
        assert client.get("/catalog/sources/fdta_valles").status_code==404
        assert len(client.get("/sources/alpha/observations").json())==2
        print("FIXTURE_CATALOG="+str(catalog))
        print("FIXTURE_PIPELINE_STATE="+str(state_path))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
