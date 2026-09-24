"""Bounded, content-free timings for Hub/scene loading (no behavioral changes)."""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from flocks.utils.log import Log

log = Log.create(service="hub-diagnostics")
_current: ContextVar[Trace | None] = ContextVar("hub_trace", default=None)
_parent: ContextVar[int | None] = ContextVar("hub_span", default=None)
_PATHS = {"/api/hub/catalog", "/api/hub/categories", "/api/hub/scene-suites"}
HEARTBEAT_SECONDS = 10
MAX_STAGE_EVENTS = 100


class Trace:
    def __init__(self, endpoint: str, client_id: str = ""):
        self.id = uuid.uuid4().hex
        self.endpoint = endpoint
        self.client_id = client_id if re.fullmatch(r"[a-f0-9-]{16,40}", client_id) else ""
        self.detailed = os.getenv("FLOCKS_HUB_DIAGNOSTICS", "").lower() in {"1", "true", "yes"}
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.sequence = 0
        self.active: dict[int, dict] = {}
        self.stats: dict[str, dict] = {}
        self.handled_errors: dict[tuple, int] = {}
        self.stage_events = 0
        self.suppressed = 0
        self.finished = False

    def emit(self, event: str, **fields):
        # Never pass exception messages, request headers/query strings, configs,
        # payloads or manifests here. Logging failures must not break requests.
        try:
            log.info("hub.diag." + event, {
                "trace_id": self.id, "client_id": self.client_id,
                "endpoint": self.endpoint, "pid": os.getpid(),
                "elapsed_ms": round((time.monotonic() - self.started) * 1000, 2),
                "request_finished": self.finished, **fields,
            })
        except Exception:
            pass

    def stage_event(self, event: str, **fields):
        with self.lock:
            if self.stage_events >= MAX_STAGE_EVENTS:
                self.suppressed += 1
                return
            self.stage_events += 1
        self.emit(event, **fields)

    def progress(self):
        now = time.monotonic()
        with self.lock:
            active = [dict(v, age_ms=round((now - v["started"]) * 1000, 2))
                      for v in list(self.active.values())[-12:]]
            for item in active:
                item.pop("started")
            completed = sum(s["count"] for s in self.stats.values())
        self.emit("waiting", active=active, completed_spans=completed)

    def summary(self):
        with self.lock:
            slowest = sorted(self.stats.items(), key=lambda item: item[1]["total_ms"], reverse=True)[:24]
            return {"stages": [dict(stage=k, **v) for k, v in slowest],
                    "cache": dict(self.stats.get("catalog.cache_lookup", {})),
                    "handled_errors": [dict(stage=k[0], error_type=k[1], errno=k[2], count=v)
                                       for k, v in self.handled_errors.items()],
                    "suppressed_events": self.suppressed, "active_spans": len(self.active)}


@contextmanager
def span(name: str, *, detail: bool = False, target: str | None = None, build_id: str | None = None):
    trace = _current.get()
    if trace is None:
        yield {}
        return
    started = time.monotonic()
    parent = _parent.get()
    with trace.lock:
        trace.sequence += 1
        span_id = trace.sequence
        trace.active[span_id] = {"span_id": span_id, "parent_id": parent,
                                 "stage": name, "started": started}
        if build_id is not None:
            trace.active[span_id]['build_id'] = build_id
        if target is not None and trace.detailed:
            trace.active[span_id]["target"] = target[:600]
    token = _parent.set(span_id)
    fields: dict = {}
    if build_id is not None:
        fields['build_id'] = build_id
    if target is not None and trace.detailed:
        fields["target"] = target[:600]
    if trace.detailed and not detail:
        trace.stage_event("stage.begin", span_id=span_id, parent_id=parent, stage=name)
    outcome = "ok"
    try:
        yield fields
    except BaseException as exc:
        outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        fields["error_type"] = type(exc).__name__
        raise
    finally:
        duration = round((time.monotonic() - started) * 1000, 2)
        _parent.reset(token)
        with trace.lock:
            trace.active.pop(span_id, None)
            stats = trace.stats.setdefault(name, {"count": 0, "total_ms": 0, "max_ms": 0, "errors": 0})
            stats["count"] += 1
            stats["total_ms"] = round(stats["total_ms"] + duration, 2)
            stats["max_ms"] = max(stats["max_ms"], duration)
            stats["errors"] += int(outcome != "ok")
            if "cache_hit" in fields:
                stats["cache_hit"] = fields["cache_hit"]
                stats["entry_count"] = fields["entry_count"]
                stats["signature_count"] = fields["signature_count"]
        if (trace.detailed and not detail) or duration >= 500 or outcome != "ok":
            trace.stage_event("stage.end", span_id=span_id, parent_id=parent, stage=name,
                              duration_ms=duration, outcome=outcome, **fields)


