"""Bounded, payload-free diagnostics for the two bundled SOC workflows.

Collectors never await disk IO. A daemon writes rotating JSONL plus periodic
active-span snapshots, so evidence remains available if the API loop stalls.
"""
from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict, deque
from contextvars import ContextVar
from functools import wraps
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid

WORKFLOWS = ("stream_alert_denoise", "stream_alert_triage")
SCHEMA = 1
_context = ContextVar("soc_diagnostic_context", default=None)
_lock = threading.RLock()
_events = deque(maxlen=2000)
_active = {}
_counters = {wf: Counter() for wf in WORKFLOWS}
_last = {wf: {} for wf in WORKFLOWS}
_queue = queue.Queue(maxsize=2048)
_writer = None
_health = Counter()
_boot = uuid.uuid4().hex
_live_progress = OrderedDict()
_LIVE_LIMIT = 256
_LIVE_TTL_SECONDS = 30 * 60
_LIVE_STEPS = {
    WORKFLOWS[0]: frozenset(('receive_alert', 'normalize', 'filter_logs', 'dedup_and_write')),
    WORKFLOWS[1]: frozenset(('load_dedup_file', 'concurrent_triage', 'commit_cursor', 'summarize')),
}
_LIVE_METRICS = {
    WORKFLOWS[0]: ('rawCount', 'normalizedCount', 'afterFilterCount', 'uniqueCount',
                   'duplicateCount', 'filterRemovedCount'),
    WORKFLOWS[1]: ('inputCount', 'completedCount', 'cacheHitCount', 'failedCount',
                   'attackCount', 'benignCount', 'unknownCount', 'workUnitCount', 'followersReusedCount'),
}
_FIELDS = {"trace", "execution", "node", "phase", "status", "error_type", "sqlite_errorcode", "sqlite_errorname", "backoff_seconds", "step", "duration_ms",
           "stop_source", "stop_stage", "stop_reason", "stop_detail_present",
           "queue_size", "queue_capacity", "active_runs", "matched", "executed", "count",
           "thread", "parent", "worker_count", "listener_alive", "raw_count", "after_filter_count", "unique_key_count",
           "dedup_removed_count", "selected_count", "processed_count", "has_error",
           "output_path_present", "alerts_count", "pending_count", "processed_mark_count",
           "batch_records", "batch_bytes", "cursor_enabled", "cursor_invalidated", "cursor_committed",
           "cursor_revision", "cursor_commit_failed", "has_more", "_triage_persistence_succeeded",
           "candidate_file_count", "touched_file_count", "record_count", "bytes_read", "bad_lines",
           "oversized_lines", "partial_lines", "missing_files", "total", "work_units", "cache_hit",
           "followers_reused", "triaged", "triage_failed", "concurrency", "soc_db_rows"}


def _count(value):
    # Counts are exact nonnegative integers. False, missing and malformed values
    # must never become a reassuring zero in the live UI.
    return value if type(value) is int and value >= 0 else None


def _live_counts(workflow, outputs):
    """Extract bounded aggregate facts only, never alerts, subjects or payloads."""
    out = outputs if isinstance(outputs, dict) else {}
    stats = out.get('stats') if isinstance(out.get('stats'), dict) else {}
    load = out.get('load_stats') if isinstance(out.get('load_stats'), dict) else {}
    triage = out.get('triage_stats') if isinstance(out.get('triage_stats'), dict) else {}
    if workflow == WORKFLOWS[0]:
        result = {field: _count(stats.get(source)) for field, source in (
            ('rawCount', 'raw_count'), ('normalizedCount', 'normalized_count'),
            ('afterFilterCount', 'after_filter_count'), ('uniqueCount', 'unique_key_count'),
            ('duplicateCount', 'dedup_removed_count'), ('filterRemovedCount', 'filter_removed_count'))}
        # after_dedup_count includes enriched duplicate records, so is not a
        # unique count. Only derive uniqueness from two known compatible counts.
        if result['uniqueCount'] is None and result['afterFilterCount'] is not None and result['duplicateCount'] is not None:
            value = result['afterFilterCount'] - result['duplicateCount']
            result['uniqueCount'] = value if value >= 0 else None
        if (out.get('is_duplicate') is True and result['rawCount'] == 1
                and result['afterFilterCount'] == 1 and result['uniqueCount'] == 1
                and result['duplicateCount'] == 0 and 'unique_key_count' not in stats):
            # Older singleton outputs counted only in-batch duplicates. Their
            # explicit cross-batch duplicate flag is the observed result. A
            # current unique_key_count remains authoritative when provided.
            result['uniqueCount'], result['duplicateCount'] = 0, 1
        return result
    if workflow != WORKFLOWS[1]:
        return {}
    result = {field: _count(triage.get(source)) for field, source in (
        ('inputCount', 'total'), ('cacheHitCount', 'cache_hit'), ('failedCount', 'triage_failed'),
        ('workUnitCount', 'work_units'), ('followersReusedCount', 'followers_reused'))}
    if result['inputCount'] is None:
        result['inputCount'] = _count(load.get('record_count'))
    completed = [_count(triage.get(key)) for key in ('triaged', 'cache_hit', 'triage_failed')]
    # These are leader/work-unit counts, including handled failures. Never mix
    # them with input record_count, which also includes duplicate followers.
    result['completedCount'] = sum(completed) if all(value is not None for value in completed) else None
    verdicts = triage.get('verdict_counts') if isinstance(triage.get('verdict_counts'), dict) else {}
    attack = [_count(verdicts.get(key)) for key in ('attack', 'attack_success', 'attack_failed') if key in verdicts]
    result['attackCount'] = sum(attack) if attack and all(value is not None for value in attack) else None
    result['benignCount'] = _count(verdicts.get('non_attack'))
    result['unknownCount'] = _count(verdicts.get('unknown'))
    return result


