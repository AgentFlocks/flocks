"""
Task Store — SQLite persistence for Task Center.

Manages three tables:
  - tasks: stable task definitions and latest state
  - task_execution_records: per-execution history for scheduled tasks
  - task_queue_refs: queued/running references to task definitions
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from pydantic import BaseModel

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .models import (
    DeliveryStatus,
    QueueTaskItem,
    Task,
    TaskExecution,
    TaskExecutionRecord,
    TaskPriority,
    TaskQueueRef,
    TaskStatus,
    TaskType,
)

log = Log.create(service="task.store")


class TaskStore:
    """SQLite-backed CRUD for tasks and execution records."""

    _initialized = False
    _conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    @classmethod
    async def init(cls) -> None:
        if cls._initialized:
            return
        await Storage._ensure_init()
        db_path = Storage._db_path
        cls._conn = await aiosqlite.connect(db_path)
        await cls._conn.execute("PRAGMA foreign_keys = ON")
        await cls._conn.executescript(_TASKS_DDL)
        for stmt in _INDEX_STMTS:
            try:
                await cls._conn.execute(stmt)
            except Exception:
                pass
        for stmt in _MIGRATION_STMTS:
            try:
                await cls._conn.execute(stmt)
            except Exception:
                pass  # column already exists
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

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def create_task(cls, task: Task) -> Optional[Task]:
        """Persist a new task.

        If the task has a ``dedup_key`` and an active task (PENDING / QUEUED /
        RUNNING, or FAILED-with-pending-retry) with the same key already
        exists, the insert is skipped and ``None`` is returned so the caller
        can detect the dedup.
        """
        if task.dedup_key:
            existing = await cls.get_active_by_dedup_key(task.dedup_key)
            if existing is not None:
                log.info("task.dedup_skipped", {
                    "dedup_key": task.dedup_key,
                    "title": task.title,
                    "existing_id": existing.id,
                })
                return None

        db = await cls._db()
        await db.execute(
            """INSERT INTO tasks
               (id, title, description, type, status, priority,
                source, schedule, execution, delivery_status,
                execution_mode, agent_name, workflow_id, skills, category,
                context, workspace_directory, retry, tags, created_at,
                updated_at, created_by, dedup_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            cls._task_to_row(task),
        )
        await db.commit()
        if task.status == TaskStatus.QUEUED:
            await cls.enqueue_task_ref(
                task.id,
                execution_record_id=(task.context or {}).get("_execution_record_id"),
            )
        log.info("task.created", {"id": task.id, "type": task.type.value})
        return task

    @classmethod
    async def get_task(cls, task_id: str) -> Optional[Task]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_task(row) if row else None

    @classmethod
    async def list_tasks(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        task_type: Optional[TaskType] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[Task], int]:
        """Return (items, total_count)."""
        where, params = cls._build_where(
            status=status,
            task_type=task_type,
            priority=priority,
            delivery_status=delivery_status,
        )
        allowed_sort = {"created_at", "updated_at", "priority"}
        col = sort_by if sort_by in allowed_sort else "created_at"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT COUNT(*) as cnt FROM tasks {where}", params
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        async with db.execute(
            f"SELECT * FROM tasks {where} ORDER BY {col} {direction} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows], total

    @classmethod
    async def list_queue_items(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[List[QueueTaskItem], int]:
        _, manual_total = await cls.list_tasks(
            status=status,
            task_type=TaskType.QUEUED,
            priority=priority,
            delivery_status=delivery_status,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=0,
            limit=1,
        )
        manual_tasks: List[Task] = []
        if manual_total:
            manual_tasks, _ = await cls.list_tasks(
                status=status,
                task_type=TaskType.QUEUED,
                priority=priority,
                delivery_status=delivery_status,
                sort_by=sort_by,
                sort_order=sort_order,
                offset=0,
                limit=manual_total,
            )
        scheduled_items = await cls._list_scheduled_queue_items(
            status=status,
            priority=priority,
            delivery_status=delivery_status,
        )

        items: List[QueueTaskItem] = [
            QueueTaskItem(
                **task.model_dump(mode="python"),
                task_id=task.id,
                source_task_type=task.type,
                record_id=None,
            )
            for task in manual_tasks
        ]
        items.extend(scheduled_items)

        reverse = sort_order.lower() != "asc"
        if sort_by == "priority":
            items.sort(key=lambda item: item.priority.weight, reverse=reverse)
        elif sort_by == "updated_at":
            items.sort(key=lambda item: item.updated_at, reverse=reverse)
        else:
            items.sort(key=lambda item: item.created_at, reverse=reverse)

        total = len(items)
        paged = items[offset: offset + limit]
        return paged, total

    @classmethod
    async def update_task(cls, task: Task) -> Task:
        task.touch()
        db = await cls._db()
        await db.execute(
            """UPDATE tasks SET
                 title=?, description=?, status=?, priority=?,
                 source=?, schedule=?, execution=?, delivery_status=?,
                 execution_mode=?, agent_name=?, workflow_id=?,
                 skills=?, category=?,
                 context=?, workspace_directory=?, retry=?, tags=?, updated_at=?, created_by=?,
                 dedup_key=?
               WHERE id=?""",
            (
                task.title,
                task.description,
                task.status.value,
                task.priority.value,
                _json(task.source),
                _json(task.schedule),
                _json(task.execution),
                task.delivery_status.value,
                task.execution_mode.value,
                task.agent_name,
                task.workflow_id,
                json.dumps(task.skills),
                task.category,
                _json(task.context),
                task.workspace_directory,
                _json(task.retry),
                json.dumps(task.tags),
                task.updated_at.isoformat(),
                task.created_by,
                task.dedup_key,
                task.id,
            ),
        )
        await db.commit()
        return task

    @classmethod
    async def delete_task(cls, task_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def batch_delete(cls, task_ids: List[str]) -> int:
        if not task_ids:
            return 0
        placeholders = ",".join("?" for _ in task_ids)
        db = await cls._db()
        cur = await db.execute(
            f"DELETE FROM tasks WHERE id IN ({placeholders})",
            tuple(task_ids),
        )
        await db.commit()
        return cur.rowcount

    @classmethod
    async def batch_update_status(
        cls, task_ids: List[str], status: TaskStatus
    ) -> int:
        if not task_ids:
            return 0
        placeholders = ",".join("?" for _ in task_ids)
        now = datetime.now(timezone.utc).isoformat()
        db = await cls._db()
        cur = await db.execute(
            f"UPDATE tasks SET status=?, updated_at=? WHERE id IN ({placeholders})",
            (status.value, now, *task_ids),
        )
        await db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Execution Records (for scheduled tasks)
    # ------------------------------------------------------------------

    @classmethod
    async def create_record(cls, record: TaskExecutionRecord) -> TaskExecutionRecord:
        db = await cls._db()
        await db.execute(
            """INSERT INTO task_execution_records
               (id, task_id, status, started_at, completed_at,
                duration_ms, result_summary, error, session_id, delivery_status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                record.id,
                record.task_id,
                record.status.value,
                record.started_at.isoformat() if record.started_at else None,
                record.completed_at.isoformat() if record.completed_at else None,
                record.duration_ms,
                record.result_summary,
                record.error,
                record.session_id,
                record.delivery_status.value,
            ),
        )
        await db.commit()
        return record

    @classmethod
    async def update_record(cls, record: TaskExecutionRecord) -> TaskExecutionRecord:
        db = await cls._db()
        await db.execute(
            """UPDATE task_execution_records SET
                 status=?, completed_at=?, duration_ms=?,
                 result_summary=?, error=?, session_id=?, delivery_status=?
               WHERE id=?""",
            (
                record.status.value,
                record.completed_at.isoformat() if record.completed_at else None,
                record.duration_ms,
                record.result_summary,
                record.error,
                record.session_id,
                record.delivery_status.value,
                record.id,
            ),
        )
        await db.commit()
        return record

    @classmethod
    async def list_records(
        cls, task_id: str, *, limit: int = 5, offset: int = 0
    ) -> tuple[List[TaskExecutionRecord], int]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM task_execution_records WHERE task_id=?",
            (task_id,),
        ) as cur:
            total = (await cur.fetchone())["cnt"]
        async with db.execute(
            "SELECT * FROM task_execution_records WHERE task_id=? "
            "ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (task_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_record(r) for r in rows], total

    @classmethod
    async def get_record(cls, record_id: str) -> Optional[TaskExecutionRecord]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM task_execution_records WHERE id = ?",
            (record_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_record(row) if row else None

    @classmethod
    async def delete_record(cls, record_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute(
            "DELETE FROM task_execution_records WHERE id = ?",
            (record_id,),
        )
        await db.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Dashboard helpers
    # ------------------------------------------------------------------

    @classmethod
    async def dashboard_counts(cls) -> Dict[str, Any]:
        from datetime import timedelta
        week_start = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat()
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        counts: Dict[str, Any] = {}

        async def _count(sql: str, params: tuple = ()) -> int:
            async with db.execute(sql, params) as cur:
                return (await cur.fetchone())["c"]

        counts["running"] = await _count(
            "SELECT COUNT(*) as c FROM tasks WHERE status='running'"
        )
        counts["queued"] = await _count(
            "SELECT COUNT(*) as c FROM task_queue_refs WHERE status='queued'"
        )
        counts["scheduled_active"] = await _count(
            "SELECT COUNT(*) as c FROM tasks WHERE type='scheduled' AND status!='cancelled' AND json_extract(schedule, '$.enabled') = 1"
        )

        manual_completed_week = await _count(
            "SELECT COUNT(*) as c FROM tasks WHERE type!='scheduled' AND status='completed' AND updated_at>=?",
            (week_start,),
        )
        scheduled_completed_week = await _count(
            """SELECT COUNT(*) as c
               FROM task_execution_records r
               JOIN tasks t ON t.id = r.task_id
               WHERE t.type='scheduled'
                 AND r.status='completed'
                 AND COALESCE(r.completed_at, r.started_at) >= ?""",
            (week_start,),
        )
        counts["completed_week"] = manual_completed_week + scheduled_completed_week

        manual_completed_unviewed = await _count(
            "SELECT COUNT(*) as c FROM tasks WHERE type!='scheduled' AND status='completed' AND delivery_status!='viewed'"
        )
        scheduled_completed_unviewed = await _count(
            """SELECT COUNT(*) as c
               FROM task_execution_records r
               JOIN tasks t ON t.id = r.task_id
               WHERE t.type='scheduled'
                 AND r.status='completed'
                 AND r.delivery_status!='viewed'"""
        )
        counts["completed_unviewed"] = (
            manual_completed_unviewed + scheduled_completed_unviewed
        )

        manual_failed_week = await _count(
            "SELECT COUNT(*) as c FROM tasks WHERE type!='scheduled' AND status='failed' AND updated_at>=?",
            (week_start,),
        )
        scheduled_failed_week = await _count(
            """SELECT COUNT(*) as c
               FROM task_execution_records r
               JOIN tasks t ON t.id = r.task_id
               WHERE t.type='scheduled'
                 AND r.status='failed'
                 AND COALESCE(r.completed_at, r.started_at) >= ?""",
            (week_start,),
        )
        counts["failed_week"] = manual_failed_week + scheduled_failed_week
        return counts

    # ------------------------------------------------------------------
    # Queue reference helpers (used by TaskQueue)
    # ------------------------------------------------------------------

    @classmethod
    async def enqueue_task_ref(
        cls,
        task_id: str,
        *,
        execution_record_id: Optional[str] = None,
    ) -> Optional[TaskQueueRef]:
        active = await cls.get_active_queue_ref(task_id)
        if active is not None:
            return None
        ref = TaskQueueRef(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            execution_record_id=execution_record_id,
        )
        db = await cls._db()
        await db.execute(
            """INSERT INTO task_queue_refs
               (id, task_id, status, created_at, started_at, execution_record_id)
               VALUES (?,?,?,?,?,?)""",
            (
                ref.id,
                ref.task_id,
                ref.status.value,
                ref.created_at.isoformat(),
                ref.started_at.isoformat() if ref.started_at else None,
                ref.execution_record_id,
            ),
        )
        await db.commit()
        return ref

    @classmethod
    async def get_active_queue_ref(cls, task_id: str) -> Optional[TaskQueueRef]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM task_queue_refs
               WHERE task_id = ?
                 AND status IN ('queued', 'running')
               ORDER BY created_at ASC
               LIMIT 1""",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_queue_ref(row) if row else None

    @classmethod
    async def claim_next_queue_task(
        cls, *, exclude_ids: Optional[List[str]] = None
    ) -> Optional[Tuple[Task, TaskQueueRef]]:
        """Pick and claim the next queued task reference."""
        excl = ""
        params: list = []
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            excl = f"AND t.id NOT IN ({placeholders})"
            params.extend(exclude_ids)
        sql = f"""
            SELECT
                q.id AS queue_ref_id,
                q.task_id AS queue_ref_task_id,
                q.status AS queue_ref_status,
                q.created_at AS queue_ref_created_at,
                q.started_at AS queue_ref_started_at,
                q.execution_record_id AS queue_ref_execution_record_id,
                t.*
            FROM task_queue_refs q
            JOIN tasks t ON t.id = q.task_id
            WHERE q.status='queued' {excl}
            ORDER BY
              CASE t.priority
                WHEN 'urgent' THEN 1
                WHEN 'high'   THEN 2
                WHEN 'normal' THEN 3
                WHEN 'low'    THEN 4
              END,
              q.created_at ASC
            LIMIT 1
        """
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        queue_ref = TaskQueueRef(
            id=row["queue_ref_id"],
            task_id=row["queue_ref_task_id"],
            status=TaskStatus(row["queue_ref_status"]),
            created_at=datetime.fromisoformat(row["queue_ref_created_at"]),
            started_at=(
                datetime.fromisoformat(row["queue_ref_started_at"])
                if row["queue_ref_started_at"] else None
            ),
            execution_record_id=row["queue_ref_execution_record_id"],
        )

        claimed_at = datetime.now(timezone.utc)
        await db.execute(
            """UPDATE task_queue_refs
               SET status = ?, started_at = ?
               WHERE id = ? AND status = 'queued'""",
            (TaskStatus.RUNNING.value, claimed_at.isoformat(), queue_ref.id),
        )
        await db.commit()

        queue_ref.status = TaskStatus.RUNNING
        queue_ref.started_at = claimed_at
        task_data = dict(row)
        for key in (
            "queue_ref_id", "queue_ref_task_id", "queue_ref_status",
            "queue_ref_created_at", "queue_ref_started_at",
            "queue_ref_execution_record_id",
        ):
            task_data.pop(key, None)
        return cls._row_to_task(task_data), queue_ref

    @classmethod
    async def dequeue_next(
        cls, *, exclude_ids: Optional[List[str]] = None
    ) -> Optional[Task]:
        claimed = await cls.claim_next_queue_task(exclude_ids=exclude_ids)
        if not claimed:
            return None
        task, queue_ref = claimed
        if queue_ref.execution_record_id:
            ctx = dict(task.context or {})
            ctx["_execution_record_id"] = queue_ref.execution_record_id
            task.context = ctx
        return task

    @classmethod
    async def finish_queue_ref(cls, task_id: str) -> None:
        db = await cls._db()
        await db.execute(
            "DELETE FROM task_queue_refs WHERE task_id = ? AND status IN ('queued', 'running')",
            (task_id,),
        )
        await db.commit()

    @classmethod
    async def count_running(cls) -> int:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE status='running'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def count_queued_refs(cls) -> int:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) as c FROM task_queue_refs WHERE status='queued'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def requeue_running_refs(cls) -> int:
        db = await cls._db()
        cur = await db.execute(
            """UPDATE task_queue_refs
               SET status = 'queued', started_at = NULL
               WHERE status = 'running'"""
        )
        await db.commit()
        return cur.rowcount

    @classmethod
    async def get_unviewed_results(cls) -> List[Task]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE status='completed' AND delivery_status!='viewed' "
            "ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows]

    @classmethod
    async def get_scheduled_tasks(cls, *, enabled_only: bool = True) -> List[Task]:
        where = "WHERE type='scheduled' AND status != 'cancelled'"
        if enabled_only:
            where += " AND json_extract(schedule, '$.enabled') = 1"
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM tasks {where}") as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows]

    @classmethod
    async def get_active_by_dedup_key(cls, dedup_key: str) -> Optional[Task]:
        """Return the active task (PENDING/QUEUED/RUNNING or FAILED-with-retry)
        that holds the given dedup_key, or None."""
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE dedup_key = ?
                 AND (status IN ('pending', 'queued', 'running')
                      OR (status = 'failed'
                          AND json_extract(retry, '$.retry_after') IS NOT NULL
                          AND json_extract(retry, '$.retry_count') < json_extract(retry, '$.max_retries')))
               LIMIT 1""",
            (dedup_key,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_task(row) if row else None

    @classmethod
    async def get_by_dedup_key(cls, dedup_key: str) -> Optional[Task]:
        """Return the most recent task with the given dedup_key regardless of
        status, or None if no such task has ever been created."""
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE dedup_key = ? ORDER BY created_at DESC LIMIT 1",
            (dedup_key,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_task(row) if row else None

    # ------------------------------------------------------------------
    # Startup-recovery / retry / expiry helpers
    # ------------------------------------------------------------------

    @classmethod
    async def list_by_status(cls, status: TaskStatus) -> List[Task]:
        """Return all tasks matching the given status (used for startup recovery)."""
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE status = ?",
            (status.value,),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows]

    @classmethod
    async def list_retryable_failed(cls) -> List[Task]:
        """Return FAILED tasks whose retry_after timestamp has passed and that have
        remaining retry attempts."""
        now_iso = datetime.now(timezone.utc).isoformat()
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE status = 'failed'
                 AND json_extract(retry, '$.retry_after') IS NOT NULL
                 AND json_extract(retry, '$.retry_after') <= ?
                 AND json_extract(retry, '$.retry_count') < json_extract(retry, '$.max_retries')""",
            (now_iso,),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows]

    @classmethod
    async def list_stale_queued(cls, before: datetime) -> List[Task]:
        """Return PENDING/QUEUED tasks whose last activity (updated_at) is older
        than *before*.  Using updated_at instead of created_at avoids
        accidentally expiring tasks that were recently re-queued by retry."""
        db = await cls._db()
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('pending', 'queued')
                 AND type != 'scheduled'
                 AND updated_at < ?""",
            (before.isoformat(),),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_task(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _task_to_row(cls, t: Task) -> tuple:
        return (
            t.id,
            t.title,
            t.description,
            t.type.value,
            t.status.value,
            t.priority.value,
            _json(t.source),
            _json(t.schedule),
            _json(t.execution),
            t.delivery_status.value,
            t.execution_mode.value,
            t.agent_name,
            t.workflow_id,
            json.dumps(t.skills),
            t.category,
            _json(t.context),
            t.workspace_directory,
            _json(t.retry),
            json.dumps(t.tags),
            t.created_at.isoformat(),
            t.updated_at.isoformat(),
            t.created_by,
            t.dedup_key,
        )

    @classmethod
    def _row_to_task(cls, row: aiosqlite.Row) -> Task:
        d = dict(row)
        for col in ("source", "schedule", "execution", "context", "retry"):
            if d.get(col):
                d[col] = json.loads(d[col])
            elif col in ("context",):
                d[col] = {}
            else:
                d[col] = None
        for json_list_col in ("tags", "skills"):
            if d.get(json_list_col):
                d[json_list_col] = json.loads(d[json_list_col])
            else:
                d[json_list_col] = []
        # dedup_key is stored as plain TEXT; keep as-is (None if absent)
        d.setdefault("dedup_key", None)
        return Task(**d)

    @classmethod
    def _row_to_record(cls, row: aiosqlite.Row) -> TaskExecutionRecord:
        return TaskExecutionRecord(**dict(row))

    @classmethod
    def _row_to_queue_ref(cls, row: aiosqlite.Row) -> TaskQueueRef:
        return TaskQueueRef(**dict(row))

    @classmethod
    async def _list_scheduled_queue_items(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
    ) -> List[QueueTaskItem]:
        db = await cls._db()
        db.row_factory = aiosqlite.Row

        clauses = ["t.type = 'scheduled'"]
        params: list[Any] = []
        if status:
            clauses.append("r.status = ?")
            params.append(status.value)
        if priority:
            clauses.append("t.priority = ?")
            params.append(priority.value)
        if delivery_status:
            clauses.append("r.delivery_status = ?")
            params.append(delivery_status.value)

        where = " AND ".join(clauses)
        async with db.execute(
            f"""
            SELECT
                r.id AS record_id,
                r.task_id AS task_id,
                r.status AS record_status,
                r.started_at AS record_started_at,
                r.completed_at AS record_completed_at,
                r.duration_ms AS record_duration_ms,
                r.result_summary AS record_result_summary,
                r.error AS record_error,
                r.session_id AS record_session_id,
                r.delivery_status AS record_delivery_status,
                t.*
            FROM task_execution_records r
            JOIN tasks t ON t.id = r.task_id
            WHERE {where}
            """,
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()

        items: List[QueueTaskItem] = []
        for row in rows:
            task_data = dict(row)
            record_id = task_data.pop("record_id")
            task_id = task_data.pop("task_id")
            record_status = TaskStatus(task_data.pop("record_status"))
            record_started_at = task_data.pop("record_started_at")
            record_completed_at = task_data.pop("record_completed_at")
            record_duration_ms = task_data.pop("record_duration_ms")
            record_result_summary = task_data.pop("record_result_summary")
            record_error = task_data.pop("record_error")
            record_session_id = task_data.pop("record_session_id")
            record_delivery_status = DeliveryStatus(task_data.pop("record_delivery_status"))

            base_task = cls._row_to_task(task_data)
            created_at = (
                datetime.fromisoformat(record_started_at)
                if record_started_at else base_task.updated_at
            )
            updated_at = (
                datetime.fromisoformat(record_completed_at)
                if record_completed_at else created_at
            )

            items.append(
                QueueTaskItem(
                    id=record_id,
                    task_id=task_id,
                    title=base_task.title,
                    description=base_task.description,
                    type=TaskType.QUEUED,
                    status=record_status,
                    priority=base_task.priority,
                    source=base_task.source.model_copy(
                        update={"source_type": "scheduled_trigger"}
                    ),
                    schedule=None,
                    execution=TaskExecution(
                        session_id=record_session_id,
                        agent=base_task.agent_name,
                        started_at=(
                            datetime.fromisoformat(record_started_at)
                            if record_started_at else None
                        ),
                        completed_at=(
                            datetime.fromisoformat(record_completed_at)
                            if record_completed_at else None
                        ),
                        duration_ms=record_duration_ms,
                        result_summary=record_result_summary,
                        error=record_error,
                    ),
                    delivery_status=record_delivery_status,
                    execution_mode=base_task.execution_mode,
                    agent_name=base_task.agent_name,
                    workflow_id=base_task.workflow_id,
                    skills=base_task.skills,
                    category=base_task.category,
                    context=base_task.context,
                    workspace_directory=base_task.workspace_directory,
                    retry=base_task.retry,
                    tags=base_task.tags,
                    dedup_key=None,
                    created_at=created_at,
                    updated_at=updated_at,
                    created_by=base_task.created_by,
                    source_task_type=base_task.type,
                    record_id=record_id,
                )
            )
        return items

    @classmethod
    def _build_where(
        cls,
        *,
        status: Optional[TaskStatus] = None,
        task_type: Optional[TaskType] = None,
        priority: Optional[TaskPriority] = None,
        delivery_status: Optional[DeliveryStatus] = None,
    ) -> tuple[str, tuple]:
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        elif task_type == TaskType.SCHEDULED:
            # Scheduled tasks that have been soft-deleted (CANCELLED) should not
            # appear in the list unless the caller explicitly filters by status.
            clauses.append("status != ?")
            params.append(TaskStatus.CANCELLED.value)
        if task_type:
            clauses.append("type = ?")
            params.append(task_type.value)
        if priority:
            clauses.append("priority = ?")
            params.append(priority.value)
        if delivery_status:
            clauses.append("delivery_status = ?")
            params.append(delivery_status.value)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, tuple(params)


# ======================================================================
# DDL
# ======================================================================

_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'queued',
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        TEXT NOT NULL DEFAULT 'normal',
    source          TEXT,            -- JSON
    schedule        TEXT,            -- JSON (scheduled tasks only)
    execution       TEXT,            -- JSON
    delivery_status TEXT NOT NULL DEFAULT 'unread',
    execution_mode  TEXT NOT NULL DEFAULT 'agent',
    agent_name      TEXT NOT NULL DEFAULT 'rex',
    workflow_id     TEXT,
    skills          TEXT DEFAULT '[]', -- JSON array
    category        TEXT,
    context         TEXT DEFAULT '{}', -- JSON
    workspace_directory TEXT,
    retry           TEXT,            -- JSON
    tags            TEXT DEFAULT '[]', -- JSON array
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'rex',
    dedup_key       TEXT
);

CREATE TABLE IF NOT EXISTS task_execution_records (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    started_at      TEXT,
    completed_at    TEXT,
    duration_ms     INTEGER,
    result_summary  TEXT,
    error           TEXT,
    session_id      TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'unread',
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_queue_refs (
    id                  TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    execution_record_id TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (execution_record_id) REFERENCES task_execution_records(id) ON DELETE SET NULL
);
"""

_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(type)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_delivery ON tasks(delivery_status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_dedup ON tasks(dedup_key)",
    "CREATE INDEX IF NOT EXISTS idx_texec_task ON task_execution_records(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_texec_started ON task_execution_records(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_tqref_task ON task_queue_refs(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_tqref_status_created ON task_queue_refs(status, created_at)",
]

# Migrations for tables created before these columns existed.
_MIGRATION_STMTS = [
    "ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'agent'",
    "ALTER TABLE tasks ADD COLUMN agent_name TEXT NOT NULL DEFAULT 'rex'",
    "ALTER TABLE tasks ADD COLUMN workflow_id TEXT",
    "ALTER TABLE tasks ADD COLUMN skills TEXT DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN category TEXT",
    "ALTER TABLE tasks ADD COLUMN workspace_directory TEXT",
    "ALTER TABLE tasks ADD COLUMN dedup_key TEXT",
]


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump_json()
    return json.dumps(obj)
