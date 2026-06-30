"""Shared helpers for workflow execution history persistence."""

from __future__ import annotations

import asyncio
from itertools import islice
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from flocks.session.recorder import Recorder
from flocks.utils.log import Log
from flocks.workflow.runner import RunWorkflowResult
from flocks.workflow.store import WorkflowStore

log = Log.create(service="workflow.execution_store")

# Keys whose values are expected to be large alert/event lists that have
# already been persisted elsewhere (typically JSONL on disk).  When writing
# the execution record to SQLite we replace them with a ``_<key>_count``
# integer to keep row sizes bounded.  Callers may extend or override this
# set via the ``keys`` argument of the compact helpers below.
DEFAULT_LARGE_LIST_KEYS: frozenset[str] = frozenset(
    {
        "enriched_alerts",
        "unique_alerts",
        "raw_alerts",
        "normalized_alerts",
        "filtered_alerts",
    }
)

# Lists smaller than this many items are passed through verbatim.  The cap
# protects against accidentally stripping small metadata lists that happen
# to share a name with a known large-list key.
DEFAULT_COMPACT_SIZE_THRESHOLD: int = 100
DEFAULT_GENERIC_SEQUENCE_THRESHOLD: int = 1_000
DEFAULT_MAX_INLINE_STRING_CHARS: int = 20_000
DEFAULT_MAX_INLINE_DICT_KEYS: int = 200
DEFAULT_PREVIEW_ITEMS: int = 3
DEFAULT_PREVIEW_CHARS: int = 500


def _sequence_preview(value: Any, *, limit: int = DEFAULT_PREVIEW_ITEMS) -> list[Any]:
    if isinstance(value, (list, tuple)):
        items = value[:limit]
    else:
        items = islice(value, limit)
    return [_summarize_large_value(item, depth=1) for item in items]


def _summarize_large_value(value: Any, *, depth: int = 0) -> Dict[str, Any]:
    if isinstance(value, str):
        return {
            "_type": "string",
            "chars": len(value),
            "preview": value[:DEFAULT_PREVIEW_CHARS],
        }
    if isinstance(value, dict):
        return {
            "_type": "dict",
            "key_count": len(value),
            "keys": list(islice(value.keys(), DEFAULT_PREVIEW_ITEMS * 10)),
        }
    if isinstance(value, (list, tuple, set)):
        summary: Dict[str, Any] = {
            "_type": type(value).__name__,
            "count": len(value),
        }
        if depth == 0:
            summary["preview"] = _sequence_preview(value)
        return summary
    return {
        "_type": type(value).__name__,
        "preview": str(value)[:DEFAULT_PREVIEW_CHARS],
    }


def _compact_value_for_storage(
    value: Any,
    *,
    key: Optional[str],
    known_large_keys: frozenset[str],
    size_threshold: int,
    generic_sequence_threshold: int,
    max_inline_string_chars: int,
    max_inline_dict_keys: int,
    depth: int = 0,
) -> Any:
    if (
        key in known_large_keys
        and isinstance(value, (list, tuple))
        and len(value) > size_threshold
    ):
        return {f"_{key}_count": len(value)}

    if isinstance(value, str):
        if len(value) > max_inline_string_chars:
            return _summarize_large_value(value)
        return value

    if isinstance(value, (list, tuple, set)):
        if len(value) > generic_sequence_threshold:
            return _summarize_large_value(value)
        return value

    if isinstance(value, dict):
        if len(value) > max_inline_dict_keys:
            return _summarize_large_value(value)
        if depth >= 2:
            return value
        compacted: Dict[str, Any] = {}
        changed = False
        for child_key, child_value in value.items():
            child_compacted = _compact_value_for_storage(
                child_value,
                key=str(child_key),
                known_large_keys=known_large_keys,
                size_threshold=size_threshold,
                generic_sequence_threshold=generic_sequence_threshold,
                max_inline_string_chars=max_inline_string_chars,
                max_inline_dict_keys=max_inline_dict_keys,
                depth=depth + 1,
            )
            if isinstance(child_compacted, dict) and len(child_compacted) == 1:
                marker_key = next(iter(child_compacted))
                if marker_key.startswith("_") and marker_key.endswith("_count"):
                    compacted[marker_key] = child_compacted[marker_key]
                    changed = True
                    continue
            compacted[child_key] = child_compacted
            changed = changed or child_compacted is not child_value
        return compacted if changed else value

    return value


