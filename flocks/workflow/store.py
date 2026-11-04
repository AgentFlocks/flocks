"""SQLite persistence for workflow runtime data."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

import aiosqlite

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="workflow.store")

_MIGRATION_MARKER_KEY = "workflow_store.migration.tables.v1"
_JSON_TYPE = "json"
_WORKFLOW_KV_PREFIXES = (
    "workflow_registry/",
    "workflow_release/",
    "workflow_runtime/",
    "workflow_local_pid/",
    "workflow_api_service/",
)
_WORKFLOW_TABLE_PREFIXES = (
    "workflow_execution/",
    "workflow_execution_index/",
    "workflow_execution_step/",
    "workflow/",
    "workflow_integration_config/",
    "workflow_kafka_config/",
    "workflow_poller_config/",
    "workflow_syslog_config/",
)
_WORKFLOW_PREFIXES = _WORKFLOW_KV_PREFIXES + _WORKFLOW_TABLE_PREFIXES
_EXECUTION_UPSERT_SQL = """
    INSERT OR REPLACE INTO workflow_executions
    (id, workflow_id, status, current_phase, current_node_id, current_node_type,
     current_step_index, step_count, input_params, output_results, error_message,
     trigger_id, trigger_type, started_at, finished_at, duration, updated_at, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
#: A running summary never replaces the record of a run that already ended:
#: progress that a worker thread reports after its run was recorded as
#: cancelled (ownership.abandon_execution) must not bring it back to running.
_EXECUTION_RUNNING_UPSERT_SQL = """
    INSERT INTO workflow_executions
    (id, workflow_id, status, current_phase, current_node_id, current_node_type,
     current_step_index, step_count, input_params, output_results, error_message,
     trigger_id, trigger_type, started_at, finished_at, duration, updated_at, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        workflow_id = excluded.workflow_id, status = excluded.status,
        current_phase = excluded.current_phase, current_node_id = excluded.current_node_id,
        current_node_type = excluded.current_node_type,
        current_step_index = excluded.current_step_index, step_count = excluded.step_count,
        input_params = excluded.input_params, output_results = excluded.output_results,
        error_message = excluded.error_message, trigger_id = excluded.trigger_id,
        trigger_type = excluded.trigger_type, started_at = excluded.started_at,
        finished_at = excluded.finished_at, duration = excluded.duration,
        updated_at = excluded.updated_at, payload = excluded.payload
    WHERE workflow_executions.status = 'running'
"""
_ABANDONED_EXECUTION_MESSAGE = "Run stopped before it finished"


def _write_operation(operation: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Serialize and retry the complete mutation, never an individual SQL statement."""

    @wraps(operation)
    async def wrapped(cls, *args, **kwargs):
        return await cls._run_write_transaction(
            lambda: operation(cls, *args, **kwargs), action=operation.__name__
        )

    return wrapped


class WorkflowStore:
    """Workflow-domain store backed by ``workflow.db`` tables."""

    _initialized = False
    _conn: Optional[aiosqlite.Connection] = None
    _completion_conn: Optional[aiosqlite.Connection] = None
    _init_pid: Optional[int] = None
    _db_path: Optional[Path] = None
    _completion_lock: Optional[asyncio.Lock] = None
    _writer_open_lock: Optional[asyncio.Lock] = None
    _lifecycle_lock: Optional[asyncio.Lock] = None
    _lifecycle_loop: Optional[asyncio.AbstractEventLoop] = None
    _write_retry_attempts = 3
    _write_retry_base_delay_s = 0.05

    @classmethod
    def _lock(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if cls._lifecycle_lock is None or cls._lifecycle_loop is not loop:
            cls._lifecycle_lock = asyncio.Lock()
            cls._lifecycle_loop = loop
        return cls._lifecycle_lock

    @classmethod
    def get_db_path(cls) -> Path:
        return Storage.get_workflow_db_path()

    @classmethod
    async def init(cls) -> None:
        async with cls._lock():
            await cls._init_unlocked()

    @classmethod
    async def _init_unlocked(cls) -> None:
        current_pid = os.getpid()
        db_path = cls.get_db_path()
        if cls._initialized and cls._init_pid == current_pid and cls._db_path == db_path:
            return
        pid_changed = cls._initialized and cls._init_pid is not None and cls._init_pid != current_pid
        db_path_changed = cls._initialized and cls._db_path is not None and cls._db_path != db_path
        if pid_changed or db_path_changed:
            log.warn(
                "workflow.store.fork_detected",
                {
                    "parent_pid": cls._init_pid,
                    "child_pid": current_pid,
                    "old_db_path": str(cls._db_path) if cls._db_path else None,
                    "new_db_path": str(db_path),
                },
            )
            if not pid_changed:
                if cls._conn:
                    await cls._conn.close()
                if cls._completion_conn:
                    await cls._completion_conn.close()
            cls._conn = None
            cls._completion_conn = None
            cls._initialized = False
            cls._init_pid = None
            cls._completion_lock = None
            cls._writer_open_lock = None

        await Storage._ensure_init()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _open_and_migrate() -> None:
            cls._conn = await aiosqlite.connect(
                db_path,
                timeout=Storage._sqlite_timeout_s,
                isolation_level=None,
            )
            cls._conn.row_factory = aiosqlite.Row
            await Storage.configure_connection(cls._conn)
            await cls._conn.executescript(_WORKFLOW_DDL)
            for stmt in _INDEX_STMTS:
                await cls._conn.execute(stmt)
            await cls._conn.commit()
            # A SELECT may retain a WAL snapshot across awaits. Never promote
            # this connection to a writer: an intervening completion commit
            # would make that snapshot fail with SQLITE_BUSY_SNAPSHOT.
            await cls._conn.execute("PRAGMA query_only=ON")
            cls._completion_conn = await aiosqlite.connect(
                db_path,
                timeout=Storage._sqlite_timeout_s,
                isolation_level=None,
            )
            cls._completion_conn.row_factory = aiosqlite.Row
            await Storage.configure_connection(cls._completion_conn)
            cls._initialized = True
            cls._init_pid = current_pid
            cls._db_path = db_path
            cls._completion_lock = asyncio.Lock()
            cls._writer_open_lock = asyncio.Lock()
            await cls._migrate_legacy_kv()

        try:
            await _open_and_migrate()
            log.info("workflow.store.initialized")
        except BaseException as exc:
            if cls._conn:
                await cls._conn.close()
            if cls._completion_conn:
                await cls._completion_conn.close()
            cls._conn = None
            cls._completion_conn = None
            cls._initialized = False
            cls._init_pid = None
            cls._db_path = None
            cls._completion_lock = None
            cls._writer_open_lock = None
            if isinstance(exc, Exception) and Storage._is_db_corruption_error(exc):
                await Storage.recover_corrupt_db(
                    db_path,
                    action="workflow.store.init",
                    exc=exc,
                    reinitialize=_open_and_migrate,
                )
                log.info("workflow.store.initialized")
                return
            raise

    @classmethod
    async def close(cls) -> None:
        async with cls._lock():
            # Lock order: lifecycle -> active transaction -> writer reopen.
            # A transaction may reopen a discarded writer while it owns the
            # transaction gate, so close must not acquire the open gate first.
            write_lock = cls._completion_lock
            if write_lock is not None:
                await write_lock.acquire()
            open_lock = cls._writer_open_lock
            open_acquired = False
            try:
                if open_lock is not None:
                    await open_lock.acquire()
                    open_acquired = True
                async def finish_close() -> None:
                    connections = [conn for conn in (cls._conn, cls._completion_conn) if conn is not None]
                    results = await asyncio.gather(*(conn.close() for conn in connections), return_exceptions=True)
                    cls._conn = None
                    cls._completion_conn = None
                    cls._initialized = False
                    cls._init_pid = None
                    cls._db_path = None
                    cls._completion_lock = None
                    cls._writer_open_lock = None
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result

                cancelled = await cls._finish_cleanup(finish_close())
                if cancelled:
                    raise asyncio.CancelledError
            finally:
                if open_acquired:
                    open_lock.release()
                if write_lock is not None:
                    write_lock.release()

    @classmethod
    async def _db(cls) -> aiosqlite.Connection:
        if cls._initialized and cls._init_pid is not None and cls._init_pid != os.getpid():
            await cls.init()
        if not cls._conn or not cls._initialized:
            await cls.init()
        return cls._conn  # type: ignore[return-value]

    @classmethod
    async def raw_db(cls) -> aiosqlite.Connection:
        """Writable diagnostic escape hatch; callers must not manage concurrent transactions.

        Production mutations must use the store APIs and their transaction gate.
        Keep raw writes off the internal read connection, even for diagnostics.
        """
        return await cls._completion_db()

    @classmethod
    async def _completion_db(cls) -> aiosqlite.Connection:
        while True:
            await cls._db()
            if cls._completion_conn is not None:
                return cls._completion_conn
            # A close may replace this gate while a diagnostic caller waits.
            open_lock = cls._writer_open_lock
            assert open_lock is not None
            async with open_lock:
                if not cls._initialized or open_lock is not cls._writer_open_lock:
                    continue
                if cls._completion_conn is None:
                    conn = await aiosqlite.connect(
                        cls.get_db_path(), timeout=Storage._sqlite_timeout_s, isolation_level=None
                    )
                    try:
                        conn.row_factory = aiosqlite.Row
                        await Storage.configure_connection(conn)
                    except BaseException:
                        await conn.close()
                        raise
                    cls._completion_conn = conn
                return cls._completion_conn

    @classmethod
    async def raw_completion_db(cls) -> aiosqlite.Connection:
        """Return the completion connection for transaction-level tests."""
        return await cls._completion_db()

    @staticmethod
    async def _finish_cleanup(operation: Awaitable[Any]) -> bool:
        """Drain queued SQLite cleanup before releasing the gate, even on repeat cancellation."""
        task = asyncio.create_task(operation)
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        return cancelled

    @classmethod
    @asynccontextmanager
    async def _write_gate(cls):
        while True:
            await cls._db()
            lock = cls._completion_lock
            assert lock is not None
            await lock.acquire()
            if cls._initialized and lock is cls._completion_lock:
                break
            # close/reinitialize may run before this queued waiter. It must
            # join the new writer gate rather than write under a stale lock.
            lock.release()
        try:
            yield
        finally:
            lock.release()

    @classmethod
    async def _run_write_transaction(
        cls, operation: Callable[[], Awaitable[Any]], *, action: str
    ) -> Any:
        async with cls._write_gate():
            # Resolve again after acquiring the gate: a preceding transaction
            # may have discarded its connection after a rollback failure.
            db = await cls._completion_db()
            attempts = max(1, cls._write_retry_attempts)
            for attempt in range(1, attempts + 1):
                phase = "begin"
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    phase = "execute"
                    result = await operation()
                    phase = "commit"
                    await db.commit()
                    return result
                except BaseException as exc:
                    fields = {
                        "action": action, "phase": phase, "attempt": attempt,
                        "max_attempts": attempts, "error": str(exc),
                        "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None),
                        "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
                        "in_transaction": db.in_transaction,
                    }
                    try:
                        cancelled = await cls._finish_cleanup(db.rollback())
                    except BaseException as rollback_exc:
                        log.error("workflow.store.rollback_failed", {**fields, "error": str(rollback_exc)})
                        cls._completion_conn = None
                        await cls._finish_cleanup(db.close())
                        raise
                    if cancelled and not isinstance(exc, asyncio.CancelledError):
                        raise asyncio.CancelledError from exc
                    code = getattr(exc, "sqlite_errorcode", 0) or 0
                    busy = isinstance(exc, Exception) and (
                        (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                        or Storage._is_sqlite_busy_error(exc)
                    )
                    if not busy or attempt == attempts:
                        if isinstance(exc, Exception):
                            log.warning("workflow.store.transaction_failed", fields)
                        raise
                    log.warning("workflow.store.transaction_retry", fields)
                    await asyncio.sleep(cls._write_retry_base_delay_s * 2 ** (attempt - 1))

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _json_loads(value: Optional[str], default: Any = None) -> Any:
        if value is None:
            return default
        try:
            return json.loads(value)
        except Exception:
            return default

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now(UTC).timestamp() * 1000)

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @classmethod
    def _legacy_table_exists(cls, conn: sqlite3.Connection) -> bool:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = 'storage'").fetchone()
        return row is not None

    @classmethod
    def _legacy_rows_from_db(cls, db_path: Path) -> list[sqlite3.Row]:
        if not db_path.exists():
            return []
        conn = Storage.connect_sync(db_path)
        try:
            if not cls._legacy_table_exists(conn):
                return []
            clauses = " OR ".join("key LIKE ?" for _ in _WORKFLOW_PREFIXES)
            params = tuple(f"{prefix}%" for prefix in _WORKFLOW_PREFIXES)
            return conn.execute(
                f"""
                SELECT key, value, type, created_at, updated_at
                FROM storage
                WHERE {clauses}
                ORDER BY key
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

    @classmethod
    async def _migrate_legacy_kv(cls) -> None:
        if await cls.kv_get(_MIGRATION_MARKER_KEY) is not None:
            return
        rows_by_key: dict[str, sqlite3.Row] = {}
        for db_path in (Storage.get_db_path(), cls.get_db_path()):
            for row in await asyncio.to_thread(cls._legacy_rows_from_db, db_path):
                rows_by_key[str(row["key"])] = row

        counts = {
            "executions": 0,
            "steps": 0,
            "stats": 0,
            "configs": 0,
            "kv": 0,
            "skipped": 0,
        }
        for key, row in rows_by_key.items():
            value = cls._json_loads(str(row["value"]), None)
            if value is None:
                counts["skipped"] += 1
                continue
            if key.startswith("workflow_execution_step/") and isinstance(value, dict):
                parts = key.split("/")
                if len(parts) >= 3:
                    try:
                        await cls.record_step(parts[1], int(parts[2]), value)
                        counts["steps"] += 1
                    except Exception:
                        counts["skipped"] += 1
                continue
            if key.startswith("workflow_execution/") and isinstance(value, dict):
                await cls.upsert_execution(value)
                counts["executions"] += 1
                continue
            if key.startswith("workflow/") and key.endswith("/stats") and isinstance(value, dict):
                workflow_id = key[len("workflow/") : -len("/stats")]
                await cls.put_stats(workflow_id, value)
                counts["stats"] += 1
                continue
            if key.startswith("workflow_integration_config/") and isinstance(value, dict):
                workflow_id = key[len("workflow_integration_config/") :]
                await cls.put_config(workflow_id, value)
                counts["configs"] += 1
                continue
            if key.startswith(_WORKFLOW_KV_PREFIXES):
                await cls.kv_put(key, value)
                counts["kv"] += 1
                continue
            if key.startswith(
                (
                    "workflow_kafka_config/",
                    "workflow_poller_config/",
                    "workflow_syslog_config/",
                )
            ) and isinstance(value, dict):
                workflow_id = key.rsplit("/", 1)[-1]
                await cls.put_config(workflow_id, value, kind=key.split("/", 1)[0])
                counts["configs"] += 1

        await cls.kv_put(
            _MIGRATION_MARKER_KEY,
            {
                "version": 1,
                "migrated_at": cls._now_iso(),
                "source_db": str(Storage.get_db_path()),
                "workflow_db": str(cls.get_db_path()),
                **counts,
            },
        )
        log.info("workflow.store.legacy_kv_migrated", counts)

    @classmethod
    def _execution_row(
        cls,
        exec_data: Dict[str, Any],
    ) -> Tuple[str, str, Tuple[Any, ...]]:
        payload = dict(exec_data)
        exec_id = str(payload.get("id") or "")
        workflow_id = str(payload.get("workflowId") or payload.get("workflow_id") or "")
        if not exec_id or not workflow_id:
            raise ValueError("workflow execution requires id and workflowId")
        return (
            exec_id,
            workflow_id,
            (
                exec_id,
                workflow_id,
                str(payload.get("status") or "running"),
                payload.get("currentPhase"),
                payload.get("currentNodeId"),
                payload.get("currentNodeType"),
                cls._as_int(payload.get("currentStepIndex")),
                cls._as_int(payload.get("stepCount")) or 0,
                cls._json_dumps(payload.get("inputParams") or {}),
                cls._json_dumps(payload.get("outputResults") or {}),
                payload.get("errorMessage"),
                payload.get("triggerId"),
                payload.get("triggerType"),
                cls._as_int(payload.get("startedAt")) or cls._now_ms(),
                cls._as_int(payload.get("finishedAt")),
                cls._as_float(payload.get("duration")),
                cls._as_int(payload.get("updatedAt")) or cls._now_ms(),
                cls._json_dumps(payload),
            ),
        )

    @classmethod
    @_write_operation
    async def upsert_execution(cls, exec_data: Dict[str, Any]) -> None:
        db = await cls._completion_db()
        _, _, row = cls._execution_row(exec_data)
        running = row[2] == "running"
        await db.execute(_EXECUTION_RUNNING_UPSERT_SQL if running else _EXECUTION_UPSERT_SQL, row)

    @classmethod
    async def recover_expired_executions(cls, message: str, *, limit: int = 500) -> int:
        """Finalize executions left running by a process that is gone.

        An execution runs only in the process that started it, so one still
        recorded as running that is not live here (ownership.running_here) can
        never finish.  Returns how many were finalized (at most *limit* per call).
        """
        from flocks.workflow.ownership import running_here

        db = await cls._db()
        async with db.execute(
            "SELECT payload FROM workflow_executions WHERE status = 'running' ORDER BY started_at ASC",
        ) as cur:
            rows = await cur.fetchall()
        recovered = 0
        for row in rows:
            if recovered >= max(1, limit):
                break
            exec_data = cls._json_loads(row["payload"], None)
            if not isinstance(exec_data, dict) or running_here(str(exec_data.get("id") or "")):
                continue
            now = cls._now_ms()
            exec_data.update(
                status="error", currentPhase="error", errorMessage=message, finishedAt=now, updatedAt=now,
            )
            await cls.upsert_execution(exec_data)
            recovered += 1
        return recovered

    @classmethod
    async def request_execution_cancel(cls, exec_id: str) -> bool:
        """Ask the run to stop at its next node; False when it is not live in this process."""
        from flocks.workflow.ownership import request_cancel

        return request_cancel(exec_id)

    @classmethod
    @_write_operation
    async def end_abandoned_execution(cls, exec_id: str) -> bool:
        """Record a run that nobody will finish as cancelled, unless it already ended."""
        db = await cls._completion_db()
        async with db.execute(
            "SELECT payload FROM workflow_executions WHERE id = ? AND status = 'running'",
            (exec_id,),
        ) as cur:
            row = await cur.fetchone()
        exec_data = cls._json_loads(row["payload"], None) if row else None
        if not isinstance(exec_data, dict):
            return False
        now = cls._now_ms()
        started_at = cls._as_int(exec_data.get("startedAt"))
        exec_data.update(
            status="cancelled",
            currentPhase="cancelled",
            errorMessage=exec_data.get("errorMessage") or _ABANDONED_EXECUTION_MESSAGE,
            finishedAt=now,
            updatedAt=now,
        )
        if started_at is not None and not isinstance(exec_data.get("duration"), (int, float)):
            exec_data["duration"] = max(0.0, (now - started_at) / 1000.0)
        await db.execute(_EXECUTION_UPSERT_SQL, cls._execution_row(exec_data)[2])
        return True

    @classmethod
    async def get_execution(cls, exec_id: str) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT payload FROM workflow_executions WHERE id = ?",
            (exec_id,),
        ) as cur:
            rows = await cur.fetchall()
            row = rows[0] if rows else None
        if not row:
            return None
        value = cls._json_loads(row["payload"], None)
        return value if isinstance(value, dict) else None

    @classmethod
    async def list_executions(
        cls,
        workflow_id: str,
        *,
        limit: int = 50,
        trigger_id: Optional[str] = None,
        trigger_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        db = await cls._db()
        clauses = ["workflow_id = ?"]
        params: list[Any] = [workflow_id]
        if trigger_id:
            clauses.append("trigger_id = ?")
            params.append(trigger_id)
        if trigger_type:
            clauses.append("trigger_type = ?")
            params.append(trigger_type)
        params.append(max(int(limit), 0))
        async with db.execute(
            f"""
            SELECT payload FROM workflow_executions
            WHERE {" AND ".join(clauses)}
            ORDER BY started_at DESC, rowid DESC
            LIMIT ?
            """,
            tuple(params),
        ) as cur:
            rows = await cur.fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            value = cls._json_loads(row["payload"], None)
            if isinstance(value, dict):
                items.append(value)
        return items

    @classmethod
    @_write_operation
    async def delete_execution(cls, exec_id: str) -> bool:
        db = await cls._completion_db()
        await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        cur = await db.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        return cur.rowcount > 0

    @classmethod
    @_write_operation
    async def delete_executions_for_workflow(cls, workflow_id: str) -> int:
        db = await cls._completion_db()
        async with db.execute(
            "SELECT id FROM workflow_executions WHERE workflow_id = ?",
            (workflow_id,),
        ) as cur:
            exec_ids = [str(row["id"]) for row in await cur.fetchall()]
        for exec_id in exec_ids:
            await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        cur = await db.execute("DELETE FROM workflow_executions WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount

    @classmethod
    @_write_operation
    async def trim_executions(cls, workflow_id: str, *, keep: int) -> List[str]:
        """Delete finished executions past the newest *keep* finished ones.

        A run still going is neither removed nor counted: a long run would
        otherwise lose its record once enough shorter ones finished after it.
        """
        db = await cls._completion_db()
        async with db.execute(
            """
            SELECT id FROM workflow_executions
            WHERE workflow_id = ? AND status <> 'running'
            ORDER BY started_at DESC, rowid DESC
            LIMIT -1 OFFSET ?
            """,
            (workflow_id, max(int(keep), 0)),
        ) as cur:
            exec_ids = [str(row["id"]) for row in await cur.fetchall()]
        for exec_id in exec_ids:
            await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
            await db.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        return exec_ids

    @classmethod
    def _step_rows(
        cls,
        exec_id: str,
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> List[Tuple[Any, ...]]:
        return [
            (
                exec_id,
                int(step_index),
                step_payload.get("node_id"),
                step_payload.get("node_type") or step_payload.get("type"),
                cls._json_dumps(step_payload.get("inputs") or {}),
                cls._json_dumps(step_payload.get("outputs") or {}),
                step_payload.get("error"),
                cls._json_dumps(step_payload),
            )
            for step_index, step_payload in steps
        ]

    @classmethod
    async def record_step(
        cls,
        exec_id: str,
        step_index: int,
        step_payload: Dict[str, Any],
    ) -> None:
        await cls.record_steps(exec_id, [(step_index, step_payload)])

    @classmethod
    async def record_steps(
        cls,
        exec_id: str,
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> None:
        rows = cls._step_rows(exec_id, steps)
        if not rows:
            return

        async def write_steps() -> None:
            db = await cls._completion_db()
            await db.executemany(
                """
                INSERT OR REPLACE INTO workflow_execution_steps
                (exec_id, step_index, node_id, node_type, inputs, outputs, error, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

        await cls._run_write_transaction(write_steps, action="record_steps")

    @classmethod
    async def complete_execution(
        cls,
        exec_data: Dict[str, Any],
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> None:
        """Atomically persist one final execution summary and its step batch."""
        exec_id, _, execution_row = cls._execution_row(exec_data)
        step_rows = cls._step_rows(exec_id, steps)

        async def write_result() -> None:
            db = await cls._completion_db()
            if step_rows:
                await db.executemany(
                    """
                    INSERT OR REPLACE INTO workflow_execution_steps
                    (exec_id, step_index, node_id, node_type, inputs, outputs, error, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    step_rows,
                )
            await db.execute(_EXECUTION_UPSERT_SQL, execution_row)

        await cls._run_write_transaction(write_result, action="complete_execution")

    @classmethod
    async def list_steps(
        cls,
        exec_id: str,
        *,
        offset: int = 0,
        limit: int = 500,
    ) -> Tuple[List[Dict[str, Any]], int]:
        db = await cls._db()
        safe_offset = max(int(offset), 0)
        safe_limit = max(int(limit), 0)
        async with db.execute(
            "SELECT COUNT(*) AS total FROM workflow_execution_steps WHERE exec_id = ?",
            (exec_id,),
        ) as cur:
            rows = await cur.fetchall()
            row = rows[0] if rows else None
            total = int(row["total"]) if row else 0
        if safe_limit == 0:
            return [], total
        async with db.execute(
            """
            SELECT payload FROM workflow_execution_steps
            WHERE exec_id = ?
            ORDER BY step_index
            LIMIT ? OFFSET ?
            """,
            (exec_id, safe_limit, safe_offset),
        ) as cur:
            rows = await cur.fetchall()
        steps: List[Dict[str, Any]] = []
        for row in rows:
            value = cls._json_loads(row["payload"], None)
            if isinstance(value, dict):
                steps.append(value)
        return steps, total

    @classmethod
    @_write_operation
    async def clear_steps(cls, exec_id: str) -> int:
        db = await cls._completion_db()
        cur = await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        return cur.rowcount

    @classmethod
    async def get_stats(cls, workflow_id: str) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT * FROM workflow_stats WHERE workflow_id = ?",
            (workflow_id,),
        ) as cur:
            rows = await cur.fetchall()
            row = rows[0] if rows else None
        if not row:
            return None
        return {
            "callCount": int(row["call_count"] or 0),
            "successCount": int(row["success_count"] or 0),
            "errorCount": int(row["error_count"] or 0),
            "totalRuntime": float(row["total_runtime"] or 0.0),
            "avgRuntime": float(row["avg_runtime"] or 0.0),
            "thumbsUp": int(row["thumbs_up"] or 0),
            "thumbsDown": int(row["thumbs_down"] or 0),
        }

    @classmethod
    @_write_operation
    async def put_stats(cls, workflow_id: str, stats: Dict[str, Any]) -> None:
        db = await cls._completion_db()
        await db.execute(
            """
            INSERT OR REPLACE INTO workflow_stats
            (workflow_id, call_count, success_count, error_count, total_runtime,
             avg_runtime, thumbs_up, thumbs_down, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                int(stats.get("callCount") or stats.get("call_count") or 0),
                int(stats.get("successCount") or stats.get("success_count") or 0),
                int(stats.get("errorCount") or stats.get("error_count") or 0),
                float(stats.get("totalRuntime") or stats.get("total_runtime") or 0.0),
                float(stats.get("avgRuntime") or stats.get("avg_runtime") or 0.0),
                int(stats.get("thumbsUp") or stats.get("thumbs_up") or 0),
                int(stats.get("thumbsDown") or stats.get("thumbs_down") or 0),
                cls._now_ms(),
            ),
        )

    @classmethod
    @_write_operation
    async def delete_stats(cls, workflow_id: str) -> bool:
        db = await cls._completion_db()
        cur = await db.execute("DELETE FROM workflow_stats WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount > 0

    @classmethod
    @_write_operation
    async def increment_stats(cls, workflow_id: str, *, success: bool, duration: float) -> None:
        db = await cls._completion_db()
        runtime = float(duration)
        success_delta = 1 if success else 0
        error_delta = 0 if success else 1
        updated_at = cls._now_ms()
        await db.execute(
            """
            INSERT INTO workflow_stats (
                workflow_id,
                call_count,
                success_count,
                error_count,
                total_runtime,
                avg_runtime,
                thumbs_up,
                thumbs_down,
                updated_at
            )
            VALUES (?, 1, ?, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                call_count = workflow_stats.call_count + 1,
                success_count = workflow_stats.success_count + excluded.success_count,
                error_count = workflow_stats.error_count + excluded.error_count,
                total_runtime = workflow_stats.total_runtime + excluded.total_runtime,
                avg_runtime = (
                    workflow_stats.total_runtime + excluded.total_runtime
                ) / (workflow_stats.call_count + 1),
                updated_at = excluded.updated_at
            """,
            (
                workflow_id,
                success_delta,
                error_delta,
                runtime,
                runtime,
                updated_at,
            ),
        )

    @classmethod
    @_write_operation
    async def put_config(
        cls,
        workflow_id: str,
        config: Dict[str, Any],
        *,
        kind: Optional[str] = None,
    ) -> None:
        db = await cls._completion_db()
        config_kind = kind or str(config.get("kind") or "workflow.integration-config")
        version = cls._as_int(config.get("version"))
        await db.execute(
            """
            INSERT OR REPLACE INTO workflow_configs
            (workflow_id, kind, version, config, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (workflow_id, config_kind, version, cls._json_dumps(config), cls._now_ms()),
        )

    @classmethod
    async def get_config(
        cls,
        workflow_id: str,
        *,
        kind: str = "workflow.integration-config",
    ) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT config FROM workflow_configs WHERE workflow_id = ? AND kind = ?",
            (workflow_id, kind),
        ) as cur:
            rows = await cur.fetchall()
            row = rows[0] if rows else None
        if not row:
            return None
        value = cls._json_loads(row["config"], None)
        return value if isinstance(value, dict) else None

    @classmethod
    async def list_configs(cls, *, kind: str) -> List[Tuple[str, Dict[str, Any]]]:
        db = await cls._db()
        async with db.execute(
            "SELECT workflow_id, config FROM workflow_configs WHERE kind = ? ORDER BY workflow_id",
            (kind,),
        ) as cur:
            rows = await cur.fetchall()
        items: List[Tuple[str, Dict[str, Any]]] = []
        for row in rows:
            value = cls._json_loads(row["config"], None)
            if isinstance(value, dict):
                items.append((str(row["workflow_id"]), value))
        return items

    @classmethod
    @_write_operation
    async def delete_config(cls, workflow_id: str, *, kind: Optional[str] = None) -> int:
        db = await cls._completion_db()
        if kind:
            cur = await db.execute(
                "DELETE FROM workflow_configs WHERE workflow_id = ? AND kind = ?",
                (workflow_id, kind),
            )
        else:
            cur = await db.execute("DELETE FROM workflow_configs WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount

    @classmethod
    async def claim_webhook_delivery(
        cls, key: str, delivery_id: str, window_s: float, claim_token: str = "",
    ) -> Optional[str]:
        """Record a webhook delivery unless the same one is still within its window.

        Returns ``None`` -- go ahead and run -- or the first delivery's id when
        *key* is a replay.  Nothing is recorded here, so every delivery runs.
        *claim_token* identifies this claim to :meth:`release_webhook_delivery`.
        """
        return None

    @classmethod
    async def release_webhook_delivery(cls, key: str, claim_token: str) -> None:
        """Forget the delivery one claim recorded, so the same one may be sent again.

        Only that claim: a later claim of the same key carries another token and stays.
        """

    @classmethod
    @_write_operation
    async def kv_put(cls, key: str, value: Any, value_type: str = _JSON_TYPE) -> None:
        db = await cls._completion_db()
        now = cls._now_iso()
        await db.execute(
            """
            INSERT OR REPLACE INTO workflow_kv (key, value, type, created_at, updated_at)
            VALUES (?, ?, ?,
                COALESCE((SELECT created_at FROM workflow_kv WHERE key = ?), ?),
                ?)
            """,
            (key, cls._json_dumps(value), value_type, key, now, now),
        )

    @classmethod
    async def kv_get(cls, key: str) -> Optional[Any]:
        db = await cls._db()
        async with db.execute("SELECT value FROM workflow_kv WHERE key = ?", (key,)) as cur:
            rows = await cur.fetchall()
            row = rows[0] if rows else None
        if not row:
            return None
        return cls._json_loads(row["value"], None)

    @classmethod
    @_write_operation
    async def kv_remove(cls, key: str) -> bool:
        db = await cls._completion_db()
        cur = await db.execute("DELETE FROM workflow_kv WHERE key = ?", (key,))
        return cur.rowcount > 0

    @classmethod
    async def kv_list_keys(cls, prefix: str) -> List[str]:
        db = await cls._db()
        async with db.execute(
            "SELECT key FROM workflow_kv WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
            (Storage._like_prefix_pattern(prefix),),
        ) as cur:
            rows = await cur.fetchall()
        return [str(row["key"]) for row in rows]

    @classmethod
    async def kv_list(cls, prefix: str) -> List[str]:
        return await cls.kv_list_keys(prefix)

    @classmethod
    async def kv_entries(cls, prefix: str) -> List[Tuple[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT key, value FROM workflow_kv WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
            (Storage._like_prefix_pattern(prefix),),
        ) as cur:
            rows = await cur.fetchall()
        entries: List[Tuple[str, Any]] = []
        for row in rows:
            entries.append((str(row["key"]), cls._json_loads(row["value"], None)))
        return entries

    @classmethod
    @_write_operation
    async def kv_clear(cls, prefix: str) -> int:
        db = await cls._completion_db()
        cur = await db.execute(
            "DELETE FROM workflow_kv WHERE key LIKE ? ESCAPE '\\'",
            (Storage._like_prefix_pattern(prefix),),
        )
        return cur.rowcount


_WORKFLOW_DDL = """
CREATE TABLE IF NOT EXISTS workflow_executions (
    id                 TEXT PRIMARY KEY,
    workflow_id        TEXT NOT NULL,
    status             TEXT NOT NULL,
    current_phase      TEXT,
    current_node_id    TEXT,
    current_node_type  TEXT,
    current_step_index INTEGER,
    step_count         INTEGER NOT NULL DEFAULT 0,
    input_params       TEXT NOT NULL DEFAULT '{}',
    output_results     TEXT NOT NULL DEFAULT '{}',
    error_message      TEXT,
    trigger_id         TEXT,
    trigger_type       TEXT,
    started_at         INTEGER NOT NULL,
    finished_at        INTEGER,
    duration           REAL,
    updated_at         INTEGER,
    payload            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_execution_steps (
    exec_id    TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    node_id    TEXT,
    node_type  TEXT,
    inputs     TEXT NOT NULL DEFAULT '{}',
    outputs    TEXT NOT NULL DEFAULT '{}',
    error      TEXT,
    payload    TEXT NOT NULL,
    PRIMARY KEY (exec_id, step_index)
);

CREATE TABLE IF NOT EXISTS workflow_stats (
    workflow_id   TEXT PRIMARY KEY,
    call_count    INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count   INTEGER NOT NULL DEFAULT 0,
    total_runtime REAL NOT NULL DEFAULT 0,
    avg_runtime   REAL NOT NULL DEFAULT 0,
    thumbs_up     INTEGER NOT NULL DEFAULT 0,
    thumbs_down   INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER
);

CREATE TABLE IF NOT EXISTS workflow_configs (
    workflow_id TEXT NOT NULL,
    kind        TEXT NOT NULL,
    version     INTEGER,
    config      TEXT NOT NULL,
    updated_at  INTEGER,
    PRIMARY KEY (workflow_id, kind)
);

CREATE TABLE IF NOT EXISTS workflow_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    type       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_started ON workflow_executions(workflow_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_status ON workflow_executions(workflow_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_trigger ON workflow_executions(workflow_id, trigger_type, trigger_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_execution_steps_exec_step ON workflow_execution_steps(exec_id, step_index)",
]
