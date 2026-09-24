"""Migration preserves application visibility and source numeric values."""

import json
import os
import sqlite3

import asyncpg
import pytest

from flocks.db.migrate import migrate
from flocks.workflow.store import WorkflowStore

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")]


async def test_migration_orders_memory_parents_before_chunks(tmp_path):
    source = tmp_path / "memory.db"
    with sqlite3.connect(source) as db:
        db.executescript("""
            CREATE TABLE memory_files (scope TEXT,scope_id TEXT,path TEXT,source TEXT,hash TEXT,mtime REAL,size INTEGER,indexed_at REAL,PRIMARY KEY(scope,scope_id,path));
            CREATE TABLE memory_chunks (id TEXT PRIMARY KEY,scope TEXT,scope_id TEXT,path TEXT,source TEXT,start_line INTEGER,end_line INTEGER,hash TEXT,text TEXT,created_at REAL,updated_at REAL);
        """)
        db.execute("INSERT INTO memory_files VALUES (?,?,?,?,?,?,?,?)", ("global", "", "note.md", "memory", "file-hash", 1, 12, 1))
        db.execute("INSERT INTO memory_chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("chunk", "global", "", "note.md", "memory", 1, 1, "chunk-hash", "保留内容", 1, 1))
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    assert report.verified == 2


async def test_legacy_workflow_keys_are_visible_through_current_store(tmp_path):
    source = tmp_path / "workflow.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE storage (key TEXT PRIMARY KEY,value TEXT,type TEXT)")
        payloads = {
            "workflow_execution/old-run": {"id": "old-run", "workflowId": "wf", "status": "success", "startedAt": 10},
            "workflow/wf/stats": {"callCount": 2, "successCount": 2},
            "workflow_poller_config/wf": {"enabled": False, "intervalSeconds": 15},
            "workflow_runtime/wf": {"status": "stopped"},
        }
        db.executemany("INSERT INTO storage VALUES (?,?,?)", [(key, json.dumps(value), "json") for key, value in payloads.items()])
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    execution = await WorkflowStore.get_execution("old-run")
    assert execution is not None and execution["status"] == "success"
    assert (await WorkflowStore.get_stats("wf"))["callCount"] == 2
    assert (await WorkflowStore.get_config("wf", kind="workflow_poller_config"))["intervalSeconds"] == 15
    assert await WorkflowStore.kv_get("workflow_runtime/wf") == {"status": "stopped"}


async def test_unknown_numeric_column_keeps_large_exact_integer(tmp_path):
    source = tmp_path / "numeric.db"
    expected = 9007199254740993
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE plugin_numeric (id TEXT PRIMARY KEY,amount NUMERIC)")
        db.execute("INSERT INTO plugin_numeric VALUES (?,?)", ("one", expected))
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        actual = await connection.fetchval("SELECT amount FROM plugin_numeric WHERE id=$1", "one")
        assert actual == expected
    finally:
        await connection.close()


async def test_imported_workflow_trigger_config_stays_disabled_until_review(tmp_path):
    source = tmp_path / "workflow-config.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE workflow_configs (workflow_id TEXT,kind TEXT,config TEXT,updated_at INTEGER,PRIMARY KEY(workflow_id,kind))")
        db.execute("INSERT INTO workflow_configs VALUES (?,?,?,?)", ("wf", "workflow_poller_config", '{"enabled":true,"intervalSeconds":15}', 1))
    report = await migrate([source], report_dir=tmp_path / "reports", apply=True)
    assert report.ok, report.errors
    assert (await WorkflowStore.get_config("wf", kind="workflow_poller_config"))["enabled"] is False
