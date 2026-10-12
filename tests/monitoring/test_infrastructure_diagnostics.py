"""Real SQLite contention and support export; no real mail/device operations."""
import asyncio
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException

from flocks.monitoring import diagnostics as diag
from flocks.monitoring.diagnostic_state import task_state
from flocks.monitoring.models import COMPONENT_ID
from flocks.server.routes import security_monitoring as api
from flocks.task import diagnostics as dbdiag
from flocks.task.store import TaskStore


class Capture:
    def __init__(self):
        self.records = []

    def submit(self, fields):
        self.records.append(diag.safe_record(fields))

    def snapshot(self):
        return list(self.records), {}


@pytest.fixture
def captured(monkeypatch):
    sink = Capture()
    monkeypatch.setattr(diag, '_sink', sink)
    monkeypatch.setattr(dbdiag, '_cycles', {})
    return sink.records


async def test_real_busy_snapshot_preserves_failed_transaction_and_extended_code(tmp_path, captured):
    path = tmp_path / 'sqlite-contention.db'
    raw = await aiosqlite.connect(path, timeout=.01)
    db = dbdiag.observe(raw, kind='task', database=path, timeout_ms=10)
    try:
        await db.execute('PRAGMA journal_mode=WAL')
        await db.execute('CREATE TABLE task_fixture(id INTEGER PRIMARY KEY, value TEXT)')
        await db.execute('INSERT INTO task_fixture VALUES(1,?)', ('SECRET_CONTENT',))
        await db.commit()
        with closing(sqlite3.connect(path)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('UPDATE task_fixture SET value=? WHERE id=1', ('SECRET_UPDATED',))
            with pytest.raises(sqlite3.OperationalError) as original:
                await db.execute('UPDATE task_fixture SET value=? WHERE id=1', ('SECRET_WRITE',))
            assert original.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert db.in_transaction  # Observation must not recover or roll back.
            async with db.execute('SELECT value FROM task_fixture') as cursor:
                assert (await cursor.fetchone())[0] == 'SECRET_CONTENT'
            writer.commit()
            with pytest.raises(sqlite3.OperationalError) as original:
                await db.execute('UPDATE task_fixture SET value=? WHERE id=1', ('SECRET_RETRY',))
            assert original.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT
        failures = [row for row in captured if row['event'] == 'db.operation']
        assert [row['db_error'] for row in failures] == ['busy', 'busy_snapshot']
        assert failures[-1]['sqlite_error_name'] == 'SQLITE_BUSY_SNAPSHOT'
        assert failures[-1]['sqlite_error_code'] == 517
        assert failures[-1]['transaction_open'] and failures[-1]['sql_table'] == 'task_fixture'
        assert failures[-1]['failure_count'] == 2 and failures[-1]['timeout_ms'] == 10
        assert 'SECRET' not in json.dumps(captured) and str(path) not in json.dumps(captured)
        await db.rollback()
        await db.executemany('UPDATE task_fixture SET value=? WHERE id=?', [('safe', 1)])
        await db.commit()
    finally:
        await db.close()


async def test_observer_retains_await_and_context_manager_semantics(tmp_path, captured):
    async with dbdiag.observed_connect(tmp_path / 'semantics.db', timeout=.01) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript('CREATE TABLE task_fixture(value INTEGER); INSERT INTO task_fixture VALUES(1);')
        cursor = await db.execute('SELECT value FROM task_fixture')
        assert (await cursor.fetchone())['value'] == 1
        await cursor.close()
        async with db.execute('SELECT value FROM task_fixture') as cursor:
            assert (await cursor.fetchone())['value'] == 1
        await db.execute('UPDATE task_fixture SET value=2')
        assert db.in_transaction and db.snapshot()['transaction_age_ms'] >= 0
        assert db.snapshot()['transaction_origin'] is None  # Test paths are deliberately omitted.
        await db.commit()
        assert not db.in_transaction


async def test_pending_operation_is_visible_without_an_extra_query(captured):
    entered, finish = asyncio.Event(), asyncio.Event()
    async def slow_execute(*args):
        entered.set()
        await finish.wait()
        return 'result'
    raw = SimpleNamespace(execute=AsyncMock(side_effect=slow_execute), in_transaction=False)
    db = dbdiag.observe(raw, kind='task', database='private-path', timeout_ms=5000)
    async def query():
        return await db.execute('SELECT * FROM task_fixture WHERE id=?', ('SECRET_PARAMETER',))
    task = asyncio.create_task(query())
    await entered.wait()
    state = dbdiag.snapshot()
    pending = next(item for item in state['connections'] if item['connection'] == db._info['connection'])['pending']
    assert len(pending) == 1 and pending[0]['db_phase'] == 'execute'
    assert pending[0]['duration_ms'] >= 0 and raw.execute.call_count == 1
    assert 'SECRET' not in json.dumps(state) and 'private-path' not in json.dumps(state)
    finish.set()
    assert await task == 'result'


async def test_real_taskstore_failure_names_the_application_operation(tmp_path, captured):
    from flocks.task.models import TaskScheduler
    scheduler = TaskScheduler(title='SECRET_SCHEDULER')
    await TaskStore.create_scheduler(scheduler)
    native = await TaskStore._db()
    await native.execute('PRAGMA busy_timeout=10')
    with closing(sqlite3.connect(TaskStore.get_db_path())) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE task_schedulers SET title=? WHERE id=?', ('SECRET_NEW', scheduler.id))
        with pytest.raises(sqlite3.OperationalError):
            await TaskStore.update_scheduler(scheduler)
        failure = next(row for row in captured if row['event'] == 'db.operation' and row.get('sql_table') == 'task_schedulers')
        assert 'store.py:update_scheduler:' in failure['code_location']
        assert failure['connection'] == native._info['connection']
        assert failure['transaction_origin'] == failure['code_location']
        assert 'SECRET' not in json.dumps(captured)
        writer.rollback()
    await native.rollback()


async def test_cancellation_is_recorded_without_rollback(captured):
    entered = asyncio.Event()
    async def slow_execute(*args):
        entered.set()
        await asyncio.sleep(60)
    raw = SimpleNamespace(execute=AsyncMock(side_effect=slow_execute), in_transaction=True,
                          rollback=AsyncMock())
    db = dbdiag.observe(raw, kind='task', database='path', timeout_ms=5000)
    async def write():
        await db.execute('UPDATE task_fixture SET value=?', ('SECRET',))
    task = asyncio.create_task(write())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[-1]['outcome'] == 'cancelled' and captured[-1]['transaction_open']
    assert not db._pending and raw.rollback.await_count == 0


@pytest.mark.parametrize('failure', [False, True])
async def test_diagnostic_sink_failure_cannot_change_db_result(monkeypatch, failure):
    monkeypatch.setattr(dbdiag, 'SLOW_MS', 0)
    def broken(*args, **kwargs):
        raise OSError('SECRET_DISK')
    monkeypatch.setattr(diag._sink, 'submit', broken)
    error = sqlite3.OperationalError('SECRET_ERROR')
    raw = SimpleNamespace(execute=AsyncMock(side_effect=error if failure else None, return_value='result'), in_transaction=False)
    db = dbdiag.observe(raw, kind='task', database='path', timeout_ms=5000)
    if failure:
        with pytest.raises(sqlite3.OperationalError) as caught:
            await db.execute('UPDATE task_fixture SET value=?', ('SECRET',))
        assert caught.value is error
    else:
        assert await db.execute('SELECT 1') == 'result'


def test_loop_failure_and_recovery_have_independent_heartbeat(captured):
    error = sqlite3.OperationalError('SECRET_ERROR')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    error.sqlite_errorname = 'SQLITE_BUSY_SNAPSHOT'
    with pytest.raises(sqlite3.OperationalError):
        with dbdiag.cycle('scheduler'):
            raise error
    with dbdiag.cycle('scheduler'):
        pass
    state = dbdiag.snapshot()['services']['scheduler']
    assert state['failure_count'] == 1 and state['consecutive_failures'] == 0
    assert state['last_error_at'] and state['last_success_at'] and not state['running']
    assert {row['event'] for row in captured} >= {'service.error', 'service.recovered', 'service.heartbeat'}
    assert 'SECRET' not in json.dumps(captured)


async def test_pause_failure_retains_original_sqlite_error_and_exact_stage(monkeypatch, captured):
    from flocks.monitoring import lifecycle
    error = sqlite3.OperationalError('SECRET_CONNECTION_DETAIL')
    error.sqlite_errorcode = 517
    error.sqlite_errorname = 'SQLITE_BUSY_SNAPSHOT'
    monkeypatch.setattr(lifecycle, '_owned_installation', AsyncMock(return_value=({'owner': 'owner', 'scheduler_id': 'private-scheduler'}, None, None)))
    monkeypatch.setattr(lifecycle, 'write', AsyncMock())
    monkeypatch.setattr(lifecycle.TaskManager, 'disable_scheduler', AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as caught:
        await api._control(lifecycle.pause_monitoring, 'owner')
    assert caught.value.status_code == 500
    failure = next(row for row in captured if row.get('stage') == 'pause.disable' and row['event'] == 'stage.end')
    assert failure['outcome'] == 'error' and failure['sqlite_error_code'] == 517
    assert any('lifecycle.py:_pause_monitor:' in frame for frame in failure['error_frames'])
    assert captured[-1]['event'] == 'trace.end' and captured[-1]['outcome'] == 'error'
    assert 'SECRET' not in json.dumps(captured) and 'private-scheduler' not in json.dumps(captured)


async def test_successful_control_and_snapshot_failure_are_separate(monkeypatch, captured):
    action = AsyncMock()
    monkeypatch.setattr(api, 'resolve_day', AsyncMock(return_value='2026-10-12'))
    monkeypatch.setattr(api, 'snapshot', AsyncMock(side_effect=RuntimeError('SECRET_SNAPSHOT')))
    with pytest.raises(RuntimeError):
        await api._control(action, 'owner')
    stages = {row.get('stage'): row for row in captured if row['event'] == 'stage.end'}
    assert stages['control.action']['outcome'] == 'ok'
    assert stages['control.snapshot']['outcome'] == 'error'
    assert 'SECRET' not in json.dumps(captured)


async def test_pause_waiting_for_lifecycle_lock_is_distinguishable_from_db_wait(monkeypatch, captured):
    from flocks.monitoring import lifecycle
    lock = asyncio.Lock()
    monkeypatch.setattr(lifecycle, '_lock', lock)
    monkeypatch.setattr(lifecycle, '_owned_installation', AsyncMock(side_effect=FileNotFoundError('not installed')))
    await lock.acquire()
    task = asyncio.create_task(api._control(lifecycle.pause_monitoring, 'owner'))
    try:
        await asyncio.sleep(.01)
        waiting = next(row for row in captured if row['event'] == 'control.lock')
        assert waiting['control_lock_busy'] and waiting['outcome'] == 'running'
        assert not task.done()
    finally:
        lock.release()
    with pytest.raises(HTTPException) as caught:
        await task
    assert caught.value.status_code == 404
    acquired = next(row for row in captured if row['event'] == 'control.lock' and row['outcome'] == 'ok')
    assert acquired['duration_ms'] >= 10


def _support_db(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE monitor_installations(owner TEXT,scope TEXT,scheduler_id TEXT,installed INT,ready INT);
        CREATE TABLE task_schedulers(id TEXT,status TEXT,trigger TEXT);
        CREATE TABLE task_executions(id TEXT,scheduler_id TEXT,status TEXT,created_at TEXT,started_at TEXT,completed_at TEXT);
    ''')
    for owner in ('owner', 'other'):
        db.execute('INSERT INTO monitor_installations VALUES(?,?,?,1,1)', (owner, COMPONENT_ID, owner))
        db.execute('INSERT INTO task_schedulers VALUES(?,?,?)', (owner, 'active', json.dumps({'next_run': '2026-10-09T14:00:00+00:00'})))
        db.execute('INSERT INTO task_executions VALUES(?,?,?,?,?,?)', (owner + '-private-event', owner, 'completed', '2026-10-09T13:30:00+00:00', None, '2026-10-09T13:30:34+00:00'))
    db.commit()
    return db


async def test_export_reads_owner_state_while_writer_is_locked_and_never_reuses_taskstore(monkeypatch, tmp_path, captured):
    path = tmp_path / 'support.db'
    with closing(_support_db(path)) as writer:
        monkeypatch.setattr(TaskStore, 'get_db_path', lambda: path)
        monkeypatch.setattr(TaskStore, '_db', AsyncMock(side_effect=AssertionError('must not reuse writer')))
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE task_schedulers SET status='disabled'")
        async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
            diag.result('completed')
        diag.infrastructure_event('service.heartbeat', service='scheduler', consecutive_failures=7)
        bundle = await asyncio.to_thread(diag.export_bundle, 'owner', COMPONENT_ID)
        state = bundle['infrastructure']['monitoring']
        assert state['snapshot_available'] and state['mode'] == 'read_only'
        assert state['scheduler_status'] == 'active' and state['overdue_seconds'] > 0
        assert state['execution_counts'] == {'completed': 1}
        assert len(state['recent_executions']) == 1 and state['recent_executions'][0]['execution'] == diag.opaque('owner-private-event')
        assert bundle['infrastructure']['records'][0]['service'] == 'scheduler'
        assert writer.in_transaction and TaskStore._db.await_count == 0
        assert 'private-event' not in json.dumps(bundle) and str(path) not in json.dumps(bundle)
        writer.rollback()


def test_snapshot_busy_or_missing_does_not_prevent_trace_export(monkeypatch, tmp_path, captured):
    path = tmp_path / 'support.db'
    monkeypatch.setattr(TaskStore, 'get_db_path', lambda: path)
    assert task_state('owner', COMPONENT_ID)['reason'] == 'database_missing'
    assert not path.exists()
    with closing(_support_db(path)) as writer:
        writer.execute('PRAGMA journal_mode=DELETE')
        writer.execute('BEGIN EXCLUSIVE')
        writer.execute("UPDATE task_schedulers SET status='disabled'")
        bundle = diag.export_bundle('owner', COMPONENT_ID)
        assert bundle['infrastructure']['monitoring']['reason'] == 'read_failed'
        assert bundle['infrastructure']['monitoring']['db_error'] == 'busy'
        assert 'records' in bundle and writer.in_transaction
        writer.rollback()


async def test_infrastructure_owner_isolation_and_safe_schema(captured):
    async with diag.trace_scope('other', COMPONENT_ID, 'private-execution'):
        diag.infrastructure_event('db.operation', outcome='error', sql='SECRET_SQL',
                                  code_location='/SECRET/file.py', error_frames=['/SECRET/file.py', 'flocks/task/store.py:update_scheduler:425'])
    diag.infrastructure_event('service.heartbeat', service='executor')
    bundle = diag.export_bundle('owner', COMPONENT_ID)
    assert len(bundle['infrastructure']['records']) == 1
    other = diag.export_bundle('other', COMPONENT_ID)
    failure = next(row for row in other['infrastructure']['records'] if row['event'] == 'db.operation')
    assert failure['error_frames'] == ['flocks/task/store.py:update_scheduler:425']
    assert 'SECRET' not in json.dumps(other)


async def test_diagnostic_download_mail_reads_cannot_request_a_write_lock(monkeypatch, tmp_path, captured):
    from flocks.monitoring import mailflow, store
    path = tmp_path / 'read-only-mail.db'
    monkeypatch.setattr(TaskStore, 'get_db_path', lambda: path)
    with closing(_support_db(path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE task_schedulers SET status='disabled'")
        monkeypatch.setattr(store, 'connection', lambda: pytest.fail('export must not migrate/request a write lock'))
        async def mail_snapshot(owner):
            result = await store.rows('SELECT status FROM task_schedulers WHERE id=?', (owner,))
            return {'snapshot_available': True, 'scheduler_status': result[0]['status']}
        monkeypatch.setattr(mailflow, 'diagnostic_state', mail_snapshot)
        response = await asyncio.wait_for(api.diagnostics(user=SimpleNamespace(id='owner')), 2)
        assert json.loads(response.body)['mail']['scheduler_status'] == 'active'
        assert not store._diagnostic_reads.get()
        writer.rollback()


async def test_read_only_diagnostic_scope_resets_after_failure(monkeypatch, tmp_path):
    from flocks.monitoring import store
    path = tmp_path / 'read-scope.db'
    monkeypatch.setattr(TaskStore, 'get_db_path', lambda: path)
    with closing(_support_db(path)):
        with store.read_only_diagnostics():
            with pytest.raises(ValueError, match='must_be_select'):
                await store.rows('UPDATE task_schedulers SET status=?', ('disabled',))
        assert not store._diagnostic_reads.get()
        assert task_state('owner', COMPONENT_ID)['scheduler_status'] == 'active'
