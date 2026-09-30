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
    diag._live_progress.clear()
    diag._health.clear()
    for wf in diag.WORKFLOWS:
        diag._counters[wf].clear()
        diag._last[wf].clear()
    yield
    diag._active.clear()
    diag._live_progress.clear()


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


def test_live_counts_use_real_stats_and_keep_unknowns_null():
    result = diag._live_counts(diag.WORKFLOWS[0], {'stats': {
        'raw_count': 12, 'normalized_count': 11, 'after_filter_count': 9,
        'after_dedup_count': 9, 'unique_key_count': 4, 'dedup_removed_count': 5,
        'filter_removed_count': 2, 'password': 'never expose'}, 'alerts': ['private']})
    assert result == {'rawCount': 12, 'normalizedCount': 11, 'afterFilterCount': 9,
                      'uniqueCount': 4, 'duplicateCount': 5, 'filterRemovedCount': 2}
    assert diag._live_counts(diag.WORKFLOWS[0], {'stats': {'after_dedup_count': 9}})['uniqueCount'] is None
    assert diag._live_counts(diag.WORKFLOWS[0], {'stats': {'after_filter_count': 9, 'dedup_removed_count': 5}})['uniqueCount'] == 4
    assert diag._live_counts(diag.WORKFLOWS[0], {'stats': {'raw_count': False}})['rawCount'] is None
    assert diag._live_counts(diag.WORKFLOWS[0], {'stats': {'raw_count': 0}})['rawCount'] == 0
    assert 'private' not in json.dumps(result)
    assert 'never expose' not in json.dumps(result)


@pytest.mark.parametrize('unique_field,duplicate_flag,expected', [
    ({}, True, (0, 1)),
    ({}, False, (1, 0)),
    ({}, 'true', (1, 0)),
    ({'unique_key_count': 1}, True, (1, 0)),
    ({'unique_key_count': 0}, True, (0, 0)),
    ({'unique_key_count': None}, True, (1, 0)),
])
def test_legacy_singleton_duplicate_metrics_match_dashboard_without_overriding_explicit_unique(unique_field, duplicate_flag, expected):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / '.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/api/handlers.py'
    spec = importlib.util.spec_from_file_location('soc_dashboard_metric_contract', path)
    dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard)
    outputs = {'stats': {'raw_count': 1, 'normalized_count': 1, 'after_filter_count': 1,
                         'after_dedup_count': 1, 'dedup_removed_count': 0, **unique_field},
               'is_duplicate': duplicate_flag}
    memory = diag._live_counts(diag.WORKFLOWS[0], outputs)
    persisted = dashboard._dashboard_live_metrics('denoise', [outputs])
    assert memory == persisted
    assert (memory['uniqueCount'], memory['duplicateCount']) == expected


def test_legacy_duplicate_flag_does_not_reclassify_a_batch():
    metrics = diag._live_counts(diag.WORKFLOWS[0], {'is_duplicate': True, 'stats': {
        'raw_count': 10, 'after_filter_count': 10, 'dedup_removed_count': 0}})
    assert metrics['uniqueCount'] == 10 and metrics['duplicateCount'] == 0


def test_triage_live_counts_distinguish_input_records_from_work_units():
    assert diag._live_counts(diag.WORKFLOWS[1], {'load_stats': {'record_count': 20}}) == {
        'inputCount': 20, 'completedCount': None, 'cacheHitCount': None, 'failedCount': None,
        'attackCount': None, 'benignCount': None, 'unknownCount': None,
        'workUnitCount': None, 'followersReusedCount': None,
    }
    metrics = diag._live_counts(diag.WORKFLOWS[1], {'triage_stats': {
        'total': 20, 'work_units': 8, 'followers_reused': 12,
        'triaged': 5, 'cache_hit': 2, 'triage_failed': 1,
        'verdict_counts': {'attack': 1, 'attack_success': 2, 'attack_failed': 1, 'non_attack': 3, 'unknown': 1},
        'raw': 'secret',
    }})
    assert metrics == {'inputCount': 20, 'completedCount': 8, 'cacheHitCount': 2, 'failedCount': 1,
                       'attackCount': 4, 'benignCount': 3, 'unknownCount': 1,
                       'workUnitCount': 8, 'followersReusedCount': 12}
    assert diag._live_counts(diag.WORKFLOWS[1], {'triage_stats': {'triaged': 3, 'cache_hit': 2}})['completedCount'] is None


