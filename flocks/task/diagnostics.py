"""Observe task DB operations without changing transactions or issuing SQL.

Only timings, SQLite error codes and repository source locations are retained.
Diagnostic failures must never replace the original result or exception.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import sqlite3
import sys
import time
import uuid
import weakref

import aiosqlite

SLOW_MS = 1000
HEARTBEAT_SECONDS = 60
_root = Path(__file__).absolute().parents[2]
_connections = weakref.WeakValueDictionary()
_cycles = {}


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _location(filename, name, line):
    try:
        relative = Path(filename).relative_to(_root).as_posix()
        if relative.startswith('flocks/') and re.fullmatch(r'[A-Za-z0-9_/.-]+', relative):
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                return f'{relative}:{name}:{line}'
    except (ValueError, TypeError):
        pass
    return None


def _caller():
    frame = sys._getframe(1)
    for _ in range(12):
        if frame is None:
            break
        if frame.f_code.co_filename != __file__:
            location = _location(frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno)
            if location:
                return location
        frame = frame.f_back
    return None


def error_fields(exc):
    """Keep original/caused error classes and SQLite extended codes, never text."""
    result = {'error_type': type(exc).__name__}
    frames, seen = [], set()
    current = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if current is not exc:
            result['cause_error_type'] = type(current).__name__
        if isinstance(current, sqlite3.Error) and 'sqlite_error_code' not in result:
            code = getattr(current, 'sqlite_errorcode', None)
            name = getattr(current, 'sqlite_errorname', None)
            if type(code) is int and 0 <= code <= 65535:
                result['sqlite_error_code'] = code
                result['db_error'] = ('busy_snapshot' if code == sqlite3.SQLITE_BUSY_SNAPSHOT else
                    {5: 'busy', 6: 'locked', 8: 'readonly', 10: 'io', 11: 'corruption',
                     13: 'full', 19: 'constraint', 26: 'corruption'}.get(code & 255, 'other'))
            if isinstance(name, str) and name.startswith('SQLITE_') and name in vars(sqlite3):
                result['sqlite_error_name'] = name
        tb = current.__traceback__
        for _ in range(40):
            if tb is None:
                break
            location = _location(tb.tb_frame.f_code.co_filename, tb.tb_frame.f_code.co_name, tb.tb_lineno)
            if location and location not in frames:
                frames.append(location)
            tb = tb.tb_next
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    if frames:
        result['error_frames'] = frames[-8:]
    return result


def _emit(name, **fields):
    try:
        from flocks.monitoring import diagnostics
        diagnostics.infrastructure_event(name, **fields)
    except Exception:
        pass


def _sql_fields(sql):
    if not isinstance(sql, str):
        return {}
    verb = re.match(r'\s*(SELECT|UPDATE|INSERT|DELETE|BEGIN|COMMIT|ROLLBACK|PRAGMA|CREATE|ALTER|DROP)\b', sql, re.I)
    table = re.search(r'\b(?:FROM|INTO|UPDATE|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+((?:task|monitor)_[a-z_]+)\b', sql, re.I)
    return {**({'sql_verb': verb[1].upper()} if verb else {}),
            **({'sql_table': table[1].lower()} if table else {})}


class _Result:
    """Match aiosqlite's awaitable AND async-context-managed execute result."""
    def __init__(self, connection, awaitable, phase, fields):
        self.connection, self.awaitable = connection, awaitable
        self.phase, self.fields = phase, fields
        self.cursor = None

    def __await__(self):
        return self.connection._run(self.awaitable, self.phase, self.fields).__await__()

    async def __aenter__(self):
        self.cursor = await self
        return self.cursor

    async def __aexit__(self, *args):
        await self.cursor.close()


