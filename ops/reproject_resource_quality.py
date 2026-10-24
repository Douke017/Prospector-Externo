"""Compare preserved DATAX projections with current policy, strictly offline.

Writes only to a new isolated destination. Never crawls, syncs or replaces a ZIP.
The baseline must have been captured before work; all protected files are checked.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prospector_externo.adapters.api.routes import create_catalog_router
from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
from prospector_externo.application.legacy_stats_exporter import LegacyStatsContractValidator
from prospector_externo.application.projection_export_pipeline import DataxProjectionExportPipeline
from prospector_externo.domain.normalizer import UrlNormalizer


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def representations(projection):
    return {r["resource_key"]: r for f in projection["families"]
            for p in f["periods"] for r in p["representations"]}


def legacy_records(document):
    records = Counter()
    def walk(node):
        if "url_descarga" in node:
            records[json.dumps(node, ensure_ascii=False, sort_keys=True)] += 1
        else:
            for value in node.values():
                if isinstance(value, dict):
                    walk(value)
    walk(document)
    return records


def hashes(roots):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in roots for p in root.rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--openapi-baseline", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("Unsafe run ID")
    history = ROOT / "output/checkpointed" / args.run_id
    package = ROOT / "output/b13-pipeline" / args.run_id
    catalog = ROOT / "output/catalog"
    protected = [history, package, catalog]
    destination = args.destination.resolve()
    if any(destination == root.resolve() or root.resolve() in destination.parents for root in protected):
        parser.error("Destination overlaps protected history")
    before = read(args.baseline)
    if hashes(protected) != before:
        raise ValueError("Protected history differs from baseline before reprojection")
    destination.mkdir(parents=True, exist_ok=False)
    evidence = {"run_id": args.run_id, "mode": "offline_no_remote_verification",
                "sources": {}, "protected_file_count": len(before)}
    for source_dir in sorted(history.iterdir()):
        if not (source_dir / "state/snapshots").is_dir():
            continue
        sid = source_dir.name
        repo = LocalJsonRepositoryAdapter(source_dir)
        snapshot = repo.get_latest_snapshot(sid)
        original = snapshot.model_dump(mode="json")
        old = read(source_dir / sid / "downstream/datax_projection.json")
        if old["run_id"] != snapshot.run_id or old["resources_hash"] != snapshot.resources_hash:
            raise ValueError("Historical projection/snapshot mismatch")
        result = DataxProjectionExportPipeline(repo).export(sid, destination)
        new = read(destination / result.projection_relpath)
        legacy = read(destination / result.legacy_relpath)
        assert LegacyStatsContractValidator.validate(legacy) == result.selected_resources
        old_legacy = read(source_dir / sid / "downstream/legacy_estadisticas.json")
        packaged = read(package / "datax-package/latest/sources" / f"{sid}.json")
        old_records, new_records = legacy_records(old_legacy), legacy_records(legacy)
        assert old_records == legacy_records(packaged)
        assert repo.get_latest_snapshot(sid).model_dump(mode="json") == original
        old_reps, new_reps = representations(old), representations(new)
        changes = []
        decisions = {d["resource_key"]: d for d in new["decisions"]}
        rejected = {d["resource_key"] for d in old["decisions"] if "invalid_download_url" in d["reason_codes"]}
        causes = Counter()
        components = Counter()
        apis = []
        for r in snapshot.resources:
            key = r.resource_key
            if key in rejected:
                parts = urlsplit(r.url)
                whitespace = [name for name in ("scheme", "netloc", "path", "query", "fragment")
                              if any(c.isspace() for c in getattr(parts, name))]
                components[",".join(whitespace)] += 1
                only_spaces = all(c == " " for c in r.url if c.isspace())
                causes["path_ascii_spaces_only" if whitespace == ["path"] and only_spaces else "other"] += 1
            old_rep, new_rep = old_reps.get(key), new_reps.get(key)
            if old_rep != new_rep:
                changes.append({"resource_key": key, "raw_url": r.raw_url, "original_url": r.url,
                    "corrected_url": new_rep["url"] if new_rep else None,
                    "origin_url": r.discovered_from_url, "discovery_method": r.discovery_method,
                    "change": "added" if old_rep is None else "removed" if new_rep is None else "modified",
                    "reason_codes": decisions[key]["reason_codes"]})
            if r.resource_type.value == "api":
                path = urlsplit(r.url).path
                category = ("oembed" if "/oembed/" in path else "prospectus_download" if "/descargarprospecto/" in path
                            else "rating_download" if "/descargarinformecalificacion/" in path
                            else "cms_metadata" if "/wp/v2/" in path else "service_index")
                apis.append({"resource_key": key, "url": r.url, "category": category,
                             "old_format": old_rep["format"] if old_rep else None,
                             "selected_after": key in new_reps, "reason_codes": decisions[key]["reason_codes"],
                             "http_status": r.http_status, "pages_sampled": r.api.pages_sampled})
        # Validate actual frontend responses against this same projection, including RAW paging.
        app = FastAPI()
        app.include_router(create_catalog_router(repo, repo))
        client = TestClient(app)
        response = client.get(f"/catalog/sources/{sid}/projection")
        assert response.status_code == 200 and response.json() == new
        raw_response = client.get("/catalog/resources", params={"source_id": sid})
        assert raw_response.status_code == 200
        raw_rows = raw_response.json()
        ordered = sorted(raw_rows, key=lambda r: (r["source_id"], r["resource_key"], r["url"]))
        pages = []
        for offset in range(0, len(raw_rows), 97):
            page = client.get("/catalog/resources", params={"source_id": sid, "limit": 97, "offset": offset})
            assert page.status_code == 200
            pages.extend(page.json())
        assert pages == ordered
        write(destination / sid / "changes.json", changes)
        write(destination / sid / "api_classification.json", apis)
        evidence["sources"][sid] = {
            "snapshot_run_id": snapshot.run_id, "resources_hash": snapshot.resources_hash,
            "raw_before_after": [len(old["decisions"]), len(new["decisions"])],
            "selected_before_after": [len(old_reps), len(new_reps)],
            "changes": dict(Counter(c["change"] for c in changes)),
            "invalid_url_causes": dict(causes), "invalid_url_whitespace_components": dict(components),
            "api_categories": dict(Counter(a["category"] for a in apis)),
            "api_selected_before_after": [sum(r["resource_type"] == "api" for r in old_reps.values()),
                                           sum(r["resource_type"] == "api" for r in new_reps.values())],
            "api_old_formats": dict(Counter(a["old_format"] for a in apis)),
            "exclusion_reasons_after": dict(Counter(reason for d in new["decisions"] if not d["selected"] for reason in d["reason_codes"])),
            "url_changes_for_retained": sum(c["change"] == "modified" for c in changes),
            "raw_http_count": len(raw_rows), "http_projection_matches_offline": True,
            "raw_pagination_matches": True,
            "legacy_record_changes": {"added": sum((new_records - old_records).values()),
                                      "removed": sum((old_records - new_records).values()),
                                      "retained_identical": sum((old_records & new_records).values())},
            "canonical_url_collisions_after": sum(n - 1 for n in Counter(
                UrlNormalizer.normalize(r["url"]) for r in new_reps.values()).values()),
            "rejected_discovery_methods": dict(Counter(r.discovery_method for r in snapshot.resources if r.resource_key in rejected)),
            "rejected_formats": dict(Counter(r.file_extension for r in snapshot.resources if r.resource_key in rejected)),
            "old_invalid_url_still_rejected": sum(key not in new_reps for key in rejected),
        }
    previous = read(args.openapi_baseline)
    current = app.openapi()
    assert all(current["paths"][path] == spec for path, spec in previous["paths"].items())
    assert all(current["components"]["schemas"][name] == spec
               for name, spec in previous["components"]["schemas"].items())
    evidence["api_compatibility"] = {"existing_paths_unchanged": len(previous["paths"]),
        "existing_schemas_unchanged": len(previous["components"]["schemas"]),
        "added_paths": sorted(set(current["paths"]) - set(previous["paths"]))}
    write(destination / "openapi_after.json", current)
    evidence["legacy_records_before"] = sum(v["selected_before_after"][0] for v in evidence["sources"].values())
    evidence["legacy_records_after"] = sum(v["selected_before_after"][1] for v in evidence["sources"].values())
    evidence["record_changes"] = dict(sum((Counter(v["changes"]) for v in evidence["sources"].values()), Counter()))
    after = hashes(protected)
    assert before == after, "Protected history mutated during offline work"
    evidence["protected_history_unchanged"] = True
    write(destination / "historical_sha256_after.json", after)
    write(destination / "comparison.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