@pytest.mark.asyncio
async def test_live_projection_is_visible_between_real_nodes_and_retained_after_runner(monkeypatch):
    from flocks.workflow.runner import run_workflow
    from flocks.workflow.models import Workflow
    from flocks.workflow.execution_store import ExecutionStepRecorder
    # Real runner callbacks, with the same ID contract used by trigger/poller.
    execution_id = 'persisted-workflow-execution-id'
    entered, release = threading.Event(), threading.Event()
    recorder = ExecutionStepRecorder()
    workflow = Workflow.model_validate({'start': 'receive_alert', 'nodes': [
        {'id': 'receive_alert', 'type': 'python', 'code': "outputs['stats'] = {'raw_count': 12}"},
        {'id': 'normalize', 'type': 'python', 'code': "outputs['stats'] = {'raw_count': 12, 'normalized_count': 11}"},
    ], 'edges': [{'from': 'receive_alert', 'to': 'normalize'}]})
    def on_start(rid, index, node, inputs):
        assert rid == execution_id
        if node.id == 'normalize':
            entered.set()
            release.wait(3)
        return True
    class Manager:
        @diag.traced('schedule.dispatch')
        async def execute(self, workflow_id):
            return await asyncio.to_thread(run_workflow, workflow=workflow, ensure_requirements=False,
                run_id=execution_id, on_step_start=on_start, on_step_complete=recorder.on_step_complete)
    task = asyncio.create_task(Manager().execute(diag.WORKFLOWS[0]))
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        live = diag.read_workflow_live_progress(diag.WORKFLOWS[0], [execution_id])[execution_id]
        assert live['nodeId'] == 'normalize' and live['phase'] == 'running'
        assert live['executionId'] == execution_id and live['workflowId'] == diag.WORKFLOWS[0]
        assert live['stepCount'] == recorder.step_count == 1
        assert live['metrics']['rawCount'] == 12
        assert live['metrics']['normalizedCount'] is None
        assert live['stepDurationsMs']['receive_alert'] >= 0
        assert 'normalize' not in live['stepDurationsMs']
        assert live['updatedAt'] >= live['startedAt']
        assert diag.read_workflow_live_progress(diag.WORKFLOWS[1], [execution_id]) == {}
        assert diag.read_workflow_live_progress(diag.WORKFLOWS[0], ['different-id']) == {}
        # A consumer cannot mutate the shared cache through returned values.
        live['metrics']['rawCount'] = 999
        live['stepDurationsMs']['receive_alert'] = -1
        assert diag.read_workflow_live_progress(diag.WORKFLOWS[0], [execution_id])[execution_id]['metrics']['rawCount'] == 12
        assert diag.read_workflow_live_progress(diag.WORKFLOWS[0], [execution_id])[execution_id]['stepDurationsMs']['receive_alert'] >= 0
    finally:
        release.set()
        await task
    final = diag.read_workflow_live_progress(diag.WORKFLOWS[0], [execution_id])[execution_id]
    assert final['phase'] == 'success' and final['stepCount'] == recorder.step_count == 2
    assert final['metrics']['rawCount'] == 12 and final['metrics']['normalizedCount'] == 11
    assert not diag.snapshot()['active']
    assert len(recorder.take_steps()) == 2  # normal buffered persistence remains intact


@pytest.mark.parametrize('workflow,node', [(diag.WORKFLOWS[0], 'normalize'), (diag.WORKFLOWS[1], 'concurrent_triage')])
def test_live_step_durations_are_recorded_before_persistence_and_cleared_on_retry(workflow, node):
    token = diag._context.set((workflow, 'trace-timing'))
    @diag.runner_observer
    def run(**kwargs):
        kwargs['on_step_start']('timed', 1, SimpleNamespace(id=node), {})
        kwargs['on_step_complete'](SimpleNamespace(node_id=node, duration_ms=50, error=None, outputs={}))
        assert diag.read_workflow_live_progress(workflow, ['timed'])['timed']['stepDurationsMs'] == {node: 50}
        kwargs['on_step_start']('timed', 2, SimpleNamespace(id=node), {})
        assert diag.read_workflow_live_progress(workflow, ['timed'])['timed']['stepDurationsMs'] == {}
        kwargs['on_step_complete'](SimpleNamespace(node_id=node, duration_ms=20, error='failed', outputs={}))
        assert diag.read_workflow_live_progress(workflow, ['timed'])['timed']['stepDurationsMs'] == {}
        return SimpleNamespace(status='failed', error='failed')
    try:
        run(run_id='timed')
    finally:
        diag._context.reset(token)