def _prune_live_progress(at):
    # Caller holds _lock; the collection is always bounded independently of IO.
    for key in [key for key, item in _live_progress.items() if at - item['_updated_monotonic'] >= _LIVE_TTL_SECONDS]:
        _live_progress.pop(key, None)
    while len(_live_progress) > _LIVE_LIMIT:
        _live_progress.popitem(last=False)


def _update_live_progress(workflow, execution, *, phase='running', node=None,
                          index=None, outputs=None, completed=False, reset=False, duration_ms=None):
    """Same-process projection; diagnostics must not change runner semantics."""
    if workflow not in WORKFLOWS or not isinstance(execution, str) or not execution or len(execution) > 160:
        return
    try:
        at, stamp = time.monotonic(), int(time.time() * 1000)
        metrics = _live_counts(workflow, outputs)
        with _lock:
            _prune_live_progress(at)
            key = (workflow, execution)
            item = None if reset else _live_progress.get(key)
            if item is None:
                item = {'workflowId': workflow, 'executionId': execution, 'startedAt': stamp,
                        'nodeId': None, 'stepIndex': None, 'stepCount': 0,
                        'metrics': dict.fromkeys(_LIVE_METRICS[workflow]), 'stepDurationsMs': {}}
                _live_progress[key] = item
            item.update(updatedAt=stamp, phase=phase, _updated_monotonic=at)
            if isinstance(node, str):
                item['nodeId'] = node[:160]
            if type(index) is int:
                item['stepIndex'] = index
            if completed:
                item['stepCount'] += 1
            if node in _LIVE_STEPS[workflow]:
                # A new attempt clears the prior attempt's duration. Only an
                # observed successful node-end supplies a value; never infer
                # one from polling, animation or whole-execution elapsed time.
                item['stepDurationsMs'].pop(node, None)
                if (completed and phase == 'running' and type(duration_ms) in (int, float)
                        and 0 <= duration_ms <= 2**53 - 1 and math.isfinite(duration_ms)):
                    item['stepDurationsMs'][node] = duration_ms
            item['metrics'].update({key: value for key, value in metrics.items() if value is not None})
            _live_progress.move_to_end(key)
            _prune_live_progress(at)
    except Exception:
        with _lock:
            _health['live_projection_errors'] += 1


def read_workflow_live_progress(workflow_id, execution_ids):
    """Return isolated copies for exact workflow/execution IDs, without IO.

    Manager-created run_id values are persisted workflow execution IDs. Node
    callbacks update this view before ExecutionStepRecorder's final DB flush.
    Missing/expired entries require the caller to fall back to persisted facts;
    this projection is neither a durable record nor shared between processes.
    """
    if workflow_id not in WORKFLOWS:
        return {}
    with _lock:
        _prune_live_progress(time.monotonic())
        result = {}
        for execution in execution_ids:
            if not isinstance(execution, str):
                continue
            item = _live_progress.get((workflow_id, execution))
            if item is not None:
                result[execution] = {key: dict(value) if key in ('metrics', 'stepDurationsMs') else value
                                     for key, value in item.items() if not key.startswith('_')}
        return result


def log_path():
    from flocks.utils.log import get_log_dir
    return get_log_dir() / "soc-workspace-diagnostics.jsonl"