def compact_outputs_for_storage(
    outputs: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
    generic_sequence_threshold: int = DEFAULT_GENERIC_SEQUENCE_THRESHOLD,
    max_inline_string_chars: int = DEFAULT_MAX_INLINE_STRING_CHARS,
    max_inline_dict_keys: int = DEFAULT_MAX_INLINE_DICT_KEYS,
) -> Dict[str, Any]:
    """Return a bounded copy of *outputs* safe for execution records.

    Known large-list keys keep the historical ``_<key>_count`` shape. Other
    oversized strings, sequences, and dictionaries are replaced with bounded
    summaries so unknown workflow payload names cannot inflate SQLite rows or
    tool metadata.
    """
    if not isinstance(outputs, dict):
        return {}
    known_large_keys = frozenset(keys)
    compacted: Dict[str, Any] = {}
    for k, v in outputs.items():
        value = _compact_value_for_storage(
            v,
            key=str(k),
            known_large_keys=known_large_keys,
            size_threshold=size_threshold,
            generic_sequence_threshold=generic_sequence_threshold,
            max_inline_string_chars=max_inline_string_chars,
            max_inline_dict_keys=max_inline_dict_keys,
        )
        if isinstance(value, dict) and len(value) == 1:
            marker_key = next(iter(value))
            if marker_key == f"_{k}_count":
                compacted[marker_key] = value[marker_key]
                continue
        compacted[k] = value
    return compacted


def compact_step_for_storage(
    step: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
) -> Any:
    """Return a copy of one history step with large ``inputs``/``outputs`` compacted."""
    if not isinstance(step, dict) and hasattr(step, "model_dump"):
        step = step.model_dump(mode="json")
    if not isinstance(step, dict):
        return step
    step_copy = dict(step)
    for field in ("inputs", "outputs"):
        raw_value = step_copy.get(field)
        if isinstance(raw_value, dict):
            step_copy[field] = compact_outputs_for_storage(raw_value, keys=keys, size_threshold=size_threshold)
    return step_copy


def compact_history_for_storage(
    history: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
) -> List[Any]:
    """Strip large alert lists from step inputs/outputs in workflow history.

    Returns an empty list when *history* is falsy.  Non-dict step entries
    (defensive: shouldn't happen with normal ``StepResult`` dumps) are
    passed through unchanged so the caller sees no surprising drops.
    """
    if not history:
        return []
    return [compact_step_for_storage(step, keys=keys, size_threshold=size_threshold) for step in history]


