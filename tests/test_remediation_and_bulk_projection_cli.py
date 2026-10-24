"""Historical tools remain usable in isolation and respect identity policy."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

from apps.b10_targeted_remediation import main as remediation
from apps.b11_bulk_projection import main as bulk


def test_targeted_remediation_resume_failure_empty_and_identity_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(remediation, "PROJECT_ROOT", tmp_path)
    config = tmp_path / "sources.yaml"
    config.write_text(yaml.safe_dump({"sources": [{"source_id": "physical", "base_url": "https://fixture.test/"}]}))
    mapping = tmp_path / "map.yaml"
    mapping.write_text(yaml.safe_dump({"sources": [{"logical_source_id": sid, "config_source_id": "physical",
        **({"acquisition_hold": "IDENTITY_UNVERIFIED_FDTA_ICCO"} if sid == "fdta" else {})}
        for sid in ["ok", "empty", "failed", "fdta"]]}))
    calls = []
    def execute(command, **kwargs):
        sid = Path(command[command.index("--output-dir") + 1]).name
        calls.append(sid)
        folder = tmp_path / "output" / sid / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        if sid != "failed":
            (folder / "run_fixture.json").write_text(json.dumps({"run_id": "fixture", "sources_failed": 0,
                "source_results": [{"source_id": "physical", "execution_status": "SUCCESS", "content_status": "EMPTY_RESULT" if sid == "empty" else "CHANGED",
                    "coverage": {"resources_found": 0 if sid == "empty" else 1, "pages_visited": 1}}]}))
        kwargs["log_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["log_path"].write_text("fixture\n")
        return 2 if sid == "failed" else 0
    monkeypatch.setattr(remediation, "stream_process", execute)
    args = ["remediation", "--sources-config", str(config), "--execution-map", str(mapping),
        "--baseline-root", str(tmp_path / "baseline"), "--output-root", str(tmp_path / "output"), "--state", str(tmp_path / "state.json")]
    monkeypatch.setattr(sys, "argv", args + ["--dry-run"])
    assert remediation.main() == 0 and calls == []
    monkeypatch.setattr(sys, "argv", args)
    assert remediation.main() == 2
    state = json.loads((tmp_path / "state.json").read_text())["sources"]
    assert {k: v["status"] for k, v in state.items()} == {"ok": "SUCCESS", "empty": "EMPTY_REVIEW", "failed": "FAILED", "fdta": "SKIPPED_POLICY"}
    monkeypatch.setattr(sys, "argv", args + ["--resume"])
    assert remediation.main() == 2
    assert calls == ["ok", "empty", "failed", "failed"]
    monkeypatch.setattr(sys, "argv", args + ["--resume", "--source-ids", "empty,empty", "--rerun-empty"])
    assert remediation.main() == 0 and calls[-1] == "empty"


def test_stream_process_captures_real_child_output(tmp_path):
    log = tmp_path / "console.log"
    assert remediation.stream_process([sys.executable, "-c", "print('isolated fixture')"], cwd=tmp_path, log_path=log) == 0
    assert log.read_text().strip() == "isolated fixture"


def test_bulk_projection_invokes_real_offline_cli_and_handles_failure(tmp_path, monkeypatch):
    from prospector_externo.adapters.persistence.local_json_adapter import LocalJsonRepositoryAdapter
    from prospector_externo.domain.models import Source, Snapshot, ResourceCandidate
    from datetime import datetime, timezone
    root = Path(__file__).resolve().parents[1]
    evidence = tmp_path / "crawl"
    repo = LocalJsonRepositoryAdapter(evidence)
    now = datetime.now(timezone.utc)
    # Persist RAW with the actual catalog adapter, then let the projection subprocess materialize it.
    source = Source(source_id="fixture", name="Fixture", entrypoint="https://fixture.test/", workflow="html")
    repo.save_source(source)
    snapshot = Snapshot(snapshot_id="snapshot_fixture", source_id="fixture", run_id="run_fixture", captured_at=now,
        resources_hash="fixture", total_resources=1,
        resources=[ResourceCandidate(source_id="fixture", url="https://fixture.test/table.csv", file_extension=".csv",
            resource_key="key", title="Monthly table", discovered_at=now, discovered_from_url="https://fixture.test/")])
    repo.save_snapshot(snapshot)
    row = {"logical_source_id": "alias", "evidence_root": str(evidence), "classification": "RAW_READY_NO_PROJECTION"}
    result = bulk.run_projection(repo_root=root, row=row, log_dir=tmp_path / "logs", dry_run=False)
    assert result["projection_source_id"] == "fixture"
    assert result["status"] == "PROJECTED" and result["after_legacy_records"] == 1
    assert bulk.run_projection(repo_root=root, row=row, log_dir=tmp_path / "logs", dry_run=True)["status"] == "DRY_RUN"
    monkeypatch.chdir(tmp_path)
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"sources": [row]}))
    monkeypatch.setattr(sys, "argv", ["bulk", "--audit", str(audit), "--state-output", str(tmp_path / "bulk.json"), "--log-dir", str(tmp_path / "logs"), "--dry-run", "--max-sources", "1"])
    assert bulk.main() == 0
    monkeypatch.setattr(bulk.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 2, "", "fixture failure"))
    assert bulk.run_projection(repo_root=root, row=row, log_dir=tmp_path / "logs", dry_run=False)["status"] == "COMMAND_FAILED"
