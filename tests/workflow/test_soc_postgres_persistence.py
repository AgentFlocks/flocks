"""Real PostgreSQL cases for the packaged SOC workflow sink.

Normal: two subprocess batches of 200 overlapping keys persist atomically.
Boundary: replay is idempotent; invalid or oversized batches change no rows.
Adjacent: original evidence/row cursor stay stable, while activity advances once.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from flocks.db import registry, sync_query
from flocks.workflow.repl_runtime import HostProcessPythonExecRuntime, SandboxPythonExecRuntime
from flocks.workflow.soc_store import MAX_ALERT_BYTES, MAX_ALERT_ROWS, ensure_schema, persist_triage_alerts

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dashboard():
    ensure_schema()
    path = ROOT / ".flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/api/handlers.py"
    spec = importlib.util.spec_from_file_location("soc_sink_test_" + uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._database.set(sync_query)
    assert module._ensure_postgres_schema()
    return module


def _alert(key, **values):
    return {"id": "source-" + key, "dedup_key": key, "is_duplicate": False,
            "event_time": int(time.time()), "source_file": "original.jsonl", "sip": "192.0.2.1",
            "triage_status": "ok", "triage_attack_verdict": "attack", "triage_attack_success": "failed",
            **values}


def _sink_code():
    definition = json.loads((ROOT / ".flocks/flockshub/plugins/workflows/stream_alert_triage/workflow.json").read_text())
    code = next(node["code"] for node in definition["nodes"] if node["id"] == "concurrent_triage")
    function = next(node for node in ast.parse(code).body if isinstance(node, ast.FunctionDef) and node.name == "_triage_write_soc_db")
    return "import json\n" + ast.get_source_segment(code, function) + "\noutputs['result'] = _triage_write_soc_db(inputs['alerts'], inputs['run_id'])"


def test_two_host_process_batches_share_dedup_and_activity(dashboard):
    code = _sink_code()
    batches = [
        [_alert(f"key-{index:03}", report_title="first") for index in range(200)],
        [_alert(f"key-{index:03}", report_title="second") for index in reversed(range(200))],
    ]
    frames = []

    def run(index):
        runtime = HostProcessPythonExecRuntime()
        dispatch = runtime._handle_rpc_request

        def capture(**kwargs):
            frames.append(kwargs["msg"]["rpc"])
            return dispatch(**kwargs)

        runtime._handle_rpc_request = capture
        output, stdout = runtime.execute(code, {"alerts": batches[index], "run_id": f"batch-{index}"})
        assert registry.current().dsn not in stdout
        return output["result"]

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    assert sum(result["rows"] for result in results) == 400
    assert sum(result["inserted_rows"] for result in results) == 200
    assert sum(result["updated_rows"] for result in results) == 200
    assert registry.current().dsn not in json.dumps(frames)
    assert all(frame["kind"] in {"soc_alerts_write", "cancelled"} for frame in frames)
    with sync_query.connect(readonly=True) as db:
        assert tuple(db.execute("SELECT COUNT(*), COUNT(DISTINCT dedup_key), COUNT(DISTINCT rowid) FROM alert_records").fetchone()) == (200, 200, 200)
        assert db.execute("SELECT COUNT(*) FROM soc_dashboard_alert_facts").fetchone()[0] == 200
        assert tuple(db.execute("SELECT COUNT(*), COUNT(DISTINCT event_key) FROM soc_dashboard_activity_events").fetchone()) == (400, 400)
    print(json.dumps({"case": "two_host_process_batches", "inputs": 400, "stored": 200,
                      "activity_events": 400, "elapsed_ms": elapsed_ms}))


def test_duplicate_update_preserves_evidence_and_cursor_replay_is_idempotent(dashboard):
    original = _alert("same", report_title="before", triage_ms=100, triage_error="old")
    assert persist_triage_alerts([original, original], "initial")["inserted_rows"] == 1
    ctx = SimpleNamespace(database=sync_query)
    before = dashboard.get_activity(ctx, SimpleNamespace(query_params={"bootstrap": "latest"}))
    assert "error" not in before
    with sync_query.connect(readonly=True) as db:
        identity = tuple(db.execute("SELECT row_id,rowid FROM alert_records WHERE dedup_key=?", ("same",)).fetchone())

    updated = _alert("same", report_title="after", sip="192.0.2.200", source_file="incoming.jsonl")
    result = persist_triage_alerts([updated, updated], "update")
    assert result["updated_rows"] == 1 and result["inserted_rows"] == 0
    with sync_query.connect(readonly=True) as db:
        row = db.execute("SELECT row_id,rowid,record_json FROM alert_records WHERE dedup_key=?", ("same",)).fetchone()
        assert tuple(row[:2]) == identity
        payload = json.loads(row["record_json"])
        assert payload["report_title"] == "after" and payload["sip"] == "192.0.2.1"
        assert payload["source_file"] == "original.jsonl"
        assert "triage_ms" not in payload and "triage_error" not in payload
    after = dashboard.get_activity(ctx, SimpleNamespace(query_params={"cursor": before["cursor"]}))
    assert "error" not in after and after["cursor"] != before["cursor"]
    assert after["batch"]["receivedCount"] == 0
    assert len(after["events"]) == 1

    assert persist_triage_alerts([updated], "update")["unchanged_rows"] == 1
    replay = dashboard.get_activity(ctx, SimpleNamespace(query_params={"cursor": after["cursor"]}))
    assert replay["events"] == [] and replay["cursor"] == after["cursor"]
    with sync_query.connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM soc_dashboard_activity_events").fetchone()[0] == 2


@pytest.mark.parametrize("bad", [
    lambda: [_alert(str(index)) for index in range(MAX_ALERT_ROWS + 1)],
    lambda: [_alert("too-large", triage_report="x" * MAX_ALERT_BYTES)],
    lambda: [_alert("invalid", metric=float("nan"))],
])
def test_invalid_batch_does_not_write(dashboard, bad):
    with pytest.raises(ValueError):
        persist_triage_alerts(bad(), "rejected")
    with sync_query.connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM alert_records").fetchone()[0] == 0


def test_bridge_capability_has_fixed_method_and_obeys_cancellation(dashboard):
    runtime = SandboxPythonExecRuntime(sandbox={}, cancel_checker=lambda: True)
    response = runtime._handle_rpc_request(msg={"token": "test", "id": "1", "rpc": {
        "kind": "soc_alerts_write", "alerts": [_alert("cancelled")], "run_id": "cancelled",
    }}, token="test")
    assert response["ok"] is False and "cancelled" in response["error"]
    response = runtime._handle_rpc_request(msg={"token": "test", "id": "2", "rpc": {
        "kind": "soc_query", "sql": "SELECT 1",
    }}, token="test")
    assert response["ok"] is False and "Unknown RPC" in response["error"]
    with sync_query.connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM alert_records").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_legacy_alert_migration_feeds_host_sink_without_duplicate_record(tmp_path):
    import sqlite3
    from flocks.db.migrate import migrate

    source = tmp_path / "legacy-alerts.db"
    original = _alert("legacy", attack_verdict="source-verdict")
    with sqlite3.connect(source) as database:
        database.execute("""CREATE TABLE alert_records (
            row_id TEXT PRIMARY KEY, asset_date TEXT NOT NULL, source_file TEXT NOT NULL,
            line_number INTEGER NOT NULL, event_time INTEGER, record_json TEXT NOT NULL)""")
        database.execute("INSERT INTO alert_records(rowid,row_id,asset_date,source_file,line_number,event_time,record_json) VALUES(?,?,?,?,?,?,?)",
                         (7, "original-row", "2026-09-23", "legacy.jsonl", 1, original["event_time"], json.dumps(original)))
    report = await migrate([source], report_dir=tmp_path / "migration-report", apply=True)
    assert report.ok, report.errors

    def verify():
        ensure_schema()
        updated = persist_triage_alerts([{**original, "attack_verdict": "replacement", "report_title": "updated"}], "migration-update")
        assert updated["inserted_rows"] == 0 and updated["updated_rows"] == 1
        assert persist_triage_alerts([_alert("new")], "new-run")["inserted_rows"] == 1
        with sync_query.connect(readonly=True) as db:
            row = db.execute("SELECT row_id,rowid,record_json FROM alert_records WHERE dedup_key=?", ("legacy",)).fetchone()
            assert tuple(row[:2]) == ("original-row", 7)
            assert json.loads(row["record_json"])["attack_verdict"] == "source-verdict"
            assert db.execute("SELECT rowid FROM alert_records WHERE dedup_key=?", ("new",)).fetchone()[0] > 7
            assert db.execute("SELECT COUNT(*) FROM alert_records").fetchone()[0] == 2
    await asyncio.to_thread(verify)
