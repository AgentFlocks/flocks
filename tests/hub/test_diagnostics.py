"""Diagnostics preserve request behavior and never serialize customer payloads."""
import asyncio
import threading
from pathlib import Path

import pytest

from flocks.hub import diagnostics as diag


@pytest.fixture
def events(monkeypatch):
    items = []
    monkeypatch.setattr(diag.log, 'info', lambda event, fields: items.append((event, fields)))
    return items


def by_event(events, name):
    return [fields for event, fields in events if event == 'hub.diag.' + name]


async def call(app, path='/api/hub/catalog', client_id=b'a' * 32):
    sent = []
    async def send(message):
        sent.append(message)
    async def receive():
        return {'type': 'http.request', 'body': b''}
    await diag.HubDiagnosticsMiddleware(app)(
        {'type': 'http', 'method': 'GET', 'path': path,
         'headers': [(b'x-flocks-hub-request-id', client_id), (b'authorization', b'secret-token')],
         'query_string': b'q=private-customer'}, receive, send)
    return sent


async def respond(scope, receive, send):
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'private-result'})


@pytest.mark.asyncio
async def test_boundary_correlates_worker_and_preserves_response(events, monkeypatch):
    monkeypatch.setenv('FLOCKS_HUB_DIAGNOSTICS', '1')
    @diag.timed('test.read')
    def work():
        return {'private-data': 'secret'}
    async def app(scope, receive, send):
        assert await diag.run_in_thread(work) == {'private-data': 'secret'}
        await respond(scope, receive, send)
    sent = await call(app)
    trace = by_event(events, 'request.begin')[0]['trace_id']
    assert all(item['trace_id'] == trace for _, item in events)
    assert all(item['client_id'] == 'a' * 32 for _, item in events)
    assert dict(sent[0]['headers'])[b'x-flocks-hub-trace-id'] == trace.encode()
    assert sent[1]['body'] == b'private-result'
    assert by_event(events, 'worker.start')[0]['queue_ms'] >= 0
    assert by_event(events, 'request.end')[0]['active_spans'] == 0
    assert 'secret' not in str(events) and 'private-' not in str(events)
    assert diag._current.get() is None


@pytest.mark.asyncio
async def test_parallel_requests_have_independent_trace_ids(events):
    await asyncio.gather(call(respond), call(respond))
    assert len({item['trace_id'] for item in by_event(events, 'request.end')}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [ValueError('secret-exception'), asyncio.CancelledError('secret-exception')])
async def test_error_and_cancellation_are_preserved(events, error):
    async def app(*args):
        with diag.span('test.failure'):
            raise error
    with pytest.raises(type(error)):
        await call(app)
    end = by_event(events, 'request.end')[0]
    assert end['outcome'] == ('cancelled' if isinstance(error, asyncio.CancelledError) else 'error')
    assert end['active_spans'] == 0
    assert 'secret-exception' not in str(events)
    assert diag._current.get() is None


@pytest.mark.asyncio
async def test_waiting_heartbeat_records_active_worker(events, monkeypatch):
    monkeypatch.setattr(diag, 'HEARTBEAT_SECONDS', .005)
    release = threading.Event()
    started = threading.Event()
    def work():
        with diag.span('test.slow_read'):
            started.set()
            assert release.wait(2)
    async def app(scope, receive, send):
        await diag.run_in_thread(work)
        await respond(scope, receive, send)
    pending = asyncio.create_task(call(app))
    try:
        for _ in range(100):
            if started.is_set() and by_event(events, 'waiting'):
                break
            await asyncio.sleep(.005)
        active = by_event(events, 'waiting')[0]['active']
        assert any(item['stage'] == 'test.slow_read' for item in active)
    finally:
        release.set()
        await pending


@pytest.mark.asyncio
async def test_cancelled_request_does_not_claim_worker_stopped(events, monkeypatch):
    monkeypatch.setenv('FLOCKS_HUB_DIAGNOSTICS', '1')
    release, started, done = threading.Event(), threading.Event(), threading.Event()
    def work():
        try:
            with diag.span('test.late_worker'):
                started.set()
                release.wait(2)
        finally:
            done.set()
    async def app(*args):
        await diag.run_in_thread(work)
    pending = asyncio.create_task(call(app))
    try:
        while not started.is_set():
            await asyncio.sleep(.005)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert by_event(events, 'request.end')[0]['active_spans'] > 0
    finally:
        release.set()
        await asyncio.to_thread(done.wait, 2)
    late = [v for v in by_event(events, 'stage.end') if v['stage'] == 'test.late_worker']
    assert late[0]['request_finished'] is True


def test_bounded_events_keep_aggregate_counts(events, monkeypatch):
    monkeypatch.setenv('FLOCKS_HUB_DIAGNOSTICS', '1')
    trace = diag.Trace('/api/hub/catalog')
    token = diag._current.set(trace)
    try:
        for _ in range(1000):
            with diag.span('test.repeated'):
                pass
    finally:
        diag._current.reset(token)
    assert len(events) == diag.MAX_STAGE_EVENTS
    assert trace.summary()['stages'][0]['count'] == 1000
    assert trace.summary()['suppressed_events'] == 1900


