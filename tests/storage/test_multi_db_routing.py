from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.models import TaskExecution, TaskScheduler
from flocks.task.store import TaskStore, _TASKS_DDL


def _reset_state() -> None:
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    Storage._init_pid = None
    TaskStore._initialized = False
    TaskStore._conn = None
    TaskStore._init_pid = None


def _fetch_storage_value(db_path: Path, key: str):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM storage WHERE key = ?",
            (key,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.fixture(autouse=True)
async def isolated_multi_db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    _reset_state()
    yield
    await TaskStore.close()
    _reset_state()


@pytest.mark.asyncio
async def test_workflow_keys_route_to_workflow_db() -> None:
    await Storage.init()

    await Storage.write("workflow/wf-1", {"name": "workflow"})
    await Storage.write("project/proj-1", {"name": "project"})

    flocks_db = Storage.get_db_path()
    workflow_db = Storage.get_workflow_db_path()

    assert _fetch_storage_value(workflow_db, "workflow/wf-1") is not None
    assert _fetch_storage_value(flocks_db, "workflow/wf-1") is None
    assert _fetch_storage_value(flocks_db, "project/proj-1") is not None
    assert _fetch_storage_value(workflow_db, "project/proj-1") is None
    assert await Storage.read("workflow/wf-1") == {"name": "workflow"}
    assert await Storage.list_keys("workflow") == ["workflow/wf-1"]


@pytest.mark.asyncio
async def test_workflow_kv_migrates_from_legacy_flocks_db() -> None:
    flocks_db = Config.get_data_path() / "flocks.db"
    flocks_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(flocks_db)
    try:
        conn.execute(
            """
            CREATE TABLE storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("workflow_registry/wf-legacy", '{"ok": true}', "json", "old", "old"),
        )
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("session:legacy", '{"ok": false}', "json", "old", "old"),
        )
        conn.commit()
    finally:
        conn.close()

    await Storage.init()

    workflow_db = Storage.get_workflow_db_path()
    assert _fetch_storage_value(workflow_db, "workflow_registry/wf-legacy") == '{"ok": true}'
    assert _fetch_storage_value(flocks_db, "workflow_registry/wf-legacy") == '{"ok": true}'
    assert _fetch_storage_value(workflow_db, "session:legacy") is None
    marker = await Storage.get(Storage._multi_db_migration_marker_key)
    assert marker["workflow_migrated"] is True
    assert marker["workflow_rows"] == 1


@pytest.mark.asyncio
async def test_task_store_uses_tasks_db_and_migrates_existing_task_tables() -> None:
    await Storage.init()
    flocks_db = Storage.get_db_path()
    conn = sqlite3.connect(flocks_db)
    try:
        conn.executescript(_TASKS_DDL)
        legacy_scheduler = TaskScheduler(title="legacy task")
        conn.execute(
            """
            INSERT INTO task_schedulers
            (id, title, description, mode, status, priority, source, trigger,
             execution_mode, agent_name, workflow_id, skills, category, context,
             workspace_directory, retry, tags, created_at, updated_at, created_by, dedup_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            TaskStore._scheduler_to_row(legacy_scheduler),
        )
        legacy_orphan_execution = TaskExecution(
            schedulerID="missing-legacy-scheduler",
            title="legacy orphan execution",
        )
        conn.execute(
            """
            INSERT INTO task_executions
            (id, scheduler_id, title, description, priority, source, trigger_type,
             status, delivery_status, queued_at, started_at, completed_at, duration_ms,
             session_id, result_summary, error, execution_input_snapshot,
             workspace_directory, retry, execution_mode, agent_name, workflow_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            TaskStore._execution_to_row(legacy_orphan_execution),
        )
        conn.commit()
    finally:
        conn.close()

    await TaskStore.init()

    tasks_db = TaskStore.get_db_path()
    conn = sqlite3.connect(tasks_db)
    try:
        row = conn.execute(
            "SELECT title FROM task_schedulers WHERE id = ?",
            (legacy_scheduler.id,),
        ).fetchone()
        orphan_row = conn.execute(
            "SELECT title FROM task_executions WHERE id = ?",
            (legacy_orphan_execution.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "legacy task"
    assert orphan_row[0] == "legacy orphan execution"

    new_scheduler = TaskScheduler(title="new task")
    await TaskStore.create_scheduler(new_scheduler)
    assert await TaskStore.get_scheduler(new_scheduler.id) is not None

    conn = sqlite3.connect(flocks_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM task_schedulers WHERE id = ?",
            (new_scheduler.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is None

    marker = await Storage.get(Storage._multi_db_migration_marker_key)
    assert marker["tasks_migrated"] is True
    assert marker["task_rows"] == 2
    assert marker["task_foreign_key_violations"] == 1

    await TaskStore.close()
    TaskStore._initialized = False
    await TaskStore.init()
    conn = sqlite3.connect(tasks_db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM task_schedulers").fetchone()[0]
    finally:
        conn.close()
    assert count == 2
