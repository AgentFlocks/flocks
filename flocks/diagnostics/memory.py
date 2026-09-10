"""Opt-in memory investigation, not a memory-leak fix.

Hot-path hooks retain only scalar metadata, never inputs/results/tracebacks. They
do no file I/O. No heap traversal, forced GC, GC disabling, or request buffering.
Allocation snapshots are explicitly optional and have soft safety thresholds;
CPython snapshot creation itself cannot be preempted by another Python thread.
"""

import asyncio
import __future__
import functools
import gc
import inspect
import json
import mmap
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar
from collections import deque
from datetime import datetime
from itertools import islice
from pathlib import Path

from .common import GC_RECORD, Journal, config_path, process_sample, read_small, root_dir


_recorder = None
_context = ContextVar("flocks_memory_diagnostic_span", default=None)
MAX_ACTIVE = 256
MAX_KEYS = 256
TRACE_METADATA_LIMIT = 16 * 1024 * 1024
TRACE_HEAP_LIMIT = 512 * 1024 * 1024
TRACE_RSS_LIMIT_KIB = 4 * 1024 * 1024


def _label(value):
    return value[:96] if type(value) is str else ""


def _shape(value, depth=0, budget=None):
    """Constant-time metadata only; do not invoke user-defined __len__/__sizeof__."""
    if type(value) in (str, bytes, bytearray, list, tuple, dict):
        result = {"type": type(value).__name__, "length": len(value), "shallow_bytes": sys.getsizeof(value)}
        budget = [12] if budget is None else budget
        if type(value) in (list, tuple, dict) and depth < 2 and budget[0] > 0:
            children = []
            items = value.values() if type(value) is dict else value
            for child in islice(items, 8):
                if budget[0] <= 0:
                    break
                budget[0] -= 1
                children.append(_shape(child, depth + 1, budget))
            result["children_sample"] = children
        return result
    return {"type": "other"}


def _workflow_label(source, path=None):
    # Workflow models have a name, not an id; execution plans also carry the
    # installed workflow path. Prefer the folder id to join Syslog queue gauges.
    path = path or getattr(source, "workflow_path", None)
    if isinstance(path, (str, Path)):
        parsed = Path(path)
        return _label(parsed.parent.name if parsed.name in {"workflow.json", "workflow.md"} else parsed.stem)
    source = getattr(source, "workflow", source)
    if type(source) is dict:
        return _label(source.get("id") or source.get("name"))
    if isinstance(source, (str, Path)):
        return _workflow_label(None, source)
    return _label(getattr(source, "id", None) or getattr(source, "name", None))


