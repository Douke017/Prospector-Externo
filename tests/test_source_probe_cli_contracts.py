"""Probes históricos con crawler real en proceso y transporte HTTP simulado."""
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import yaml

from apps.source_mapping import main as mapping
from apps.source_characterization import main as characterization
from apps.source_recovery import main as recovery
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.orchestrator import RunOrchestrator
from prospector_externo.infrastructure.http_runtime import AsyncHttpRuntime


@pytest.fixture
def crawler_subprocess(monkeypatch):
    invocations = []
    def execute(command, **kwargs):
        invocations.append(command)
        config=Path(command[command.index("--config")+1]); output=Path(command[command.index("--output-dir")+1])
        requests=[]
        def handler(request):
            requests.append(str(request.url))
            if request.url.path=="/robots.txt": return httpx.Response(200,text="User-agent: *\nAllow: /")
            if "sitemap" in request.url.path: return httpx.Response(404)
            return httpx.Response(200,text='<a href="/monthly.csv">Monthly report</a>',headers={"content-type":"text/html"})
        repo=LocalJsonRepositoryAdapter(output)
        runner=RunOrchestrator(repo,repo,config,http_runtime_factory=lambda:AsyncHttpRuntime(transport=httpx.MockTransport(handler)))
        report=runner.run_batch(force=True)
        trace='\n'.join(f'HTTP Request: GET {url} "HTTP/1.1 200 OK"' for url in requests)
        return subprocess.CompletedProcess(command,0,stdout=trace,stderr="")
    monkeypatch.setattr(subprocess,"run",execute)
    return invocations


def test_mapping_then_characterization_reindex_does_not_repeat_acquisition(tmp_path,monkeypatch,crawler_subprocess):
    inventory=tmp_path/"inventory.yaml"
    inventory.write_text(yaml.safe_dump({"sources":[
        {"source_id":sid,"logical_code":sid.upper(),"name":sid,"entrypoint":"https://fixture.test/", "host_key":"fixture.test"}
        for sid in ["primary","alias"]+[f"alias{i}" for i in range(50)]]}),encoding="utf-8")
    mapping_dir=tmp_path/"mapping"
    monkeypatch.setattr(sys,"argv",["mapping","--inventory",str(inventory),"--report-dir",str(mapping_dir),
        "--rate-limit","0","--inter-source-delay","0"])
    assert mapping.main()==0
    assert len(crawler_subprocess)==1
    rows=json.loads((mapping_dir/"latest.json").read_text(encoding="utf-8"))
    assert rows["physical_unique_entrypoint_probes"]==1
    assert len(rows["results"])==52
    assert all(r["resources_found"]==1 for r in rows["results"])
    target=tmp_path/"characterization"
    args=["characterization","--inventory",str(inventory),"--mapping-report",str(mapping_dir/"latest.json"),
          "--report-dir",str(target),"--rate-limit","0","--inter-source-delay","0"]
    monkeypatch.setattr(sys,"argv",args)
    assert characterization.main()==0
    after=len(crawler_subprocess)
    assert after>1
    monkeypatch.setattr(sys,"argv",args+["--offline-reindex"])
    assert characterization.main()==0
    assert len(crawler_subprocess)==after
    result=json.loads((target/"latest.json").read_text(encoding="utf-8"))
    assert result["logical_sources"]==52
    assert result["physical_entrypoints"]==1
    assert {r["characterization_status"] for r in result["logical_results"]}=={"HTTP_HTML_CANDIDATE"}


@pytest.mark.parametrize("status",[200,503])
def test_recovery_cli_records_actual_response_and_keeps_tls_verification(tmp_path,monkeypatch,status):
    report=tmp_path/"audit.json"
    report.write_text(json.dumps({"unresolved_results":[{"physical_probe_source_id":"fixture",
        "entrypoint":"https://example.test","logical_codes":["FIXTURE"],
        "forensic_reason":"NO_HTTP_TRACE_EXECUTION_FAILURE"}]}),encoding="utf-8")
    client_type=httpx.Client; settings=[]
    def client(**kwargs):
        settings.append(kwargs)
        return client_type(**kwargs,transport=httpx.MockTransport(lambda request:httpx.Response(status,text="User-agent: *\nAllow: /")))
    monkeypatch.setattr(recovery.httpx,"Client",client)
    monkeypatch.setattr(recovery,"resolve_host",lambda host:{"ok":True,"addresses":["127.0.0.1"]})
    monkeypatch.setattr(sys,"argv",["recovery","--audit-report",str(report),"--output-dir",str(tmp_path/"recovery")])
    assert recovery.main()==0
    result=json.loads((tmp_path/"recovery/latest.json").read_text(encoding="utf-8"))
    assert result["targets"]==1
    assert settings[0]["verify"] is True
    assert all(v["root"]["status_code"]==status for v in result["results"][0]["variants"])
