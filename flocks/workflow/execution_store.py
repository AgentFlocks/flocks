"""Shared helpers for workflow execution history persistence."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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

    Only **list or tuple** values whose key is in *keys* AND whose length
    exceeds *size_threshold* are compacted to ``_<key>_count``; everything
    else is passed through unchanged.  This prevents megabytes of alert data
    from being serialised into the ``workflow_execution`` SQLite row on every
    invocation, while still keeping small sequences (e.g. error details, short
    configuration arrays) fully inspectable in the execution-history UI.

    **Keys that are compacted by default** (see ``DEFAULT_LARGE_LIST_KEYS``):
    ``enriched_alerts``, ``unique_alerts``, ``raw_alerts``,
    ``normalized_alerts``, ``filtered_alerts``.  Keys outside this set — such
    as a generic ``alerts`` parameter — are *not* compacted unless the caller
    passes a custom *keys* argument.  Callers who depend on inspecting the
    full list contents of compacted keys must read the data from the JSONL
    files written by the workflow itself.
    """
    if not isinstance(outputs, dict):
        return {}
    key_set = frozenset(keys)
    compacted: Dict[str, Any] = {}
    for k, v in outputs.items():
        if (
            k in key_set
            and isinstance(v, (list, tuple))
            and len(v) > size_threshold
        ):
            compacted[f"_{k}_count"] = len(v)
        else:
            compacted[k] = v
    return compacted


def compact_step_for_storage(
    step: Any,
    *,
    keys: Iterable[str] = DEFAULT_LARGE_LIST_KEYS,
    size_threshold: int = DEFAULT_COMPACT_SIZE_THRESHOLD,
) -> Any:
    """Return a copy of one history step with large ``inputs``/``outputs`` compacted."""
    if not isinstance(step, dict):
        return step
    step_copy = dict(step)
    for field in ("inputs", "outputs"):
        raw_value = step_copy.get(field)
        if isinstance(raw_value, dict):
            step_copy[field] = compact_outputs_for_storage(
                raw_value, keys=keys, size_threshold=size_threshold
            )
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
    return [
        compact_step_for_storage(step, keys=keys, size_threshold=size_threshold)
        for step in history
    ]


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

    iteration = _as_positive_int(_first_value(
        merged,
        ("iteration", "loop_index", "current_index", "item_idx", "item_index", "host_idx"),
    ))
    total = _as_positive_int(_first_value(
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
    ))
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
# Trim is an O(N) scan over all workflow_execution rows; only run it every Nth
# call per workflow to amortise the cost under high syslog throughput.
_TRIM_CHECK_INTERVAL = 5
_trim_counters: Dict[str, int] = {}
# Workflows with an in-flight trim task.  Because trims run as fire-and-forget
# ``asyncio.create_task`` background jobs, a slow trim under high syslog load
# could otherwise spawn many overlapping scans that each materialise table
# state simultaneously — the exact pattern that drove RSS to 20 GB.  This
# guard ensures at most one trim per workflow is ever running.
_trim_in_flight: Set[str] = set()

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
    await Storage.write(workflow_execution_step_key(exec_id, step_index), step_payload)
    return step_payload


async def load_execution_steps(
    exec_id: str,
    *,
    offset: int = 0,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Load persisted step logs for an execution, sorted by step key."""
    page_limit = 500 if limit is None else max(limit, 0)
    selected, total = await Storage.list_entries_page(
        workflow_execution_step_prefix(exec_id),
        offset=max(offset, 0),
        limit=page_limit,
    )
    return [
        value for _key, value in selected if isinstance(value, dict)
    ], total


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
    await Storage.write(workflow_execution_key(exec_data["id"]), compact_execution_summary(exec_data))
    return exec_data


async def record_execution_result(
    workflow_id: str,
    exec_id: str,
    exec_data: Dict[str, Any],
) -> None:
    """Persist the final execution record, audit trail, and workflow stats."""
    await Storage.write(workflow_execution_key(exec_id), compact_execution_summary(exec_data))

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


# Regex patterns to extract scalar fields from raw JSON strings without
# calling json.loads.  workflowId/startedAt are always serialised near the
# start of the record (set in build_initial_execution_record), so we only
# scan a small prefix of each value string — O(prefix) instead of O(value).
_RE_WORKFLOW_ID = re.compile(r'"workflowId"\s*:\s*"([^"]+)"')
_RE_STARTED_AT = re.compile(r'"startedAt"\s*:\s*(\d+)')


async def _trim_execution_history(workflow_id: str) -> None:
    """Delete the oldest execution records once the per-workflow cap is exceeded.

    Uses ``Storage.list_raw`` + regex instead of ``list_entries`` + ``json.loads``
    so that scanning the execution-history table never materialises large JSON
    blobs as Python objects.  The previous approach caused 100% single-core CPU
    usage (``json.raw_decode``) and drove RSS to 20 GB under syslog load.

    Also guards against overlapping trim tasks via ``_trim_in_flight``: because
    trims run as fire-and-forget background tasks, without the guard a slow trim
    would spawn multiple concurrent scans that each load the full table
    simultaneously, multiplying the memory spike.
    """
    # Coalesce overlapping trims: only one scan per workflow at a time.
    if workflow_id in _trim_in_flight:
        return
    _trim_in_flight.add(workflow_id)
    try:
        raw_rows = await Storage.list_raw("workflow_execution/")
        wf_entries: List[tuple] = []
        for key, value_str in raw_rows:
            # Scan only the first 400 chars — enough for workflowId + startedAt.
            head = value_str[:400]
            m_wf = _RE_WORKFLOW_ID.search(head)
            if not m_wf or m_wf.group(1) != workflow_id:
                continue
            m_ts = _RE_STARTED_AT.search(head)
            started_at = int(m_ts.group(1)) if m_ts else 0
            wf_entries.append((key, started_at))

        if len(wf_entries) <= _MAX_EXECUTION_HISTORY_PER_WORKFLOW:
            return
        # Sort ascending by startedAt and remove the oldest excess records.
        wf_entries.sort(key=lambda kd: kd[1])
        excess = len(wf_entries) - _MAX_EXECUTION_HISTORY_PER_WORKFLOW
        for key, _ in wf_entries[:excess]:
            try:
                exec_id = key.rsplit("/", 1)[-1]
                await Storage.remove(key)
                step_rows = await Storage.list_raw(workflow_execution_step_prefix(exec_id))
                for step_key, _value in step_rows:
                    await Storage.remove(step_key)
                record_path = Recorder.paths().workflow_dir / f"{exec_id}.jsonl"
                await asyncio.to_thread(record_path.unlink, missing_ok=True)
            except Exception:
                pass
    finally:
        _trim_in_flight.discard(workflow_id)
