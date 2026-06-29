"""SQLite persistence for workflow runtime data."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


class WorkflowStore:
    """Workflow-domain store backed by ``workflow.db`` tables."""

    _initialized = False
    _conn: Optional[aiosqlite.Connection] = None
    _init_pid: Optional[int] = None
    _db_path: Optional[Path] = None

    @classmethod
    def get_db_path(cls) -> Path:
        return Storage.get_workflow_db_path()

    @classmethod
    async def init(cls) -> None:
        current_pid = os.getpid()
        db_path = cls.get_db_path()
        if cls._initialized and cls._init_pid == current_pid and cls._db_path == db_path:
            return
        if cls._initialized and (
            (cls._init_pid is not None and cls._init_pid != current_pid)
            or (cls._db_path is not None and cls._db_path != db_path)
        ):
            log.warn(
                "workflow.store.fork_detected",
                {
                    "parent_pid": cls._init_pid,
                    "child_pid": current_pid,
                    "old_db_path": str(cls._db_path) if cls._db_path else None,
                    "new_db_path": str(db_path),
                },
            )
            if cls._conn:
                await cls._conn.close()
            cls._conn = None
            cls._initialized = False
            cls._init_pid = None

        await Storage._ensure_init()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cls._conn = await aiosqlite.connect(
                db_path,
                timeout=Storage._sqlite_timeout_s,
            )
            cls._conn.row_factory = aiosqlite.Row
            await Storage.configure_connection(cls._conn)
            await cls._conn.executescript(_WORKFLOW_DDL)
            for stmt in _INDEX_STMTS:
                await cls._conn.execute(stmt)
            await cls._conn.commit()
            cls._initialized = True
            cls._init_pid = current_pid
            cls._db_path = db_path
            await cls._migrate_legacy_kv()
            log.info("workflow.store.initialized")
        except Exception:
            if cls._conn:
                await cls._conn.close()
            cls._conn = None
            cls._initialized = False
            cls._init_pid = None
            cls._db_path = None
            raise

    @classmethod
    async def close(cls) -> None:
        if cls._conn:
            await cls._conn.close()
        cls._conn = None
        cls._initialized = False
        cls._init_pid = None
        cls._db_path = None

    @classmethod
    async def _db(cls) -> aiosqlite.Connection:
        if cls._initialized and cls._init_pid is not None and cls._init_pid != os.getpid():
            await cls.init()
        if not cls._conn or not cls._initialized:
            await cls.init()
        return cls._conn  # type: ignore[return-value]

    @classmethod
    async def raw_db(cls) -> aiosqlite.Connection:
        return await cls._db()

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
    async def upsert_execution(cls, exec_data: Dict[str, Any]) -> None:
        db = await cls._db()
        payload = dict(exec_data)
        exec_id = str(payload.get("id") or "")
        workflow_id = str(payload.get("workflowId") or payload.get("workflow_id") or "")
        if not exec_id or not workflow_id:
            raise ValueError("workflow execution requires id and workflowId")
        await db.execute(
            """
            INSERT OR REPLACE INTO workflow_executions
            (id, workflow_id, status, current_phase, current_node_id, current_node_type,
             current_step_index, step_count, input_params, output_results, error_message,
             trigger_id, trigger_type, started_at, finished_at, duration, updated_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
            ORDER BY started_at DESC
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
    async def delete_execution(cls, exec_id: str) -> bool:
        db = await cls._db()
        await db.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
        cur = await db.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        await db.commit()
        return cur.rowcount > 0

    @classmethod
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
    async def trim_executions(cls, workflow_id: str, *, keep: int) -> List[str]:
        db = await cls._db()
        async with db.execute(
            """
            SELECT id FROM workflow_executions
            WHERE workflow_id = ?
            ORDER BY started_at DESC
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
    async def record_step(
        cls,
        exec_id: str,
        step_index: int,
        step_payload: Dict[str, Any],
    ) -> None:
        db = await cls._db()
        await db.execute(
            """
            INSERT OR REPLACE INTO workflow_execution_steps
            (exec_id, step_index, node_id, node_type, inputs, outputs, error, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                exec_id,
                int(step_index),
                step_payload.get("node_id"),
                step_payload.get("node_type") or step_payload.get("type"),
                cls._json_dumps(step_payload.get("inputs") or {}),
                cls._json_dumps(step_payload.get("outputs") or {}),
                step_payload.get("error"),
                cls._json_dumps(step_payload),
            ),
        )
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
    async def delete_stats(cls, workflow_id: str) -> bool:
        db = await cls._db()
        cur = await db.execute("DELETE FROM workflow_stats WHERE workflow_id = ?", (workflow_id,))
        await db.commit()
        return cur.rowcount > 0

    @classmethod
    async def increment_stats(cls, workflow_id: str, *, success: bool, duration: float) -> None:
        current = await cls.get_stats(workflow_id) or {}
        call_count = int(current.get("callCount") or 0) + 1
        success_count = int(current.get("successCount") or 0) + (1 if success else 0)
        error_count = int(current.get("errorCount") or 0) + (0 if success else 1)
        total_runtime = float(current.get("totalRuntime") or 0.0) + float(duration)
        await cls.put_stats(
            workflow_id,
            {
                "callCount": call_count,
                "successCount": success_count,
                "errorCount": error_count,
                "totalRuntime": total_runtime,
                "avgRuntime": total_runtime / call_count if call_count else 0.0,
                "thumbsUp": int(current.get("thumbsUp") or 0),
                "thumbsDown": int(current.get("thumbsDown") or 0),
            },
        )

    @classmethod
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
