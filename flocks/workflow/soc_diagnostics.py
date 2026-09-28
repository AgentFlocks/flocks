"""Bounded, payload-free diagnostics for the two bundled SOC workflows.

Collectors never await disk IO. A daemon writes rotating JSONL plus periodic
active-span snapshots, so evidence remains available if the API loop stalls.
"""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from contextvars import ContextVar
from functools import wraps
import json
import logging
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
_FIELDS = {"trace", "execution", "node", "phase", "status", "error_type", "step", "duration_ms",
           "queue_size", "queue_capacity", "active_runs", "matched", "executed", "count",
           "thread", "parent", "worker_count", "listener_alive", "raw_count", "after_filter_count", "unique_key_count",
           "dedup_removed_count", "selected_count", "processed_count", "has_error",
           "output_path_present", "alerts_count", "pending_count", "processed_mark_count",
           "batch_records", "batch_bytes", "cursor_enabled", "cursor_invalidated", "cursor_committed",
           "cursor_revision", "cursor_commit_failed", "has_more", "_triage_persistence_succeeded",
           "candidate_file_count", "touched_file_count", "record_count", "bytes_read", "bad_lines",
           "oversized_lines", "partial_lines", "missing_files", "total", "work_units", "cache_hit",
           "followers_reused", "triaged", "triage_failed", "concurrency", "soc_db_rows"}


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
                record(workflow_id, "trace.end", trace=trace, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "error", error_type=type(exc).__name__)
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
        start = kwargs.get("on_step_start")
        end = kwargs.get("on_step_complete")
        def on_start(rid, index, node, inputs):
            key = trace + ":" + str(index)
            with _lock:
                if len(_active) < 256:
                    _active[key] = {"workflow": wf, "trace": trace, "execution": rid,
                                    "node": node.id, "step": index, "phase": "node.running",
                                    "started": time.time(), "thread": threading.get_ident()}
            record(wf, "node.start", trace=trace, execution=rid, node=node.id, step=index)
            return start(rid, index, node, inputs) if start else True
        def on_end(step):
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
        progress("runner.enter", execution=kwargs.get("run_id"), thread=threading.get_ident())
        try:
            result = fn(**kwargs)
            progress("runner.return", status=result.status, has_error=bool(result.error))
            return result
        except BaseException as exc:
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
