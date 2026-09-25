"""Content-free monitoring traces and a bounded, owner-scoped support export.

Never pass tool inputs, outputs, configs or exception messages to ``event``.
``shape`` extracts only known types/counts. Disk IO runs on one bounded daemon
queue so a slow or symlinked log volume cannot delay a monitoring operation.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import threading
import time
import uuid

from flocks import __version__
from flocks.utils.log import get_log_dir

MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3
MAX_EVENTS = 200
MAX_ERRORS = 20
HEARTBEAT_SECONDS = 30
_current = ContextVar('monitor_diagnostics', default=None)
_call = ContextVar('monitor_diagnostic_call', default=None)
_export_lock = threading.Lock()


def opaque(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:24]


def shape(value):
    kind = type(value).__name__
    fields = {'value_type': kind if kind in _TYPES else 'other'}
    if isinstance(value, (str, bytes, list, dict)):
        fields['length'] = len(value)
    if isinstance(value, str):
        prefix = value[:128].lstrip().lower()
        fields['text_kind'] = ('empty' if not value else 'html' if prefix.startswith(('<html', '<!doctype html'))
                               else 'json_like' if prefix.startswith(('{', '[')) else 'fenced' if prefix.startswith('```') else 'text')
    if isinstance(value, dict):
        code = value.get('code')
        fields['code_kind'] = ('absent' if code is None else 'zero' if type(code) in (int, str) and code in (0, '0')
                               else 'http_ok' if type(code) in (int, str) and code in (200, '200')
                               else 'xdr_success' if type(code) is str and code == 'Success' else 'other')
        for key in ('data', 'list', 'total', 'code', 'success'):
            if key in value:
                name = type(value[key]).__name__
                fields[key + '_type'] = name if name in _TYPES else 'other'
        if isinstance(value.get('data'), dict):
            data = value['data']
            present = [key for key in ('list', 'item') if key in data]
            fields['list_field'] = 'both' if len(present) == 2 else present[0] if present else 'none'
            for key in present:
                name = type(data[key]).__name__
                fields['data_' + key + '_type'] = name if name in _TYPES else 'other'
            if len(present) == 1 and isinstance(data[present[0]], list):
                fields['items'] = len(data[present[0]])
    return fields


_TYPES = {'str', 'dict', 'list', 'int', 'float', 'bool', 'bytes', 'NoneType', 'other'}
_ENUMS = {
    'event': {'trace.start', 'trace.end', 'progress', 'stage.start', 'stage.end', 'query.window',
              'tool.raw', 'tool.normalized', 'adapter.result', 'adapter.structured', 'adapter.decoded', 'adapter.failure',
              'mail.received', 'mail.result', 'mail.interpreted', 'page.validated', 'run.result', 'dispatch.result', 'background.result'},
    'stage': {'dispatch', 'run', 'session.prepare', 'query.device', 'query.events', 'query.entities',
              'correlate', 'tool.execute', 'tool.handler', 'tool.normalize', 'report.export', 'step.other',
              'disposition.confirm', 'disposition.recheck', 'automatic.mark', 'mail.configure', 'mail.send', 'mail.receive', 'mail.interpret', 'mail.resolve', 'mail.mark', 'mail.readback'},
    'action': {'list', 'get_entities', 'get_proof', 'update_status', 'other'},
    'outcome': {'ok', 'error', 'cancelled', 'completed', 'failed', 'partial', 'interrupted', 'running', 'unknown'},
    'reason': {'unavailable', 'permission', 'tool_failed', 'non_json', 'not_object', 'business_error', 'structured_output', 'ambiguous_reply', 'model_failed', 'send_unknown', 'write_unknown'},
    'mail_state': {'queued','sending','sent','send_unknown','pending','interpreted','needs_review','verified','failed','mismatch','skipped','unrelated'},
    'text_kind': {'empty', 'html', 'json_like', 'fenced', 'text'},
    'code_kind': {'absent', 'zero', 'http_ok', 'xdr_success', 'other'},
    'list_field': {'list', 'item', 'both', 'none'},
    'structured_reason': {'limit', 'unsupported', 'already_truncated', 'unavailable', 'tool_failed'},
    **{key: _TYPES for key in ('value_type', 'data_type', 'data_list_type', 'data_item_type', 'list_type', 'total_type', 'code_type', 'success_type')},
}
_NUMBERS = {'schema', 'pid', 'seq', 'elapsed_ms', 'duration_ms', 'length', 'items', 'page', 'page_size',
            'window_seconds', 'devices', 'max_pages', 'timeout_seconds', 'json_position', 'json_line',
            'json_column', 'events', 'errors', 'suppressed', 'calls', 'call', 'loop_lag_ms', 'total'}
_BOOLS = {'success', 'truncated', 'has_error', 'has_saved_output', 'cursor_present'}
_IDS = {'trace', 'owner', 'scope', 'execution', 'device', 'notice', 'reply', 'project'}


def safe_record(fields):
    """Defense in depth: only the diagnostic schema can reach disk or export."""
    result = {}
    for key, value in fields.items():
        if key in _ENUMS and isinstance(value, str) and value in _ENUMS[key]:
            result[key] = value
        elif key in _NUMBERS and type(value) in (int, float) and 0 <= value <= 10**15:
            result[key] = value
        elif key in _BOOLS and type(value) is bool:
            result[key] = value
        elif key in _IDS and isinstance(value, str) and re.fullmatch(r'[a-f0-9]{24,32}', value):
            result[key] = value
        elif key == 'error_type' and isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', value):
            result[key] = value
        elif key == 'version' and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9.+_-]{1,64}', value):
            result[key] = value
        elif key == 'timestamp' and isinstance(value, str) and re.fullmatch(r'[0-9T:.+Z-]{20,40}', value):
            result[key] = value
    return result


class _PrivateFileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        return os.fdopen(fd, 'a', encoding='utf-8')

    def handleError(self, record):
        # logging's default prints the record/traceback; use only a counter.
        raise OSError('diagnostic_write_failed')


class Sink:
    def __init__(self):
        self.pending = queue.Queue(maxsize=512)
        self.recent = deque(maxlen=1000)
        self.lock = threading.Lock()
        self.started = False
        self.dropped = self.write_errors = 0

    def submit(self, fields):
        try:
            record = safe_record(fields)
            root = get_log_dir() / 'security-monitor'
            with self.lock:
                self.recent.append(record)
                if not self.started:
                    threading.Thread(target=self._work, name='monitor-diagnostics', daemon=True).start()
                    self.started = True
            try:
                self.pending.put_nowait((root, record))
            except queue.Full:
                with self.lock:
                    self.dropped += 1
        except Exception:
            pass  # Diagnostics must not change monitoring behavior.

    def _work(self):
        handler, destination = None, None
        while True:
            root, record = self.pending.get()
            try:
                if root != destination or handler is None:
                    if handler:
                        handler.close()
                    handler = None
                    root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    handler = _PrivateFileHandler(root / 'diagnostics.jsonl', maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding='utf-8')
                    destination = root
                handler.emit(logging.LogRecord('monitor-diagnostics', logging.INFO, '', 0,
                                               json.dumps(record, ensure_ascii=True), (), None))
            except Exception:
                with self.lock:
                    self.write_errors += 1
            finally:
                self.pending.task_done()

    def snapshot(self):
        with self.lock:
            return list(self.recent), {'queue_dropped': self.dropped, 'write_errors': self.write_errors,
                                       'queued': self.pending.qsize()}


_sink = Sink()


class Trace:
    def __init__(self, owner, scope, execution):
        self.id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.ids = dict(owner=opaque(owner), scope=opaque(scope), execution=opaque(execution))
        self.sequence = self.logged = self.error_logged = self.suppressed = self.calls = 0
        self.outcome = 'unknown'

    def emit(self, name, *, failure=False, terminal=False, **fields):
        if not terminal:
            if failure and self.error_logged < MAX_ERRORS:
                self.error_logged += 1
            elif self.logged < MAX_EVENTS:
                self.logged += 1
            else:
                self.suppressed += 1
                return
        self.sequence += 1
        try:
            _sink.submit(dict(schema=1, version=__version__, pid=os.getpid(), trace=self.id, seq=self.sequence,
                              timestamp=datetime.now(timezone.utc).isoformat(), **self.ids, event=name,
                              elapsed_ms=round((time.monotonic() - self.started) * 1000, 2), call=_call.get(), **fields))
        except Exception:
            pass


def event(name, *, failure=False, **fields):
    trace = _current.get()
    if trace:
        try:
            trace.emit(name, failure=failure, **fields)
        except Exception:
            pass


def tool_result(name, value):
    # Most registry calls are unrelated to monitoring; do no output inspection
    # at all on those calls. Unusual third-party outputs cannot break a tool.
    if _current.get() is None:
        return
    try:
        event(name, success=value.success, truncated=bool(value.truncated), has_error=bool(value.error),
              has_saved_output=bool((value.metadata or {}).get('output_path')), **shape(value.output))
    except Exception:
        pass


@contextmanager
def span(stage, **fields):
    started = time.monotonic()
    event('stage.start', stage=stage, **fields)
    outcome, error = 'ok', None
    try:
        yield
    except BaseException as exc:
        outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'error'
        error = type(exc).__name__
        raise
    finally:
        event('stage.end', failure=outcome != 'ok', stage=stage, outcome=outcome, error_type=error,
              duration_ms=round((time.monotonic() - started) * 1000, 2), **fields)


@contextmanager
def tool_call():
    trace = _current.get()
    if not trace:
        yield
        return
    trace.calls += 1
    token = _call.set(trace.calls)
    try:
        yield
    finally:
        _call.reset(token)


@asynccontextmanager
async def trace_scope(owner, scope, execution):
    trace = Trace(owner, scope, execution)
    token = _current.set(trace)
    trace.emit('trace.start', terminal=True)
    async def heartbeat():
        expected = time.monotonic() + HEARTBEAT_SECONDS
        while True:
            await asyncio.sleep(max(0, expected - time.monotonic()))
            now = time.monotonic()
            event('progress', calls=trace.calls, loop_lag_ms=round(max(0, now - expected) * 1000, 2))
            expected = now + HEARTBEAT_SECONDS
    pulse = asyncio.create_task(heartbeat())
    error = None
    try:
        yield trace
    except BaseException as exc:
        trace.outcome = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'error'
        error = type(exc).__name__
        raise
    finally:
        pulse.cancel()
        try:
            await asyncio.gather(pulse, return_exceptions=True)
        finally:
            trace.emit('trace.end', terminal=True, outcome=trace.outcome, error_type=error,
                       calls=trace.calls, suppressed=trace.suppressed,
                       duration_ms=round((time.monotonic() - trace.started) * 1000, 2))
            _current.reset(token)


def traced(stage):
    """The dispatch owns the trace; direct run calls (tests/recovery) get one too."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(execution, *args, **kwargs):
            if _current.get():
                with span(stage):
                    return await fn(execution, *args, **kwargs)
            policy = execution.execution_input_snapshot.get('context', {}).get('monitoring', {})
            async with trace_scope(policy.get('owner', ''), policy.get('scope', 'host-security-monitor'), execution.id) as trace:
                with span(stage):
                    result = await fn(execution, *args, **kwargs)
                if trace.outcome == 'unknown':
                    trace.outcome = 'ok'
                return result
        return wrapped
    return decorate


