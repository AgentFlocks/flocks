"""Shared helpers for workflow execution history persistence."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from flocks.session.recorder import Recorder
from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.runner import RunWorkflowResult

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


def compact_outputs_for_storage(
    outputs: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
) -> Dict[str, Any]:
    """Return a copy of *outputs* with large alert lists replaced by counts.

    Only list values whose key is in *keys* AND whose length exceeds
    *size_threshold* are compacted; everything else is passed through.
    This prevents megabytes of alert data from being serialised into the
    ``workflow_execution`` SQLite row on every invocation, while still
    keeping small lists (e.g. error details, short configuration arrays)
    fully inspectable in the execution-history UI.
    """
    if not isinstance(outputs, dict):
        return {}
    key_set = frozenset(keys)
    compacted: Dict[str, Any] = {}
    for k, v in outputs.items():
        if (
            k in key_set
            and isinstance(v, list)
            and len(v) > size_threshold
        ):
            compacted[f"_{k}_count"] = len(v)
        else:
            compacted[k] = v
    return compacted


def compact_history_for_storage(
    history: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
) -> List[Any]:
    """Strip large alert lists from step outputs in workflow history.

    Returns an empty list when *history* is falsy.  Non-dict step entries
    (defensive: shouldn't happen with normal ``StepResult`` dumps) are
    passed through unchanged so the caller sees no surprising drops.
    """
    if not history:
        return []
    result: List[Any] = []
    for step in history:
        if not isinstance(step, dict):
            result.append(step)
            continue
        step_copy = dict(step)
        raw_outputs = step_copy.get("outputs")
        if isinstance(raw_outputs, dict):
            step_copy["outputs"] = compact_outputs_for_storage(
                raw_outputs, keys=keys, size_threshold=size_threshold
            )
        result.append(step_copy)
    return result

# Maximum number of execution history records retained per workflow.
# Older records are pruned automatically to prevent a syslog flood from bloating Storage.
_MAX_EXECUTION_HISTORY_PER_WORKFLOW = 500
# Trim is an O(N) scan over all workflow_execution rows; only run it every Nth
# call per workflow to amortise the cost under high syslog throughput.
_TRIM_CHECK_INTERVAL = 50
_trim_counters: Dict[str, int] = {}

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
            key = _workflow_stats_key(workflow_id)
            try:
                stats: Dict[str, Any] = await Storage.read(key) or dict(_DEFAULT_STATS)
            except Exception:
                stats = dict(_DEFAULT_STATS)
            stats["callCount"] = stats.get("callCount", 0) + 1
            if success:
                stats["successCount"] = stats.get("successCount", 0) + 1
            else:
                stats["errorCount"] = stats.get("errorCount", 0) + 1
            total = stats.get("totalRuntime", 0.0) + duration
            stats["totalRuntime"] = total
            call_count = stats["callCount"]
            stats["avgRuntime"] = (total / call_count) if call_count > 0 else 0.0
            await Storage.write(key, stats)
        except Exception as exc:
            log.warning("workflow.stats.update_failed", {
                "workflow_id": workflow_id,
                "error": str(exc),
            })


def workflow_execution_key(exec_id: str) -> str:
    """Return the storage key for one workflow execution."""
    return f"workflow_execution/{exec_id}"


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
            error_message
            or _extract_business_failure_message(result.outputs)
            or "Workflow reported business failure.",
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
    """Create and persist a running workflow execution record."""
    exec_data = build_initial_execution_record(
        workflow_id,
        input_params=input_params,
        exec_id=exec_id,
    )
    await Storage.write(workflow_execution_key(exec_data["id"]), exec_data)
    return exec_data


async def record_execution_result(
    workflow_id: str,
    exec_id: str,
    exec_data: Dict[str, Any],
) -> None:
    """Persist the final execution record, audit trail, and workflow stats."""
    await Storage.write(workflow_execution_key(exec_id), exec_data)

    # Update call/success/error counters so all trigger paths (HTTP, syslog, etc.)
    # are reflected in the UI stats panel.
    status = exec_data.get("status", "error")
    success = status == "success"
    duration = exec_data.get("duration")
    if not isinstance(duration, (int, float)):
        started_at = exec_data.get("startedAt", 0)
        finished_at = exec_data.get("finishedAt", int(time.time() * 1000))
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
                log.debug("workflow.audit.record_failed", {
                    "exec_id": exec_id,
                    "error": str(exc),
                })

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
    # Throttled by a per-workflow counter to amortise the O(N) storage scan.
    try:
        counter = _trim_counters.get(workflow_id, 0) + 1
        _trim_counters[workflow_id] = counter
        if counter >= _TRIM_CHECK_INTERVAL:
            _trim_counters[workflow_id] = 0
            # Run trim in the background as well; it scans all execution rows
            # and we don't want to delay the caller.
            try:
                asyncio.create_task(
                    _trim_execution_history(workflow_id),
                    name=f"trim-{workflow_id}",
                )
            except RuntimeError:
                await _trim_execution_history(workflow_id)
    except Exception:
        pass


async def _trim_execution_history(workflow_id: str) -> None:
    """Delete the oldest execution records once the per-workflow cap is exceeded."""
    all_entries = await Storage.list_entries("workflow_execution/")
    wf_entries = [
        (key, data)
        for key, data in all_entries
        if isinstance(data, dict) and data.get("workflowId") == workflow_id
    ]
    if len(wf_entries) <= _MAX_EXECUTION_HISTORY_PER_WORKFLOW:
        return
    # Sort ascending by startedAt and remove the oldest excess records
    wf_entries.sort(key=lambda kd: kd[1].get("startedAt", 0))
    excess = len(wf_entries) - _MAX_EXECUTION_HISTORY_PER_WORKFLOW
    for key, _ in wf_entries[:excess]:
        try:
            await Storage.remove(key)
        except Exception:
            pass
