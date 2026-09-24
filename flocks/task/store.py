"""Task Store — PostgreSQL persistence for scheduler/execution domain."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from flocks.db.connect import open_transaction
from flocks.db.store import Transaction
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
    # The handle, not the driver connection: on PostgreSQL there is no
    # file to connect to and the connection comes from a shared pool.
    _handle: Optional[Transaction] = None
    # PID of the process that opened ``_handle``.  Used to detect ``fork()``
    # in uvicorn ``--reload`` / multi-worker launches and force a fresh
    # connection in the child; pooled connections belong to one process.
    _init_pid: Optional[int] = None
    _init_lock = asyncio.Lock()
    _init_lock_key = None

    @classmethod
    def get_db_path(cls) -> Path:
        """Return the legacy path used by explicit offline migration tools."""
        return Storage.get_db_path().with_name("tasks.db")

    @classmethod
    async def init(cls) -> None:
        key = (os.getpid(), asyncio.get_running_loop())
        if cls._init_lock_key != key:
            cls._init_lock, cls._init_lock_key = asyncio.Lock(), key
        async with cls._init_lock:
            await cls._initialize()

    @classmethod
    async def _initialize(cls) -> None:
        current_pid = os.getpid()
        if cls._initialized and cls._init_pid == current_pid:
            return
        if cls._initialized and cls._init_pid is not None and cls._init_pid != current_pid:
            # Inherited an open connection from a forked parent — discard
            # the handle (do **not** close it; that would tear down the
            # parent's connection state too) and rebuild from scratch.
            log.warn("task.store.fork_detected", {
                "parent_pid": cls._init_pid,
                "child_pid": current_pid,
            })
            cls._handle = None
            cls._initialized = False
            cls._init_pid = None
        await Storage._ensure_init()
        handle = await open_transaction()
        cls._handle = handle
        try:
            async with handle.transaction() as tx:
                await tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("flocks.tasks.schema",))
                await tx.executescript(";\n".join(_tasks_ddl(tx.dialect)))
                await cls._add_missing_execution_columns(tx)
                await tx.executescript(";\n".join(_INDEX_STMTS))
            cls._initialized = True
            cls._init_pid = current_pid
            await cls._normalize_legacy_paused_executions()
        except BaseException:
            await handle.close()
            cls._handle = None
            cls._initialized = False
            cls._init_pid = None
            raise

    @classmethod
    async def close(cls) -> None:
        if cls._handle:
            await cls._handle.close()
            cls._handle = None
            cls._initialized = False
            cls._init_pid = None

    @classmethod
    async def _db(cls):
        """A portable handle over this store's connection.

        Returns a Transaction, not the driver connection, so the statements in
        this module run unchanged on another backend; the cursor-shaped Result
        keeps ``async with ... as cur`` and ``cur.rowcount`` working.
        """
        # Trigger fork-safety re-init when called from a child process
        # that inherited an already-initialised module.
        if (
            cls._initialized
            and cls._init_pid is not None
            and cls._init_pid != os.getpid()
        ):
            await cls.init()
        if not cls._handle or not cls._initialized:
            await cls.init()
        return cls._handle

    @classmethod
    async def raw_db(cls) -> Transaction:
        return await cls._db()

    # ------------------------------------------------------------------
    # Scheduler CRUD
    # ------------------------------------------------------------------

    @classmethod
    async def create_scheduler(cls, scheduler: TaskScheduler) -> Optional[TaskScheduler]:
        db = await cls._db()
        async with db.transaction() as tx:
            if scheduler.dedup_key:
                await tx.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    ("task-scheduler:" + scheduler.dedup_key,),
                )
                existing = await tx.fetch_one(
                    "SELECT id FROM task_schedulers WHERE dedup_key = ? LIMIT 1",
                    (scheduler.dedup_key,),
                )
                if existing is not None:
                    return None
            await tx.execute(
                """
                INSERT INTO task_schedulers
                (id, title, description, mode, status, priority, source, trigger,
                 execution_mode, agent_name, workflow_id, skills, category, context,
                 workspace_directory, retry, tags, created_at, updated_at, created_by, dedup_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cls._scheduler_to_row(scheduler),
            )
        return scheduler

    @classmethod
    async def get_scheduler(cls, scheduler_id: str) -> Optional[TaskScheduler]:
        db = await cls._db()
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
        db = await cls._db()
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
                f"NOT (mode = 'once' AND {_run_immediately(db)})"
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        allowed_sort = {"created_at", "updated_at", "priority"}
        col = sort_by if sort_by in allowed_sort else "created_at"
        direction = "ASC" if sort_order.lower() == "asc" else "DESC"

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
        async with db.execute(
            """
            SELECT s.* FROM task_schedulers s
            WHERE s.status = 'active' AND s.mode IN ('once', 'cron')
              AND COALESCE(NULLIF(s.trigger::jsonb ->> 'nextRun', '')::timestamptz,
                           NULLIF(s.trigger::jsonb ->> 'runAt', '')::timestamptz) <= clock_timestamp()
              AND NOT EXISTS (SELECT 1 FROM task_executions e WHERE e.scheduler_id = s.id
                              AND e.status IN ('pending', 'queued', 'running'))
            ORDER BY s.created_at ASC LIMIT 256
            """
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_scheduler(row) for row in rows]

    @classmethod
    async def enqueue_schedule_occurrence(
        cls, snapshot: TaskScheduler, execution: TaskExecution, *, next_run: Optional[datetime]
    ) -> Optional[TaskExecution]:
        """Claim the schedule row, create its run, and advance it in one commit."""
        from .models import SchedulerMode

        db = await cls._db()
        async with db.transaction() as tx:
            row = await tx.fetch_one_mapping(
                "SELECT * FROM task_schedulers WHERE id = ? FOR UPDATE", (snapshot.id,)
            )
            if row is None:
                return None
            current = cls._row_to_scheduler(row)
            # A peer has advanced/disabled the occurrence, or an operator edited
            # its definition after this loop's snapshot. Retry on the next tick.
            if (current.status != SchedulerStatus.ACTIVE
                or current.updated_at != snapshot.updated_at
                or current.trigger != snapshot.trigger):
                return None
            active = await tx.fetch_one(
                "SELECT id FROM task_executions WHERE scheduler_id = ? "
                "AND status IN ('pending', 'queued', 'running') LIMIT 1", (current.id,)
            )
            if active is not None:
                return None
            occurrence = current.trigger.next_run or current.trigger.run_at
            if occurrence is not None and occurrence.tzinfo is None:
                occurrence = occurrence.replace(tzinfo=timezone.utc)
            if occurrence is None or occurrence > datetime.now(timezone.utc):
                return None
            execution.status = TaskStatus.QUEUED
            execution.queued_at = datetime.now(timezone.utc)
            await cls.create_execution(execution, transaction=tx)
            ref = TaskExecutionQueueRef(execution_id=execution.id)
            await tx.execute(
                "INSERT INTO task_execution_queue_refs (id, execution_id, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (ref.id, execution.id, ref.created_at.isoformat()),
            )
            current.trigger.next_run = next_run
            if current.mode == SchedulerMode.ONCE:
                current.status = SchedulerStatus.DISABLED
            await tx.execute(
                "UPDATE task_schedulers SET trigger = ?, status = ?, updated_at = ? WHERE id = ?",
                (_json(current.trigger), current.status.value, datetime.now(timezone.utc).isoformat(), current.id),
            )
        return execution

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
    async def create_execution(cls, execution: TaskExecution, *, transaction=None) -> TaskExecution:
        db = transaction or await cls._db()
        await db.execute(
            """
            INSERT INTO task_executions
            (id, scheduler_id, title, description, priority, source, trigger_type,
             status, delivery_status, queued_at, started_at, completed_at, duration_ms,
             session_id, result_summary, error, execution_input_snapshot,
             workspace_directory, retry, execution_mode, agent_name, workflow_id,
             owner_node_id, lease_expires_at, owner_generation,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._execution_to_row(execution),
        )
        if transaction is None:
            await db.commit()
        return execution

    @classmethod
    async def _add_missing_execution_columns(cls, db=None) -> None:
        """Bring an existing database up to the current execution schema.

        Adds only what is absent and never rewrites a row, so running against a
        database that already has the columns is a no-op.
        """
        db = db if db is not None else cls._handle
        if db is None:
            return
        # Inspect PostgreSQL information_schema before additive upgrades.
        d = db.dialect
        column_rows = await db.fetch_all(
            d.columns_query().format(table="task_executions"),
            d.columns_query_params("task_executions"),
        )
        name_at = d.column_name_index()
        existing = {row[name_at] for row in column_rows}

        text, num = d.text_type(), d.integer_type()
        additions = [
            ("owner_node_id", f"ALTER TABLE task_executions ADD COLUMN owner_node_id {text}"),
            ("lease_expires_at", f"ALTER TABLE task_executions ADD COLUMN lease_expires_at {text}"),
            (
                "owner_generation",
                f"ALTER TABLE task_executions ADD COLUMN owner_generation {num} NOT NULL DEFAULT 0",
            ),
        ]
        added = [name for name, _ in additions if name not in existing]
        for column, statement in additions:
            if column in existing:
                continue
            await db.execute(statement)
        if added:
            log.info("task.store.execution_columns_added", {"columns": added})

    # ------------------------------------------------------------------
    # Execution ownership (node leases)
    # ------------------------------------------------------------------

    @classmethod
    async def claim_execution(
        cls,
        execution_id: str,
        *,
        node_id: str,
        lease_seconds: int,
        expected_generation: Optional[int] = None,
        allow_own_live: bool = False,
    ) -> Optional[TaskExecution]:
        """Take ownership of an execution and start its lease.

        A live claim is never stealable, including by the same owner string.
        ``allow_own_live`` is accepted for older extensions but cannot override
        lease fencing. Node identity alone is not proof an owner has died.
        """
        db = await cls._db()
        conditions = [
            "id = ?",
            "status = 'running'",
            "(lease_expires_at IS NULL "
            "OR lease_expires_at::timestamptz <= clock_timestamp())",
        ]
        params: List[Any] = [execution_id]
        if expected_generation is not None:
            conditions.append("owner_generation = ?")
            params.append(expected_generation)

        # Read the generation under the same row lock as the increment. A
        # separate SELECT could return a concurrent same-node claimant's token.
        async with db.transaction() as tx:
            cur = await tx.execute(
                f"""
                UPDATE task_executions
                SET owner_node_id = ?,
                    lease_expires_at = to_char((clock_timestamp() + (? * interval '1 second')) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    owner_generation = owner_generation + 1,
                    updated_at = to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                WHERE {" AND ".join(conditions)}
                """,
                (node_id, max(1, lease_seconds), *params),
            )
            if cur.rowcount == 0:
                return None
            row = await tx.fetch_one_mapping("SELECT * FROM task_executions WHERE id = ?", (execution_id,))
        return cls._row_to_execution(row)

    @classmethod
    async def renew_execution_lease(
        cls,
        execution_id: str,
        *,
        node_id: str,
        generation: int,
        lease_seconds: int,
    ) -> bool:
        """Extend our claim.

        Fails if the row moved on -- a different owner or a newer generation
        means this node was already replaced and must stop, rather than renew a
        claim it no longer holds.
        """
        db = await cls._db()
        cur = await db.execute(
            """
            UPDATE task_executions
            SET lease_expires_at = to_char((clock_timestamp() + (? * interval '1 second')) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                updated_at = to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            WHERE id = ? AND owner_node_id = ? AND owner_generation = ?
              AND status = 'running' AND lease_expires_at::timestamptz > clock_timestamp()
            """,
            (max(1, lease_seconds), execution_id, node_id, generation),
        )
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def release_execution_lease(
        cls, execution_id: str, *, node_id: str, expected_generation: Optional[int] = None
    ) -> bool:
        """Give up ownership, e.g. once the execution reaches a terminal state."""
        db = await cls._db()
        generation_clause = " AND owner_generation = ?" if expected_generation is not None else ""
        params: tuple = (datetime.now(timezone.utc).isoformat(), execution_id, node_id)
        if expected_generation is not None:
            params += (expected_generation,)
        cur = await db.execute(
            f"""
            UPDATE task_executions
            SET owner_node_id = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE id = ? AND owner_node_id = ?{generation_clause}
            """,
            params,
        )
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def set_execution_session(
        cls, execution_id: str, session_id: Optional[str], *, node_id: str,
        expected_generation: int,
    ) -> bool:
        db = await cls._db()
        now = datetime.now(timezone.utc).isoformat()
        cur = await db.execute(
            """UPDATE task_executions SET session_id = ?, updated_at = ?
               WHERE id = ? AND owner_node_id = ? AND owner_generation = ?
                 AND status = 'running' AND lease_expires_at::timestamptz > clock_timestamp()""",
            (session_id, now, execution_id, node_id, expected_generation),
        )
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def list_reclaimable_executions(
        cls,
        *,
        node_id: str,
        stale_before: datetime,
        now: Optional[datetime] = None,
        include_own_live: bool = False,
    ) -> List[TaskExecution]:
        """Only expired leases or sufficiently old unclaimed rows qualify.

        Legacy ``node_id``/``include_own_live`` arguments cannot bypass a live
        lease. A recently dequeued row may still be waiting for its first claim.
        """
        moment = now.isoformat() if now is not None else None
        db = await cls._db()
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE status = 'running'
              AND (
                    (lease_expires_at IS NOT NULL AND
                     lease_expires_at::timestamptz <= COALESCE(?::text::timestamptz, clock_timestamp()))
                 OR (
                        lease_expires_at IS NULL
                    AND COALESCE(started_at, queued_at, created_at, updated_at)::timestamptz < ?::text::timestamptz
                    )
              )
            """,
            (moment, stale_before.isoformat()),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows]

    @classmethod
    async def get_execution(cls, execution_id: str) -> Optional[TaskExecution]:
        db = await cls._db()
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
    async def list_active_executions_for_scheduler(
        cls, scheduler_id: str
    ) -> List[TaskExecution]:
        db = await cls._db()
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE scheduler_id = ?
              AND status IN ('pending', 'queued', 'running')
            ORDER BY created_at ASC
            """,
            (scheduler_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows]

    @classmethod
    async def update_execution(
        cls, execution: TaskExecution, *, expected_generation: Optional[int] = None,
        expected_status: Optional[TaskStatus] = None,
    ) -> TaskExecution:
        db = await cls._db()
        return await cls._update_execution_on(
            db, execution, expected_generation=expected_generation, expected_status=expected_status,
        )

    @classmethod
    async def _update_execution_on(
        cls, db, execution: TaskExecution, *, expected_generation: Optional[int] = None,
        expected_status: Optional[TaskStatus] = None, clear_owner: bool = False,
    ) -> TaskExecution:
        execution.touch()
        generation_clause = " AND owner_generation = ?" if expected_generation is not None else ""
        status_clause = " AND status = ?" if expected_status is not None else ""
        owner_clause = ", owner_node_id = NULL, lease_expires_at = NULL" if clear_owner else ""
        cursor = await db.execute(
            f"""
            UPDATE task_executions SET
              title=?, description=?, priority=?, source=?, trigger_type=?, status=?,
              delivery_status=?, queued_at=?, started_at=?, completed_at=?, duration_ms=?,
              session_id=?, result_summary=?, error=?, execution_input_snapshot=?,
              workspace_directory=?, retry=?, execution_mode=?, agent_name=?, workflow_id=?,
              updated_at=?{owner_clause}
            WHERE id=?{generation_clause}{status_clause}
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
            ) + ((expected_generation,) if expected_generation is not None else ())
            + ((expected_status.value,) if expected_status is not None else ()),
        )
        await db.commit()
        if (expected_generation is not None or expected_status is not None) and cursor.rowcount == 0:
            from .lease import ExecutionOwnershipError
            raise ExecutionOwnershipError(f"Execution {execution.id} changed owner")
        return execution

    @classmethod
    async def requeue_execution(
        cls, execution: TaskExecution, *, expected_generation: Optional[int] = None,
        expected_status: Optional[TaskStatus] = None,
    ) -> TaskExecution:
        """Commit the execution and its queue reference as one transition."""
        execution.status = TaskStatus.QUEUED
        execution.queued_at = datetime.now(timezone.utc)
        execution.started_at = None
        execution.completed_at = None
        execution.duration_ms = None
        execution.session_id = None
        execution.result_summary = None
        execution.error = None
        db = await cls._db()
        async with db.transaction() as tx:
            await cls._update_execution_on(
                tx, execution, expected_generation=expected_generation,
                expected_status=expected_status, clear_owner=True,
            )
            updated = await tx.execute(
                """UPDATE task_execution_queue_refs SET status = 'queued', started_at = NULL
                   WHERE execution_id = ? AND status IN ('queued', 'running')""",
                (execution.id,),
            )
            if updated.rowcount == 0:
                ref = TaskExecutionQueueRef(execution_id=execution.id)
                await tx.execute(
                    """INSERT INTO task_execution_queue_refs
                       (id, execution_id, status, created_at, started_at) VALUES (?, ?, ?, ?, ?)""",
                    (ref.id, execution.id, ref.status.value, ref.created_at.isoformat(), None),
                )
        execution.owner_node_id = None
        execution.lease_expires_at = None
        return execution

    @classmethod
    async def finish_execution(
        cls,
        execution: TaskExecution,
        *,
        node_id: str,
        expected_generation: int,
    ) -> Optional[TaskExecution]:
        """Write a terminal result, but only if this node still owns the run.

        ``update_execution`` writes by id alone, which is right for the many
        callers that are simply editing a task. It is wrong for the write that
        ends a run: a node whose lease expired mid-execution -- a long GC pause,
        a network partition -- comes back and reports a result for work another
        node has since taken over and may already have finished. Without the
        ownership predicate that late write silently replaces the new owner's
        status and result.

        Returns ``None`` when the claim has moved on, so the caller can drop
        its result rather than overwrite a peer's.
        """

        execution.touch()
        db = await cls._db()
        cursor = await db.execute(
            """
            UPDATE task_executions SET
              status=?, delivery_status=?, completed_at=?, duration_ms=?,
              result_summary=?, error=?, retry=?, updated_at=?
            WHERE id=? AND owner_node_id=? AND owner_generation=?
              AND status='running' AND lease_expires_at::timestamptz > clock_timestamp()
            """,
            (
                execution.status.value,
                execution.delivery_status.value,
                _iso(execution.completed_at),
                execution.duration_ms,
                execution.result_summary,
                execution.error,
                _json(execution.retry),
                execution.updated_at.isoformat(),
                execution.id,
                node_id,
                expected_generation,
            ),
        )
        await db.commit()
        if cursor.rowcount == 0:
            log.warn(
                "task.store.finish_superseded",
                {
                    "execution_id": execution.id,
                    "node_id": node_id,
                    "generation": expected_generation,
                },
            )
            return None
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
    async def ensure_queued_execution_ref(
        cls, execution_id: str
    ) -> TaskExecutionQueueRef:
        active = await cls.get_queue_ref(execution_id)
        db = await cls._db()
        if active is None:
            ref = TaskExecutionQueueRef(execution_id=execution_id)
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

        await db.execute(
            """
            UPDATE task_execution_queue_refs
            SET status = 'queued', started_at = NULL
            WHERE id = ?
            """,
            (active.id,),
        )
        await db.commit()
        active.status = TaskStatus.QUEUED
        active.started_at = None
        return active

    @classmethod
    async def get_queue_ref(
        cls, execution_id: str
    ) -> Optional[TaskExecutionQueueRef]:
        db = await cls._db()
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
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE scheduler_id = ?
              AND status IN ('pending', 'queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (scheduler_id,),
        ) as cur:
            row = await cur.fetchone()
        return cls._row_to_execution(row) if row else None

    @classmethod
    async def list_orphaned_queued_executions(cls) -> List[TaskExecution]:
        db = await cls._db()
        async with db.execute(
            """
            SELECT e.*
            FROM task_executions e
            LEFT JOIN task_execution_queue_refs q
              ON q.execution_id = e.id
             AND q.status IN ('queued', 'running')
            WHERE e.status = 'queued'
              AND q.id IS NULL
            ORDER BY COALESCE(e.queued_at, e.created_at) ASC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [cls._row_to_execution(row) for row in rows]

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
            WHERE q.status = 'queued' AND e.status IN ('pending', 'queued') {excl}
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
        sql += " FOR UPDATE OF q, e SKIP LOCKED"
        # Selection and both state transitions share one borrowed connection.
        # Competing PostgreSQL workers lock only the claimed queue rows.
        async with db.transaction() as tx:
            async with tx.execute(sql, tuple(params)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            queue_ref = TaskExecutionQueueRef(
                id=row["queue_ref_id"],
                execution_id=row["queue_ref_execution_id"],
                status=TaskStatus(row["queue_ref_status"]),
                created_at=datetime.fromisoformat(row["queue_ref_created_at"]),
                started_at=datetime.fromisoformat(row["queue_ref_started_at"]) if row["queue_ref_started_at"] else None,
            )
            claimed_at = datetime.now(timezone.utc)
            claimed_iso = claimed_at.isoformat()
            cur = await tx.execute(
                """
                UPDATE task_execution_queue_refs
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (claimed_iso, queue_ref.id),
            )
            if cur.rowcount == 0:
                await tx.rollback()
                return None
            execution_update = await tx.execute(
                """
                UPDATE task_executions
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('pending', 'queued')
                """,
                (claimed_iso, claimed_iso, queue_ref.execution_id),
            )
            if execution_update.rowcount == 0:
                await tx.rollback()
                return None

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
        # Reflect the atomic update in the in-memory snapshot so callers see
        # the post-claim state (status=running, started_at=claimed_at).
        execution_data["status"] = TaskStatus.RUNNING.value
        if not execution_data.get("started_at"):
            execution_data["started_at"] = claimed_iso
        execution_data["updated_at"] = claimed_iso
        return cls._row_to_execution(execution_data), queue_ref

    @classmethod
    async def finish_queue_ref(
        cls, execution_id: str, *, expected_generation: Optional[int] = None
    ) -> None:
        db = await cls._db()
        guard = ""
        params: tuple = (execution_id,)
        if expected_generation is not None:
            guard = """ AND EXISTS (
                SELECT 1 FROM task_executions e
                WHERE e.id = task_execution_queue_refs.execution_id
                  AND e.owner_generation = ?
                  AND e.status IN ('completed', 'failed', 'cancelled')
            )"""
            params += (expected_generation,)
        await db.execute(
            f"""
            DELETE FROM task_execution_queue_refs
            WHERE execution_id = ? AND status IN ('queued', 'running'){guard}
            """,
            params,
        )
        await db.commit()

    @classmethod
    async def count_running(cls) -> int:
        db = await cls._db()
        async with db.execute(
            "SELECT COUNT(*) AS c FROM task_executions WHERE status = 'running'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def count_queued_refs(cls) -> int:
        db = await cls._db()
        async with db.execute(
            "SELECT COUNT(*) AS c FROM task_execution_queue_refs WHERE status = 'queued'"
        ) as cur:
            return (await cur.fetchone())["c"]

    @classmethod
    async def requeue_running_refs(cls, *, node_id: Optional[str] = None) -> int:
        """Put running queue references back in the queue after a restart.

        Restricted to what this node may take over. A shared database makes
        "running" ambiguous: a peer's healthy, in-progress execution looks
        exactly like one this node abandoned when it died. Requeueing on status
        alone had a restarting node drop its peer's live work back into the
        queue, so the same execution ran twice.
        """

        from .node import lease_seconds

        db = await cls._db()
        candidates = """
            SELECT e.id FROM task_executions e
            WHERE e.status = 'running'
              AND (e.lease_expires_at::timestamptz <= clock_timestamp()
                   OR (e.lease_expires_at IS NULL AND
                       COALESCE(e.started_at, e.queued_at, e.created_at, e.updated_at)::timestamptz
                           < clock_timestamp() - (? * interval '1 second')))
              AND EXISTS (SELECT 1 FROM task_execution_queue_refs q
                          WHERE q.execution_id = e.id AND q.status = 'running')
        """
        prefix = f"WITH reclaimable AS ({candidates} FOR UPDATE OF e) "
        candidates = "SELECT id FROM reclaimable"
        suffix = " RETURNING id"
        async with db.transaction() as tx:
            cur = await tx.execute(
                f"""{prefix}
            UPDATE task_execution_queue_refs
            SET status = 'queued', started_at = NULL
            WHERE status = 'running'
              AND execution_id IN ({candidates}){suffix}
                """,
                (lease_seconds(),),
            )
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
                f"""
                SELECT COUNT(*) AS c
                FROM task_schedulers
                WHERE status = 'active'
                  AND NOT (mode = 'once' AND {_run_immediately(db)})
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
        retry_after = _json_value(db, "retry", "retryAfter")
        retry_count = _json_value(db, "retry", "retryCount")
        max_retries = _json_value(db, "retry", "maxRetries")
        async with db.execute(
            f"""
            SELECT * FROM task_executions
            WHERE status = 'failed'
              AND {retry_after} IS NOT NULL
              AND {retry_after} <= ?
              AND CAST({retry_count} AS INTEGER) < CAST({max_retries} AS INTEGER)
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

    @classmethod
    async def list_stale_running_executions(
        cls, before: datetime
    ) -> List[TaskExecution]:
        db = await cls._db()
        async with db.execute(
            """
            SELECT * FROM task_executions
            WHERE status = 'running'
              AND COALESCE(started_at, queued_at, created_at, updated_at) < ?
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
            execution.owner_node_id,
            _iso(execution.lease_expires_at),
            execution.owner_generation,
            execution.created_at.isoformat(),
            execution.updated_at.isoformat(),
        )

    @classmethod
    def _row_to_scheduler(cls, row: Dict[str, Any]) -> TaskScheduler:
        data = dict(row)
        for col in ("source", "trigger", "context", "retry"):
            if data.get(col):
                data[col] = json.loads(data[col])
        data["skills"] = json.loads(data["skills"]) if data.get("skills") else []
        data["tags"] = json.loads(data["tags"]) if data.get("tags") else []
        data.setdefault("context", {})
        return TaskScheduler(**data)

    @classmethod
    def _row_to_execution(cls, row: Dict[str, Any]) -> TaskExecution:
        data = dict(row)
        for col in ("source", "execution_input_snapshot", "retry"):
            if data.get(col):
                data[col] = json.loads(data[col])
        data.setdefault("execution_input_snapshot", {})
        cls._normalize_execution_row(data)
        return TaskExecution(**data)

    @staticmethod
    def _normalize_execution_row(data: Dict[str, Any]) -> None:
        if data.get("status") != "paused":
            return
        data["status"] = TaskStatus.CANCELLED.value
        data["completed_at"] = (
            data.get("completed_at")
            or data.get("updated_at")
            or data.get("created_at")
        )
        if not data.get("error"):
            data["error"] = "Normalized from legacy paused state."

    @classmethod
    async def _normalize_legacy_paused_executions(cls) -> None:
        db = await cls._db()
        async with db.transaction() as tx:
            await cls._normalize_paused_in_transaction(tx)

    @classmethod
    async def _normalize_paused_in_transaction(cls, db) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            DELETE FROM task_execution_queue_refs
            WHERE status = 'paused'
               OR execution_id IN (
                   SELECT id FROM task_executions WHERE status = 'paused'
               )
            """
        )
        await db.execute(
            """
            UPDATE task_executions
            SET status = ?,
                completed_at = COALESCE(completed_at, updated_at, created_at, ?),
                error = COALESCE(NULLIF(error, ''), ?),
                updated_at = ?
            WHERE status = 'paused'
            """,
            (
                TaskStatus.CANCELLED.value,
                now,
                "Normalized from legacy paused state.",
                now,
            ),
        )
        await db.commit()

    @classmethod
    def _row_to_queue_ref(
        cls, row: Dict[str, Any]
    ) -> TaskExecutionQueueRef:
        return TaskExecutionQueueRef(**dict(row))


def _split_statements(script: str) -> list[str]:
    """Split a multi-statement DDL script into executable statements."""
    return [part.strip() for part in script.split(";") if part.strip()]


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
    owner_node_id            TEXT,
    lease_expires_at         TEXT,
    owner_generation         INTEGER NOT NULL DEFAULT 0,
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

def _tasks_ddl(dialect) -> list[str]:
    """Build PostgreSQL task tables with 64-bit generation/count columns."""
    script = _TASKS_DDL.replace(" INTEGER", f" {dialect.integer_type()}")
    return _split_statements(script)


_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_status ON task_schedulers(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_priority ON task_schedulers(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_schedulers_dedup ON task_schedulers(dedup_key)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_scheduler ON task_executions(scheduler_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_scheduler_active ON task_executions(scheduler_id) WHERE status IN ('pending', 'queued', 'running')",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions(status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_delivery ON task_executions(delivery_status)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_priority ON task_executions(priority)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_queued ON task_executions(queued_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_started ON task_executions(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_executions_completed ON task_executions(completed_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_queue_refs_execution ON task_execution_queue_refs(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_queue_refs_status_created ON task_execution_queue_refs(status, created_at)",
]


def _json_value(db, column: str, path: str) -> str:
    expression = f"({column}::jsonb)"
    return db.dialect.json_value(expression, path)


def _run_immediately(db) -> str:
    value = _json_value(db, "trigger", "runImmediately")
    return f"COALESCE(CAST({value} AS TEXT), 'false') IN ('true', '1')"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(by_alias=True)
    return json.dumps(obj)
