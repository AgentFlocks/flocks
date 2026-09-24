import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
import httpx

from flocks.monitoring import diagnostics as diag
from flocks.monitoring.adapter import XdrAdapter, ContractError
from flocks.monitoring.models import COMPONENT_ID
from flocks.server.routes import security_monitoring as api
from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolRegistry, ToolResult


class Capture:
    def __init__(self):
        self.records = []

    def submit(self, fields):
        self.records.append(diag.safe_record(fields))

    def snapshot(self):
        return self.records[:], {'queued': 0, 'write_errors': 0, 'queue_dropped': 0}


@pytest.fixture
def captured(monkeypatch):
    sink = Capture()
    monkeypatch.setattr(diag, '_sink', sink)
    return sink.records


def adapter(monkeypatch, result):
    policy = SimpleNamespace(owner='owner', devices=['private-device'], tool='sangfor_xdr_incidents')
    monkeypatch.setattr(ToolRegistry, 'get', lambda *a: SimpleNamespace(info=SimpleNamespace(enabled=True, requires_confirmation=False)))
    monkeypatch.setattr(ToolRegistry, 'execute', AsyncMock(return_value=result))
    return XdrAdapter(policy, 'private-session')


@pytest.mark.parametrize('output,reason,kind', [
    ('<html>secret-cookie-token</html>', 'non_json', 'html'),
    ('```json\n{"secret":"token"}\n```', 'non_json', 'fenced'),
    ('', 'non_json', 'empty'),
    ('[]', 'not_object', None),
    ('{"code":401,"message":"secret-cookie-token"}', 'business_error', None),
])
async def test_response_failures_are_distinct_without_body_leak(monkeypatch, captured, output, reason, kind):
    client = adapter(monkeypatch, ToolResult(success=True, output=output))
    async with diag.trace_scope('owner', COMPONENT_ID, 'private-execution'):
        with pytest.raises(ContractError):
            await client.call('private-device', {'action': 'list'}, 'private-message')
    failure = next(row for row in captured if row['event'] == 'adapter.failure')
    assert failure['reason'] == reason
    if kind:
        assert failure['text_kind'] == kind
        assert 'json_position' in failure
    text = json.dumps(captured)
    assert 'secret' not in text and 'private-' not in text
    assert len({r['trace'] for r in captured}) == 1
    assert failure['call'] == 1


async def test_real_registry_truncation_is_visible_before_json_failure(monkeypatch, tmp_path, captured):
    from flocks.tool import truncation
    output_dir = tmp_path / 'truncated'
    output_dir.mkdir()
    monkeypatch.setattr(truncation, '_OUTPUT_DIR', output_dir)
    monkeypatch.setattr(truncation, '_last_cleanup_ts', 0)
    payload = {'data': {'list': [{'uuId': str(i), 'name': 'CUSTOMER_SECRET' * 200} for i in range(100)]}}
    async def handler(ctx):
        return ToolResult(success=True, output=payload)
    tool = Tool(ToolInfo(name='fixture_incidents', description='Fixture'), handler)
    client = adapter(monkeypatch, None)
    async def execute(name, ctx, **params):
        return await tool.execute(ctx)
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        with pytest.raises(ContractError, match='非 JSON'):
            await client.call('private-device', {'action': 'list'}, 'message')
    raw = next(r for r in captured if r['event'] == 'tool.raw')
    normalized = next(r for r in captured if r['event'] == 'tool.normalized')
    failure = next(r for r in captured if r['event'] == 'adapter.failure')
    assert raw['value_type'] == 'dict' and raw['items'] == 100 and not raw['truncated']
    assert normalized['value_type'] == 'str' and normalized['truncated'] and normalized['has_saved_output']
    assert failure['reason'] == 'non_json' and failure['truncated']
    assert raw['call'] == normalized['call'] == failure['call']
    assert 'CUSTOMER_SECRET' not in json.dumps(captured)
    assert str(output_dir) not in json.dumps(captured)


async def test_tool_error_and_success_contract_are_unchanged(monkeypatch, captured):
    client = adapter(monkeypatch, ToolResult(success=False, error='Authorization: secret'))
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        with pytest.raises(ContractError, match='设备查询失败'):
            await client.call('private-device', {'action': 'list'}, 'message')
    assert any(r.get('reason') == 'tool_failed' for r in captured)
    client = adapter(monkeypatch, ToolResult(success=True, output='{"data":{"list":[],"total":0}}'))
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution2'):
        value = await client.call('private-device', {'action': 'list'}, 'message')
    assert value == {'data': {'list': [], 'total': 0}}
    assert 'secret' not in json.dumps(captured)


async def test_cancellation_terminal_and_monotonic_timing(captured):
    entered = asyncio.Event()
    async def work():
        async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
            with diag.span('tool.handler'):
                entered.set()
                await asyncio.sleep(60)
    task = asyncio.create_task(work())
    await entered.wait()
    await asyncio.sleep(.015)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[-1]['event'] == 'trace.end' and captured[-1]['outcome'] == 'cancelled'
    assert captured[-1]['duration_ms'] >= 10
    assert any(r.get('stage') == 'tool.handler' and r.get('outcome') == 'cancelled' for r in captured)
    assert diag._current.get() is None