def timed(name: str, *, detail: bool = False):
    """Synchronous functions only; preserves signatures via functools.wraps."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            trace = _current.get()
            if trace is None:
                return fn(*args, **kwargs)
            target = next((str(arg) for arg in args if isinstance(arg, Path)), None) if trace.detailed else None
            with span(name, detail=detail, target=target) as fields:
                result = fn(*args, **kwargs)
                if isinstance(result, (list, tuple, dict)):
                    fields["result_count"] = len(result)
                return result
        return wrapped
    return decorate


@contextmanager
def catalog_build_trace(build_id: str, signature_count: int):
    """Cold builders may originate outside a Hub HTTP request (e.g. nav).

    Always identify the builder, so waits can be linked to its phases even
    when detailed logging is off. Never include the signature's path content.
    """
    trace = _current.get()
    token = None
    if trace is None:
        trace = Trace('catalog-build')
        token = _current.set(trace)
    started = time.monotonic()
    trace.emit('catalog.build.begin', build_id=build_id, signature_count=signature_count)
    outcome, error_type = 'ok', None
    try:
        yield
    except BaseException as exc:
        outcome, error_type = 'error', type(exc).__name__
        raise
    finally:
        trace.emit('catalog.build.end', build_id=build_id, outcome=outcome, error_type=error_type,
                   duration_ms=round((time.monotonic() - started) * 1000, 2), **trace.summary())
        if token is not None:
            _current.reset(token)


def record_handled_error(stage: str, error: Exception):
    """Observe existing fallback paths without changing their behavior."""
    trace = _current.get()
    if trace is None:
        return
    key = (stage, type(error).__name__, error.errno if isinstance(error, OSError) else None)
    with trace.lock:
        if key not in trace.handled_errors and len(trace.handled_errors) >= 16:
            return
        trace.handled_errors[key] = trace.handled_errors.get(key, 0) + 1


def path_snapshot():
    """Opt-in, fixed-size root metadata. No recursive diagnostic scans."""
    trace = _current.get()
    if trace is None or not trace.detailed:
        return
    from flocks.config.config import Config
    from flocks.hub import local
    from flocks.hub.paths import get_bundled_hub_root

    try:
        _collect_paths(trace, Config, local, get_bundled_hub_root)
    except Exception as exc:
        trace.emit("path.error", role="collect", error_type=type(exc).__name__)


def _collect_paths(trace, Config, local, get_bundled_hub_root):
    with span("paths.collect"):
        roots = [("user", Path.home() / ".flocks"), ("data", Config.get_data_path()),
                 ("project_plugins", local._project_plugins_root()),
                 ("bundled", get_bundled_hub_root())]
        for role, root in roots:
            # A begin record is emitted before resolve/stat, including the role.
            try:
                with span("paths.resolve." + role):
                    trace.emit("path", role=role, configured=str(root),
                               resolved=str(root.resolve()), is_symlink=root.is_symlink())
            except Exception as exc:
                trace.emit("path.error", role=role, error_type=type(exc).__name__)


async def run_in_thread(fn, *args, **kwargs):
    """Measure executor scheduling independently of synchronous work."""
    queued_at = time.monotonic()
    trace = _current.get()

    def run():
        if trace:
            trace.emit("worker.start", queue_ms=round((time.monotonic() - queued_at) * 1000, 2))
        with span("worker.execute"):
            return fn(*args, **kwargs)

    with span("worker.await"):
        return await asyncio.to_thread(run)


class HubDiagnosticsMiddleware:
    """ASGI boundary includes authentication, worker queue and response send.

    We observe disconnect only if the app consumes it. We do not steal receive
    messages or assert that a proxy/browser timeout is observable server-side.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in _PATHS or scope.get("method") != "GET":
            return await self.app(scope, receive, send)
        client_id = next((v.decode("ascii", "ignore") for k, v in scope.get("headers", [])
                          if k.lower() == b"x-flocks-hub-request-id"), "")
        trace = Trace(scope["path"], client_id)
        token = _current.set(trace)
        trace.emit("request.begin", detailed=trace.detailed)
        status_code = None
        outcome = "ok"

        async def heartbeat():
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                trace.progress()

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.disconnect":
                trace.emit("disconnect.observed")
            return message

        async def observed_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message = dict(message, headers=[*message.get("headers", []),
                               (b"x-flocks-hub-trace-id", trace.id.encode("ascii"))])
                trace.emit("response.start", status=status_code)
            await send(message)

        task = asyncio.create_task(heartbeat())
        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            trace.emit("request.exception", error_type=type(exc).__name__)
            raise
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            trace.finished = True
            trace.emit("request.end", outcome=outcome, status=status_code, **trace.summary())
            _current.reset(token)
