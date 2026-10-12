"""PostgreSQL persistence for workflow runtime data."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flocks.db.connect import open_transaction
from flocks.db.store import Transaction
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
    """Workflow-domain tables in the shared PostgreSQL database."""

    _initialized = False
    # The handle, not the driver connection: on PostgreSQL there is no
    # file to connect to and the connection comes from a shared pool.
    _handle: Optional[Transaction] = None
    _init_pid: Optional[int] = None
    _db_path: Optional[Path] = None
    _init_lock = asyncio.Lock()
    _init_lock_key = None

    @classmethod
    def _lock(cls) -> asyncio.Lock:
        """Lifecycle lock shared by init and close, one per process and loop."""
        key = (os.getpid(), asyncio.get_running_loop())
        if cls._init_lock_key != key:
            cls._init_lock, cls._init_lock_key = asyncio.Lock(), key
        return cls._init_lock

    @classmethod
    def get_db_path(cls) -> Path:
        return Storage.get_workflow_db_path()

    @classmethod
    async def init(cls) -> None:
        async with cls._lock():
            await cls._initialize()

    @classmethod
    async def _initialize(cls) -> None:
        current_pid = os.getpid()
        if cls._initialized and cls._init_pid == current_pid:
            return
        await Storage._ensure_init()
        handle = await open_transaction()
        cls._handle = handle
        try:
            async with handle.transaction() as tx:
                await tx.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("flocks.workflow.schema",))
                await tx.executescript(";\n".join(_workflow_ddl(tx.dialect) + _INDEX_STMTS))
            cls._initialized = True
            cls._init_pid = current_pid
        except BaseException:
            await handle.close()
            cls._handle = None
            cls._initialized = False
            cls._init_pid = None
            raise

    @classmethod
    async def close(cls) -> None:
        from flocks.workflow.ownership import close_execution_leases
        await close_execution_leases()
        async with cls._lock():
            if cls._handle:
                await cls._handle.close()
            cls._handle = None
            cls._initialized = False
            cls._init_pid = None
            cls._db_path = None

    @classmethod
    async def _db(cls):
        """Borrow PostgreSQL connections through the shared transaction handle."""
        if cls._initialized and cls._init_pid is not None and cls._init_pid != os.getpid():
            await cls.init()
        if not cls._handle or not cls._initialized:
            await cls.init()
        return cls._handle

    @classmethod
    async def raw_db(cls) -> Transaction:
        return await cls._db()

    @classmethod
    async def raw_completion_db(cls) -> Transaction:
        """The handle ``complete_execution`` writes through (for transaction-level tests).

        The portable handle already pins one connection per transaction block,
        so completion writes share it instead of a second driver connection.
        """
        return await cls._db()

    @staticmethod
    def _recency_order(dialect) -> str:
        """Deterministic order for equal start timestamps."""
        return "started_at DESC, updated_at DESC, id DESC"

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

    _EXECUTION_COLUMNS = (
        'id', 'workflow_id', 'status', 'current_phase', 'current_node_id', 'current_node_type',
        'current_step_index', 'step_count', 'input_params', 'output_results', 'error_message',
        'trigger_id', 'trigger_type', 'started_at', 'finished_at', 'duration', 'updated_at', 'payload',
    )
    _STEP_COLUMNS = ('exec_id', 'step_index', 'node_id', 'node_type', 'inputs', 'outputs', 'error', 'payload')

    @classmethod
    def _execution_upsert_sql(cls, dialect) -> str:
        return dialect.upsert(
            "workflow_executions",
            cls._EXECUTION_COLUMNS,
            key_columns=('id',),
            update_columns=cls._EXECUTION_COLUMNS[1:],
        )

    @classmethod
    def _step_upsert_sql(cls, dialect) -> str:
        return dialect.upsert(
            "workflow_execution_steps",
            cls._STEP_COLUMNS,
            key_columns=('exec_id', 'step_index'),
            update_columns=cls._STEP_COLUMNS[2:],
        )

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
    async def upsert_execution(cls, exec_data: Dict[str, Any]) -> bool:
        db = await cls._db()
        _, _, row = cls._execution_row(exec_data)
        async with db.transaction() as tx:
            if not await cls._check_execution_owner(tx, exec_data):
                return False
            await tx.execute(cls._execution_upsert_sql(tx.dialect), row)
        return True

    @classmethod
    async def start_execution(
        cls, exec_data: Dict[str, Any], *, owner_node_id: str,
        lease_seconds: int, scope_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically acquire a run and, optionally, its no-overlap scope."""
        from flocks.workflow.ownership import WorkflowAlreadyRunning, WorkflowOwnershipError

        db = await cls._db()
        execution_id = str(exec_data["id"])
        async with db.transaction() as tx:
            if scope_key is not None:
                await tx.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (scope_key,)
                )
                live = await tx.fetch_one(
                    "SELECT execution_id FROM workflow_execution_leases "
                    "WHERE scope_key = ? AND lease_expires_at > clock_timestamp() LIMIT 1",
                    (scope_key,),
                )
                if live is not None:
                    raise WorkflowAlreadyRunning(f"Workflow is already running: {exec_data['workflowId']}")
            claimed = await tx.fetch_one_mapping(
                """INSERT INTO workflow_execution_leases
                   (lease_key, execution_id, scope_key, owner_node_id, generation, lease_expires_at)
                   VALUES (?, ?, ?, ?, 1, clock_timestamp() + (? * interval '1 second'))
                   ON CONFLICT (lease_key) DO UPDATE SET
                     owner_node_id = EXCLUDED.owner_node_id,
                     generation = workflow_execution_leases.generation + 1,
                     cancel_requested = FALSE,
                     lease_expires_at = EXCLUDED.lease_expires_at
                   WHERE workflow_execution_leases.lease_expires_at <= clock_timestamp()
                   RETURNING generation""",
                (execution_id, execution_id, scope_key, owner_node_id, max(1, lease_seconds)),
            )
            if claimed is None:
                raise WorkflowOwnershipError(f"Execution is already owned: {execution_id}")
            current = await tx.fetch_one_mapping(
                "SELECT status FROM workflow_executions WHERE id = ? FOR UPDATE", (execution_id,)
            )
            if current is not None and current["status"] != "running":
                raise WorkflowOwnershipError(f"Execution is already terminal: {execution_id}")
            owned = dict(exec_data)
            owned.update(leaseKey=execution_id, ownerNodeId=owner_node_id, ownerGeneration=claimed["generation"])
            _, _, row = cls._execution_row(owned)
            await tx.execute(cls._execution_upsert_sql(tx.dialect), row)
        return owned

    @classmethod
    async def _check_execution_owner(cls, tx, exec_data: Dict[str, Any]) -> bool:
        """Lock the claim before the execution row, matching takeover/recovery."""
        from flocks.workflow.ownership import WorkflowOwnershipError

        lease = await tx.fetch_one_mapping(
            "SELECT owner_node_id, generation, lease_expires_at > clock_timestamp() AS live "
            "FROM workflow_execution_leases WHERE execution_id = ? FOR UPDATE", (str(exec_data["id"]),)
        )
        if lease is None:
            # Legacy records are writable by the explicit importer only; once
            # claimed, every ordinary progress/final write requires its token.
            if exec_data.get("ownerGeneration") is not None:
                raise WorkflowOwnershipError("Workflow execution lease is missing")
            return True
        if (lease["owner_node_id"] != exec_data.get("ownerNodeId")
            or lease["generation"] != exec_data.get("ownerGeneration")):
            raise WorkflowOwnershipError(f"Workflow ownership changed: {exec_data['id']}")
        current = await tx.fetch_one_mapping(
            "SELECT status FROM workflow_executions WHERE id = ? FOR UPDATE", (str(exec_data["id"]),)
        )
        if current is not None and current["status"] != "running":
            return False
        if not lease["live"]:
            raise WorkflowOwnershipError(f"Workflow lease expired: {exec_data['id']}")
        return True

    @classmethod
    async def renew_execution_lease(cls, exec_id: str, token: Dict[str, Any], lease_seconds: int) -> Optional[Dict[str, Any]]:
        db = await cls._db()
        return await db.fetch_one_mapping(
            """UPDATE workflow_execution_leases
               SET lease_expires_at = clock_timestamp() + (? * interval '1 second')
               WHERE execution_id = ? AND owner_node_id = ? AND generation = ?
                 AND lease_expires_at > clock_timestamp()
               RETURNING cancel_requested""",
            (max(1, lease_seconds), exec_id, token["ownerNodeId"], token["ownerGeneration"]),
        )

    @classmethod
    async def request_execution_cancel(cls, exec_id: str) -> bool:
        db = await cls._db()
        result = await db.execute(
            "UPDATE workflow_execution_leases SET cancel_requested = TRUE "
            "WHERE execution_id = ? AND lease_expires_at > clock_timestamp() "
            "AND EXISTS (SELECT 1 FROM workflow_executions e WHERE e.id = ? AND e.status = 'running')",
            (exec_id, exec_id),
        )
        return result.rowcount > 0

    @classmethod
    async def release_execution_lease(cls, exec_id: str, token: Dict[str, Any]) -> bool:
        db = await cls._db()
        result = await db.execute(
            "UPDATE workflow_execution_leases SET lease_expires_at = clock_timestamp() "
            "WHERE execution_id = ? AND owner_node_id = ? AND generation = ?",
            (exec_id, token["ownerNodeId"], token["ownerGeneration"]),
        )
        return result.rowcount > 0

    @classmethod
    async def recover_expired_executions(cls, message: str, *, limit: int = 500) -> int:
        """Recover a bounded batch; live leases and concurrent completion win."""
        db = await cls._db()
        async with db.transaction() as tx:
            result = await tx.execute(
                """WITH expired AS MATERIALIZED (
                     SELECT l.execution_id, l.generation
                     FROM workflow_execution_leases l
                     JOIN workflow_executions e ON e.id = l.execution_id
                     WHERE l.lease_expires_at <= clock_timestamp() AND e.status = 'running'
                     ORDER BY l.lease_expires_at LIMIT ? FOR UPDATE OF l SKIP LOCKED
                   )
                   UPDATE workflow_executions e SET status = 'error', current_phase = 'error',
                     error_message = ?, finished_at = ?, updated_at = ?,
                     payload = (e.payload::jsonb || jsonb_build_object(
                       'status', 'error', 'currentPhase', 'error', 'errorMessage', ?::text,
                       'finishedAt', ?::bigint, 'updatedAt', ?::bigint))::text
                   FROM expired x WHERE e.id = x.execution_id AND e.status = 'running'
                     AND (e.payload::jsonb ->> 'ownerGeneration')::bigint = x.generation
                   RETURNING e.id""",
                (max(1, min(limit, 5000)), message, cls._now_ms(), cls._now_ms(), message, cls._now_ms(), cls._now_ms()),
            )
        return result.rowcount

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
            ORDER BY {cls._recency_order(db.dialect)}
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
    async def list_running_executions(cls) -> List[Dict[str, Any]]:
        """Every execution still recorded as running, across all workflows.

        Read at startup: an in-flight run lives only in the process that
        started it, so anything still ``running`` here belongs to a process
        that is gone and can never finish.
        """
        db = await cls._db()
        async with db.execute(
            """
            SELECT payload FROM workflow_executions
            WHERE status = 'running'
            ORDER BY started_at ASC
            """
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
        async with db.transaction() as tx:
            await tx.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
            cur = await tx.execute("DELETE FROM workflow_executions WHERE id = ?", (exec_id,))
        return cur.rowcount > 0

    @classmethod
    async def delete_executions_for_workflow(cls, workflow_id: str) -> int:
        db = await cls._db()
        async with db.transaction() as tx:
            await tx.execute(
                "DELETE FROM workflow_execution_steps WHERE exec_id IN "
                "(SELECT id FROM workflow_executions WHERE workflow_id = ?)", (workflow_id,),
            )
            cur = await tx.execute("DELETE FROM workflow_executions WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount

    @classmethod
    async def trim_executions(cls, workflow_id: str, *, keep: int) -> List[str]:
        db = await cls._db()
        async with db.transaction() as tx:
            unlimited = "ALL"
            async with tx.execute(
                f"SELECT id FROM workflow_executions WHERE workflow_id = ? AND status != 'running' "
                f"ORDER BY {cls._recency_order(tx.dialect)} LIMIT {unlimited} OFFSET ?",
                (workflow_id, max(int(keep), 0)),
            ) as cur:
                exec_ids = [str(row["id"]) for row in await cur.fetchall()]
            # Bound parameter count without one database round trip per row.
            for start in range(0, len(exec_ids), 500):
                batch = exec_ids[start:start + 500]
                marks = ",".join("?" for _ in batch)
                await tx.execute(f"DELETE FROM workflow_execution_steps WHERE exec_id IN ({marks})", batch)
                await tx.execute(f"DELETE FROM workflow_executions WHERE id IN ({marks})", batch)
                await tx.execute(f"DELETE FROM workflow_execution_leases WHERE execution_id IN ({marks})", batch)
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
        *, token: Optional[Dict[str, Any]] = None,
    ) -> None:
        await cls.record_steps(exec_id, [(step_index, step_payload)], token=token)

    @classmethod
    async def record_steps(
        cls,
        exec_id: str,
        steps: Iterable[Tuple[int, Dict[str, Any]]],
        *, token: Optional[Dict[str, Any]] = None,
    ) -> None:
        rows = cls._step_rows(exec_id, steps)
        if not rows:
            return
        db = await cls._db()
        async with db.transaction() as tx:
            if not await cls._check_execution_owner(tx, {**(token or {}), "id": exec_id}):
                return
            await tx.execute_many(cls._step_upsert_sql(tx.dialect), rows)

    @classmethod
    async def complete_execution(
        cls,
        exec_data: Dict[str, Any],
        steps: Iterable[Tuple[int, Dict[str, Any]]],
    ) -> bool:
        """Atomically persist one final execution summary and its step batch.
        The execution claim is fenced in the same transaction as the writes.
        """
        db = await cls._db()
        exec_id, workflow_id, execution_row = cls._execution_row(exec_data)
        step_rows = cls._step_rows(exec_id, steps)
        async with db.transaction() as tx:
            if not await cls._check_execution_owner(tx, exec_data):
                return False
            if step_rows:
                await tx.execute_many(cls._step_upsert_sql(tx.dialect), step_rows)
            await tx.execute(cls._execution_upsert_sql(tx.dialect), execution_row)
            await cls._increment_stats_on(
                tx, workflow_id, success=exec_data.get("status") == "success",
                duration=float(exec_data.get("duration") or 0.0),
            )
            if exec_data.get("ownerGeneration") is not None:
                await tx.execute(
                    "UPDATE workflow_execution_leases SET lease_expires_at = clock_timestamp() "
                    "WHERE execution_id = ? AND owner_node_id = ? AND generation = ?",
                    (exec_id, exec_data["ownerNodeId"], exec_data["ownerGeneration"]),
                )
        return True

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
        async with db.transaction() as tx:
            cur = await tx.execute("DELETE FROM workflow_execution_steps WHERE exec_id = ?", (exec_id,))
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
        async with db.transaction() as tx:
            await tx.execute(
                tx.dialect.upsert(
                    "workflow_stats",
                    ('workflow_id', 'call_count', 'success_count', 'error_count', 'total_runtime', 'avg_runtime', 'thumbs_up', 'thumbs_down', 'updated_at'),
                    key_columns=('workflow_id',),
                    update_columns=('call_count', 'success_count', 'error_count', 'total_runtime', 'avg_runtime', 'thumbs_up', 'thumbs_down', 'updated_at'),
                ),
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
    async def delete_stats(cls, workflow_id: str) -> bool:
        db = await cls._db()
        async with db.transaction() as tx:
            cur = await tx.execute("DELETE FROM workflow_stats WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount > 0

    @classmethod
    async def increment_stats(cls, workflow_id: str, *, success: bool, duration: float) -> None:
        db = await cls._db()
        async with db.transaction() as tx:
            await cls._increment_stats_on(tx, workflow_id, success=success, duration=duration)

    @classmethod
    async def _increment_stats_on(cls, tx, workflow_id: str, *, success: bool, duration: float) -> None:
        runtime = float(duration)
        success_delta = 1 if success else 0
        error_delta = 0 if success else 1
        updated_at = cls._now_ms()
        await tx.execute(
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
        async with db.transaction() as tx:
            await tx.execute(
                tx.dialect.upsert(
                    "workflow_configs",
                    ('workflow_id', 'kind', 'version', 'config', 'updated_at'),
                    key_columns=('workflow_id', 'kind'),
                    update_columns=('version', 'config', 'updated_at'),
                ),
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
        async with db.transaction() as tx:
            if kind:
                cur = await tx.execute(
                    "DELETE FROM workflow_configs WHERE workflow_id = ? AND kind = ?",
                    (workflow_id, kind),
                )
            else:
                cur = await tx.execute("DELETE FROM workflow_configs WHERE workflow_id = ?", (workflow_id,))
        return cur.rowcount

    @classmethod
    async def kv_put(cls, key: str, value: Any, value_type: str = _JSON_TYPE) -> None:
        db = await cls._db()
        now = cls._now_iso()
        # On conflict, created_at is simply left out of the update, which is
        # what the COALESCE subquery here used to achieve.
        async with db.transaction() as tx:
            await tx.execute(
                tx.dialect.upsert(
                    "workflow_kv",
                    ("key", "value", "type", "created_at", "updated_at"),
                    key_columns=("key",),
                    update_columns=("value", "type", "updated_at"),
                ),
                (key, cls._json_dumps(value), value_type, now, now),
            )

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
        async with db.transaction() as tx:
            cur = await tx.execute("DELETE FROM workflow_kv WHERE key = ?", (key,))
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
        async with db.transaction() as tx:
            cur = await tx.execute(
                "DELETE FROM workflow_kv WHERE key LIKE ? ESCAPE '\\'",
                (Storage._like_prefix_pattern(prefix),),
            )
        return cur.rowcount


def _workflow_ddl(dialect) -> list[str]:
    """The workflow tables, using the shared PostgreSQL column types."""
    text, num, real = dialect.text_type(), dialect.integer_type(), dialect.double_type()
    return [
        """CREATE TABLE IF NOT EXISTS workflow_execution_leases (
            lease_key TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE,
            scope_key TEXT, owner_node_id TEXT NOT NULL,
            generation BIGINT NOT NULL DEFAULT 1, cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
            lease_expires_at TIMESTAMPTZ NOT NULL)""",
        "ALTER TABLE workflow_execution_leases ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE",
        f"""CREATE TABLE IF NOT EXISTS workflow_executions (
            id {text} PRIMARY KEY, workflow_id {text} NOT NULL, status {text} NOT NULL,
            current_phase {text}, current_node_id {text}, current_node_type {text},
            current_step_index {num}, step_count {num} NOT NULL DEFAULT 0,
            input_params {text} NOT NULL DEFAULT '{{}}', output_results {text} NOT NULL DEFAULT '{{}}',
            error_message {text}, trigger_id {text}, trigger_type {text},
            started_at {num} NOT NULL, finished_at {num}, duration {real},
            updated_at {num}, payload {text} NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS workflow_execution_steps (
            exec_id {text} NOT NULL, step_index {num} NOT NULL, node_id {text}, node_type {text},
            inputs {text} NOT NULL DEFAULT '{{}}', outputs {text} NOT NULL DEFAULT '{{}}',
            error {text}, payload {text} NOT NULL, PRIMARY KEY (exec_id, step_index))""",
        f"""CREATE TABLE IF NOT EXISTS workflow_stats (
            workflow_id {text} PRIMARY KEY, call_count {num} NOT NULL DEFAULT 0,
            success_count {num} NOT NULL DEFAULT 0, error_count {num} NOT NULL DEFAULT 0,
            total_runtime {real} NOT NULL DEFAULT 0, avg_runtime {real} NOT NULL DEFAULT 0,
            thumbs_up {num} NOT NULL DEFAULT 0, thumbs_down {num} NOT NULL DEFAULT 0,
            updated_at {num})""",
        f"""CREATE TABLE IF NOT EXISTS workflow_configs (
            workflow_id {text} NOT NULL, kind {text} NOT NULL, version {num},
            config {text} NOT NULL, updated_at {num}, PRIMARY KEY (workflow_id, kind))""",
        f"""CREATE TABLE IF NOT EXISTS workflow_kv (
            key {text} PRIMARY KEY, value {text} NOT NULL, type {text} NOT NULL,
            created_at {text} NOT NULL, updated_at {text} NOT NULL)""",
    ]

_INDEX_STMTS = [
    "CREATE INDEX IF NOT EXISTS idx_workflow_leases_scope_expiry ON workflow_execution_leases(scope_key, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_leases_expiry ON workflow_execution_leases(lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_started ON workflow_executions(workflow_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_status ON workflow_executions(workflow_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_executions_trigger ON workflow_executions(workflow_id, trigger_type, trigger_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflow_execution_steps_exec_step ON workflow_execution_steps(exec_id, step_index)",
]
