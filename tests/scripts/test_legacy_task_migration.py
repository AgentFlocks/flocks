"""scripts/migrate_legacy_task_tables.py against the pre-#53 task schema."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_legacy_task_tables.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_legacy_task_tables", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LEGACY_DDL = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'queued', status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'normal', source TEXT, schedule TEXT, execution TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'unread', execution_mode TEXT NOT NULL DEFAULT 'agent',
    agent_name TEXT NOT NULL DEFAULT 'rex', workflow_id TEXT, skills TEXT DEFAULT '[]',
    category TEXT, context TEXT DEFAULT '{}', retry TEXT, tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT 'rex',
    dedup_key TEXT
);
CREATE TABLE task_execution_records (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT, completed_at TEXT, duration_ms INTEGER, result_summary TEXT, error TEXT,
    session_id TEXT, delivery_status TEXT NOT NULL DEFAULT 'unread'
);
CREATE TABLE task_queue_refs (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, execution_record_id TEXT, status TEXT,
    created_at TEXT, started_at TEXT
);
"""


def _legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_DDL)
    now = "2026-09-14T00:00:00+00:00"
    # A manual task has NULL ``schedule`` (the column is "scheduled tasks only").
    conn.execute(
        "INSERT INTO tasks (id, title, type, status, created_at, updated_at) VALUES ('t1', 'manual', 'queued', 'completed', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO tasks (id, title, type, status, schedule, created_at, updated_at) VALUES ('t2', 'sched', 'scheduled', 'running', ?, ?, ?)",
        (json.dumps({"cron": "*/5 * * * *", "enabled": True}), now, now),
    )
    conn.execute(
        "INSERT INTO task_execution_records (id, task_id, status, started_at, completed_at) VALUES ('r1', 't1', 'completed', ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()


def test_execution_record_of_a_manual_task_migrates(tmp_path: Path) -> None:
    """Regression: NULL ``schedule``/``source`` on the task row crashed the whole run."""
    module = _load_script()
    db = tmp_path / "flocks.db"
    _legacy_db(db)
    state = tmp_path / "state.json"

    assert module.migrate(db, state) == 0

    conn = sqlite3.connect(tmp_path / "tasks.db")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    executions = {row[0]: row[1] for row in conn.execute("SELECT id, trigger_type FROM task_executions")}
    conn.close()

    assert executions["r1"] == "scheduled" and "t1" in executions and "legacy_exec_t2" in executions
    assert not tables & {"tasks", "task_execution_records", "task_queue_refs"}
    with sqlite3.connect(db) as source:
        assert source.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        assert source.execute("SELECT COUNT(*) FROM task_execution_records").fetchone()[0] == 1
        assert source.execute("SELECT COUNT(*) FROM task_queue_refs").fetchone()[0] == 0
        assert source.execute(
            "SELECT name FROM sqlite_master WHERE name = 'task_schedulers'"
        ).fetchone() is None
    assert not state.exists()


def _snapshot(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def test_repeat_migration_keeps_user_edits_and_new_records(tmp_path: Path) -> None:
    module = _load_script()
    source = tmp_path / "flocks.db"
    target = tmp_path / "tasks.db"
    _legacy_db(source)
    original_source = _snapshot(source)
    assert module.migrate(source) == 0
    with sqlite3.connect(target) as conn:
        conn.execute("UPDATE task_schedulers SET title = ? WHERE id = ?", ("user edit", "t1"))
        conn.execute("UPDATE task_executions SET status = ? WHERE id = ?", ("cancelled", "legacy_exec_t2"))
        module._insert_schedulers(conn, [module._build_scheduler_payload({"id": "new-user-task", "title": "new task"})])
    before_repeat = _snapshot(target)

    assert module.migrate(source) == 0

    assert _snapshot(target) == before_repeat
    assert _snapshot(source) == original_source


@pytest.mark.parametrize("failure", ["malformed_json", "late_write", "existing_id"])
def test_failed_migration_rolls_back_and_preserves_source(tmp_path: Path, monkeypatch, failure: str) -> None:
    module = _load_script()
    source = tmp_path / "flocks.db"
    target = tmp_path / "tasks.db"
    state = tmp_path / "state.json"
    _legacy_db(source)
    with sqlite3.connect(target) as conn:
        conn.executescript(module.TASKS_DDL)
        existing_id = "t2" if failure == "existing_id" else "user-task"
        module._insert_schedulers(conn, [module._build_scheduler_payload({"id": existing_id, "title": "keep this"})])
    if failure == "malformed_json":
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE tasks SET context = ? WHERE id = ?", ("invalid-json", "t1"))
    elif failure == "late_write":
        def fail_queue_write(_conn, _payloads):
            raise RuntimeError("injected failure after scheduler/execution writes")
        monkeypatch.setattr(module, "_insert_queue_refs", fail_queue_write)
    original_source = _snapshot(source)
    original_target = _snapshot(target)

    assert module.migrate(source, state) == 1

    assert _snapshot(source) == original_source
    assert _snapshot(target) == original_target
    assert json.loads(state.read_text())["failed"] is True
    assert json.loads(state.read_text())["error"]


def test_migration_recovers_after_failed_attempt(tmp_path: Path, monkeypatch) -> None:
    module = _load_script()
    source = tmp_path / "flocks.db"
    target = tmp_path / "tasks.db"
    state = tmp_path / "state.json"
    _legacy_db(source)
    with monkeypatch.context() as attempt:
        def fail_queue_write(_conn, _payloads):
            raise RuntimeError("injected failure")
        attempt.setattr(module, "_insert_queue_refs", fail_queue_write)
        assert module.migrate(source, state) == 1
    with sqlite3.connect(target) as conn:
        # Schema creation participates in the failed transaction too.
        assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []

    assert module.migrate(source, state) == 0

    assert not state.exists()
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_schedulers").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM task_legacy_migrations").fetchone()[0] == 1


def test_missing_source_does_not_create_an_empty_database(tmp_path: Path) -> None:
    module = _load_script()
    source = tmp_path / "missing.db"
    assert module.migrate(source) == 1
    assert not source.exists()
    assert not (tmp_path / "tasks.db").exists()


def test_target_cannot_overwrite_retained_source(tmp_path: Path) -> None:
    module = _load_script()
    source = tmp_path / "flocks.db"
    _legacy_db(source)
    original = _snapshot(source)
    assert module.migrate(source, target_db_path=source) == 1
    assert _snapshot(source) == original


def test_explicit_target_and_runtime_root_are_respected(tmp_path: Path, monkeypatch) -> None:
    module = _load_script()
    source = tmp_path / "source.db"
    target = tmp_path / "other" / "tasks.db"
    _legacy_db(source)
    assert module.migrate(source, target_db_path=target) == 0
    assert target.exists()
    assert not (tmp_path / "tasks.db").exists()

    monkeypatch.delenv("FLOCKS_DATA_DIR", raising=False)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    assert module._default_db_path() == tmp_path / "data" / "flocks.db"