def _metadata(kind, args, kwargs):
    result = {"kind": kind, "native_tid": threading.get_native_id(), "python_ident": threading.get_ident()}
    parent = _context.get()
    if kind == "rpc" and args and _recorder is not None:
        parent = _recorder.runtime_parent(id(args[0])) or parent
    if parent:
        result.update({key: parent[key] for key in ("workflow", "node", "run_id", "runtime_id") if key in parent})
        result["parent_id"] = parent["id"]
    if kind == "workflow":
        wf = kwargs.get("workflow")
        result.update(workflow=_workflow_label(wf), run_id=_label(kwargs.get("run_id")),
                      input=_shape(kwargs.get("inputs")), profile=_label(kwargs.get("execution_profile")))
    elif kind == "node":
        owner = args[0]
        node = args[1] if len(args) > 1 else kwargs.get("node")
        result.update(workflow=_workflow_label(getattr(owner, "workflow", None), getattr(owner, "workflow_path", None)),
                      node=_label(getattr(node, "id", "")),
                      input=_shape(args[2] if len(args) > 2 else kwargs.get("inputs")))
    elif kind == "llm":
        result["input"] = _shape(args[1] if len(args) > 1 else kwargs.get("prompt"))
    elif kind == "runtime":
        result.update(runtime_id=id(args[0]), runtime_type=type(args[0]).__name__[:64],
                      input=_shape(args[2] if len(args) > 2 else kwargs.get("inputs")))
    elif kind == "llm_wait":
        coro = args[0] if args else kwargs.get("coro")
        if inspect.iscoroutine(coro):
            result["awaited_function"] = coro.cr_code.co_name[:96]
    elif kind == "provider_chat":
        result["input"] = _shape(args[2] if len(args) > 2 else kwargs.get("messages"))
    elif kind.startswith("db.") or kind == "log_write":
        if kind in ("db.decode", "db.encode"):
            value = args[0] if args else kwargs.get("value")
        elif kind == "log_write":
            value = args[1] if len(args) > 1 else kwargs.get("message")
        else:
            value = kwargs.get("exec_data", kwargs.get("value", kwargs.get("outputs")))
        result["input"] = _shape(value)
    elif kind == "rpc":
        msg = kwargs.get("msg")
        rpc = msg.get("rpc") if type(msg) is dict else None
        rpc_kind = rpc.get("kind") if type(rpc) is dict else None
        result["rpc_kind"] = rpc_kind if rpc_kind in ("llm", "tool", "cancelled") else "other"
    elif kind == "http":
        scope = args[1] if len(args) > 1 else kwargs.get("scope", {})
        if scope.get("type") != "http":
            return None
        # Never retain a URL/query/header. Static category only, even for unknown routes.
        parts = scope.get("path", "")[:256].split("/", 3)
        category = parts[2] if len(parts) > 2 and parts[1] == "api" else "other"
        result["category"] = category if category in {
            "workflow", "workflows", "event", "events", "knowledge", "plugins", "health", "ping",
            "session", "sessions", "chat", "config", "soc", "skill", "llm"} else "other"
    return result


