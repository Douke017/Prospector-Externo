"""Herramienta independiente sobre fixtures: cero peticiones a sitios públicos."""
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from apps.site_validation import main as cli
from src.site_validation.core import IndependentReferenceProbe


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reference_fixture(tmp_path, monkeypatch):
    config = tmp_path / "config"; config.mkdir()
    for name in ("source_operational_plan.yaml", "source_execution_map.yaml"):
        shutil.copy2(ROOT / "config" / name, config / name)
    calls = []
    def get(self, url, **kwargs):
        calls.append(url)
        parsed = urlsplit(url); origin=f"{parsed.scheme}://{parsed.netloc}"
        if parsed.path=="/robots.txt":
            text=f"User-agent: *\nDisallow: /private\nSitemap: {origin}/sitemap.xml\nSitemap: https://outside.test/sitemap.xml"
            kind="text/plain"
        elif parsed.path=="/sitemap.xml":
            text=f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{origin}/data.csv</loc></url><url><loc>{origin}/listing</loc></url></urlset>'
            kind="application/xml"
        elif parsed.path=="/sitemap_index.xml":
            text="broken xml"; kind="application/xml"
        else:
            text='<a href="/data.csv?utm_source=demo#section">Data</a><a href="/private">Private</a><a href="/listing">List</a><a href="mailto:example@example.test">Mail</a>'
            kind="text/html"
        response=requests.Response(); response.status_code=200; response.url=url
        response._content=text.encode(); response.headers['content-type']=kind
        return response
    monkeypatch.setattr(requests.Session, "get", get)
    return tmp_path, calls


def test_independent_reference_respects_robots_scope_and_compares_baseline(reference_fixture):
    root,calls=reference_fixture
    assert cli.command_plan(root)==0
    assert cli.command_run(root,source_id="finrural",baseline_run="missing",run_id="fixture",max_pages=2,
                           max_resources=10,timeout=.5,delay_seconds=0)==0
    summary=json.loads((root/"output/site-validation/fixture/finrural/summary.json").read_text(encoding="utf-8"))
    assert not any("outside.test" in url or url.endswith('/private') for url in calls)
    assert summary["comparison_counts"]["reference_candidates"]==1
    assert cli.command_recheck_origins(root,source_id="finrural",baseline_run="missing",run_id="origins",
                                       timeout=.5,delay_seconds=0)==0


def test_operational_reference_batch_resume_and_final_matrix_stay_offline(reference_fixture):
    root,calls=reference_fixture
    args=dict(baseline_run="missing",run_id="batch",workers=2,max_pages=1,max_resources=10,timeout=.5,delay_seconds=0)
    assert cli.command_batch(root,**args,resume=False,dry_run=True)==0
    assert not calls
    assert cli.command_batch(root,**args,resume=False,dry_run=False)==0
    before=len(calls)
    assert cli.command_batch(root,**args,resume=True,dry_run=False)==0
    assert len(calls)==before
    assert cli.command_a7(root,batch_run="batch",baseline_run="missing",run_id="a7",workers=1,timeout=.5,
                         delay_seconds=0,resume=False,threshold=.8,dry_run=False)==0
    assert cli.command_a10(root,output_run="matrix",baseline_run="missing",batch_run="batch",a7_run="a7",
        specialized_run="none",a8_run="none",a9_run="none",bcb_origin_run="origins")==0
    assert cli.command_a11(root,matrix_run="matrix",output_run="report")==0
    matrix=json.loads((root/"output/site-validation/matrix/FINAL_MATRIX_52.json").read_text(encoding="utf-8"))
    assert matrix["logical_sources"]==52


@pytest.mark.parametrize("command", ["plan","a9-special-cases","a8-status-only","batch","a7"])
def test_cli_dispatch_dry_run_does_not_probe(reference_fixture,monkeypatch,command):
    root,calls=reference_fixture
    args=["validation","--repo-root",str(root),command]
    if command!="plan": args.extend(["--run-id","fixture","--dry-run"])
    if command=="a7":
        batch=root/"output/site-validation/audit-operational-001/batch_summary.json"
        batch.parent.mkdir(parents=True); batch.write_text('{"results":[]}',encoding="utf-8")
    monkeypatch.setattr(sys,"argv",args)
    assert cli.main()==0
    assert not calls


@pytest.mark.parametrize("status", [403,500,503])
def test_independent_probe_does_not_crawl_when_robots_is_unavailable(monkeypatch,status):
    calls=[]
    def get(self,url,**kwargs):
        calls.append(url); response=requests.Response(); response.url=url; response.status_code=status
        response._content=b'Unavailable'; return response
    monkeypatch.setattr(requests.Session,"get",get)
    rows,stats,_=IndependentReferenceProbe(delay_seconds=0).run("https://example.test/")
    assert rows==[]
    assert calls==["https://example.test/robots.txt"]
