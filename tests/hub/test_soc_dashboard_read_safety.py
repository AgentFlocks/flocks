"""Dashboard reads must stay isolated from ingestion and HTTP scheduling."""

import asyncio
import importlib.util
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest


def load_handlers():
    path = Path(__file__).resolve().parents[2] / (
        ".flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/api/handlers.py"
    )
    spec = importlib.util.spec_from_file_location("soc_read_safety", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_counters_and_sources_need_no_database_writes(tmp_path):
    handlers = load_handlers()
    handlers.WORKFLOW_DB = tmp_path / "workflow.db"
    handlers.DEFAULT_SQLITE_DB = tmp_path / "soc.db"
    handlers.USAGE_DB = tmp_path / "missing-usage.db"
    now = int(time.time())
    date = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    # Legacy database: cumulative counters have no timestamp, and retained
    # execution records / metric rollups may not exist at all.
    with sqlite3.connect(handlers.WORKFLOW_DB) as conn:
        conn.execute("CREATE TABLE workflow_stats (workflow_id TEXT, call_count INTEGER)")
        conn.execute("INSERT INTO workflow_stats VALUES ('stream_alert_denoise', 100)")
    with sqlite3.connect(handlers.DEFAULT_SQLITE_DB) as conn:
        conn.execute("CREATE TABLE alert_records (record_json TEXT, asset_date TEXT, event_time INTEGER)")
        for source in ("tdp", "hids"):
            record = {"_source_type": source, "triage_status": "ok", "triage_attack_verdict": "attack"}
            conn.execute("INSERT INTO alert_records VALUES (?, ?, ?)", (json.dumps(record), date, now))
    before = {path: path.read_bytes() for path in (handlers.WORKFLOW_DB, handlers.DEFAULT_SQLITE_DB)}
    params = {"startTime": str(now - 7 * 86400), "endTime": str(now + 1), "force": "1"}
    result = handlers._get_stats(params)
    assert result["denoise"]["totalRaw"] == 100
    assert result["denoise"]["totalNormalized"] == 100
    assert result["denoise"]["totalUnique"] == 2
    assert result["denoise"]["duplicateRate"] == 0.98
    sources = {item["key"]: item["value"] for item in result["sources"]}
    assert sources["ndr"] == sources["edr"] == 1
    assert result["sourceStatus"]["metricQuality"]["metricsAvailable"] is True
    assert result["sourceStatus"]["metricQuality"]["sourceMetricDataSource"] == "soc.db.alert_records"
    assert handlers._get_activity(params)["workflowStats"]["callCount"] == 100
    assert before == {path: path.read_bytes() for path in before}
    with handlers._read_db(handlers.WORKFLOW_DB) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM workflow_stats")


def test_sqlite_scan_has_a_deadline_and_lock_wait_is_short(tmp_path):
    handlers = load_handlers()
    path = tmp_path / "locked.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE records (value INTEGER)")
    handlers._READ_BUDGET_SECONDS = 0.03
    started = time.monotonic()
    with handlers._read_db(path) as conn:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            conn.execute(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) "
                "SELECT SUM(x) FROM n"
            ).fetchone()
    assert time.monotonic() - started < 1
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with handlers._read_db(path) as conn:
                conn.execute("SELECT * FROM records").fetchall()
        assert time.monotonic() - started < 0.75


@pytest.mark.asyncio
async def test_refresh_storm_is_bounded_and_cancellation_keeps_worker_slot():
    handlers = load_handlers()
    release = threading.Event()
    calls = []

    def blocked(key):
        calls.append(key)
        release.wait(2)
        return {"key": key}

    first = asyncio.create_task(handlers._page_read(blocked, "same"))
    duplicate = asyncio.create_task(handlers._page_read(blocked, "same"))
    second = asyncio.create_task(handlers._page_read(blocked, "second"))
    try:
        for _ in range(100):
            if len(calls) == 2:
                break
            await asyncio.sleep(0.005)
        assert sorted(calls) == ["same", "second"]
        # The event loop still responds while both database workers are busy.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "ping"), 0.5) == "ping"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        results = await asyncio.gather(
            *(handlers._page_read(blocked, f"refresh-{i}") for i in range(100)),
            return_exceptions=True,
        )
        assert all(isinstance(result, RuntimeError) for result in results)
        assert len(calls) == 2
        release.set()
        assert await duplicate == {"key": "same"}
        assert await second == {"key": "second"}
        assert await handlers._page_read(blocked, "same") == {"key": "same"}
        assert len(calls) == 2  # repeated snapshots come from the short cache
    finally:
        release.set()
        await asyncio.gather(first, duplicate, second, return_exceptions=True)
        handlers._read_pool.shutdown(wait=True)