def record(workflow, event, **fields):
    if workflow not in WORKFLOWS:
        return
    try:
        item = {"time": time.time(), "pid": os.getpid(), "boot": _boot, "workflow": workflow, "event": event}
        item.update({k: v[:160] if isinstance(v, str) else v for k, v in fields.items()
                     if k in _FIELDS and (v is None or isinstance(v, (str, int, float, bool)))})
        with _lock:
            _counters[workflow][event] += 1
            _last[workflow][event] = item["time"]
            # Ingest volume is represented by exact counters; sample event rows.
            if event in {"transport.received", "received", "enqueued", "queue_full"} and _counters[workflow][event] % 100 != 1:
                return
            _events.append(item)
            _ensure_writer()
            try:
                _queue.put_nowait(item)
            except queue.Full:
                _health["dropped_records"] += 1
    except Exception:
        _health["collector_errors"] += 1


def _ensure_writer():
    global _writer
    if _writer is None:
        _writer = threading.Thread(target=_write_loop, name="soc-diagnostics", daemon=True)
        _writer.start()


class _Handler(RotatingFileHandler):
    def handleError(self, record):
        _health["writer_errors"] += 1


def _write_loop():
    handler = None
    next_snapshot = time.monotonic()
    while True:
        try:
            if handler is None:
                path = log_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = _Handler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
            try:
                item = _queue.get(timeout=1)
                handler.emit(logging.LogRecord("soc", logging.INFO, "", 0, json.dumps(item, ensure_ascii=False), (), None))
            except queue.Empty:
                pass
            if time.monotonic() >= next_snapshot:
                data = snapshot()
                data["event"] = "snapshot"
                handler.emit(logging.LogRecord("soc", logging.INFO, "", 0, json.dumps(data, ensure_ascii=False), (), None))
                next_snapshot = time.monotonic() + 5
        except Exception:
            _health["writer_errors"] += 1
            if handler is not None:
                handler.close()
            handler = None
            time.sleep(1)


def snapshot():
    now = time.time()
    with _lock:
        active = [{**v, "elapsed_seconds": round(now - v["started"], 3)} for v in _active.values()]
        result = {"time": now, "pid": os.getpid(), "boot": _boot,
                  "counters": {k: dict(v) for k, v in _counters.items()},
                  "last_activity": {k: dict(v) for k, v in _last.items()},
                  "active": active, "health": dict(_health), "pending_log_records": _queue.qsize()}
    # Code locations only: never locals, full paths, source lines or payloads.
    if any(v["elapsed_seconds"] >= 60 for v in active):
        stacks = []
        for tid, frame in list(sys._current_frames().items())[:64]:
            frames = []
            while frame is not None and len(frames) < 24:
                frames.append({"file": Path(frame.f_code.co_filename).name,
                               "function": frame.f_code.co_name, "line": frame.f_lineno})
                frame = frame.f_back
            stacks.append({"thread": tid, "frames": frames})
        result["thread_locations"] = stacks
    return result


def progress(phase, **fields):
    ctx = _context.get()
    if ctx:
        wf, trace = ctx
        with _lock:
            if trace in _active:
                _active[trace].update(phase=phase, last_progress=time.time())
        record(wf, "progress", trace=trace, phase=phase, **fields)