def observe(kind):
    def decorate(fn):
        def begin(args, kwargs):
            recorder = _recorder
            if recorder is None:
                return None, None, None
            try:
                metadata = _metadata(kind, args, kwargs)
                token = recorder.begin(metadata) if metadata is not None else None
                context_token = None
                if token is not None:
                    scope = {key: metadata[key] for key in ("workflow", "node", "run_id", "runtime_id") if key in metadata}
                    scope["id"] = token[0]
                    context_token = _context.set(scope)
                return recorder, token, context_token
            except Exception:
                return None, None, None

        def finish(recorder, token, context_token, error_type, result):
            try:
                if recorder is not None and token is not None:
                    recorder.finish(token, error_type, _shape(result))
            except Exception:
                pass  # Diagnostics must not replace the application's exception/result.
            finally:
                if context_token is not None:
                    _context.reset(context_token)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if _recorder is None:
                    return await fn(*args, **kwargs)
                recorder, token, context_token = begin(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as error:
                    finish(recorder, token, context_token, type(error).__name__, None)
                    raise
                finish(recorder, token, context_token, None, result)
                return result
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            if _recorder is None:
                return fn(*args, **kwargs)
            recorder, token, context_token = begin(args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as error:
                finish(recorder, token, context_token, type(error).__name__, None)
                raise
            finish(recorder, token, context_token, None, result)
            return result
        return sync_wrapper
    return decorate


def diagnostic_code(code):
    """Name trusted host node bytecode without changing source/line numbers/future flags."""
    if _recorder is None:
        return code
    context = _context.get()
    if not context or not context.get("node"):
        return code
    filename = f"<flocks-node:{context.get('workflow', '')}/{context['node']}>"
    # repl_runtime has `from __future__ import annotations`; exec(str) inherits it.
    return compile(code, filename, "exec", flags=__future__.annotations.compiler_flag, dont_inherit=True)


def watch_resource(kind, value):
    recorder = _recorder
    if recorder is not None:
        try:
            recorder.watch(kind, value, _context.get() or {})
        except Exception:
            pass


def child_started(pid):
    recorder = _recorder
    if recorder is not None:
        try:
            recorder.emit({"type": "node_child_started", "pid": pid, **(_context.get() or {})})
        except Exception:
            pass


def count_syslog(size):
    recorder = _recorder
    if recorder is not None:
        # Called on the Syslog event loop; no per-packet allocation, queueing or I/O.
        recorder.syslog_packets += 1
        recorder.syslog_bytes += size


def container_gauges():
    """Inspect only already loaded modules and bounded container metadata."""
    values = {}
    try:
        module = sys.modules.get("flocks.ingest.syslog.manager")
        manager = getattr(module, "default_manager", None)
        if manager is not None:
            values["syslog_queues"] = [{"workflow": _label(key), "size": value.qsize(),
                                        "capacity": value.maxsize}
                                       for key, value in islice(manager._queues.items(), 32)]
            values["syslog_pools"] = len(manager._worker_pools)
            values["syslog_draining_workers_sample"] = sum(len(value) for value in
                                                          islice(manager._draining_workers.values(), 32))
        module = sys.modules.get("flocks.server.routes.event")
        broadcaster = getattr(getattr(module, "EventBroadcaster", None), "_instance", None)
        if broadcaster is not None:
            values["sse_clients"] = len(broadcaster._clients)
            values["sse_queue_sizes_sample"] = [item.qsize() for item in islice(broadcaster._clients, 64)]
        module = sys.modules.get("flocks.project.instance")
        instance = getattr(module, "Instance", None)
        if instance is not None:
            values["instance_cache"] = len(instance._cache)
        manager = getattr(module, "_state_manager", None)
        if manager is not None:
            values["instance_state_directories"] = len(manager._states)
        module = sys.modules.get("flocks.workflow.store")
        connection = getattr(getattr(module, "WorkflowStore", None), "_conn", None)
        tx = getattr(connection, "_tx", None)
        if tx is not None and hasattr(tx, "qsize"):
            values["workflow_sqlite_pending"] = tx.qsize()
        # WeakSet length is O(1). This is NOT a count of unfinished tasks.
        values["asyncio_live_task_objects"] = len(asyncio.tasks._all_tasks)
        for module_name, class_name, field, label in (
            ("flocks.provider.provider", "Provider", "_providers", "providers"),
            ("flocks.provider.provider", "Provider", "_models", "provider_models"),
            ("flocks.utils.log", "Log", "_loggers", "cached_loggers"),
            ("flocks.session.prompt", "SessionPrompt", "_message_token_cache", "message_token_cache"),
        ):
            owner = getattr(sys.modules.get(module_name), class_name, None)
            value = getattr(owner, field, None)
            if value is not None:
                values[label] = len(value)
    except Exception as error:
        values["partial_error"] = type(error).__name__
    return values


def thread_frames():
    """Code locations only; no source lines, arguments, local values or exceptions."""
    if threading.active_count() > 256:
        return {"skipped": "over_256_python_threads"}
    frames = sys._current_frames()
    rows = []
    try:
        for ident, frame in islice(frames.items(), 64):
            locations = []
            for _ in range(12):
                if frame is None:
                    break
                locations.append({"file": frame.f_code.co_filename[-384:],
                                  "function": frame.f_code.co_name[:96], "line": frame.f_lineno})
                frame = frame.f_back
            rows.append({"python_ident": ident, "locations": locations})
        return {"threads": rows, "truncated": len(frames) > 64}
    finally:
        frames.clear()
        frame = None


class Recorder:
    def __init__(self, directory, options):
        self.directory = Path(directory)
        self.options = options
        self.journal = Journal(self.directory / "runtime.jsonl")
        self.allocations = Journal(self.directory / "allocations.jsonl")
        self.events = queue.Queue(maxsize=1024)
        self.lock = threading.Lock()
        self.active = {}
        self.runtime_scopes = {}
        self.resources = {}
        self.resource_registrations = {}
        self.resource_overflow = 0
        self.completed_ids = deque()
        self.completed_set = set()
        self.stats = {}
        self.sequence = 0
        self.dropped = 0
        self.untracked = 0
        self.syslog_packets = 0
        self.syslog_bytes = 0
        self.stopping = threading.Event()
        self.started = time.monotonic()
        self.heartbeat = self.started
        self.heartbeat_task = None
        self.thread = None
        self.child = None
        self.process_start_ticks = None
        self.gc_map = None
        self.gc_sequence = 0
        self.gc_totals = [0, 0, 0]
        self.trace = None
        self.trace_owned = False
        self.trace_started = 0
        self.trace_count = 0
        self.trace_previous = {}
        self.trace_requested = None
        self.trace_previous_complete = True
        self.last_frames = 0
        self.trace_windows = 0
        self.last_trace_stop = self.started
        self.shared_loop_heartbeat = None
        self.shared_loop_pending = False
        self.last_snapshot_at = 0
        self.last_snapshot_bytes = 0

    def emit(self, record):
        try:
            self.events.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def begin(self, metadata):
        now = time.monotonic()
        # Lock covers only capped dictionaries, never callbacks, disk or snapshots.
        with self.lock:
            if self.stopping.is_set():
                return None
            key = "/".join(metadata.get(name, "") for name in ("kind", "workflow", "node", "category", "rpc_kind"))
            if key not in self.stats and len(self.stats) >= MAX_KEYS:
                key = "overflow"
            stat = self.stats.setdefault(key, {"started": 0, "finished": 0, "errors": 0, "seconds": 0.0})
            stat["started"] += 1
            self.sequence += 1
            token = self.sequence
            if len(self.active) >= MAX_ACTIVE:
                self.untracked += 1
                # Still return a scalar-only token so aggregate completions remain correct.
                return (token, key, now, False)
            self.active[token] = {**metadata, "id": token, "started": now}
            if metadata["kind"] == "runtime":
                self.runtime_scopes.setdefault(metadata["runtime_id"], {})[token] = {
                    key: self.active[token][key] for key in ("id", "workflow", "node", "run_id", "runtime_id")
                    if key in self.active[token]}
        if metadata["kind"] not in {"log_write", "db.encode", "db.decode"}:
            self.emit({"type": "operation_start", **metadata, "id": token, "monotonic": now,
                       "process_traced_bytes": self.trace.get_traced_memory()[0] if self.trace_owned else None})
        return (token, key, now, True)

    def finish(self, token, error_type, result):
        index, key, started, tracked = token
        elapsed = max(0, time.monotonic() - started)
        with self.lock:
            if self.stopping.is_set():
                return
            metadata = self.active.pop(index, {}) if tracked else {}
            self.completed_ids.append(index)
            self.completed_set.add(index)
            if len(self.completed_ids) > 2048:
                self.completed_set.discard(self.completed_ids.popleft())
            if metadata.get("kind") == "runtime":
                scopes = self.runtime_scopes.get(metadata["runtime_id"], {})
                scopes.pop(index, None)
                if not scopes:
                    self.runtime_scopes.pop(metadata["runtime_id"], None)
            stat = self.stats.get(key)
            if stat is not None:
                stat["finished"] += 1
                stat["errors"] += int(error_type is not None)
                stat["seconds"] += elapsed
                stat["max_seconds"] = max(stat.get("max_seconds", 0), elapsed)
                stat["max_output_shallow_bytes"] = max(stat.get("max_output_shallow_bytes", 0), result.get("shallow_bytes", 0))
                stat["max_input_shallow_bytes"] = max(stat.get("max_input_shallow_bytes", 0), metadata.get("input", {}).get("shallow_bytes", 0))
        if tracked and metadata.get("kind") not in {"log_write", "db.encode", "db.decode"}:
            self.emit({"type": "operation_end", **metadata, "id": index, "seconds": elapsed,
                       "error_type": error_type, "output": result,
                       "process_traced_bytes": self.trace.get_traced_memory()[0] if self.trace_owned else None})

    def runtime_parent(self, runtime_id):
        with self.lock:
            scopes = self.runtime_scopes.get(runtime_id, {})
            # Do not invent ownership if a runtime is unexpectedly used concurrently.
            return dict(next(iter(scopes.values()))) if len(scopes) == 1 else None

    def watch(self, kind, value, context):
        with self.lock:
            if kind not in self.resource_registrations and len(self.resource_registrations) >= 16:
                kind = "other"
            self.resource_registrations[kind] = self.resource_registrations.get(kind, 0) + 1
            if len(self.resources) < 128:
                self.resources[(kind, id(value))] = (weakref.ref(value), dict(context))
            else:
                self.resource_overflow += 1

    def resource_sample(self):
        rows = []
        with self.lock:
            entries = list(self.resources.items())
        for key, (reference, context) in entries:
            value = reference()
            if value is None:
                with self.lock:
                    self.resources.pop(key, None)
                continue
            row = {"resource_kind": key[0], **context}
            try:
                if key[0].endswith("budget"):
                    row.update(used_bytes=value._used, limit_bytes=value._limit)
                elif key[0] == "rpc_queue":
                    row.update(size=value.qsize(), capacity=value.maxsize)
                elif key[0] == "runtime":
                    scope = getattr(value, "globals", None)
                    if type(scope) is dict:
                        row["globals_shape"] = _shape(scope)
                elif key[0] == "llm_response_stream":
                    row.update(received_bytes=value._received_bytes, max_bytes=value._max_bytes)
                elif key[0] == "sqlite_connection":
                    row["connection_open"] = getattr(value, "_connection", None) is not None
                    tx = getattr(value, "_tx", None)
                    if tx is not None and hasattr(tx, "qsize"):
                        row["pending"] = tx.qsize()
            except Exception as error:
                row["partial_error"] = type(error).__name__
            rows.append(row)
        return rows

    def shared_loop_tick(self):
        self.shared_loop_heartbeat = time.monotonic()
        self.shared_loop_pending = False

    def gc_callback(self, phase, info):
        # Fixed-size shared page: observer can see a collection start even if GIL
        # becomes unavailable during the collection. No flush, log or heap scan.
        try:
            generation = info["generation"]
            if phase == "stop" and generation in (0, 1, 2):
                self.gc_totals[generation] += 1
            self.gc_sequence += 2
            struct.pack_into("<Q", self.gc_map, 0, self.gc_sequence - 1)
            GC_RECORD.pack_into(self.gc_map, 0, self.gc_sequence - 1, 1 if phase == "start" else 0,
                                generation, time.monotonic(), threading.get_native_id(),
                                info.get("collected", 0), info.get("uncollectable", 0))
            struct.pack_into("<Q", self.gc_map, 0, self.gc_sequence)
        except Exception:
            pass

    async def pulse(self):
        while not self.stopping.is_set():
            self.heartbeat = time.monotonic()
            await asyncio.sleep(1)

    def launch(self):
        fd = os.open(self.directory / "gc-state.bin", os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.ftruncate(fd, 4096)
            self.gc_map = mmap.mmap(fd, 4096)
        finally:
            os.close(fd)
        gc.callbacks.append(self.gc_callback)
        self.heartbeat_task = asyncio.get_running_loop().create_task(self.pulse(), name="memory-diagnostic-pulse")
        metadata = {"type": "capture_start", "pid": os.getpid(), "python": sys.version,
                    "executable": sys.executable, "options": self.options, "capture_directory": str(self.directory),
                    "platform": sys.platform, "instrumentation": "memory-v2",
                    "limits": {"event_queue": 1024, "active": MAX_ACTIVE, "keys": MAX_KEYS + 1,
                               "trace_metadata_bytes": TRACE_METADATA_LIMIT,
                               "trace_heap_bytes": TRACE_HEAP_LIMIT, "trace_rss_kib": TRACE_RSS_LIMIT_KIB}}
        self.journal.write(metadata)
        if sys.platform == "linux":
            sample = process_sample(os.getpid())
            self.process_start_ticks = sample["start_ticks"]
            self.child = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("sidecar.py")), "--pid", str(os.getpid()),
                 "--start-ticks", str(sample["start_ticks"]), "--directory", str(self.directory),
                 "--seconds", str(self.options["duration_s"])], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        self.thread = threading.Thread(target=self.run, name="flocks-memory-observer", daemon=True)
        self.thread.start()

    def start_trace(self):
        import tracemalloc
        self.trace = tracemalloc
        self.trace_windows += 1
        self.last_trace_stop = time.monotonic()
        if self.trace.is_tracing():
            self.allocations.write({"type": "trace_skipped", "reason": "owned_by_other_profiler"})
            return
        try:
            sample = process_sample(os.getpid())
            if sample.get("VmRSS", 0) + sample.get("VmSwap", 0) >= TRACE_RSS_LIMIT_KIB:
                self.allocations.write({"type": "trace_skipped", "reason": "process_already_over_4GiB"})
                return
        except (OSError, ValueError, IndexError):
            pass
        # Tracing begins at backend lifespan, not before Python module imports.
        self.trace.start(self.options.get("trace_frames", 1))
        self.trace_owned = True
        self.trace_started = time.monotonic()
        self.trace_count = 0
        self.trace_previous = {}
        self.trace_previous_complete = True
        self.last_snapshot_at = 0
        self.last_snapshot_bytes = 0
        self.allocations.write({"type": "trace_start", "frames": self.options.get("trace_frames", 1), "window": self.trace_windows})

    def stop_trace(self, reason):
        if self.trace_owned:
            self.trace.stop()
            self.trace_owned = False
            self.last_trace_stop = time.monotonic()
            self.trace_previous.clear()
            self.allocations.write({"type": "trace_stop", "reason": reason})

    def trace_step(self, process):
        if not self.trace_owned:
            return
        try:
            process = process_sample(os.getpid())
        except (OSError, ValueError, IndexError):
            pass
        current, peak = self.trace.get_traced_memory()
        metadata = self.trace.get_tracemalloc_memory()
        elapsed = time.monotonic() - self.trace_started
        reason = None
        if metadata >= TRACE_METADATA_LIMIT:
            reason = "metadata_limit"
        elif current >= TRACE_HEAP_LIMIT:
            reason = "traced_heap_limit"
        elif process.get("VmRSS", 0) + process.get("VmSwap", 0) >= TRACE_RSS_LIMIT_KIB:
            reason = "resident_plus_swap_limit"
        elif elapsed >= self.options["trace_seconds"] or self.trace_count >= 30:
            reason = "trace_time_or_snapshot_limit"
        if reason:
            with self.lock:
                contexts = [{key: row[key] for key in ("id", "parent_id", "kind", "workflow", "node", "native_tid")
                             if key in row} for row in islice(self.active.values(), 64)]
            self.allocations.write({"type": "trace_limit", "current": current, "peak": peak,
                                    "metadata": metadata, "reason": reason, "active_contexts_sample": contexts})
            self.stop_trace(reason)
            return
        # 10 s initial interval captures early growth, then 30 s. No huge final snapshot.
        due = 0 if self.trace_count == 0 else 10 if self.trace_count == 1 else 10 + (self.trace_count - 1) * 30
        fast_growth = current - self.last_snapshot_bytes >= 16 * 1024 * 1024 and time.monotonic() - self.last_snapshot_at >= 2
        if elapsed < due and not fast_growth:
            return
        started = time.monotonic()
        snapshot = self.trace.take_snapshot()
        statistics = snapshot.statistics("traceback")
        del snapshot
        # Only bounded scalar summaries retained between snapshots, not Snapshot objects.
        selected = statistics[:4096]
        current_lines = {tuple((frame.filename, frame.lineno) for frame in row.traceback): (row.size, row.count)
                         for row in selected}
        rows = []
        for stack, (size, count) in current_lines.items():
            filename, line = stack[-1]
            previous = self.trace_previous.get(stack)
            rows.append({"file": filename[-384:], "line": line, "bytes": size, "count": count,
                         "stack": [{"file": name[-384:], "line": number} for name, number in stack],
                         "delta_bytes": size - previous[0] if previous is not None else
                         size if self.trace_previous_complete else None})
        self.allocations.write({"type": "allocation_snapshot", "number": self.trace_count,
                                "elapsed_s": elapsed, "snapshot_seconds": time.monotonic() - started,
                                "window": self.trace_windows,
                                "current": current, "peak": peak, "metadata": metadata,
                                "top": sorted(rows, key=lambda row: row["bytes"], reverse=True)[:40],
                                "growth": sorted((row for row in rows if row["delta_bytes"] is not None),
                                                 key=lambda row: row["delta_bytes"], reverse=True)[:40],
                                "summary_truncated": len(statistics) > 4096,
                                "note": "delta only for lines present in previous bounded summary; not exclusive per-operation memory"})
        self.trace_previous = current_lines
        self.trace_previous_complete = len(statistics) <= 4096
        self.last_snapshot_at = time.monotonic()
        self.last_snapshot_bytes = current
        self.trace_count += 1
        del statistics, selected, rows
        if time.monotonic() - started > 2:
            self.stop_trace("snapshot_took_over_2s")

    def sample(self):
        now = time.monotonic()
        try:
            process = process_sample(os.getpid())
        except (OSError, ValueError, IndexError):
            process = {}
        with self.lock:
            active = [{**value, "age_s": now - value["started"],
                       "parent_state": "active" if value.get("parent_id") in self.active else
                       "completed_recently" if value.get("parent_id") in self.completed_set else "unknown"}
                      for value in self.active.values()]
            stats = {key: dict(value) for key, value in self.stats.items()}
        traced = self.trace.get_traced_memory() if self.trace_owned else None
        module = sys.modules.get("flocks.workflow._async_runtime")
        shared_loop = getattr(module, "_loop", None)
        if shared_loop is not None and not shared_loop.is_closed() and not self.shared_loop_pending:
            try:
                self.shared_loop_pending = True
                shared_loop.call_soon_threadsafe(self.shared_loop_tick)
            except RuntimeError:
                self.shared_loop_pending = False
        self.journal.write({"type": "runtime_sample", "monotonic": now, "process": process,
                            "event_loop_heartbeat_age_s": now - self.heartbeat,
                            "python_threads": threading.active_count(), "gc_counts": gc.get_count(),
                            "gc_stats": gc.get_stats(), "gc_callbacks_stop_total": list(self.gc_totals),
                            "active_count": len(active), "gauges": container_gauges(),
                            "resource_registrations": dict(self.resource_registrations),
                            "resource_watch_overflow": self.resource_overflow,
                            "shared_llm_loop_heartbeat_age_s": now - self.shared_loop_heartbeat if self.shared_loop_heartbeat else None,
                            "shared_llm_loop_probe_pending": self.shared_loop_pending,
                            "syslog_packets": self.syslog_packets, "syslog_bytes": self.syslog_bytes,
                            "dropped_events": self.dropped, "untracked_operations": self.untracked,
                            "trace_current_peak": traced,
                            "observer_returncode": self.child.poll() if self.child is not None else None})
        self.journal.write({"type": "operation_summary", "monotonic": now, "operations": stats})
        for offset in range(0, len(active), 32):
            self.journal.write({"type": "active_operations", "monotonic": now, "offset": offset,
                                "active": active[offset:offset + 32]})
        resources = self.resource_sample()
        for offset in range(0, len(resources), 16):
            self.journal.write({"type": "resource_sample", "monotonic": now, "offset": offset,
                                "resources": resources[offset:offset + 16]})
        if now - self.last_frames >= 30 and process.get("VmRSS", 0) + process.get("VmSwap", 0) < TRACE_RSS_LIMIT_KIB:
            self.journal.write({"type": "thread_frames", **thread_frames()})
            self.last_frames = now
        return process

    def run(self):
        global _recorder
        reason = "time_limit"
        next_sample = 0
        process = {}
        try:
            if self.options["allocations"]:
                self.start_trace()
            while not self.stopping.wait(1):
                now = time.monotonic()
                if now - self.started >= self.options["duration_s"]:
                    break
                if (self.directory / "STOP").exists():
                    reason = "operator_stop"
                    break
                request_path = self.directory / "TRACE"
                if request_path.exists():
                    requested = request_path.stat().st_mtime_ns
                    if requested != self.trace_requested:
                        self.trace_requested = requested
                        if not self.trace_owned:
                            self.start_trace()
                batch = []
                for _ in range(256):
                    try:
                        batch.append(self.events.get_nowait())
                    except queue.Empty:
                        break
                self.journal.write_many(batch)
                if now >= next_sample:
                    process = self.sample()
                    next_sample = now + 5
                self.trace_step(process)
                if (self.options.get("repeat_trace") and self.options["allocations"] and not self.trace_owned
                        and self.trace_windows < 12 and now - self.last_trace_stop >= 120):
                    self.start_trace()
            if self.stopping.is_set():
                reason = "service_shutdown"
        except Exception as error:
            reason = "diagnostic_error:" + type(error).__name__
        finally:
            if _recorder is self:
                _recorder = None
            self.stopping.set()
            # Remove GC callback before closing its shared map. Never change GC policy.
            if self.gc_callback in gc.callbacks:
                gc.callbacks.remove(self.gc_callback)
            if self.gc_map is not None:
                self.gc_map.close()
            try:
                self.stop_trace(reason)
                self.journal.write({"type": "capture_stop", "reason": reason, "dropped_events": self.dropped})
            except Exception:
                pass
            with self.lock:
                self.active.clear()
                self.stats.clear()
                self.runtime_scopes.clear()
                self.resources.clear()
                self.completed_ids.clear()
                self.completed_set.clear()
            # Independent observer intentionally stays alive until target exit/operator stop.

    def stop(self):
        self.stopping.set()
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
        # No thread join on event loop, especially during shutdown under memory pressure.


def start():
    global _recorder
    if _recorder is not None:
        return
    recorder = None
    try:
        path = config_path()
        if not path.is_file() or path.stat().st_size > 8192:
            return
        config = json.loads(read_small(path, 8192))
        if config.get("enabled") is not True:
            return
        options = {"allocations": config.get("allocations") is True,
                   "trace_frames": min(3, max(1, int(config.get("trace_frames", 3)))),
                   "repeat_trace": config.get("repeat_trace") is True,
                   "duration_s": min(21600, max(60, int(config.get("duration_s", 10800)))),
                   "trace_seconds": min(900, max(10, int(config.get("trace_seconds", 600))))}
        now = datetime.now()
        directory = root_dir() / "workspace" / "outputs" / now.strftime("%Y-%m-%d") / "memory-diagnostics" / (now.strftime("%H%M%S-%f") + f"-{os.getpid()}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        recorder = Recorder(directory, options)
        _recorder = recorder
        recorder.launch()
        latest = root_dir() / "memory-diagnostics-latest.json"
        temporary = latest.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps({"pid": os.getpid(), "start_ticks": recorder.process_start_ticks,
                                         "directory": str(directory)}), encoding="utf-8")
        temporary.replace(latest)
    except Exception as error:
        _recorder = None
        if recorder is not None:
            recorder.stop()
            # launch can fail before the cleanup thread exists.
            if recorder.thread is None:
                if recorder.gc_callback in gc.callbacks:
                    gc.callbacks.remove(recorder.gc_callback)
                if recorder.gc_map is not None:
                    recorder.gc_map.close()
        print(f"[memory-diagnostics] disabled after setup error: {type(error).__name__}", file=sys.stderr)


def stop():
    global _recorder
    recorder, _recorder = _recorder, None
    if recorder is not None:
        recorder.stop()


def diagnostic_lifespan(factory):
    @functools.wraps(factory)
    @asynccontextmanager
    async def wrapped(*args, **kwargs):
        start()
        try:
            async with factory(*args, **kwargs) as value:
                yield value
        finally:
            stop()
    return wrapped
