"""Task Store — SQLite persistence for scheduler/execution domain."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from pydantic import BaseModel

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .models import (
    DeliveryStatus,
    SchedulerStatus,
    TaskExecution,
    TaskExecutionQueueRef,
    TaskPriority,
    TaskScheduler,
    TaskStatus,
)

log = Log.create(service="task.store")


class TaskStore:
    _initialized = False
    _conn: Optional[aiosqlite.Connection] = None

    @classmethod
    async def init(cls) -> None:
        if cls._initialized:
            return
        await Storage._ensure_init()
        cls._conn = await aiosqlite.connect(Storage._db_path)
        await cls._conn.execute("PRAGMA foreign_keys = ON")
        await cls._conn.executescript(_TASKS_DDL)
        for stmt in _INDEX_STMTS:
            await cls._conn.execute(stmt)
        await cls._conn.commit()
        cls._initialized = True
        log.info("task.store.initialized")

    @classmethod
    async def close(cls) -> None:
        if cls._conn:
            await cls._conn.close()
            cls._conn = None
            cls._initialized = False

    @classmethod
    async def _db(cls) -> aiosqlite.Connection:
        if not cls._conn:
            await cls.init()
        return cls._conn  # type: ignore[return-value]

    @classmethod
    async def raw_db(cls) -> aiosqlite.Connection:
        return await cls._db()

    # ------------------------------------------------------------------
    # Scheduler CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def create_scheduler(cls, scheduler: TaskScheduler) -> Optional[TaskScheduler]:
        if scheduler.dedup_key:
            existing = await cls.get_scheduler_by_dedup_key(scheduler.dedup_key)
            if existing is not None:
                return None
        db = await cls._db()
        await db.execute(
            """
            INSERT INTO task_schedulers
            (id, title, description, mode, status, priority, source, trigger,
             execution_mode, agent_name, workflow_id, skills, category, context,
             workspace_directory, retry, tags, created_at, updated_at, created_by, dedup_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._scheduler_to_row(scheduler),
        )
        await db.commit()
        return scheduler

    @classmethod
    async def get_scheduler(cls, scheduler_id: str) -> Optional[TaskScheduler]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM task_schedulers WHERE id = ?",
            (scheduler_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_scheduler(row) if row else None

    @classmethod
    async def get_scheduler_by_dedup_key(
        cls, dedup_key: str
    ) -> Optional[TaskScheduler]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM task_schedulers WHERE dedup_key = ? ORDER BY created_at DESC LIMIT 1",
            (dedup_key,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_scheduler(row) if row else None

    @classmethod
    async def list_schedulers(
        cls,
        *,
        status: Optional[SchedulerStatus] = None,
        priority: Optional[TaskPriority] = None,
        scheduled_only: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[TaskScheduler], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        else:
            clauses.append("status != ?")
            params.append(SchedulerStatus.ARCHIVED.value)
        if priority:
            clauses.append("priority = ?")
            params.append(priority.value)
        if scheduled_only:
            clauses.append(
                "NOT (mode = 'once' AND COALESCE(json_extract(trigger, '$.runImmediately'), 0) = 1)"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        allowed_sort = {"created_at", "updated_at", "priority"}
        col = sort_by if sort_by in allowed_sort else "created_at"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT COUNT(*) AS cnt FROM task_schedulers {where}",
            tuple(params),
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        async with db.execute(
            f"SELECT * FROM task_schedulers {where} ORDER BY {col} {direction} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_scheduler(row) for row in rows], total

    @classmethod
    async def list_due_schedulers(cls) -> List[TaskScheduler]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM task_schedulers
            WHERE status = 'active'
            ORDER BY created_at ASC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_scheduler(row) for row in rows]

    @classmethod
    async def update_scheduler(cls, scheduler: TaskScheduler) -> TaskScheduler:
        scheduler.touch()
        db = await cls._db()
        await db.execute(
            """
            UPDATE task_schedulers SET
              title=?, description=?, mode=?, status=?, priority=?, source=?, trigger=?,
              execution_mode=?, agent_name=?, workflow_id=?, skills=?, category=?, context=?,
              workspace_directory=?, retry=?, tags=?, updated_at=?, created_by=?, dedup_key=?
            WHERE id=?
            """,
            (
                scheduler.title,
                scheduler.description,
                scheduler.mode.value,
                scheduler.status.value,
                scheduler.priority.value,
                _json(scheduler.source),
                _json(scheduler.trigger),
                scheduler.execution_mode.value,
                scheduler.agent_name,
                scheduler.workflow_id,
                json.dumps(scheduler.skills),
                scheduler.category,
                _json(scheduler.context),
                scheduler.workspace_directory,
                _json(scheduler.retry),
                json.dumps(scheduler.tags),
                scheduler.updated_at.isoformat(),
                scheduler.created_by,
                scheduler.dedup_key,
                scheduler.id,
            ),
        )
        await db.commit()
        return scheduler

    @classmethod
    async def delete_scheduler(cls, scheduler_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute(
            "DELETE FROM task_schedulers WHERE id = ?",
            (scheduler_id,),
        )
        await db.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Execution CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def create_execution(cls, execution: TaskExecution) -> TaskExecution:
        db = await cls._db()
        await db.execute(
            """
            INSERT INTO task_executions
            (id, scheduler_id, title, description, priority, source, trigger_type,
             status, delivery_status, queued_at, started_at, completed_at, duration_ms,
             session_id, result_summary, error, execution_input_snapshot,
             workspace_directory, retry, execution_mode, agent_name, workflow_id,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._execution_to_row(execution),
        )
        await db.commit()
        return execution

    @classmethod
    async def get_execution(cls, execution_id: str) -> Optional[TaskExecution]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM task_executions WHERE id = ?",
            (execution_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_execution(row) if row else None

    @classmethod
    async def list_executions(
        cls,
        *,
        scheduler_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        sort_by: str = "queued_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[TaskExecution], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if scheduler_id:
            clauses.append("scheduler_id = ?")
            params.append(scheduler_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if priority:
            clauses.append("priority = ?")
            params.append(priority.value)
        if delivery_status:
            clauses.append("delivery_status = ?")
            params.append(delivery_status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        allowed_sort = {
            "created_at",
            "updated_at",
            "priority",
            "queued_at",
            "started_at",
            "completed_at",
        }
        col = sort_by if sort_by in allowed_sort else "queued_at"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT COUNT(*) AS cnt FROM task_executions {where}",
            tuple(params),
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        async with db.execute(
            f"SELECT * FROM task_executions {where} ORDER BY {col} {direction} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows], total

    @classmethod
    async def list_scheduler_executions(
        cls, scheduler_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[List[TaskExecution], int]:
        return await cls.list_executions(
            scheduler_id=scheduler_id,
            sort_by="started_at",
            sort_order="desc",
            offset=offset,
            limit=limit,
        )

    @classmethod
    async def update_execution(cls, execution: TaskExecution) -> TaskExecution:
        execution.touch()
        db = await cls._db()
        await db.execute(
            """
            UPDATE task_executions SET
              title=?, description=?, priority=?, source=?, trigger_type=?, status=?,
              delivery_status=?, queued_at=?, started_at=?, completed_at=?, duration_ms=?,
              session_id=?, result_summary=?, error=?, execution_input_snapshot=?,
              workspace_directory=?, retry=?, execution_mode=?, agent_name=?, workflow_id=?,
              updated_at=?
            WHERE id=?
            """,
            (
                execution.title,
                execution.description,
                execution.priority.value,
                _json(execution.source),
                execution.trigger_type.value,
                execution.status.value,
                execution.delivery_status.value,
                _iso(execution.queued_at),
                _iso(execution.started_at),
                _iso(execution.completed_at),
                execution.duration_ms,
                execution.session_id,
                execution.result_summary,
                execution.error,
                _json(execution.execution_input_snapshot),
                execution.workspace_directory,
                _json(execution.retry),
                execution.execution_mode.value,
                execution.agent_name,
                execution.workflow_id,
                execution.updated_at.isoformat(),
                execution.id,
            ),
        )
        await db.commit()
        return execution

    @classmethod
    async def delete_execution(cls, execution_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute(
            "DELETE FROM task_executions WHERE id = ?",
            (execution_id,),
        )
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def batch_update_execution_status(
        cls, execution_ids: List[str], status: TaskStatus
    ) -> int:
        if not execution_ids:
            return 0
        placeholders = ",".join("?" for _ in execution_ids)
        now = datetime.now(timezone.utc).isoformat()
        db = await cls._db()
        cur = await db.execute(
            f"UPDATE task_executions SET status=?, updated_at=? WHERE id IN ({placeholders})",
            (status.value, now, *execution_ids),
        )
        await db.commit()
        return cur.rowcount

    @classmethod
    async def batch_delete_executions(cls, execution_ids: List[str]) -> int:
        if not execution_ids:
            return 0
        placeholders = ",".join("?" for _ in execution_ids)
        db = await cls._db()
        cur = await db.execute(
            f"DELETE FROM task_executions WHERE id IN ({placeholders})",
            tuple(execution_ids),
        )
        await db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    @classmethod
    async def enqueue_execution_ref(
        cls, execution_id: str
    ) -> Optional[TaskExecutionQueueRef]:
        active = await cls.get_queue_ref(execution_id)
        if active is not None:
            return None
        ref = TaskExecutionQueueRef(execution_id=execution_id)
        db = await cls._db()
        await db.execute(
            """
            INSERT INTO task_execution_queue_refs (id, execution_id, status, created_at, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ref.id,
                ref.execution_id,
                ref.status.value,
                ref.created_at.isoformat(),
                None,
            ),
        )
        await db.commit()
        return ref

    @classmethod
    async def get_queue_ref(
        cls, execution_id: str
    ) -> Optional[TaskExecutionQueueRef]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM task_execution_queue_refs
            WHERE execution_id = ? AND status IN ('queued', 'running')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (execution_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_queue_ref(row) if row else None

    @classmethod
    async def get_active_execution_for_scheduler(
        cls, scheduler_id: str
    ) -> Optional[TaskExecution]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE scheduler_id = ?
              AND status IN ('pending', 'queued', 'running', 'paused')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (scheduler_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_execution(row) if row else None

    @classmethod
    async def claim_next_queue_execution(
        cls, *, exclude_ids: Optional[List[str]] = None
    ) -> Optional[Tuple[TaskExecution, TaskExecutionQueueRef]]:
        excl = ""
        params: list[Any] = []
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            excl = f"AND e.id NOT IN ({placeholders})"
            params.extend(exclude_ids)

        sql = f"""
            SELECT
              q.id AS queue_ref_id,
              q.execution_id AS queue_ref_execution_id,
              q.status AS queue_ref_status,
              q.created_at AS queue_ref_created_at,
              q.started_at AS queue_ref_started_at,
              e.*
            FROM task_execution_queue_refs q
            JOIN task_executions e ON e.id = q.execution_id
            WHERE q.status = 'queued' {excl}
            ORDER BY
              CASE e.priority
                WHEN 'urgent' THEN 1
                WHEN 'high' THEN 2
                WHEN 'normal' THEN 3
                WHEN 'low' THEN 4
              END,
              q.created_at ASC
            LIMIT 1
        """
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, tuple(params)) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        queue_ref = TaskExecutionQueueRef(
            id=row["queue_ref_id"],
            execution_id=row["queue_ref_execution_id"],
            status=TaskStatus(row["queue_ref_status"]),
            created_at=datetime.fromisoformat(row["queue_ref_created_at"]),
            started_at=(
                datetime.fromisoformat(row["queue_ref_started_at"])
                if row["queue_ref_started_at"] else None
            ),
        )
        claimed_at = datetime.now(timezone.utc)
        await db.execute(
            """
            UPDATE task_execution_queue_refs
            SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (claimed_at.isoformat(), queue_ref.id),
        )
        await db.commit()
        queue_ref.status = TaskStatus.RUNNING
        queue_ref.started_at = claimed_at
        execution_data = dict(row)
        for key in (
            "queue_ref_id",
            "queue_ref_execution_id",
            "queue_ref_status",
            "queue_ref_created_at",
            "queue_ref_started_at",
        ):
            execution_data.pop(key, None)
        return cls._row_to_execution(execution_data), queue_ref

    @classmethod
    async def finish_queue_ref(cls, execution_id: str) -> None:
        db = await cls._db()
        await db.execute(
            """
            DELETE FROM task_execution_queue_refs
            WHERE execution_id = ? AND status IN ('queued', 'running')
            """,
            (execution_id,),
        )
        await db.commit()

    @classmethod
    async def count_running(cls) -> int:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS c FROM task_executions WHERE status = 'running'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def count_queued_refs(cls) -> int:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) AS c FROM task_execution_queue_refs WHERE status = 'queued'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def requeue_running_refs(cls) -> int:
        db = await cls._db()
        cur = await db.execute(
            """
            UPDATE task_execution_queue_refs
            SET status = 'queued', started_at = NULL
            WHERE status = 'running'
            """
        )
        await db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Dashboard / delivery
    # ------------------------------------------------------------------

    @classmethod
    async def dashboard_counts(cls) -> Dict[str, Any]:
        week_start = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat()
        db = await cls._db()
        db.row_factory = aiosqlite.Row

        async def _count(sql: str, params: tuple = ()) -> int:
            async with db.execute(sql, params) as cur:
                return (await cur.fetchone())["c"]

        return {
            "running": await _count(
                "SELECT COUNT(*) AS c FROM task_executions WHERE status = 'running'"
            ),
            "queued": await _count(
                "SELECT COUNT(*) AS c FROM task_execution_queue_refs WHERE status = 'queued'"
            ),
            "scheduled_active": await _count(
                """
                SELECT COUNT(*) AS c
                FROM task_schedulers
                WHERE status = 'active'
                  AND NOT (mode = 'once' AND COALESCE(json_extract(trigger, '$.runImmediately'), 0) = 1)
                """
            ),
            "completed_week": await _count(
                """
                SELECT COUNT(*) AS c FROM task_executions
                WHERE status = 'completed'
                  AND COALESCE(completed_at, started_at, queued_at, created_at) >= ?
                """,
                (week_start,),
            ),
            "completed_unviewed": await _count(
                """
                SELECT COUNT(*) AS c FROM task_executions
                WHERE status = 'completed' AND delivery_status != 'viewed'
                """
            ),
            "failed_week": await _count(
                """
                SELECT COUNT(*) AS c FROM task_executions
                WHERE status = 'failed'
                  AND COALESCE(completed_at, started_at, queued_at, created_at) >= ?
                """,
                (week_start,),
            ),
        }

    @classmethod
    async def get_unviewed_results(cls) -> List[TaskExecution]:
        items, _ = await cls.list_executions(
            status=TaskStatus.COMPLETED,
            delivery_status=DeliveryStatus.UNREAD,
            sort_by="completed_at",
            sort_order="desc",
            limit=100,
        )
        notified, _ = await cls.list_executions(
            status=TaskStatus.COMPLETED,
            delivery_status=DeliveryStatus.NOTIFIED,
            sort_by="completed_at",
            sort_order="desc",
            limit=100,
        )
        merged = sorted(
            [*items, *notified],
            key=lambda item: item.completed_at or item.started_at or item.queued_at or item.created_at,
            reverse=True,
        )
        return merged[:100]

    @classmethod
    async def mark_execution_viewed(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        execution = await cls.get_execution(execution_id)
        if not execution:
            return None
        execution.delivery_status = DeliveryStatus.VIEWED
        return await cls.update_execution(execution)

    @classmethod
    async def mark_execution_notified(
        cls, execution_id: str
    ) -> Optional[TaskExecution]:
        execution = await cls.get_execution(execution_id)
        if not execution:
            return None
        if execution.delivery_status == DeliveryStatus.UNREAD:
            execution.delivery_status = DeliveryStatus.NOTIFIED
            return await cls.update_execution(execution)
        return execution

    # ------------------------------------------------------------------
    # Recovery / retry / expiry helpers
    # ------------------------------------------------------------------

    @classmethod
    async def list_executions_by_status(
        cls, status: TaskStatus
    ) -> List[TaskExecution]:
        items, _ = await cls.list_executions(status=status, limit=1000)
        return items

    @classmethod
    async def list_retryable_failed_executions(cls) -> List[TaskExecution]:
        now_iso = datetime.now(timezone.utc).isoformat()
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE status = 'failed'
              AND json_extract(retry, '$.retryAfter') IS NOT NULL
              AND json_extract(retry, '$.retryAfter') <= ?
              AND json_extract(retry, '$.retryCount') < json_extract(retry, '$.maxRetries')
            """,
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows]

    @classmethod
    async def list_stale_queued_executions(
        cls, before: datetime
    ) -> List[TaskExecution]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE status IN ('pending', 'queued')
              AND updated_at < ?
            """,
            (before.isoformat(),),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows]

    # ------------------------------------------------------------------
    # Row helpers
    # ------------------------------------------------------------------

    @classmethod
    def _scheduler_to_row(cls, scheduler: TaskScheduler) -> tuple:
        return (
            scheduler.id,
            scheduler.title,
            scheduler.description,
            scheduler.mode.value,
            scheduler.status.value,
            scheduler.priority.value,
            _json(scheduler.source),
            _json(scheduler.trigger),
            scheduler.execution_mode.value,
            scheduler.agent_name,
            scheduler.workflow_id,
            json.dumps(scheduler.skills),
            scheduler.category,
            _json(scheduler.context),
            scheduler.workspace_directory,
            _json(scheduler.retry),
            json.dumps(scheduler.tags),
            scheduler.created_at.isoformat(),
            scheduler.updated_at.isoformat(),
            scheduler.created_by,
            scheduler.dedup_key,
        )

    @classmethod
    def _execution_to_row(cls, execution: TaskExecution) -> tuple:
        return (
            execution.id,
            execution.scheduler_id,
            execution.title,
            execution.description,
            execution.priority.value,
            _json(execution.source),
            execution.trigger_type.value,
            execution.status.value,
            execution.delivery_status.value,
            _iso(execution.queued_at),
            _iso(execution.started_at),
            _iso(execution.completed_at),
            execution.duration_ms,
            execution.session_id,
            execution.result_summary,
            execution.error,
            _json(execution.execution_input_snapshot),
            execution.workspace_directory,
            _json(execution.retry),
            execution.execution_mode.value,
            execution.agent_name,
            execution.workflow_id,
            execution.created_at.isoformat(),
            execution.updated_at.isoformat(),
        )

    @classmethod
    def _row_to_scheduler(cls, row: aiosqlite.Row | Dict[str, Any]) -> TaskScheduler:
        data = dict(row)
        for col in ("source", "trigger", "context", "retry"):
            if data.get(col):
                data[col] = json.loads(data[col])
        data["skills"] = json.loads(data["skills"]) if data.get("skills") else []
        data["tags"] = json.loads(data["tags"]) if data.get("tags") else []
        data.setdefault("context", {})
        return TaskScheduler(**data)

    @classmethod
    def _row_to_execution(cls, row: aiosqlite.Row | Dict[str, Any]) -> TaskExecution:
        data = dict(row)
        for col in ("source", "execution_input_snapshot", "retry"):
            if data.get(col):
                data[col] = json.loads(data[col])
        data.setdefault("execution_input_snapshot", {})
        return TaskExecution(**data)

    @classmethod
    def _row_to_queue_ref(
        cls, row: aiosqlite.Row | Dict[str, Any]
    ) -> TaskExecutionQueueRef:
        return TaskExecutionQueueRef(**dict(row))


_TASKS_DDL = """
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
    updated_at               TEXT NOT NULL,
    FOREIGN KEY (scheduler_id) REFERENCES task_schedulers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_execution_queue_refs (
    id           TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    FOREIGN KEY (execution_id) REFERENCES task_executions(id) ON DELETE CASCADE
);
"""

_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_status ON task_schedulers(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_priority ON task_schedulers(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_dedup ON task_schedulers(dedup_key)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_scheduler ON task_executions(scheduler_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_delivery ON task_executions(delivery_status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_priority ON task_executions(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_queued ON task_executions(queued_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_started ON task_executions(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_completed ON task_executions(completed_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_queue_refs_status_created ON task_execution_queue_refs(status, created_at)",
]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(by_alias=True)
    return json.dumps(obj)
