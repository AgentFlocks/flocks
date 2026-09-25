import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest
import yaml
from pydantic import ValidationError

from flocks.monitoring import disposition as d
from flocks.monitoring.adapter import ContractError, normalize
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.reports import snapshot
from flocks.monitoring.store import rows, write, encode
from flocks.project.project import Project
from flocks.session.interaction_policy import unattended_scope, monitoring_read_scope
from flocks.tool.registry import ToolRegistry, ToolResult
from flocks.tool.tool_loader import yaml_to_tool


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    directory = tmp_path / '.flocks/workspace/monitor'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='monitor', worktree=str(directory))
    policy = MonitoringPolicy(development_sample=False, owner='owner', project=project.id, directory=str(directory), devices=['device'])
    event = normalize('device', {'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 4, 'dealStatus': 0})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready) VALUES(?,?,?,?,1)',
                ('owner', policy.scope, project.id, encode(policy.model_dump())))
    day = datetime.now(timezone.utc).date().isoformat()
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('attempt', 'execution', 'owner', project.id, policy.scope, day, 'daily', 'message', d.stamp(), 'completed'))
    await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('attempt', event['key'], encode(event)))
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *_: SimpleNamespace(enabled=True))
    state = SimpleNamespace(policy=policy, event=event, day=day, calls=[], statuses=[0, 40], fail_write=False)
    class Fake:
        def __init__(self, policy, session_id): self.policy, self.session_id = policy, session_id
        async def call(self, device, params, message):
            state.calls.append(params)
            if params['action'] == 'update_status':
                if state.fail_write:
                    raise TimeoutError('SECRET')
                return {'code': 'Success', 'data': {}}
            status = state.statuses.pop(0)
            if isinstance(status, Exception):
                raise status
            if isinstance(status, dict):
                return status
            return {'code': 'Success', 'data': {'item': [{'uuId': 'event', 'dealStatus': status}], 'total': 1}}
    state.adapter = Fake
    return state


def request(event_key='device:incident:event', **changes):
    return d.DispositionRequest(**{'request_id': uuid4(), 'event_key': event_key, 'comment': '已核实并完成修复', 'confirmed': True, **changes})


async def confirm(state, req=None):
    return await d.confirm('owner', req or request(), state.adapter)


async def test_confirm_write_readback_and_deduplication(setup):
    req = request()
    result, duplicate = await asyncio.gather(confirm(setup, req), confirm(setup, req))
    assert result == duplicate and result['status'] == 'verified'
    assert [c['action'] for c in setup.calls] == ['list', 'update_status', 'list']
    assert setup.calls[1] == {'action': 'update_status', 'uuids': ['event'], 'deal_status': 40, 'deal_comment': req.comment}
    assert all(c['white_status'] == [] and c['deal_statuses'] == [] and c['start_time'] == 0 for c in setup.calls if c['action'] == 'list')
    data = await snapshot('owner', setup.policy.scope, setup.day)
    assert data['metrics']['closed'] == 1 and data['metrics']['openRisk'] == 0
    assert data['events'][0]['closure'] == 'closed'
    report = (await rows('SELECT * FROM monitor_reports'))[0]
    assert '已闭环（XDR 回查确认）' in report['content']
    assert '处置未启用' not in report['content']
    with pytest.raises(ValueError, match='不同处置'):
        await confirm(setup, req.model_copy(update={'comment': 'changed'}))


@pytest.mark.parametrize('readback', [10, 50, 60, 70, None, True, '40', 2,
    ContractError('回查失败'), {'data': {'item': [], 'total': 0}},
    {'data': {'item': [{'uuId': 'other', 'dealStatus': 40}], 'total': 1}}])
