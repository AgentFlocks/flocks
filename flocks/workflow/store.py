"""SQLite persistence for workflow runtime data."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
from contextvars import ContextVar
from functools import wraps
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# All mutations use the dedicated writer; read cursors never share its snapshot.
# Context only lives for a store operation (no workflow/model work runs under it).
_writer_connection = ContextVar("workflow_store_writer", default=None)
_WRITE_BEGIN_ATTEMPTS = 3


def sqlite_error_fields(exc: BaseException) -> Dict[str, Any]:
    fields = {"error_type": type(exc).__name__}
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if isinstance(code, int):
        fields["sqlite_errorcode"] = code
    if isinstance(name, str) and name.startswith("SQLITE_"):
        fields["sqlite_errorname"] = name
    return fields


def is_sqlite_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and (
        (getattr(exc, "sqlite_errorcode", 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        or str(exc).lower() in {"database is locked", "database table is locked"}
    )


async def _rollback_owned(db: aiosqlite.Connection) -> None:
    # Keep ownership until rollback finishes, even if shutdown cancels us again.
    task = asyncio.create_task(db.rollback())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    await task


def _write_transaction(fn):
    @wraps(fn)
    async def wrapped(cls, *args, **kwargs):
        db = await cls._completion_db()
        lock = cls._completion_lock
        async with lock:
            token = _writer_connection.set(db)
            try:
                # Recovery is safe here: every writer uses this ownership lock.
                if db.in_transaction:
                    await _rollback_owned(db)
                for attempt in range(_WRITE_BEGIN_ATTEMPTS):
                    try:
                        await db.execute("BEGIN IMMEDIATE")
                        break
                    except sqlite3.OperationalError as exc:
                        from flocks.workflow import soc_diagnostics
                        soc_diagnostics.progress("storage.begin_failed", **sqlite_error_fields(exc))
                        await _rollback_owned(db)
                        if not is_sqlite_busy(exc) or attempt + 1 == _WRITE_BEGIN_ATTEMPTS:
                            raise
                        await asyncio.sleep(0.05 * (2 ** attempt))
                result = await fn(cls, *args, **kwargs)
                # Empty/no-op methods may return without their existing commit.
                if db.in_transaction:
                    await db.commit()
                return result
            except BaseException as exc:
                from flocks.workflow import soc_diagnostics
                soc_diagnostics.progress("storage.write_failed", **sqlite_error_fields(exc))
                try:
                    await _rollback_owned(db)
                except BaseException as rollback_exc:
                    log.error("workflow.store.rollback_failed", sqlite_error_fields(rollback_exc))
                # Do not replay a body/commit whose outcome may be uncertain.
                raise
            finally:
                _writer_connection.reset(token)
    return wrapped


class WorkflowStore:
    """Workflow-domain store backed by ``workflow.db`` tables."""

    _initialized = False
    _init_lock: Optional[asyncio.Lock] = None
    _init_lock_pid: Optional[int] = None
    _conn: Optional[aiosqlite.Connection] = None
    _completion_conn: Optional[aiosqlite.Connection] = None
    _init_pid: Optional[int] = None
    _db_path: Optional[Path] = None
    _completion_lock: Optional[asyncio.Lock] = None

    @classmethod
    def get_db_path(cls) -> Path:
        return Storage.get_workflow_db_path()

    @classmethod
    async def init(cls) -> None:
        # Concurrent first dispatches must share one writer and ownership lock.
        pid = os.getpid()
        if cls._init_lock is None or cls._init_lock_pid != pid:
            cls._init_lock = asyncio.Lock()
            cls._init_lock_pid = pid
        async with cls._init_lock:
            await cls._initialize()

    @classmethod
    async def _initialize(cls) -> None:
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
            await cls._ensure_step_duration_column(cls._conn)
            for stmt in _INDEX_STMTS:
                await cls._conn.execute(stmt)
            await cls._conn.commit()
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
            await cls._migrate_legacy_kv()

        try:
            await _open_and_migrate()
            log.info("workflow.store.initialized")
        except Exception as exc:
            if cls._conn:
                await cls._conn.close()
            if cls._completion_conn:
                await cls._completion_conn.close()
            cls._conn = None
            cls._completion_conn = None
            cls._initialized = False
            cls._init_pid = None
            cls._db_path = None
            if Storage._is_db_corruption_error(exc):
                await Storage.recover_corrupt_db(
                    db_path,
                    action="workflow.store.init",
                    exc=exc,
                    reinitialize=_open_and_migrate,
                )
                log.info("workflow.store.initialized")
                return
            raise

    @staticmethod
    async def _ensure_step_duration_column(db: aiosqlite.Connection) -> None:
        # Serialize the check and ALTER across processes. Existing rows stay
        # NULL: initialization must never scan/backfill large step payloads.
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("PRAGMA table_info(workflow_execution_steps)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            if "duration_ms" not in columns:
                await db.execute("ALTER TABLE workflow_execution_steps ADD COLUMN duration_ms REAL")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    @classmethod
    async def close(cls) -> None:
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
        cls._init_lock = None
        cls._init_lock_pid = None

    @classmethod
    async def _db(cls) -> aiosqlite.Connection:
        writer = _writer_connection.get()
        if writer is not None:
            return writer
        if cls._initialized and cls._init_pid is not None and cls._init_pid != os.getpid():
            await cls.init()
        if not cls._conn or not cls._initialized:
            await cls.init()
        return cls._conn  # type: ignore[return-value]

    @classmethod
    async def raw_db(cls) -> aiosqlite.Connection:
        return await cls._db()

    @classmethod
    async def _completion_db(cls) -> aiosqlite.Connection:
        if cls._initialized and cls._init_pid is not None and cls._init_pid != os.getpid():
            await cls.init()
        if not cls._completion_conn or not cls._initialized:
            await cls.init()
        return cls._completion_conn  # type: ignore[return-value]

    @classmethod
    async def raw_completion_db(cls) -> aiosqlite.Connection:
        """Return the completion connection for transaction-level tests."""
        return await cls._completion_db()

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
    @_write_transaction
    async def upsert_execution(cls, exec_data: Dict[str, Any]) -> None:
        db = await cls._db()
        _, _, row = cls._execution_row(exec_data)
        await db.execute(_EXECUTION_UPSERT_SQL, row)
        await db.commit()

    @classmethod
    async def get_execution(cls, exec_id: str) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT payload FROM workflow_executions WHERE id = ?",
            (exec_id,),
        ) as cur:
            row = await cur.fetchone()
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
    @_write_transaction
    async def delete_execution(cls, exec_id: str) -> bool:
        db = await cls._db()
        await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        cur = await db.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    @_write_transaction
    async def delete_executions_for_workflow(cls, workflow_id: str) -> int:
        db = await cls._db()
        async with db.execute(
            "SELECT id FROM workflow_executions WHERE workflow_id = ?",
            (workflow_id,),
        ) as cur:
            exec_ids = [str(row["id"]) for row in await cur.fetchall()]
        for exec_id in exec_ids:
            await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        cur = await db.execute("DELETE FROM workflow_executions WHERE workflow_id = ?", (workflow_id,))
        await db.commit()
        return cur.rowcount

    @classmethod
    @_write_transaction
    async def trim_executions(cls, workflow_id: str, *, keep: int) -> List[str]:
        db = await cls._db()
        async with db.execute(
            """
            SELECT id FROM workflow_executions
            WHERE workflow_id = ?
            ORDER BY started_at DESC, rowid DESC
            LIMIT -1 OFFSET ?
            """,
            (workflow_id, max(int(keep), 0)),
        ) as cur:
            exec_ids = [str(row["id"]) for row in await cur.fetchall()]
        for exec_id in exec_ids:
            await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
            await db.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        await db.commit()
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
                cls._step_duration_ms(step_payload.get("duration_ms")),
            )
            for step_index, step_payload in steps
        ]

    @staticmethod
    def _step_duration_ms(value: Any) -> Optional[float]:
        if type(value) in (int, float) and 0 <= value <= 2**53 - 1 and math.isfinite(value):
            return float(value)
        return None

    @classmethod
    async def record_step(
        cls,
        exec_id: str,
        step_index: int,
        step_payload: Dict[str, Any],
    ) -> None:
        await cls.record_steps(exec_id, [(step_index, step_payload)])

    @classmethod
    @_write_transaction
    async def record_steps(
        cls,
        exec_id: str,
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> None:
        rows = cls._step_rows(exec_id, steps)
        if not rows:
            return
        db = await cls._db()
        await db.executemany(
            """
            INSERT OR REPLACE INTO workflow_execution_steps
            (exec_id, step_index, node_id, node_type, inputs, outputs, error, payload, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()

    @classmethod
    @_write_transaction
    async def complete_execution(
        cls,
        exec_data: Dict[str, Any],
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> None:
        """Atomically persist one final execution summary and its step batch."""
        db = await cls._db()
        _, _, execution_row = cls._execution_row(exec_data)
        step_rows = cls._step_rows(steps=steps, exec_id=str(exec_data["id"]))
        if step_rows:
            await db.executemany(
                """
                INSERT OR REPLACE INTO workflow_execution_steps
                (exec_id, step_index, node_id, node_type, inputs, outputs, error, payload, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                step_rows,
            )
        await db.execute(_EXECUTION_UPSERT_SQL, execution_row)
        await db.commit()

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
            row = await cur.fetchone()
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
    @_write_transaction
    async def clear_steps(cls, exec_id: str) -> int:
        db = await cls._db()
        cur = await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        await db.commit()
        return cur.rowcount

    @classmethod
    async def get_stats(cls, workflow_id: str) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        async with db.execute(
            "SELECT * FROM workflow_stats WHERE workflow_id = ?",
            (workflow_id,),
        ) as cur:
            row = await cur.fetchone()
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
    @_write_transaction
    async def put_stats(cls, workflow_id: str, stats: Dict[str, Any]) -> None:
        db = await cls._db()
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
        await db.commit()

    @classmethod
    @_write_transaction
    async def delete_stats(cls, workflow_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute("DELETE FROM workflow_stats WHERE workflow_id = ?", (workflow_id,))
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    @_write_transaction
    async def increment_stats(cls, workflow_id: str, *, success: bool, duration: float) -> None:
        db = await cls._db()
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
        await db.commit()

    @classmethod
    @_write_transaction
    async def put_config(
        cls,
        workflow_id: str,
        config: Dict[str, Any],
        *,
        kind: Optional[str] = None,
    ) -> None:
        db = await cls._db()
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
        await db.commit()

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
            row = await cur.fetchone()
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
    @_write_transaction
    async def delete_config(cls, workflow_id: str, *, kind: Optional[str] = None) -> int:
        db = await cls._db()
        if kind:
            cur = await db.execute(
                "DELETE FROM workflow_configs WHERE workflow_id = ? AND kind = ?",
                (workflow_id, kind),
            )
        else:
            cur = await db.execute("DELETE FROM workflow_configs WHERE workflow_id = ?", (workflow_id,))
        await db.commit()
        return cur.rowcount

    @classmethod
    @_write_transaction
    async def kv_put(cls, key: str, value: Any, value_type: str = _JSON_TYPE) -> None:
        db = await cls._db()
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
        await db.commit()

    @classmethod
    async def kv_get(cls, key: str) -> Optional[Any]:
        db = await cls._db()
        async with db.execute("SELECT value FROM workflow_kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return cls._json_loads(row["value"], None)

    @classmethod
    @_write_transaction
    async def kv_remove(cls, key: str) -> bool:
        db = await cls._db()
        cur = await db.execute("DELETE FROM workflow_kv WHERE key = ?", (key,))
        await db.commit()
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
    @_write_transaction
    async def kv_clear(cls, prefix: str) -> int:
        db = await cls._db()
        cur = await db.execute(
            "DELETE FROM workflow_kv WHERE key LIKE ? ESCAPE '\\'",
            (Storage._like_prefix_pattern(prefix),),
        )
        await db.commit()
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
    duration_ms REAL,
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
