"""Opt-in memory investigation, not a memory-leak fix.

Hot-path hooks retain only scalar metadata, never inputs/results/tracebacks. They
do no file I/O. No heap traversal, forced GC, GC disabling, or request buffering.
Allocation snapshots are explicitly optional and have soft safety thresholds;
CPython snapshot creation itself cannot be preempted by another Python thread.
"""

import asyncio
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
from contextlib import asynccontextmanager
from datetime import datetime
from itertools import islice
from pathlib import Path

from .common import GC_RECORD, Journal, config_path, process_sample, read_small, root_dir


_recorder = None
MAX_ACTIVE = 256
MAX_KEYS = 256
TRACE_METADATA_LIMIT = 16 * 1024 * 1024
TRACE_HEAP_LIMIT = 512 * 1024 * 1024
TRACE_RSS_LIMIT_KIB = 4 * 1024 * 1024


def _label(value):
    return value[:96] if type(value) is str else ""


def _shape(value):
    """Constant-time metadata only; do not invoke user-defined __len__/__sizeof__."""
    if type(value) in (str, bytes, bytearray, list, tuple, dict):
        return {"type": type(value).__name__, "length": len(value), "shallow_bytes": sys.getsizeof(value)}
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
                return None, None
            try:
                metadata = _metadata(kind, args, kwargs)
                return recorder, recorder.begin(metadata) if metadata is not None else None
            except Exception:
                return None, None

        def finish(recorder, token, error_type, result):
            if recorder is not None and token is not None:
                try:
                    recorder.finish(token, error_type, _shape(result))
                except Exception:
                    pass  # Diagnostics must not replace the application's exception/result.

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if _recorder is None:
                    return await fn(*args, **kwargs)
                recorder, token = begin(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as error:
                    finish(recorder, token, type(error).__name__, None)
                    raise
                finish(recorder, token, None, result)
                return result
            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            if _recorder is None:
                return fn(*args, **kwargs)
            recorder, token = begin(args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as error:
                finish(recorder, token, type(error).__name__, None)
                raise
            finish(recorder, token, None, result)
            return result
        return sync_wrapper
    return decorate


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
        self.emit({"type": "operation_start", **metadata, "id": token, "monotonic": now})
        return (token, key, now, True)

    def finish(self, token, error_type, result):
        index, key, started, tracked = token
        elapsed = max(0, time.monotonic() - started)
        with self.lock:
            if self.stopping.is_set():
                return
            metadata = self.active.pop(index, {}) if tracked else {}
            stat = self.stats.get(key)
            if stat is not None:
                stat["finished"] += 1
                stat["errors"] += int(error_type is not None)
                stat["seconds"] += elapsed
        if tracked:
            self.emit({"type": "operation_end", **metadata, "id": index, "seconds": elapsed,
                       "error_type": error_type, "output": result})

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
                    "platform": sys.platform, "instrumentation": "memory-v1",
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
        self.trace.start(1)
        self.trace_owned = True
        self.trace_started = time.monotonic()
        self.trace_count = 0
        self.trace_previous = {}
        self.trace_previous_complete = True
        self.allocations.write({"type": "trace_start", "frames": 1})

    def stop_trace(self, reason):
        if self.trace_owned:
            self.trace.stop()
            self.trace_owned = False
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
            self.allocations.write({"type": "trace_limit", "current": current, "peak": peak,
                                    "metadata": metadata, "reason": reason})
            self.stop_trace(reason)
            return
        # 10 s initial interval captures early growth, then 30 s. No huge final snapshot.
        due = 0 if self.trace_count == 0 else 10 if self.trace_count == 1 else 10 + (self.trace_count - 1) * 30
        if elapsed < due:
            return
        started = time.monotonic()
        snapshot = self.trace.take_snapshot()
        statistics = snapshot.statistics("lineno")
        del snapshot
        # Only bounded scalar summaries retained between snapshots, not Snapshot objects.
        selected = statistics[:4096]
        current_lines = {(row.traceback[0].filename, row.traceback[0].lineno): (row.size, row.count)
                         for row in selected}
        rows = []
        for (filename, line), (size, count) in current_lines.items():
            previous = self.trace_previous.get((filename, line))
            rows.append({"file": filename[-384:], "line": line, "bytes": size, "count": count,
                         "delta_bytes": size - previous[0] if previous is not None else
                         size if self.trace_previous_complete else None})
        self.allocations.write({"type": "allocation_snapshot", "number": self.trace_count,
                                "elapsed_s": elapsed, "snapshot_seconds": time.monotonic() - started,
                                "current": current, "peak": peak, "metadata": metadata,
                                "top": sorted(rows, key=lambda row: row["bytes"], reverse=True)[:40],
                                "growth": sorted((row for row in rows if row["delta_bytes"] is not None),
                                                 key=lambda row: row["delta_bytes"], reverse=True)[:40],
                                "summary_truncated": len(statistics) > 4096,
                                "note": "delta only for lines present in previous bounded summary; not exclusive per-operation memory"})
        self.trace_previous = current_lines
        self.trace_previous_complete = len(statistics) <= 4096
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
            active = [{**value, "age_s": now - value["started"]} for value in self.active.values()]
            stats = {key: dict(value) for key, value in self.stats.items()}
        traced = self.trace.get_traced_memory() if self.trace_owned else None
        self.journal.write({"type": "runtime_sample", "monotonic": now, "process": process,
                            "event_loop_heartbeat_age_s": now - self.heartbeat,
                            "python_threads": threading.active_count(), "gc_counts": gc.get_count(),
                            "gc_stats": gc.get_stats(), "gc_callbacks_stop_total": list(self.gc_totals),
                            "active": active, "operations": stats, "gauges": container_gauges(),
                            "syslog_packets": self.syslog_packets, "syslog_bytes": self.syslog_bytes,
                            "dropped_events": self.dropped, "untracked_operations": self.untracked,
                            "trace_current_peak": traced,
                            "observer_returncode": self.child.poll() if self.child is not None else None})
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
