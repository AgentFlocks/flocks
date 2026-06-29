from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.models import TaskExecution, TaskScheduler
from flocks.task.store import TaskStore, _TASKS_DDL
from flocks.workflow.store import WorkflowStore


def _reset_state() -> None:
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    Storage._init_pid = None
    TaskStore._initialized = False
    TaskStore._conn = None
    TaskStore._init_pid = None
    WorkflowStore._initialized = False
    WorkflowStore._conn = None
    WorkflowStore._init_pid = None
    WorkflowStore._db_path = None


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


def _fetch_workflow_kv_value(db_path: Path, key: str):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM workflow_kv WHERE key = ?",
            (key,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _fetch_table_count(db_path: Path, table_name: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
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
    await WorkflowStore.close()
    _reset_state()


@pytest.mark.asyncio
async def test_storage_no_longer_routes_workflow_keys_to_workflow_db() -> None:
    await Storage.init()

    await Storage.write("workflow/wf-1", {"name": "workflow"})
    await Storage.write("project/proj-1", {"name": "project"})

    flocks_db = Storage.get_db_path()
    workflow_db = Storage.get_workflow_db_path()

    assert _fetch_storage_value(flocks_db, "workflow/wf-1") is not None
    assert _fetch_storage_value(flocks_db, "project/proj-1") is not None
    assert not workflow_db.exists()
    assert await Storage.read("workflow/wf-1") == {"name": "workflow"}
    assert await Storage.list_keys("workflow") == ["workflow/wf-1"]


@pytest.mark.asyncio
async def test_short_non_workflow_prefix_stays_on_flocks_db() -> None:
    await Storage.init()

    await Storage.write("workspace/item-1", {"name": "workspace"})
    await Storage.write("workflow/item-1", {"name": "workflow"})

    assert await Storage.list_keys("work") == ["workflow/item-1", "workspace/item-1"]


@pytest.mark.asyncio
async def test_clear_without_prefix_clears_flocks_db_only() -> None:
    await Storage.init()

    await Storage.write("project/proj-1", {"name": "project"})
    await Storage.write("workflow/wf-1", {"name": "workflow"})

    assert await Storage.clear() == 2
    assert await Storage.read("project/proj-1") is None
    assert await Storage.read("workflow/wf-1") is None


@pytest.mark.asyncio
async def test_list_without_prefix_reads_flocks_db_only() -> None:
    await Storage.init()

    await Storage.write("project/proj-1", {"name": "project"})
    await Storage.write("workflow/wf-1", {"name": "workflow"})

    keys = await Storage.list_keys()
    entries = dict(await Storage.list_entries())
    raw_entries = dict(await Storage.list_raw())

    assert {"project/proj-1", "workflow/wf-1"}.issubset(set(keys))
    assert entries["project/proj-1"] == {"name": "project"}
    assert entries["workflow/wf-1"] == {"name": "workflow"}
    assert raw_entries["project/proj-1"] == '{"name": "project"}'
    assert raw_entries["workflow/wf-1"] == '{"name": "workflow"}'


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

    await WorkflowStore.init()

    workflow_db = Storage.get_workflow_db_path()
    assert _fetch_workflow_kv_value(workflow_db, "workflow_registry/wf-legacy") == '{"ok": true}'
    assert _fetch_storage_value(flocks_db, "workflow_registry/wf-legacy") == '{"ok": true}'
    assert _fetch_workflow_kv_value(workflow_db, "session:legacy") is None
    assert await WorkflowStore.kv_get("workflow_registry/wf-legacy") == {"ok": True}
    marker = await WorkflowStore.kv_get("workflow_store.migration.tables.v1")
    assert marker["kv"] == 1


@pytest.mark.asyncio
async def test_workflow_prefix_migration_treats_underscore_literally() -> None:
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
            ("workflow_registry/wf-ok", '{"ok": true}', "json", "old", "old"),
        )
        conn.execute(
            "INSERT INTO storage (key, value, type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("workflowXregistry/wf-bad", '{"bad": true}', "json", "old", "old"),
        )
        conn.commit()
    finally:
        conn.close()

    await WorkflowStore.init()

    workflow_db = Storage.get_workflow_db_path()
    assert _fetch_workflow_kv_value(workflow_db, "workflow_registry/wf-ok") == '{"ok": true}'
    assert _fetch_workflow_kv_value(workflow_db, "workflowXregistry/wf-bad") is None
    assert _fetch_storage_value(flocks_db, "workflow_registry/wf-ok") == '{"ok": true}'
    assert _fetch_storage_value(flocks_db, "workflowXregistry/wf-bad") == '{"bad": true}'
    assert await Storage.list_keys("workflow_registry/") == ["workflow_registry/wf-ok"]
    assert await WorkflowStore.kv_list_keys("workflow_registry/") == ["workflow_registry/wf-ok"]


@pytest.mark.asyncio
async def test_workflow_store_recreates_workflow_db_if_it_disappears() -> None:
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
        conn.commit()
    finally:
        conn.close()

    await WorkflowStore.init()
    workflow_db = Storage.get_workflow_db_path()
    assert workflow_db.exists()
    assert await WorkflowStore.kv_get("workflow_registry/wf-legacy") == {"ok": True}
    await WorkflowStore.close()
    workflow_db.unlink()
    _reset_state()

    await WorkflowStore.init()
    assert Storage.get_workflow_db_path().exists()
    assert await WorkflowStore.kv_get("workflow_registry/wf-legacy") == {"ok": True}


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
        legacy_non_utf8_execution = TaskExecution(
            schedulerID=legacy_scheduler.id,
            title="legacy non-utf8 execution",
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
            TaskStore._execution_to_row(legacy_non_utf8_execution),
        )
        conn.execute(
            "UPDATE task_executions SET description = CAST(? AS TEXT) WHERE id = ?",
            ("扫描 Windows 主机 192.168.254.1".encode("gbk"), legacy_non_utf8_execution.id),
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
        non_utf8_row = conn.execute(
            "SELECT description FROM task_executions WHERE id = ?",
            (legacy_non_utf8_execution.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "legacy task"
    assert orphan_row[0] == "legacy orphan execution"
    assert non_utf8_row[0] == "扫描 Windows 主机 192.168.254.1"

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
    assert marker["task_rows"] == 3
    assert marker["task_foreign_key_violations"] == 1
    assert marker["task_source_rows_deleted"] == 0
    assert _fetch_table_count(flocks_db, "task_schedulers") == 1
    assert _fetch_table_count(flocks_db, "task_executions") == 2

    await TaskStore.close()
    TaskStore._initialized = False
    await TaskStore.init()
    conn = sqlite3.connect(tasks_db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM task_schedulers").fetchone()[0]
    finally:
        conn.close()
    assert count == 2


@pytest.mark.asyncio
async def test_completed_task_migration_fails_if_tasks_db_disappears() -> None:
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
        conn.commit()
    finally:
        conn.close()

    await TaskStore.init()
    tasks_db = TaskStore.get_db_path()
    assert tasks_db.exists()
    await TaskStore.close()
    tasks_db.unlink()
    _reset_state()

    with pytest.raises(RuntimeError, match="tasks.db is missing"):
        await TaskStore.init()


@pytest.mark.asyncio
async def test_task_store_init_failure_clears_half_open_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await Storage.init()

    async def fail_migration(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        TaskStore,
        "_migrate_task_tables_to_tasks_db",
        classmethod(fail_migration),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await TaskStore.init()

    assert TaskStore._conn is None
    assert TaskStore._initialized is False