async def test_event_budget_reserves_failure_and_terminal(captured):
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        for _ in range(1000):
            diag.event('adapter.result', **diag.shape('safe'))
        for _ in range(100):
            diag.event('adapter.failure', failure=True, reason='non_json')
        diag.result('failed')
    assert len(captured) <= diag.MAX_EVENTS + diag.MAX_ERRORS + 2
    assert any(r.get('reason') == 'non_json' for r in captured)
    assert captured[-1]['outcome'] == 'failed' and captured[-1]['suppressed'] > 0


async def test_broken_sink_does_not_change_business(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('secret disk path')
    monkeypatch.setattr(diag._sink, 'submit', fail)
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        with diag.span('run'):
            diag.result('completed')
    assert diag._current.get() is None


async def test_symlink_logs_survive_restart_and_export_only_owner(tmp_path, monkeypatch):
    target = tmp_path / 'data' / 'flocks'
    target.mkdir(parents=True)
    link = tmp_path / 'linked-flocks'
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv('FLOCKS_LOG_DIR', str(link / 'logs'))
    sink = diag.Sink()
    monkeypatch.setattr(diag, '_sink', sink)
    for owner in ('owner', 'other'):
        async with diag.trace_scope(owner, COMPONENT_ID, 'execution'):
            diag.result('completed')
    await asyncio.wait_for(asyncio.to_thread(sink.pending.join), 2)
    path = target / 'logs/security-monitor/diagnostics.jsonl'
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(diag, '_sink', Capture())  # emulate empty memory after restart
    bundle = await asyncio.to_thread(diag.export_bundle, 'owner', COMPONENT_ID)
    assert bundle['record_count'] == 3
    assert all(r['owner'] == diag.opaque('owner') for r in bundle['records'])
    assert diag.opaque('other') not in json.dumps(bundle)
    assert str(tmp_path) not in json.dumps(bundle)


def test_private_rotation_is_bounded(tmp_path):
    path = tmp_path / 'diagnostics.jsonl'
    handler = diag._PrivateFileHandler(path, maxBytes=500, backupCount=3, encoding='utf-8')
    try:
        for _ in range(100):
            handler.emit(logging.LogRecord('fixture', 20, '', 0, json.dumps({'data': 'x' * 90}), (), None))
    finally:
        handler.close()
    files = list(tmp_path.glob('diagnostics.jsonl*'))
    assert len(files) == 4 and all(f.stat().st_size < 500 for f in files)


def test_queue_full_is_nonblocking_and_export_has_health(monkeypatch, tmp_path):
    sink = diag.Sink()
    sink.started = True  # a stuck disk worker must not block the producer
    monkeypatch.setattr(diag, '_sink', sink)
    monkeypatch.setenv('FLOCKS_LOG_DIR', str(tmp_path / 'missing'))
    trace = diag.Trace('owner', COMPONENT_ID, 'execution')
    for _ in range(600):
        trace.emit('progress', terminal=True)
    assert sink.pending.qsize() == 512 and sink.dropped == 88
    bundle = diag.export_bundle('owner', COMPONENT_ID)
    assert bundle['health']['queue_dropped'] == 88 and bundle['record_count'] == 600


async def test_disk_write_failure_preserves_business_and_memory_export(monkeypatch, tmp_path):
    sink = diag.Sink()
    monkeypatch.setattr(diag, '_sink', sink)
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('fixture')
    monkeypatch.setenv('FLOCKS_LOG_DIR', str(blocked))
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        diag.result('completed')
    await asyncio.wait_for(asyncio.to_thread(sink.pending.join), 2)
    bundle = await asyncio.to_thread(diag.export_bundle, 'owner', COMPONENT_ID)
    assert bundle['record_count'] == 3
    assert bundle['health']['write_errors'] == 3 and bundle['health']['read_errors'] == 4
    assert bundle['records'][-1]['outcome'] == 'completed'


async def test_download_endpoint_auth_and_owner_scope(monkeypatch, captured):
    async with diag.trace_scope('owner', COMPONENT_ID, 'execution'):
        diag.result('failed')
    app = FastAPI()
    app.include_router(api.router)
    # Keep the actual route dependency: a request without an authenticated user
    # must not download diagnostics from the shared data directory.
    from flocks.auth.context import set_current_auth_user, reset_current_auth_user
    token = set_current_auth_user(None)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/monitoring/host-security-monitor/diagnostics')
            assert response.status_code == 401
    finally:
        reset_current_auth_user(token)
    for owner, expected in (('owner', 3), ('other', 0)):
        response = await api.diagnostics(user=SimpleNamespace(id=owner))
        assert json.loads(response.body)['record_count'] == expected
        assert response.headers['cache-control'] == 'no-store'
        assert 'attachment;' in response.headers['content-disposition']


def test_export_schema_discards_body_and_unknown_fields(tmp_path, monkeypatch, captured):
    monkeypatch.setenv('FLOCKS_LOG_DIR', str(tmp_path))
    root = tmp_path / 'security-monitor'
    root.mkdir()
    row = dict(trace='a' * 32, seq=1, event='adapter.failure', owner=diag.opaque('owner'), scope=diag.opaque(COMPONENT_ID),
               reason='non_json', body='SECRET', token='SECRET', stage='SECRET', error_type='SECRET=/private/host')
    (root / 'diagnostics.jsonl').write_text(json.dumps(row) + '\n' + '{invalid\n')
    bundle = diag.export_bundle('owner', COMPONENT_ID)
    assert bundle['record_count'] == 1 and 'SECRET' not in json.dumps(bundle)