def _first_value(data: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def derive_loop_progress(
    *,
    node_id: Optional[str],
    global_step_index: int,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Infer loop progress metadata from common workflow counter fields.

    Workflows often carry their global loop state in normal inputs/outputs
    (for example ``iteration``/``total_iterations``/``current_item``).  The
    engine currently exposes only node-level callbacks, so this helper derives
    a best-effort loop snapshot without changing the runtime data flow.
    """
    merged: Dict[str, Any] = {}
    if isinstance(inputs, dict):
        merged.update(inputs)
    if isinstance(outputs, dict):
        merged.update(outputs)

    iteration = _as_positive_int(
        _first_value(
            merged,
            ("iteration", "loop_index", "current_index", "item_idx", "item_index", "host_idx"),
        )
    )
    total = _as_positive_int(
        _first_value(
            merged,
            (
                "total_iterations",
                "total_items",
                "item_count",
                "items_count",
                "total_hosts",
                "host_count",
                "hosts_count",
                "hosts_total",
            ),
        )
    )
    if total is None:
        hosts = merged.get("hosts")
        if isinstance(hosts, list):
            total = len(hosts)

    current_item = _first_value(
        merged,
        ("current_item", "item", "current_host", "last_host", "host", "ssh_target", "last_ssh_target"),
    )

    if iteration is None and total is None and current_item is None:
        return None

    return {
        "loop_node_id": merged.get("loop_node_id") or merged.get("loop_id"),
        "iteration": iteration,
        "total_iterations": total,
        "current_item": current_item,
        "current_inner_node_id": node_id,
        "global_step_index": global_step_index,
    }


# Maximum number of execution history records retained per workflow.
# Keep this intentionally small so high-frequency workflows do not keep
# inflating the SQLite row set and matching JSONL audit files indefinitely.
_MAX_EXECUTION_HISTORY_PER_WORKFLOW = 30
# Per-workflow trim lock.  Trims are awaited by the writer so the retention cap
# is enforced before ``record_execution_result`` returns, while concurrent runs
# for the same workflow serialize instead of skipping cleanup.
_trim_locks: Dict[str, asyncio.Lock] = {}

# Per-workflow lock to serialize read-modify-write of stats. Concurrent
# executions of the same workflow (e.g. syslog-triggered runs with
# semaphore=8) would otherwise race on ``Storage.read → mutate → write``
# and silently lose counter increments.
_stats_locks: Dict[str, asyncio.Lock] = {}


def _get_stats_lock(workflow_id: str) -> asyncio.Lock:
    lock = _stats_locks.get(workflow_id)
    if lock is None:
        lock = asyncio.Lock()
        _stats_locks[workflow_id] = lock
    return lock


def _workflow_stats_key(workflow_id: str) -> str:
    return f"workflow/{workflow_id}/stats"


def _get_trim_lock(workflow_id: str) -> asyncio.Lock:
    lock = _trim_locks.get(workflow_id)
    if lock is None:
        lock = asyncio.Lock()
        _trim_locks[workflow_id] = lock
    return lock


_DEFAULT_STATS: Dict[str, Any] = {
    "callCount": 0,
    "successCount": 0,
    "errorCount": 0,
    "totalRuntime": 0.0,
    "avgRuntime": 0.0,
    "thumbsUp": 0,
    "thumbsDown": 0,
}


async def _update_workflow_stats(workflow_id: str, success: bool, duration: float) -> None:
    """Increment workflow call/success/error counters and update avgRuntime.

    Serialised per workflow to keep concurrent updates from clobbering each
    other (read → mutate → write race).
    """
    lock = _get_stats_lock(workflow_id)
    async with lock:
        try:
            await WorkflowStore.increment_stats(workflow_id, success=success, duration=duration)
        except Exception as exc:
            log.warning(
                "workflow.stats.update_failed",
                {
                    "workflow_id": workflow_id,
                    "error": str(exc),
                },
            )


def workflow_execution_key(exec_id: str) -> str:
    """Return the storage key for one workflow execution."""
    return f"workflow_execution/{exec_id}"


def workflow_execution_index_prefix(workflow_id: str) -> str:
    """Return the storage prefix for one workflow's execution index."""
    return f"workflow_execution_index/{workflow_id}/"


def workflow_execution_index_key(
    workflow_id: str,
    started_at: int,
    exec_id: str,
) -> str:
    """Return the index key used to trim one workflow without full-table scans."""
    return f"{workflow_execution_index_prefix(workflow_id)}{started_at:020d}/{exec_id}"


def workflow_execution_step_key(exec_id: str, step_index: int) -> str:
    """Return the storage key for one workflow execution step."""
    return f"workflow_execution_step/{exec_id}/{step_index:08d}"


def workflow_execution_step_prefix(exec_id: str) -> str:
    """Return the storage key prefix for all steps of one execution."""
    return f"workflow_execution_step/{exec_id}/"


def compact_execution_summary(exec_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return an execution record safe to keep in the hot summary row.

    Step details are stored separately under ``workflow_execution_step`` keys.
    Keeping ``executionLog`` out of the summary row avoids rewriting an
    ever-growing JSON blob on every progress update.
    """
    summary = dict(exec_data)
    summary["executionLog"] = []
    return summary


async def record_execution_step(
    exec_id: str,
    step_index: int,
    step: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist one compacted execution step and return the stored payload."""
    step_payload = compact_step_for_storage(step)
    await WorkflowStore.record_step(exec_id, step_index, step_payload)
    return step_payload


class ExecutionStepRecorder:
    """Bridge synchronous workflow step callbacks to append-only step rows."""

    def __init__(
        self,
        *,
        exec_id: str,
        loop: asyncio.AbstractEventLoop,
        logger: Any = None,
        log_event: str = "workflow.execution_step.write_failed",
        step_compactor: Callable[[Any], Dict[str, Any]] = compact_step_for_storage,
        write_timeout_s: float = 5.0,
    ) -> None:
        self.exec_id = exec_id
        self.loop = loop
        self.logger = logger or log
        self.log_event = log_event
        self.step_compactor = step_compactor
        self.write_timeout_s = write_timeout_s
        self.step_count = 0
        self.summary: Dict[str, Any] = {}

    def on_step_complete(self, step_result: Any) -> None:
        raw_step = step_result.model_dump(mode="json") if hasattr(step_result, "model_dump") else step_result
        step_dict = self.step_compactor(raw_step)
        if not isinstance(step_dict, dict):
            return

        self.step_count += 1
        loop_progress = derive_loop_progress(
            node_id=step_dict.get("node_id"),
            global_step_index=self.step_count,
            inputs=step_dict.get("inputs"),
            outputs=step_dict.get("outputs"),
        )
        self.summary.update(
            {
                "stepCount": self.step_count,
                "currentNodeId": step_dict.get("node_id"),
                "currentNodeType": step_dict.get("node_type") or step_dict.get("type"),
                "currentPhase": "running",
                "currentStepIndex": self.step_count,
                "loopProgress": loop_progress,
                "updatedAt": int(time.time() * 1000),
            }
        )
        try:
            asyncio.run_coroutine_threadsafe(
                record_execution_step(self.exec_id, self.step_count, step_dict),
                self.loop,
            ).result(timeout=self.write_timeout_s)
        except Exception as exc:
            self.logger.warning(
                self.log_event,
                {
                    "exec_id": self.exec_id,
                    "step_index": self.step_count,
                    "error": str(exc),
                },
            )


async def _backfill_execution_steps(
    exec_id: str,
    execution_log: Any,
) -> int:
    """Persist legacy inline executionLog entries as append-only step rows."""
    if not isinstance(execution_log, list):
        return 0

    written = 0
    for step_index, step in enumerate(execution_log, start=1):
        step_payload = compact_step_for_storage(step)
        if not isinstance(step_payload, dict):
            continue
        try:
            await WorkflowStore.record_step(exec_id, step_index, step_payload)
            written += 1
        except Exception as exc:
            log.warning(
                "workflow.execution_step.backfill_failed",
                {
                    "exec_id": exec_id,
                    "step_index": step_index,
                    "error": str(exc),
                },
            )
    return written


async def load_execution_steps(
    exec_id: str,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Load persisted step logs for an execution, sorted by step key."""
    page_limit = 500 if limit is None else max(limit, 0)
    return await WorkflowStore.list_steps(
        exec_id,
        offset=max(offset, 0),
        limit=page_limit,
    )


def normalize_execution_status(status: str) -> str:
    """Map runner status values to API status values."""
    normalized = (status or "").strip().upper()
    if normalized == "SUCCEEDED":
        return "success"
    if normalized == "FAILED":
        return "error"
    if normalized == "TIMED_OUT":
        return "timeout"
    if normalized == "CANCELLED":
        return "cancelled"
    return (status or "error").strip().lower() or "error"


def _extract_business_failure_message(outputs: Dict[str, Any]) -> Optional[str]:
    """Return a user-facing failure reason from workflow outputs."""
    for key in ("reason", "error_message", "errorMessage", "message"):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_execution_outcome(result: RunWorkflowResult) -> tuple[str, Optional[str]]:
    """Resolve API execution status from runner status and workflow outputs."""
    status_value = normalize_execution_status(result.status)
    error_message = result.error

    if status_value != "success" or not isinstance(result.outputs, dict):
        return status_value, error_message

    if result.outputs.get("workflow_success") is False:
        return (
            "error",
            error_message or _extract_business_failure_message(result.outputs) or "Workflow reported business failure.",
        )

    return status_value, error_message


def build_initial_execution_record(
    workflow_id: str,
    *,
    input_params: Optional[Dict[str, Any]] = None,
    exec_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the initial running execution payload."""
    return {
        "id": exec_id or str(uuid.uuid4()),
        "workflowId": workflow_id,
        "inputParams": input_params or {},
        "status": "running",
        "startedAt": int(time.time() * 1000),
        "executionLog": [],
        "currentPhase": "queued",
        "currentStepIndex": 0,
    }


async def create_execution_record(
    workflow_id: str,
    *,
    input_params: Optional[Dict[str, Any]] = None,
    exec_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create and persist a running workflow execution record.

    *input_params* is passed through ``compact_outputs_for_storage`` before
    writing to SQLite so that batch HTTP calls whose inputs contain a key in
    ``DEFAULT_LARGE_LIST_KEYS`` (e.g. ``{"raw_alerts": [...10k items...]}``
    ) don't bloat the row.  Keys outside the default set — such as a generic
    ``alerts`` parameter — are stored verbatim; pass a custom *keys* argument
    to ``compact_outputs_for_storage`` directly if you need broader coverage.
    """
    compacted_params = compact_outputs_for_storage(input_params or {})
    exec_data = build_initial_execution_record(
        workflow_id,
        input_params=compacted_params,
        exec_id=exec_id,
    )
    await WorkflowStore.upsert_execution(compact_execution_summary(exec_data))
    return exec_data


async def record_execution_result(
    workflow_id: str,
    exec_id: str,
    exec_data: Dict[str, Any],
) -> None:
    """Persist the final execution record, audit trail, and workflow stats."""
    summary_data = dict(exec_data)
    backfilled_steps = await _backfill_execution_steps(exec_id, summary_data.get("executionLog"))
    existing_step_count = _as_positive_int(summary_data.get("stepCount"))
    if backfilled_steps and (existing_step_count is None or existing_step_count < backfilled_steps):
        summary_data["stepCount"] = backfilled_steps

    await WorkflowStore.upsert_execution(compact_execution_summary(summary_data))

    # Update call/success/error counters so all trigger paths (HTTP, syslog, etc.)
    # are reflected in the UI stats panel.
    status = summary_data.get("status", "error")
    success = status == "success"
    duration = summary_data.get("duration")
    if not isinstance(duration, (int, float)):
        started_at = summary_data.get("startedAt", 0)
        finished_at = summary_data.get("finishedAt", int(time.time() * 1000))
        duration = max(0.0, (finished_at - started_at) / 1000.0)
    await _update_workflow_stats(workflow_id, success, float(duration))

    # Recorder writes to its own SQLite tables and can be slow under load.
    # Run it as a background task so the syslog/HTTP dispatcher can release the
    # concurrency slot immediately instead of waiting on session-history I/O.
    try:

        async def _record_audit() -> None:
            try:
                await Recorder.record_workflow_execution(
                    exec_id=exec_id,
                    workflow_id=workflow_id,
                    run_result=exec_data,
                )
            except Exception as exc:
                log.debug(
                    "workflow.audit.record_failed",
                    {
                        "exec_id": exec_id,
                        "error": str(exc),
                    },
                )

        asyncio.create_task(_record_audit(), name=f"audit-{exec_id}")
    except RuntimeError:
        # No running loop (e.g. unit tests) — best-effort sync fallback.
        try:
            await Recorder.record_workflow_execution(
                exec_id=exec_id,
                workflow_id=workflow_id,
                run_result=exec_data,
            )
        except Exception:
            pass

    # Prune old execution records when the per-workflow limit is exceeded.
    # This is awaited so a successful completion does not silently leave the
    # workflow above its retention cap.
    try:
        await _trim_execution_history(workflow_id)
    except Exception as exc:
        log.error(
            "workflow.history.trim_failed",
            {
                "workflow_id": workflow_id,
                "exec_id": exec_id,
                "error": str(exc),
            },
        )


async def _delete_execution_history_record(
    execution_key: str,
    *,
    index_key: Optional[str] = None,
) -> None:
    exec_id = execution_key.rsplit("/", 1)[-1]
    deleted_steps = await WorkflowStore.clear_steps(exec_id)
    removed_execution = await WorkflowStore.delete_execution(exec_id)
    record_path = Recorder.paths().workflow_dir / f"{exec_id}.jsonl"
    await asyncio.to_thread(record_path.unlink, missing_ok=True)
    log.debug(
        "workflow.history.trim_deleted",
        {
            "exec_id": exec_id,
            "execution_key": execution_key,
            "steps": deleted_steps,
            "removed_execution": removed_execution,
        },
    )


async def _trim_execution_history(workflow_id: str) -> None:
    """Delete the oldest execution records once the per-workflow cap is exceeded.

    New records carry a per-workflow ``workflow_execution_index`` key, so hot
    trims avoid scanning unrelated workflows.  This path is intentionally
    index-only: if an old execution has no index key, it is outside the hot
    retention path and should be handled by a separate migration/GC task.

    A per-workflow lock serializes concurrent trims.  Cleanup is awaited by
    ``record_execution_result`` so the retention cap is enforced synchronously
    instead of being an opportunistic background task.
    """
    lock = _get_trim_lock(workflow_id)
    async with lock:
        failures: List[str] = []
        for exec_id in await WorkflowStore.trim_executions(
            workflow_id,
            keep=_MAX_EXECUTION_HISTORY_PER_WORKFLOW,
        ):
            try:
                record_path = Recorder.paths().workflow_dir / f"{exec_id}.jsonl"
                await asyncio.to_thread(record_path.unlink, missing_ok=True)
            except Exception as exc:
                failures.append(f"{exec_id}: {exc}")
                log.warning(
                    "workflow.history.trim_delete_failed",
                    {
                        "workflow_id": workflow_id,
                        "exec_id": exec_id,
                        "error": str(exc),
                    },
                )

        if failures:
            raise RuntimeError("Failed to trim workflow execution history: " + "; ".join(failures[:3]))
