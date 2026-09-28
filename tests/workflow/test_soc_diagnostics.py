"""SOC support telemetry must explain waits without changing workflow execution."""
import asyncio
import json
import queue
import threading
from types import SimpleNamespace

import pytest

from flocks.workflow import soc_diagnostics as diag


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(diag, '_ensure_writer', lambda: None)
    monkeypatch.setattr(diag, 'log_path', lambda: tmp_path / 'soc.jsonl')
    monkeypatch.setattr(diag, '_queue', queue.Queue(maxsize=2048))
    diag._events.clear()
    diag._active.clear()
    diag._health.clear()
    for wf in diag.WORKFLOWS:
        diag._counters[wf].clear()
        diag._last[wf].clear()
    yield
    diag._active.clear()


def test_ingest_exact_counts_sampled_rows_and_overflow_never_blocks():
    diag._queue = queue.Queue(maxsize=1)
    for _ in range(1001):
        diag.record(diag.WORKFLOWS[0], 'received', raw='secret', password='secret')
    result = diag.export_bundle()
    assert result['runtime']['counters'][diag.WORKFLOWS[0]]['received'] == 1001
    assert len(result['recent_events']) == 11
    assert result['runtime']['health']['dropped_records'] == 10
    assert 'secret' not in json.dumps(result)


@pytest.mark.asyncio
async def test_waiting_thread_is_visible_until_completion_and_callbacks_preserved():
    started, release = threading.Event(), threading.Event()
    calls = []

    @diag.runner_observer
    def run(**kwargs):
        token = kwargs['on_step_start']('run-1', 1, SimpleNamespace(id='concurrent_triage'), {'secret': 'payload'})
        assert token == 'existing-token'
        started.set()
        release.wait(2)
        kwargs['on_step_complete'](SimpleNamespace(node_id='concurrent_triage', duration_ms=1, error=None,
                                                   outputs={'stats': {'raw_count': 4, 'password': 'secret'}}))
        return SimpleNamespace(status='COMPLETED', error=None)

    class Manager:
        @diag.traced('schedule.dispatch')
        async def execute(self, workflow_id):
            return await asyncio.to_thread(run, run_id='run-1', on_step_start=lambda *a: 'existing-token',
                                           on_step_complete=lambda step: calls.append(step.node_id))

    task = asyncio.create_task(Manager().execute(diag.WORKFLOWS[1]))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        snap = diag.snapshot()
        node = next(v for v in snap['active'] if v.get('node') == 'concurrent_triage')
        assert node['execution'] == 'run-1'
        assert node['elapsed_seconds'] >= 0
        assert 'secret' not in json.dumps(snap)
    finally:
        release.set()
        await task
    assert calls == ['concurrent_triage']
    assert not diag.snapshot()['active']
    assert any(x['event'] == 'node.counts' and x['raw_count'] == 4 for x in diag._events)