def traced(phase):
    """Async manager boundary: preserve results/exceptions/cancellation unchanged."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(self, workflow_id, *args, **kwargs):
            if workflow_id not in WORKFLOWS:
                return await fn(self, workflow_id, *args, **kwargs)
            trace = uuid.uuid4().hex
            token = _context.set((workflow_id, trace))
            with _lock:
                if len(_active) < 256:
                    _active[trace] = {"workflow": workflow_id, "trace": trace, "phase": phase,
                                      "started": time.time(), "last_progress": time.time()}
                else:
                    _health["active_overflow"] += 1
            record(workflow_id, "trace.start", trace=trace, phase=phase)
            try:
                result = await fn(self, workflow_id, *args, **kwargs)
                record(workflow_id, "trace.end", trace=trace, status="returned")
                return result
            except BaseException as exc:
                from flocks.hooks.execution import execution_stop_diagnostics
                from flocks.workflow.store import sqlite_error_fields
                stop = execution_stop_diagnostics(exc)
                # Keep the support bundle payload-free. Extension detail can
                # contain arbitrary input; source/stage/code identify the
                # stopping hook without exporting the detail or message body.
                stop_fields = {
                    key: stop[key]
                    for key in ("stop_source", "stop_stage", "stop_reason")
                    if key in stop
                }
                if stop:
                    stop_fields["stop_detail_present"] = bool(stop.get("stop_detail_present"))
                record(workflow_id, "trace.end", trace=trace, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "error", **sqlite_error_fields(exc), **stop_fields)
                raise
            finally:
                with _lock:
                    _active.pop(trace, None)
                _context.reset(token)
        return wrapped
    return decorate


def runner_observer(fn):
    @wraps(fn)
    def wrapped(**kwargs):
        ctx = _context.get()
        if not ctx:
            return fn(**kwargs)
        wf, trace = ctx
        execution = kwargs.get('run_id')
        start = kwargs.get("on_step_start")
        end = kwargs.get("on_step_complete")
        def on_start(rid, index, node, inputs):
            nonlocal execution
            execution = rid
            _update_live_progress(wf, execution, node=node.id, index=index)
            key = trace + ":" + str(index)
            with _lock:
                if len(_active) < 256:
                    _active[key] = {"workflow": wf, "trace": trace, "execution": rid,
                                    "node": node.id, "step": index, "phase": "node.running",
                                    "started": time.time(), "thread": threading.get_ident()}
            record(wf, "node.start", trace=trace, execution=rid, node=node.id, step=index)
            return start(rid, index, node, inputs) if start else True
        def on_end(step):
            _update_live_progress(wf, execution, node=step.node_id, outputs=step.outputs,
                                  completed=True, phase='failed' if step.error else 'running',
                                  duration_ms=step.duration_ms)
            with _lock:
                for key in [k for k, v in _active.items() if k.startswith(trace + ":") and v.get("node") == step.node_id]:
                    _active.pop(key, None)
            record(wf, "node.end", trace=trace, node=step.node_id, duration_ms=step.duration_ms,
                   has_error=bool(step.error), status="error" if step.error else "ok")
            # Known numeric counters only; no output data is retained.
            out = step.outputs or {}
            counts = {}
            for section in ("stats", "load_stats", "triage_stats"):
                stats = out.get(section) if isinstance(out.get(section), dict) else {}
                counts.update({k: v for k, v in stats.items() if k in _FIELDS and type(v) in (int, float, bool)})
            for key in ("batch_records", "batch_bytes", "cursor_enabled", "cursor_invalidated",
                        "cursor_committed", "cursor_revision", "has_more", "_triage_persistence_succeeded"):
                if type(out.get(key)) in (int, float, bool):
                    counts[key] = out[key]
            if "cursor_commit_error" in out:
                counts["cursor_commit_failed"] = bool(out["cursor_commit_error"])
            for key in ("selected_count", "processed_count"):
                if type(out.get(key)) in (int, float):
                    counts[key] = out[key]
            for key in ("pending_count", "processed_mark_count"):
                if type(out.get(key)) in (int, float):
                    counts[key] = out[key]
            if "output_path" in out:
                counts["output_path_present"] = bool(out["output_path"])
            if isinstance(out.get("alerts"), list):
                counts["alerts_count"] = len(out["alerts"])
            if counts:
                record(wf, "node.counts", trace=trace, node=step.node_id, **counts)
            if end:
                return end(step)
        kwargs.update(on_step_start=on_start, on_step_complete=on_end)
        _update_live_progress(wf, execution, reset=True)
        progress("runner.enter", execution=kwargs.get("run_id"), thread=threading.get_ident())
        try:
            result = fn(**kwargs)
            status = str(result.status).lower()
            phase = ('cancelled' if status in ('cancelled', 'canceled') else
                     'failed' if result.error or status in ('failed', 'error') else 'success')
            _update_live_progress(wf, execution, phase=phase, outputs=getattr(result, 'outputs', None))
            progress("runner.return", status=result.status, has_error=bool(result.error))
            return result
        except BaseException as exc:
            _update_live_progress(wf, execution,
                                  phase='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed')
            progress("runner.error", error_type=type(exc).__name__)
            raise
        finally:
            with _lock:
                for key in [k for k in _active if k.startswith(trace + ":")]:
                    _active.pop(key, None)
    return wrapped


def export_bundle():
    from flocks import __version__
    events = deque(maxlen=6000)
    errors = []
    path = log_path()
    for candidate in [Path(str(path) + f".{i}") for i in (3, 2, 1)] + [path]:
        try:
            with candidate.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        events.append(json.loads(line))
                    except (ValueError, TypeError):
                        errors.append("invalid_line") if len(errors) < 10 else None
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append(type(exc).__name__)
    with _lock:
        recent = list(_events)
    return {"schema": SCHEMA, "component": "soc-workspace", "flocks_version": __version__,
            "instrumentation_revision": __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest()[:16],
            "captured_at": time.time(), "runtime": snapshot(), "persisted_events": list(events),
            "recent_events": recent, "read_errors": errors,
            "limits": {"disk_bytes_per_file": 2097152, "backups": 3, "export_rows": 6000,
                       "received_sample_every": 100, "snapshot_seconds": 5},
            "notes": ["Recent events may overlap persisted events.",
                      "Long elapsed time is evidence of waiting, not proof of deadlock."]}