@pytest.mark.parametrize('duration, expected', [(0, 0), (.25, .25), (None, None), (-1, None),
    (True, None), ('20', None), (float('inf'), None), (float('nan'), None), (10**1000, None)])
def test_live_step_durations_reject_invalid_values_and_unknown_node_names(duration, expected):
    workflow = diag.WORKFLOWS[1]
    diag._update_live_progress(workflow, 'timed', node='load_dedup_file', completed=True, duration_ms=duration)
    diag._update_live_progress(workflow, 'timed', node='arbitrary-secret-node', completed=True, duration_ms=20)
    live = diag.read_workflow_live_progress(workflow, ['timed'])['timed']
    assert live['stepDurationsMs'] == ({} if expected is None else {'load_dedup_file': expected})
    assert len(live['stepDurationsMs']) <= 4


def test_live_projection_is_bounded_expires_without_io_and_preserves_known_counts(monkeypatch):
    tick = [100.0]
    monkeypatch.setattr(diag.time, 'monotonic', lambda: tick[0])
    for index in range(300):
        diag._update_live_progress(diag.WORKFLOWS[0], f'run-{index}', outputs={'stats': {'raw_count': index}})
        tick[0] += 1
    assert len(diag._live_progress) == diag._LIVE_LIMIT
    assert not diag.read_workflow_live_progress(diag.WORKFLOWS[0], ['run-0'])
    diag._update_live_progress(diag.WORKFLOWS[0], 'run-299', node='later-node')
    live = diag.read_workflow_live_progress(diag.WORKFLOWS[0], ['run-299'])['run-299']
    assert live['metrics']['rawCount'] == 299 and live['metrics']['normalizedCount'] is None
    tick[0] += diag._LIVE_TTL_SECONDS
    assert diag.read_workflow_live_progress(diag.WORKFLOWS[0], ['run-299']) == {}
    assert not diag._live_progress
    assert not diag._events and diag._queue.empty()  # projection itself never queues IO


def test_live_projection_supports_parallel_runners_without_cross_execution_leaks():
    from concurrent.futures import ThreadPoolExecutor
    def work(index):
        workflow = diag.WORKFLOWS[index % 2]
        token = diag._context.set((workflow, f'trace-{index}'))
        execution = f'run-{index}'
        @diag.runner_observer
        def run(**kwargs):
            kwargs['on_step_start'](execution, 1, SimpleNamespace(id='node'), {})
            kwargs['on_step_complete'](SimpleNamespace(node_id='node', outputs={
                'stats': {'raw_count': index}, 'load_stats': {'record_count': index},
            }, duration_ms=1, error=None))
            return SimpleNamespace(status='success', error=None)
        try:
            run(run_id=execution)
        finally:
            diag._context.reset(token)
        return workflow, execution, index
    with ThreadPoolExecutor(max_workers=8) as pool:
        completed = list(pool.map(work, range(100)))
    for workflow, execution, index in completed:
        live = diag.read_workflow_live_progress(workflow, [execution])[execution]
        field = 'rawCount' if workflow == diag.WORKFLOWS[0] else 'inputCount'
        assert live['metrics'][field] == index and live['stepCount'] == 1 and live['phase'] == 'success'
        other = diag.WORKFLOWS[1] if workflow == diag.WORKFLOWS[0] else diag.WORKFLOWS[0]
        assert not diag.read_workflow_live_progress(other, [execution])
    assert not diag._active


@pytest.mark.parametrize('error', [RuntimeError('secret failure'), asyncio.CancelledError()])
def test_live_projection_retains_failed_or_cancelled_runner_without_error_text(error):
    token = diag._context.set((diag.WORKFLOWS[0], 'trace-error'))
    @diag.runner_observer
    def run(**kwargs):
        kwargs['on_step_start']('run-error', 1, SimpleNamespace(id='receive_alert'), {'secret': 'payload'})
        raise error
    try:
        with pytest.raises(type(error)):
            run(run_id='run-error')
    finally:
        diag._context.reset(token)
    live = diag.read_workflow_live_progress(diag.WORKFLOWS[0], ['run-error'])['run-error']
    assert live['phase'] == ('cancelled' if isinstance(error, asyncio.CancelledError) else 'failed')
    assert live['metrics']['rawCount'] is None and live['stepCount'] == 0
    assert 'secret' not in json.dumps(live)