@pytest.mark.asyncio
async def test_cancellation_preserved_and_no_stale_dispatch():
    class Manager:
        @diag.traced('syslog.dispatch')
        async def execute(self, workflow_id):
            await asyncio.Event().wait()
    task = asyncio.create_task(Manager().execute(diag.WORKFLOWS[0]))
    await asyncio.sleep(0)
    assert diag.snapshot()['active']
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not diag.snapshot()['active']
    assert diag._events[-1]['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_non_soc_unmodified_and_no_collection():
    class Manager:
        @diag.traced('schedule.dispatch')
        async def execute(self, workflow_id):
            return 42
    assert await Manager().execute('user-workflow') == 42
    assert not diag._events


def test_rotation_history_survives_restart_and_partial_line():
    path = diag.log_path()
    path.with_name(path.name + '.1').write_text('{"event":"before_restart"}\n')
    path.write_text('{"event":"after_restart"}\n{"incomplete":')
    result = diag.export_bundle()
    assert [x['event'] for x in result['persisted_events']] == ['before_restart', 'after_restart']
    assert result['read_errors'] == ['invalid_line']


@pytest.mark.asyncio
async def test_real_runner_callbacks_and_node_error_are_visible():
    from flocks.workflow.runner import run_workflow
    from flocks.workflow.models import Workflow
    workflow = Workflow.model_validate({'start': 'broken', 'nodes': [
        {'id': 'broken', 'type': 'python', 'code': 'raise ValueError("sensitive-payload")'}], 'edges': []})
    class Manager:
        @diag.traced('schedule.dispatch')
        async def execute(self, workflow_id):
            return await asyncio.to_thread(run_workflow, workflow=workflow, ensure_requirements=False, run_id='run-error')
    result = await Manager().execute(diag.WORKFLOWS[1])
    assert result.error
    assert any(x['event'] == 'node.end' and x['has_error'] for x in diag._events)
    assert 'sensitive-payload' not in json.dumps(diag.export_bundle())


@pytest.mark.asyncio
async def test_schedule_skip_is_distinct_from_no_events(monkeypatch):
    from flocks.workflow.poller_manager import WorkflowPollerManager
    manager = WorkflowPollerManager()
    monkeypatch.setattr(manager, '_cleanup_done_runs', lambda _: 1)
    await manager._schedule_run(diag.WORKFLOWS[1], {}, {'noOverlap': True})
    assert diag._counters[diag.WORKFLOWS[1]]['schedule.overlap_skipped'] == 1
    assert not diag.snapshot()['active']


@pytest.mark.asyncio
async def test_export_requires_admin_and_omits_runtime_error_content(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from flocks.server.routes.workflow import router
    from flocks.ingest.syslog.manager import default_manager as syslog
    from flocks.workflow.poller_manager import default_manager as poller
    app = FastAPI()
    role = [None]
    @app.middleware('http')
    async def user(request, call_next):
        if role[0]:
            request.state.auth_user = SimpleNamespace(role=role[0])
        return await call_next(request)
    app.include_router(router, prefix='/api')
    monkeypatch.setattr(syslog, 'get_listener_status', lambda wf: {'state': 'failed', 'error': 'secret-password'})
    monkeypatch.setattr(poller, 'get_status', lambda wf: {'state': 'running', 'lastError': 'private-url'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/soc-workspace/diagnostics')).status_code == 401
        role[0] = 'member'
        assert (await client.get('/api/soc-workspace/diagnostics')).status_code == 403
        role[0] = 'admin'
        response = await client.get('/api/soc-workspace/diagnostics')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['trigger_runtime'][diag.WORKFLOWS[0]]['listener_has_error']
    assert 'secret-password' not in response.text
    assert 'private-url' not in response.text


def test_log_path_follows_shared_root_symlink(monkeypatch, tmp_path):
    from flocks.utils.log import get_log_dir
    target = tmp_path / 'data'
    target.mkdir()
    link = tmp_path / '.flocks'
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.delenv('FLOCKS_LOG_DIR', raising=False)
    monkeypatch.setenv('FLOCKS_ROOT', str(link))
    path = get_log_dir() / 'soc-workspace-diagnostics.jsonl'
    path.parent.mkdir(parents=True)
    handler = diag._Handler(path, maxBytes=120, backupCount=3)
    import logging
    for i in range(20):
        handler.emit(logging.LogRecord('soc', 20, '', 0, json.dumps({'event': 'snapshot', 'time': i}), (), None))
    handler.close()
    assert (target / 'logs' / path.name).exists()
    assert len(list(path.parent.glob(path.name + '*'))) <= 4


@pytest.mark.asyncio
async def test_syslog_filter_not_matched_does_not_start_runner(monkeypatch):
    from flocks.ingest.syslog.manager import SyslogManager
    from flocks.workflow.triggers.models import TriggerDefinition
    manager = SyslogManager()
    trigger = TriggerDefinition.model_validate({'id': 'test', 'type': 'syslog', 'filter': {'expr': 'False'}})
    await manager._trigger_workflow(diag.WORKFLOWS[0], None, {'secret': 'payload'}, 'syslog_message', trigger=trigger)
    assert any(e.get('phase') == 'trigger.filtered' and e['matched'] is False for e in diag._events)
    assert not any(e.get('phase') == 'runner.enter' for e in diag._events)
    assert 'payload' not in json.dumps(diag.export_bundle())


def test_datagram_received_is_distinct_from_parse_and_callback_failure(monkeypatch):
    from flocks.ingest.syslog import listener
    def callback(parsed):
        raise OSError('secret-path')
    callback._diagnostic_workflow_id = diag.WORKFLOWS[0]
    protocol = listener.SyslogUDPProtocol(callback, 'auto')
    protocol.datagram_received(b'private syslog body', None)
    assert diag._counters[diag.WORKFLOWS[0]]['transport.received'] == 1
    assert diag._counters[diag.WORKFLOWS[0]]['transport.callback_failed'] == 1
    def broken(*args):
        raise ValueError('private packet')
    monkeypatch.setattr(listener, 'parse_syslog', broken)
    with pytest.raises(ValueError):
        protocol.datagram_received(b'private packet', None)
    assert diag._counters[diag.WORKFLOWS[0]]['transport.parse_failed'] == 1
    assert 'private' not in json.dumps(diag.export_bundle())


def test_failed_disk_write_is_counted_without_propagating(monkeypatch):
    import logging
    handler = diag._Handler(diag.log_path(), maxBytes=100, backupCount=3, delay=True)
    def fail():
        raise OSError('disk full')
    monkeypatch.setattr(handler, '_open', fail)
    handler.emit(logging.LogRecord('soc', 20, '', 0, 'event', (), None))
    assert diag.snapshot()['health']['writer_errors'] == 1


def test_long_running_snapshot_has_locations_without_locals():
    diag._active['trace'] = {'workflow': diag.WORKFLOWS[0], 'started': __import__('time').time() - 61}
    secret = 'never_export_local_values'
    result = diag.snapshot()
    assert result['thread_locations']
    assert secret not in json.dumps(result)
    assert set(result['thread_locations'][0]['frames'][0]) == {'file', 'function', 'line'}


@pytest.mark.asyncio
async def test_sqlite_extended_error_is_exported_without_raw_payload():
    import sqlite3
    class Manager:
        @diag.traced('syslog.dispatch')
        async def execute(self, workflow_id):
            error = sqlite3.OperationalError('sensitive SQL text')
            error.sqlite_errorcode = 517
            error.sqlite_errorname = 'SQLITE_BUSY_SNAPSHOT'
            raise error
    with pytest.raises(sqlite3.OperationalError):
        await Manager().execute(diag.WORKFLOWS[0])
    event = diag._events[-1]
    assert event['sqlite_errorcode'] == 517
    assert event['sqlite_errorname'] == 'SQLITE_BUSY_SNAPSHOT'
    assert 'sensitive SQL text' not in json.dumps(diag.export_bundle())
