#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TASKS_DDL = """
CREATE TABLE IF NOT EXISTS task_schedulers (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    mode                TEXT NOT NULL DEFAULT 'once',
    status              TEXT NOT NULL DEFAULT 'active',
    priority            TEXT NOT NULL DEFAULT 'normal',
    source              TEXT,
    trigger             TEXT NOT NULL,
    execution_mode      TEXT NOT NULL DEFAULT 'agent',
    agent_name          TEXT NOT NULL DEFAULT 'rex',
    workflow_id         TEXT,
    skills              TEXT DEFAULT '[]',
    category            TEXT,
    context             TEXT DEFAULT '{}',
    workspace_directory TEXT,
    retry               TEXT,
    tags                TEXT DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL DEFAULT 'rex',
    dedup_key           TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id                       TEXT PRIMARY KEY,
    scheduler_id             TEXT NOT NULL,
    title                    TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    priority                 TEXT NOT NULL DEFAULT 'normal',
    source                   TEXT,
    trigger_type             TEXT NOT NULL DEFAULT 'run_once',
    status                   TEXT NOT NULL DEFAULT 'pending',
    delivery_status          TEXT NOT NULL DEFAULT 'unread',
    queued_at                TEXT,
    started_at               TEXT,
    completed_at             TEXT,
    duration_ms              INTEGER,
    session_id               TEXT,
    result_summary           TEXT,
    error                    TEXT,
    execution_input_snapshot TEXT NOT NULL DEFAULT '{}',
    workspace_directory      TEXT,
    retry                    TEXT,
    execution_mode           TEXT NOT NULL DEFAULT 'agent',
    agent_name               TEXT NOT NULL DEFAULT 'rex',
    workflow_id              TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_execution_queue_refs (
    id           TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    created_at   TEXT NOT NULL,
    started_at   TEXT
);

CREATE TABLE IF NOT EXISTS task_legacy_migrations (
    source_db    TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _enum_value(value: Any, default: str, allowed: set[str]) -> str:
    value_str = str(value) if value is not None else default
    return value_str if value_str in allowed else default


def _fetch_all(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _write_state(state_path: Path | None, state: dict[str, Any]) -> None:
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")


def _clear_state(state_path: Path | None) -> None:
    if state_path is None:
        return
    if state_path.exists():
        state_path.unlink()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _legacy_tables_exist(conn: sqlite3.Connection) -> bool:
    return any(
        _table_exists(conn, name)
        for name in ("tasks", "task_execution_records", "task_queue_refs")
    )


def _insert_schedulers(conn: sqlite3.Connection, payloads: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO task_schedulers
        (id, title, description, mode, status, priority, source, trigger,
         execution_mode, agent_name, workflow_id, skills, category, context,
         workspace_directory, retry, tags, created_at, updated_at, created_by, dedup_key)
        VALUES (:id, :title, :description, :mode, :status, :priority, :source, :trigger,
                :execution_mode, :agent_name, :workflow_id, :skills, :category, :context,
                :workspace_directory, :retry, :tags, :created_at, :updated_at, :created_by, :dedup_key)
        """,
        payloads,
    )


def _insert_executions(conn: sqlite3.Connection, payloads: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO task_executions
        (id, scheduler_id, title, description, priority, source, trigger_type, status,
         delivery_status, queued_at, started_at, completed_at, duration_ms, session_id,
         result_summary, error, execution_input_snapshot, workspace_directory, retry,
         execution_mode, agent_name, workflow_id, created_at, updated_at)
        VALUES (:id, :scheduler_id, :title, :description, :priority, :source, :trigger_type, :status,
                :delivery_status, :queued_at, :started_at, :completed_at, :duration_ms, :session_id,
                :result_summary, :error, :execution_input_snapshot, :workspace_directory, :retry,
                :execution_mode, :agent_name, :workflow_id, :created_at, :updated_at)
        """,
        payloads,
    )


def _insert_queue_refs(conn: sqlite3.Connection, payloads: Iterable[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO task_execution_queue_refs (id, execution_id, status, created_at, started_at)
        VALUES (:id, :execution_id, :status, :created_at, :started_at)
        """,
        payloads,
    )


def _build_scheduler_payload(row: dict[str, Any]) -> dict[str, Any]:
    schedule = _json_loads(row.get("schedule")) or {}
    source = _json_loads(row.get("source")) or {"sourceType": "user_conversation"}
    trigger = {
        "runImmediately": row.get("type") != "scheduled",
        "runAt": _dt_to_iso(_parse_dt(schedule.get("run_at") or schedule.get("runAt"))),
        "cron": schedule.get("cron"),
        "timezone": schedule.get("timezone") or "Asia/Shanghai",
        "nextRun": _dt_to_iso(_parse_dt(schedule.get("next_run") or schedule.get("nextRun"))),
        "cronDescription": schedule.get("cron_description") or schedule.get("cronDescription"),
    }
    if row.get("type") == "scheduled":
        mode = "once" if schedule.get("run_once") or schedule.get("runOnce") else "cron"
        status = "active" if schedule.get("enabled", True) and row.get("status") != "cancelled" else "disabled"
    else:
        mode = "once"
        status = "archived" if row.get("status") in ("completed", "failed", "cancelled") else "active"
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "mode": mode,
        "status": status,
        "priority": _enum_value(row.get("priority"), "normal", {"urgent", "high", "normal", "low"}),
        "source": json.dumps(source),
        "trigger": json.dumps(trigger),
        "execution_mode": _enum_value(row.get("execution_mode"), "agent", {"agent", "workflow"}),
        "agent_name": row.get("agent_name") or "rex",
        "workflow_id": row.get("workflow_id"),
        "skills": row.get("skills") or "[]",
        "category": row.get("category"),
        "context": row.get("context") or "{}",
        "workspace_directory": row.get("workspace_directory"),
        "retry": row.get("retry") or json.dumps({"maxRetries": 3, "retryCount": 0, "retryDelaySeconds": 60}),
        "tags": row.get("tags") or "[]",
        "created_at": row.get("created_at") or _now_iso(),
        "updated_at": row.get("updated_at") or row.get("created_at") or _now_iso(),
        "created_by": row.get("created_by") or "migration",
        "dedup_key": row.get("dedup_key"),
    }


def _legacy_execution(row: dict[str, Any]) -> dict[str, Any]:
    return _json_loads(row.get("execution")) or {}


def _build_manual_execution_payload(row: dict[str, Any]) -> dict[str, Any]:
    legacy_execution = _legacy_execution(row)
    source = _json_loads(row.get("source")) or {"sourceType": "user_conversation"}
    queued_at = _parse_dt(row.get("created_at"))
    started_at = _parse_dt(legacy_execution.get("started_at") or legacy_execution.get("startedAt"))
    if queued_at is None:
        queued_at = started_at
    return {
        "id": row["id"],
        "scheduler_id": row["id"],
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "priority": _enum_value(row.get("priority"), "normal", {"urgent", "high", "normal", "low"}),
        "source": json.dumps(source),
        "trigger_type": "run_once",
        "status": _enum_value(
            row.get("status"),
            "pending",
            {"pending", "queued", "running", "completed", "failed", "cancelled", "paused"},
        ),
        "delivery_status": row.get("delivery_status") or "unread",
        "queued_at": _dt_to_iso(queued_at),
        "started_at": _dt_to_iso(started_at),
        "completed_at": legacy_execution.get("completed_at") or legacy_execution.get("completedAt"),
        "duration_ms": legacy_execution.get("duration_ms") or legacy_execution.get("durationMs"),
        "session_id": legacy_execution.get("session_id") or legacy_execution.get("sessionID"),
        "result_summary": legacy_execution.get("result_summary") or legacy_execution.get("resultSummary"),
        "error": legacy_execution.get("error"),
        "execution_input_snapshot": json.dumps(
            {
                "title": row.get("title"),
                "description": row.get("description"),
                "source": source,
                "context": _json_loads(row.get("context")) or {},
                "workspaceDirectory": row.get("workspace_directory"),
                "tags": _json_loads(row.get("tags")) or [],
            }
        ),
        "workspace_directory": row.get("workspace_directory"),
        "retry": row.get("retry") or json.dumps({"maxRetries": 3, "retryCount": 0, "retryDelaySeconds": 60}),
        "execution_mode": _enum_value(row.get("execution_mode"), "agent", {"agent", "workflow"}),
        "agent_name": row.get("agent_name") or "rex",
        "workflow_id": row.get("workflow_id"),
        "created_at": row.get("created_at") or _now_iso(),
        "updated_at": row.get("updated_at") or row.get("created_at") or _now_iso(),
    }


def _build_record_execution_payload(row: dict[str, Any], task_row: dict[str, Any] | None) -> dict[str, Any]:
    # ``schedule`` is NULL for every non-scheduled legacy task and ``source``
    # may be NULL too; an execution record of such a task must not abort the
    # whole migration.
    source = (_json_loads(task_row.get("source")) if task_row else None) or {}
    schedule = (_json_loads(task_row.get("schedule")) if task_row else None) or {}
    run_once = bool(schedule.get("run_once") or schedule.get("runOnce"))
    started_at = _parse_dt(row.get("started_at"))
    queued_at = started_at or _parse_dt(task_row.get("updated_at") if task_row else None)
    return {
        "id": row["id"],
        "scheduler_id": row["task_id"],
        "title": task_row.get("title") if task_row else row["task_id"],
        "description": task_row.get("description") if task_row else "",
        "priority": _enum_value(task_row.get("priority") if task_row else None, "normal", {"urgent", "high", "normal", "low"}),
        "source": json.dumps(source or {"sourceType": "scheduled_trigger"}),
        "trigger_type": "run_once" if run_once else "scheduled",
        "status": _enum_value(
            row.get("status"),
            "pending",
            {"pending", "queued", "running", "completed", "failed", "cancelled", "paused"},
        ),
        "delivery_status": row.get("delivery_status") or "unread",
        "queued_at": _dt_to_iso(queued_at),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "duration_ms": row.get("duration_ms"),
        "session_id": row.get("session_id"),
        "result_summary": row.get("result_summary"),
        "error": row.get("error"),
        "execution_input_snapshot": json.dumps(
            {
                "title": task_row.get("title") if task_row else "",
                "description": task_row.get("description") if task_row else "",
                "source": source or {},
                "context": _json_loads(task_row.get("context")) if task_row else {},
                "workspaceDirectory": task_row.get("workspace_directory") if task_row else None,
                "tags": _json_loads(task_row.get("tags")) if task_row else [],
            }
        ),
        "workspace_directory": task_row.get("workspace_directory") if task_row else None,
        "retry": task_row.get("retry") if task_row else json.dumps({"maxRetries": 3, "retryCount": 0, "retryDelaySeconds": 60}),
        "execution_mode": _enum_value(task_row.get("execution_mode") if task_row else None, "agent", {"agent", "workflow"}),
        "agent_name": task_row.get("agent_name") if task_row else "rex",
        "workflow_id": task_row.get("workflow_id") if task_row else None,
        "created_at": row.get("started_at") or (task_row.get("created_at") if task_row else _now_iso()),
        "updated_at": row.get("completed_at") or row.get("started_at") or (task_row.get("updated_at") if task_row else _now_iso()),
    }


def _build_synthetic_active_execution(row: dict[str, Any], execution_id: str) -> dict[str, Any]:
    payload = _build_manual_execution_payload(row)
    payload["id"] = execution_id
    payload["scheduler_id"] = row["id"]
    payload["trigger_type"] = "run_once"
    return payload


def migrate(
    db_path: Path,
    state_path: Path | None = None,
    *,
    target_db_path: Path | None = None,
) -> int:
    """Copy an immutable legacy snapshot into the task store in one transaction.

    The source is retained. A committed target-side receipt makes retries a
    no-op, including after users have updated their migrated tasks. Unrelated
    pre-existing IDs raise a constraint error instead of overwriting live data.
    """
    conn: sqlite3.Connection | None = None
    try:
        source_path = db_path.resolve()
        target_path = (target_db_path or source_path.with_name("tasks.db")).resolve()
        if target_path == source_path:
            raise ValueError("Migration target must differ from the retained legacy source")
        # Read-only open also rejects a mistyped/missing source path.
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30)
        try:
            source.execute("BEGIN")
            if not _legacy_tables_exist(source):
                _clear_state(state_path)
                return 0
            tasks = _fetch_all(source, "SELECT * FROM tasks") if _table_exists(source, "tasks") else []
            records = _fetch_all(source, "SELECT * FROM task_execution_records") if _table_exists(source, "task_execution_records") else []
            queue_refs = _fetch_all(source, "SELECT * FROM task_queue_refs") if _table_exists(source, "task_queue_refs") else []
        finally:
            source.close()

        target_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target_path, timeout=30)
        conn.execute("BEGIN IMMEDIATE")
        # executescript would implicitly commit an existing transaction.
        for statement in TASKS_DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        if conn.execute(
            "SELECT 1 FROM task_legacy_migrations WHERE source_db = ?",
            (str(source_path),),
        ).fetchone():
            conn.rollback()
            _clear_state(state_path)
            return 0

        task_map = {row["id"]: row for row in tasks}
        record_ids = {row["id"] for row in records}
        synthetic_execution_ids: dict[str, str] = {}

        _insert_schedulers(conn, (_build_scheduler_payload(row) for row in tasks))
        execution_payloads = {
            row["id"]: _build_manual_execution_payload(row)
            for row in tasks if row.get("type") != "scheduled"
        }
        execution_payloads.update({
            row["id"]: _build_record_execution_payload(row, task_map.get(row["task_id"]))
            for row in records
        })

        for row in tasks:
            if row.get("type") != "scheduled":
                continue
            if row.get("status") not in ("queued", "running", "paused"):
                continue
            context = _json_loads(row.get("context")) or {}
            record_id = context.get("_execution_record_id")
            if record_id and record_id in record_ids:
                continue
            synthetic_id = f"legacy_exec_{row['id']}"
            synthetic_execution_ids[row["id"]] = synthetic_id
            execution_payloads[synthetic_id] = _build_synthetic_active_execution(row, synthetic_id)

        # Paused was removed from the runtime enum. Normalize before writing so
        # an already-running TaskStore sees the same result as a fresh startup.
        for payload in execution_payloads.values():
            if payload["status"] == "paused":
                payload["status"] = "cancelled"
                payload["completed_at"] = (
                    payload["completed_at"] or payload["updated_at"] or payload["created_at"] or _now_iso()
                )
                payload["error"] = payload["error"] or "Normalized from legacy paused state."
        _insert_executions(conn, execution_payloads.values())

        seen_queue_targets: set[str] = set()
        queue_payloads: list[dict[str, Any]] = []
        for row in queue_refs:
            execution_id = row.get("execution_record_id")
            if not execution_id:
                task_row = task_map.get(row["task_id"])
                if task_row and task_row.get("type") == "scheduled":
                    execution_id = synthetic_execution_ids.get(row["task_id"])
                else:
                    execution_id = row["task_id"]
            if not execution_id:
                continue
            seen_queue_targets.add(execution_id)
            queue_status = row.get("status") or "queued"
            if queue_status == "paused" or execution_payloads.get(execution_id, {}).get("status") == "cancelled":
                queue_status = "completed"
            queue_payloads.append(
                {
                    "id": row["id"],
                    "execution_id": execution_id,
                    "status": queue_status,
                    "created_at": row.get("created_at") or _now_iso(),
                    "started_at": row.get("started_at"),
                },
            )

        for row in tasks:
            if row.get("type") == "scheduled":
                continue
            if row.get("status") not in ("queued", "running") or row["id"] in seen_queue_targets:
                continue
            queue_payloads.append(
                {
                    "id": f"legacy_qref_{row['id']}",
                    "execution_id": row["id"],
                    "status": row["status"],
                    "created_at": row.get("created_at") or _now_iso(),
                    "started_at": _legacy_execution(row).get("started_at"),
                },
            )

        _insert_queue_refs(conn, queue_payloads)
        conn.execute(
            "INSERT INTO task_legacy_migrations (source_db, completed_at) VALUES (?, ?)",
            (str(source_path), _now_iso()),
        )
        conn.commit()
        _clear_state(state_path)
        return 0
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        _write_state(
            state_path,
            {
                "failed": True,
                "error": str(exc),
                "notice_count": 0,
                "updated_at": _now_iso(),
            },
        )
        print(f"legacy migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


def _default_db_path() -> Path:
    data_dir = os.environ.get("FLOCKS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "flocks.db"
    root = os.environ.get("FLOCKS_ROOT")
    if root:
        return Path(root) / "data" / "flocks.db"
    return Path.home() / ".flocks" / "data" / "flocks.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy task tables into scheduler/execution tables.")
    parser.add_argument("--db", dest="db_path", default=None, help="Legacy source SQLite database (preserved)")
    parser.add_argument("--target-db", default=None, help="Target SQLite database (default: tasks.db beside source)")
    parser.add_argument("--state-file", dest="state_file", default=None, help="Migration state file path")
    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    state_path = Path(args.state_file) if args.state_file else None
    target_db_path = Path(args.target_db) if args.target_db else None
    return migrate(db_path, state_path, target_db_path=target_db_path)


if __name__ == "__main__":
    raise SystemExit(main())