@pytest.mark.parametrize('linked', [False, True])
def test_root_diagnostics_regular_and_symlink(events, monkeypatch, tmp_path, linked):
    from flocks.hub import local
    from flocks.hub.paths import get_bundled_hub_root
    monkeypatch.setenv('FLOCKS_HUB_DIAGNOSTICS', '1')
    home = tmp_path / 'home'
    home.mkdir()
    target = tmp_path / 'data'
    target.mkdir()
    user = home / '.flocks'
    if linked:
        user.symlink_to(target, target_is_directory=True)
    else:
        user.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: home)
    class Config:
        @staticmethod
        def get_data_path():
            return user
    trace = diag.Trace('/api/hub/catalog')
    token = diag._current.set(trace)
    try:
        diag._collect_paths(trace, Config, local, lambda: tmp_path)
    finally:
        diag._current.reset(token)
    entry = next(v for v in by_event(events, 'path') if v['role'] == 'user')
    assert entry['is_symlink'] is linked
    assert entry['resolved'] == str(target if linked else user)


@pytest.mark.asyncio
async def test_unrelated_routes_untouched(events):
    sent = await call(respond, path='/api/session')
    assert events == []
    assert sent[0]['headers'] == []


@pytest.mark.asyncio
async def test_rejects_arbitrary_correlation_header(events):
    await call(respond, client_id=b'secret-customer\ncontent')
    assert by_event(events, 'request.begin')[0]['client_id'] == ''
    assert 'secret-customer' not in str(events)


def test_cache_hit_is_distinct_from_signature_scan(events, monkeypatch):
    from flocks.hub import catalog
    monkeypatch.setattr(catalog, '_catalog_entries_cache_key', lambda: ())
    monkeypatch.setattr(catalog, '_build_catalog_entries', __import__('functools').lru_cache()(lambda key: ()))
    trace = diag.Trace('/api/hub/catalog')
    token = diag._current.set(trace)
    try:
        catalog._catalog_entries_snapshot()
        assert trace.stats['catalog.cache_lookup']['cache_hit'] is False
        catalog._catalog_entries_snapshot()
        assert trace.stats['catalog.cache_lookup']['cache_hit'] is True
        assert trace.stats['catalog.lock_wait']['count'] == 2
    finally:
        diag._current.reset(token)


@pytest.mark.asyncio
async def test_logging_failure_does_not_fail_request(monkeypatch):
    def broken(*args):
        raise OSError('disk full')
    monkeypatch.setattr(diag.log, 'info', broken)
    sent = await call(respond)
    assert sent[0]['status'] == 200


def test_real_catalog_cold_and_warm_under_diagnostics(events, monkeypatch, tmp_path):
    from flocks.config.config import Config
    from flocks.hub import catalog, local
    monkeypatch.setenv('FLOCKS_HUB_DIAGNOSTICS', '1')
    monkeypatch.setattr(local, '_user_plugins_root', lambda: tmp_path / 'user-plugins')
    monkeypatch.setattr(local, '_project_plugins_root', lambda: tmp_path / 'project-plugins')
    monkeypatch.setattr(Config, 'get_data_path', lambda: tmp_path / 'data')
    catalog.clear_catalog_caches()
    trace = diag.Trace('/api/hub/catalog')
    token = diag._current.set(trace)
    try:
        first = catalog.list_catalog(plugin_type='component')
        assert first
        assert trace.summary()['cache']['cache_hit'] is False
        second = catalog.list_catalog(plugin_type='component')
        assert first == second
        assert trace.summary()['cache']['cache_hit'] is True
        assert trace.stats['catalog.installed_plugins_cache_key']['count'] == 2
        assert trace.stats['catalog.read_yaml']['count'] > 0
        assert trace.stats['catalog.path_signature']['count'] > 0
        assert trace.active == {}
        assert len(by_event(events, 'stage.begin')) + len(by_event(events, 'stage.end')) <= diag.MAX_STAGE_EVENTS
    finally:
        diag._current.reset(token)
        catalog.clear_catalog_caches()


def test_handled_parse_and_stat_errors_remain_fallbacks(events, tmp_path):
    from flocks.hub import catalog
    bad = tmp_path / 'bad.yaml'
    bad.write_text('private-secret: [broken')
    trace = diag.Trace('/api/hub/catalog')
    token = diag._current.set(trace)
    try:
        assert catalog._read_yaml(bad) == {}
        assert catalog._path_signature(tmp_path / 'absent')[1:] == (-1, -1)
    finally:
        diag._current.reset(token)
    errors = trace.summary()['handled_errors']
    assert {row['stage'] for row in errors} == {'catalog.read_yaml', 'catalog.path_signature'}
    assert 'private-secret' not in str(trace.summary())