async def test_only_exact_confirmed_status_closes(setup, readback):
    setup.statuses = [0, readback]
    result = await confirm(setup)
    assert result['status'] in ('pending', 'mismatch')
    data = await snapshot('owner', setup.policy.scope, setup.day)
    assert data['metrics']['closed'] == 0 and data['metrics']['openRisk'] == 1
    assert len([c for c in setup.calls if c['action'] == 'update_status']) == 1
    setup.statuses = [40]
    result = await d.recheck('owner', result['id'], setup.adapter)
    assert result['status'] == 'verified'
    assert len([c for c in setup.calls if c['action'] == 'update_status']) == 1


async def test_ambiguous_write_cannot_be_repeated_and_logs_are_safe(setup):
    setup.fail_write = True
    req = request()
    result = await confirm(setup, req)
    assert result['status'] == 'pending' and 'SECRET' not in str(result)
    await confirm(setup, req)
    with pytest.raises(ValueError, match='已有处置'):
        await confirm(setup)
    setup.statuses = [40]
    assert (await d.recheck('owner', result['id'], setup.adapter))['status'] == 'verified'
    assert len([c for c in setup.calls if c['action'] == 'update_status']) == 1


@pytest.mark.parametrize('current', [30, 40, 50, None, '0'])
async def test_preflight_changed_or_unknown_never_writes(setup, current):
    setup.statuses = [current]
    result = await confirm(setup)
    assert result['status'] == ('verified' if current == 40 else 'failed')
    assert [c['action'] for c in setup.calls] == ['list']
    if current == 30:
        assert result['observed_status'] == 30 and '状态已变化' in result['error']


async def test_protected_is_known_but_not_closed_after_write(setup):
    setup.statuses = [0, 30]
    result = await confirm(setup)
    assert result['status'] == 'mismatch' and result['observed_status'] == 30
    assert (await snapshot('owner', setup.policy.scope, setup.day))['metrics']['closed'] == 0
    setup.statuses = [30]
    result = await d.recheck('owner', result['id'], setup.adapter)
    assert result['status'] == 'mismatch'
    assert len([c for c in setup.calls if c['action'] == 'update_status']) == 1


async def test_owner_device_and_event_binding(setup):
    with pytest.raises(FileNotFoundError):
        await d.confirm('other', request(), setup.adapter)
    with pytest.raises(FileNotFoundError):
        await confirm(setup, request('device:incident:other'))
    await write('UPDATE monitor_installations SET policy=?', (encode(setup.policy.model_copy(update={'devices': ['other']}).model_dump()),))
    with pytest.raises(ValueError, match='监测范围'):
        await confirm(setup)
    assert setup.calls == []


@pytest.mark.parametrize('fields', [{'confirmed': False}, {'confirmed': 'true'}, {'comment': '  '}, {'comment': 'x' * 2049}, {'deal_status': 60}])
def test_confirmation_contract(fields):
    with pytest.raises(ValidationError): request(**fields)


async def test_crash_recovery_is_read_only(setup):
    result = await confirm(setup)
    await write("UPDATE monitor_dispositions SET status='writing'")
    with pytest.raises(ValueError, match='正在执行'):
        await d.recheck('owner', result['id'], setup.adapter)
    await write('UPDATE monitor_dispositions SET updated_at=?', ((datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),))
    setup.statuses = [40]
    assert (await d.recheck('owner', result['id'], setup.adapter))['status'] == 'verified'
    assert len([c for c in setup.calls if c['action'] == 'update_status']) == 1


async def test_later_active_observation_reopens_event(setup):
    await confirm(setup)
    await write('UPDATE monitor_attempts SET started_at=?', ((datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),))
    data = await snapshot('owner', setup.policy.scope, setup.day)
    assert data['metrics']['closed'] == 0 and data['events'][0]['closure'] == 'open'