class ObservedConnection:
    def __init__(self, raw, kind, database, timeout_ms):
        from flocks.monitoring.diagnostics import opaque
        object.__setattr__(self, '_raw', raw)
        object.__setattr__(self, '_info', dict(db_kind=kind, connection=uuid.uuid4().hex,
                                             database=opaque(database), timeout_ms=timeout_ms))
        object.__setattr__(self, '_pending', {})
        object.__setattr__(self, '_transaction_at', None)
        object.__setattr__(self, '_transaction_origin', None)
        object.__setattr__(self, '_failures', 0)
        object.__setattr__(self, '_last_error', {})
        _connections[self._info['connection']] = self

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            object.__setattr__(self, name, value)
        else:
            setattr(self._raw, name, value)

    def _in_transaction(self):
        try:
            return bool(self._raw.in_transaction)
        except Exception:
            return False

    def _transaction(self, location):
        active = self._in_transaction()
        if active and self._transaction_at is None:
            self._transaction_at = time.monotonic()
            self._transaction_origin = location
        elif not active and self._transaction_at is not None:
            duration = round((time.monotonic() - self._transaction_at) * 1000, 2)
            if duration >= SLOW_MS:
                _emit('db.transaction', **self._info, duration_ms=duration,
                      transaction_origin=self._transaction_origin, transaction_open=False)
            self._transaction_at = self._transaction_origin = None
        return {'transaction_open': active,
                'transaction_age_ms': round((time.monotonic() - self._transaction_at) * 1000, 2) if self._transaction_at is not None else 0,
                'transaction_origin': self._transaction_origin}

    async def _run(self, awaitable, phase, fields):
        started = time.monotonic()
        operation = uuid.uuid4().hex
        info = {**self._info, **fields, 'db_phase': phase, 'operation': operation}
        try:
            info['transaction_open_before'] = self._in_transaction()
            self._pending[operation] = (started, info)
        except Exception:
            pass
        try:
            result = await awaitable
        except BaseException as exc:
            try:
                self._failures += 1
                self._last_error = {'first_error_at': self._last_error.get('first_error_at', _stamp()),
                                    'last_error_at': _stamp(), **error_fields(exc)}
                _emit('db.operation', **info, **self._transaction(info.get('code_location')),
                      **error_fields(exc), outcome='cancelled' if isinstance(exc, asyncio.CancelledError) else 'error',
                      failure_count=self._failures, duration_ms=round((time.monotonic() - started) * 1000, 2))
            except Exception:
                pass
            raise
        else:
            try:
                transaction = self._transaction(info.get('code_location'))
                duration = round((time.monotonic() - started) * 1000, 2)
                if duration >= SLOW_MS:
                    _emit('db.operation', **info, **transaction, outcome='ok', duration_ms=duration)
            except Exception:
                pass
            return result
        finally:
            self._pending.pop(operation, None)

    def execute(self, sql, *args, **kwargs):
        return _Result(self, self._raw.execute(sql, *args, **kwargs), 'execute',
                       {**_sql_fields(sql), 'code_location': _caller()})

    def executemany(self, sql, *args, **kwargs):
        return _Result(self, self._raw.executemany(sql, *args, **kwargs), 'executemany',
                       {**_sql_fields(sql), 'code_location': _caller()})

    def executescript(self, sql):
        return _Result(self, self._raw.executescript(sql), 'executescript', {'code_location': _caller()})

    async def commit(self):
        return await self._run(self._raw.commit(), 'commit', {'code_location': _caller()})

    async def rollback(self):
        return await self._run(self._raw.rollback(), 'rollback', {'code_location': _caller()})

    async def close(self):
        try:
            return await self._run(self._raw.close(), 'close', {'code_location': _caller()})
        finally:
            _connections.pop(self._info['connection'], None)

    def snapshot(self):
        return {**self._info, 'transaction_open': self._in_transaction(),
                'transaction_age_ms': round((time.monotonic() - self._transaction_at) * 1000, 2) if self._transaction_at is not None else 0,
                'transaction_origin': self._transaction_origin, 'failure_count': self._failures,
                **self._last_error,
                'pending': [{**info, 'duration_ms': round((time.monotonic() - started) * 1000, 2)}
                            for started, info in list(self._pending.values())[:16]]}


def observe(raw, *, kind, database, timeout_ms):
    try:
        return ObservedConnection(raw, kind, database, timeout_ms)
    except Exception:
        return raw


@asynccontextmanager
async def observed_connect(database, *, timeout):
    async with aiosqlite.connect(database, timeout=timeout) as raw:
        db = observe(raw, kind='monitoring', database=database, timeout_ms=round(timeout * 1000))
        try:
            yield db
        finally:
            if isinstance(db, ObservedConnection):
                _connections.pop(db._info['connection'], None)


@contextmanager
def cycle(service):
    """Keep a live heartbeat even when no monitoring execution can be created."""
    state = _cycles.setdefault(service, {'failure_count': 0, 'consecutive_failures': 0})
    started = time.monotonic()
    state.update(started=started, running=True, last_started_at=_stamp())
    try:
        yield
    except BaseException as exc:
        state['consecutive_failures'] += 1
        state['failure_count'] += 1
        state.update(first_error_at=state.get('first_error_at', _stamp()), last_error_at=_stamp(), **error_fields(exc))
        _emit('service.error', service=service, consecutive_failures=state['consecutive_failures'],
              failure_count=state['failure_count'], **error_fields(exc))
        raise
    else:
        if state['consecutive_failures']:
            _emit('service.recovered', service=service, consecutive_failures=state['consecutive_failures'])
        state['consecutive_failures'] = 0
        state['last_success_at'] = _stamp()
    finally:
        state.update(running=False, last_finished_at=_stamp(), duration_ms=round((time.monotonic() - started) * 1000, 2))
        if time.monotonic() - state.get('heartbeat_at', -HEARTBEAT_SECONDS) >= HEARTBEAT_SECONDS:
            state['heartbeat_at'] = time.monotonic()
            _emit('service.heartbeat', service=service, duration_ms=state['duration_ms'],
                  consecutive_failures=state['consecutive_failures'], failure_count=state['failure_count'])


def snapshot():
    """No SQL, awaits, lock acquisition or transaction recovery during export."""
    try:
        return {'snapshot_available': True, 'pid': os.getpid(),
                'connections': [db.snapshot() for db in list(_connections.values())[:64]],
                'services': {name: {key: value for key, value in state.items() if key not in {'started', 'heartbeat_at'}} |
                             {'active_duration_ms': round((time.monotonic() - state['started']) * 1000, 2) if state.get('running') else 0}
                             for name, state in list(_cycles.items())}}
    except Exception as exc:
        return {'snapshot_available': False, **error_fields(exc)}