def result(outcome, **fields):
    trace = _current.get()
    if trace:
        trace.outcome = outcome if outcome in _ENUMS['outcome'] else 'unknown'
    event('run.result', failure=outcome not in {'completed', 'ok'}, outcome=outcome, **fields)


def export_bundle(owner, scope):
    """Bounded read of our files only; no raw service log or customer DB export."""
    if not _export_lock.acquire(blocking=False):
        raise RuntimeError('diagnostic_export_busy')
    try:
        records = {}
        ids = dict(owner=opaque(owner), scope=opaque(scope))
        def add(raw):
            row = safe_record(raw)
            if all(row.get(k) == v for k, v in ids.items()) and row.get('event'):
                records[(row.get('trace'), row.get('seq'))] = row
        root = get_log_dir() / 'security-monitor'
        read_errors = 0
        for suffix in ('.3', '.2', '.1', ''):
            try:
                with (root / ('diagnostics.jsonl' + suffix)).open('rb') as handle:
                    handle.seek(0, 2)
                    offset = max(0, handle.tell() - MAX_BYTES)
                    handle.seek(offset)
                    if offset:
                        handle.readline(4096)
                    data = handle.read(MAX_BYTES)
                for line in data.splitlines():
                    if len(line) > 4096:
                        continue
                    try:
                        raw = json.loads(line)
                        if isinstance(raw, dict):
                            add(raw)
                    except (ValueError, UnicodeError):
                        continue
            except FileNotFoundError:
                continue
            except OSError:
                read_errors += 1
        recent, health = _sink.snapshot()
        for row in recent:
            add(row)
        # Prefer the newest evidence, keeping a hard cap on download size.
        selected = sorted(records.values(), key=lambda r: (r.get('timestamp', ''), r.get('seq', 0)))
        capped = len(selected) > 5000
        selected = selected[-5000:]
        return {'schema': 1, 'component': 'host-security-monitor', 'version': __version__, 'component_version': '1.3.0', 'mail_policy': 'mail-feedback-v1',
                'exported_at': datetime.now(timezone.utc).isoformat(),
                'coverage': 'recent_rotating_logs', 'record_count': len(selected), 'capped': capped,
                'health': {**health, 'read_errors': read_errors}, 'records': selected}
    finally:
        _export_lock.release()