@pytest.fixture
async def real_tool(setup, monkeypatch):
    path = Path(__file__).resolve().parents[2] / '.flocks/plugins/tools/device/sangfor_xdr_v2_2/sangfor_xdr_incidents.yaml'
    tool = yaml_to_tool(yaml.safe_load(path.read_text()), path)
    import inspect
    fn = inspect.getclosurevars(tool.handler).nonlocals['fn']
    calls = []
    async def send(method, path, data=None, params=None):
        calls.append((path, data))
        if path.endswith('/dealstatus'):
            return ToolResult(success=True, output={'code': 'Success'})
        status = 0 if len(calls) == 1 else 40
        return ToolResult(success=True, output={'code': 'Success', 'data': {'item': [{'uuId': 'event', 'dealStatus': status}], 'total': 1}})
    monkeypatch.setitem(fn.__globals__, '_run_request', send)
    monkeypatch.setattr(ToolRegistry, '_tools', {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    monkeypatch.setattr(ToolRegistry, '_sync_configured_enabled_states', lambda: None)
    monkeypatch.setattr(ToolRegistry, '_resolve_device_target', AsyncMock(return_value=('device', None)))
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=True))
    @asynccontextmanager
    async def credentials(device): yield True
    monkeypatch.setattr('flocks.tool.credential_context.activate_device_credentials', credentials)
    return calls


async def test_real_registry_yaml_handler_write_contract(setup, real_tool):
    result = await d.confirm('owner', request())
    assert result['status'] == 'verified', result
    assert real_tool[1] == ('/api/xdr/v1/incidents/dealstatus', {'uuIds': ['event'], 'dealStatus': 40, 'dealComment': '已核实并完成修复'})
    assert real_tool[2][1]['whiteStatus'] == [] and real_tool[2][1]['dealStatus'] == []


async def test_hook_cannot_change_confirmed_payload(setup, real_tool, monkeypatch):
    from flocks.hooks.pipeline import HookPipeline
    async def patch(payload):
        return SimpleNamespace(output={'decision': {'validated_input_patch': {'uuids': ['victim']}}}, execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline, 'run_tool_before', patch)
    result = await d.confirm('owner', request())
    assert result['status'] == 'failed' and real_tool == []


async def test_unattended_cannot_use_manual_adapter(setup, real_tool):
    with unattended_scope(), monitoring_read_scope(setup.policy.tool, setup.policy.devices):
        result = await d.confirm('owner', request())
    assert result['status'] == 'failed' and real_tool == []


async def test_foreign_recheck_is_hidden(setup):
    result = await confirm(setup)
    with pytest.raises(FileNotFoundError): await d.recheck('other', result['id'], setup.adapter)


async def test_disposition_trace_has_no_comment_device_or_body(setup, real_tool, monkeypatch):
    from flocks.monitoring import diagnostics as diag
    captured = []
    monkeypatch.setattr(diag._sink, 'submit', lambda fields: captured.append(diag.safe_record(fields)))
    result = await d.confirm('owner', request(comment='PRIVATE_COMMENT'))
    assert result['status'] == 'verified'
    assert any(row.get('stage') == 'disposition.confirm' for row in captured)
    assert any(row.get('action') == 'update_status' for row in captured)
    assert captured[-1]['outcome'] == 'completed'
    assert 'PRIVATE_COMMENT' not in json.dumps(captured)


async def test_routes_validate_confirmation_and_owner_scope(setup, monkeypatch):
    from fastapi import FastAPI
    from flocks.server.routes import security_monitoring as api
    import httpx
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_user] = lambda: SimpleNamespace(id='owner')
    mock = AsyncMock(return_value={'status': 'verified'})
    monkeypatch.setattr(d, 'confirm', mock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        path = '/monitoring/host-security-monitor/dispositions'
        body = request().model_dump(mode='json')
        for bad in ({**body, 'confirmed': False}, {**body, 'owner': 'other'}, {**body, 'deal_status': 60}):
            assert (await client.post(path, json=bad)).status_code == 422
        assert mock.call_count == 0
        assert (await client.post(path, json=body)).status_code == 200
        assert mock.call_args.args[0] == 'owner'
