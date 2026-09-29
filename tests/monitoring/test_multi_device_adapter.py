import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import adapter, capabilities, sessions
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import encode, rows, write
from flocks.session.interaction_policy import monitoring_read_scope, require_monitor_read
from flocks.session.session import Session
from flocks.tool.registry import ToolInfo, ToolParameter, ToolRegistry, ToolResult, ParameterType
from flocks.tool.structured_output import capture_output


FIELDS = {'action', 'start_time', 'end_time', 'page_num', 'page_size', 'uuid', 'entity_type',
          'deal_statuses', 'white_status', 'time_field'}


def device(identifier, *, name='同名 XDR', provider='provider', **updates):
    return SimpleNamespace(id=identifier, name=name, storage_key=provider, service_id='sangfor_xdr',
                           **{'enabled': True, 'fields_set': {'host': True, 'auth_code': True},
                              'status': 'unknown', **updates})


def tool(name='sangfor_xdr_incidents', *, provider='provider', fields=FIELDS, **updates):
    return ToolInfo(name=name, description='Synthetic XDR', source='device', provider=provider,
                    parameters=[ToolParameter(name=field, type=ParameterType.STRING) for field in fields],
                    **updates)


def registry(monkeypatch, devices, tools):
    monkeypatch.setattr('flocks.tool.device.store.list_devices', AsyncMock(return_value=devices))
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=True))
    monkeypatch.setattr(ToolRegistry, 'init_async', AsyncMock())
    monkeypatch.setattr(ToolRegistry, 'list_tools', lambda: tools)


def policy(tmp_path, **updates):
    return MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), **updates)


async def test_catalog_keeps_same_name_devices_distinct_and_does_not_auto_bind_multiple(monkeypatch):
    registry(monkeypatch, [device('first'), device('second')], [tool()])
    catalog = await adapter.device_catalog()
    assert catalog == [
        {'id': identifier, 'name': '同名 XDR', 'available': True, 'reason': None, 'tool': 'sangfor_xdr_incidents'}
        for identifier in ('first', 'second')]
    devices, selected_tool, reason = await adapter.discover()
    assert devices == [] and selected_tool is None and reason.startswith('请在监测配置中选择')


@pytest.mark.parametrize('case,reason', [
    ('disabled_device', '已停用'), ('credentials', '认证信息'), ('missing', '工具不可用'),
    ('disabled_tool', '工具不可用'), ('device_tool_disabled', '工具不可用'),
    ('confirmation', '工具不可用'), ('schema', '工具不可用'), ('wrong_provider', '工具不可用'),
    ('wrong_name', '工具不可用'), ('ambiguous', '多个匹配项'),
])
async def test_catalog_includes_unavailable_devices_without_exposing_configuration(monkeypatch, case, reason):
    candidate = device('unavailable')
    tools = [tool()]
    if case == 'disabled_device':
        candidate.enabled = False
    elif case == 'credentials':
        candidate.fields_set = {'host': True, 'auth_code': False}
    elif case == 'missing':
        tools = []
    elif case == 'disabled_tool':
        tools[0].enabled = False
    elif case == 'confirmation':
        tools[0].requires_confirmation = True
    elif case == 'schema':
        tools = [tool(fields=FIELDS - {'white_status'})]
    elif case == 'wrong_provider':
        tools = [tool(provider='other')]
    elif case == 'wrong_name':
        tools = [tool('sangfor_xdr_incidents_unrelated')]
    elif case == 'ambiguous':
        tools.append(tool('sangfor_xdr_incidents__second'))
    registry(monkeypatch, [candidate], tools)
    if case == 'device_tool_disabled':
        monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=False))
    catalog = await adapter.device_catalog()
    assert len(catalog) == 1
    assert catalog[0]['id'] == 'unavailable' and catalog[0]['available'] is False
    assert catalog[0]['tool'] is None and reason in catalog[0]['reason']
    assert set(catalog[0]) == {'id', 'name', 'available', 'reason', 'tool'}


async def test_catalog_resolves_each_provider_and_unique_legacy_discovery(monkeypatch):
    registry(monkeypatch, [device('first', provider='one'), device('second', provider='two')],
             [tool('sangfor_xdr_incidents__one', provider='one'), tool('sangfor_xdr_incidents__two', provider='two')])
    catalog = await adapter.device_catalog()
    assert [entry['tool'] for entry in catalog] == ['sangfor_xdr_incidents__one', 'sangfor_xdr_incidents__two']
    registry(monkeypatch, [device('first', provider='one'), device('disabled', enabled=False)],
             [tool('sangfor_xdr_incidents__one', provider='one')])
    assert await adapter.discover() == (['first'], 'sangfor_xdr_incidents__one', None)


async def test_each_adapter_call_pins_device_tool_and_structured_capture(monkeypatch, tmp_path):
    bound = policy(tmp_path, devices=['first', 'second'], tool='legacy',
                   device_tools={'first': 'xdr_one', 'second': 'xdr_two'})
    monkeypatch.setattr(ToolRegistry, 'get', lambda name: SimpleNamespace(info=tool(name)))
    seen = []

    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        assert name == bound.device_tools[params['device_id']]
        for other_name, changes in [(name, {'device_id': 'other'}), ('legacy', {}),
                                    (name, {'action': 'get_proof'}),
                                    (name, {'device_id': 'second' if params['device_id'] == 'first' else 'first'})]:
            with pytest.raises(PermissionError):
                await require_monitor_read(other_name, {**params, **changes}, ctx.session_id)
        result = ToolResult(success=True, output={'data': {'list': [params['device_id']]}})
        capture_output(ctx, name, result)
        seen.append((name, params['device_id']))
        return result

    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    with monitoring_read_scope(bound.tool, bound.devices, bound.device_tools):
        for identifier in bound.devices:
            response = await adapter.XdrAdapter(bound, 'session').call(identifier, {'action': 'list'}, 'message')
            assert response['data']['list'] == [identifier]
        with pytest.raises(PermissionError):
            await adapter.XdrAdapter(bound, 'session').call('unbound', {'action': 'list'}, 'message')
    assert seen == [('xdr_one', 'first'), ('xdr_two', 'second')]


async def test_read_scope_map_does_not_widen_or_allow_cross_tool_calls(tmp_path):
    bound = policy(tmp_path, devices=['first', 'second'], tool='legacy',
                   device_tools={'first': 'xdr_one', 'second': 'xdr_two'})
    original_map = dict(bound.device_tools)
    with monitoring_read_scope(bound.tool, bound.devices, original_map):
        original_map['first'] = 'wrong'
        await require_monitor_read('xdr_one', {'action': 'list', 'device_id': 'first'})
        await require_monitor_read('xdr_two', {'action': 'get_proof'}, resolved_device='second')
        for name, identifier in [('xdr_two', 'first'), ('xdr_one', 'second'), ('legacy', 'first'), ('xdr_two', 'other')]:
            with pytest.raises(PermissionError):
                await require_monitor_read(name, {'action': 'list', 'device_id': identifier})

        async def child():
            with monitoring_read_scope('wrong', ['first', 'third'], {'first': 'wrong', 'third': 'xdr_three'}):
                with pytest.raises(PermissionError):
                    await require_monitor_read('wrong', {'action': 'list', 'device_id': 'first'})
                with pytest.raises(PermissionError):
                    await require_monitor_read('xdr_three', {'action': 'list', 'device_id': 'third'})
        await asyncio.create_task(child())

    assert bound.tool_for('first') == 'xdr_one' and bound.tool_for('unmapped') == 'legacy'
    with monitoring_read_scope('legacy', ['old']):
        await require_monitor_read('legacy', {'action': 'list', 'device_id': 'old'})


async def test_capabilities_select_each_devices_own_tool(monkeypatch, tmp_path):
    registry(monkeypatch, [device('first', provider='one'), device('second', provider='two')],
             [tool('sangfor_xdr_incidents__one', provider='one'), tool('sangfor_xdr_incidents__two', provider='two')])
    bound = policy(tmp_path, devices=['first', 'second'], tool='legacy',
                   device_tools={'first': 'sangfor_xdr_incidents__one', 'second': 'sangfor_xdr_incidents__two'})
    catalog, unavailable = await capabilities.discover(bound)
    assert unavailable == []
    assert [(item.device, item.tool, item.kind) for item in catalog] == [
        ('first', 'sangfor_xdr_incidents__one', 'xdr'), ('second', 'sangfor_xdr_incidents__two', 'xdr')]
    assert [item.device for item in capabilities.for_event(catalog, {'device': 'second'})] == ['second']


async def test_daily_session_updates_only_read_grants_and_rejects_stale_policy(tmp_path):
    bound = policy(tmp_path, devices=['first'], tool='legacy', device_tools={'first': 'xdr_one'})
    started = datetime.now(timezone.utc)
    session, _ = await sessions.ensure_daily(bound, started)
    await Session.mutate_metadata(bound.project, session.id, lambda metadata: {**metadata, 'retained': 'value'})
    changed = bound.model_copy(update={'devices': ['second'], 'device_tools': {'second': 'xdr_two'},
                                      'targets_configured': True})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready) VALUES(?,?,?,?,1)',
                (changed.owner, changed.scope, changed.project, encode(changed.model_dump())))
    await sessions.sync_daily_read_scope(changed)
    updated, _ = await sessions.ensure_daily(changed, started)
    assert updated.id == session.id and updated.metadata['retained'] == 'value'
    assert updated.metadata['readOnlyTools'] == {'tool': 'legacy', 'devices': ['second'],
                                                  'device_tools': {'second': 'xdr_two'}}
    await require_monitor_read('xdr_two', {'action': 'list', 'device_id': 'second'}, session.id)
    with pytest.raises(PermissionError):
        await require_monitor_read('xdr_one', {'action': 'list', 'device_id': 'first'}, session.id)
    with pytest.raises(PermissionError, match='配置已变化'):
        await sessions.ensure_daily(bound, started)
    with pytest.raises(PermissionError, match='配置已变化'):
        await sessions.ensure_daily(bound, started + timedelta(days=1))
    assert len(await rows('SELECT * FROM monitor_daily_sessions')) == 1
    assert (await Session.get(bound.project, session.id)).metadata == updated.metadata


async def test_legacy_daily_session_metadata_remains_readable(monkeypatch):
    monkeypatch.setattr(Session, 'get_by_id', AsyncMock(return_value=SimpleNamespace(metadata={
        'monitorScope': 'host-security-monitor', 'readOnlyTools': {'tool': 'legacy', 'devices': ['old']}})))
    await require_monitor_read('legacy', {'action': 'list', 'device_id': 'old'}, 'old-session')
    with pytest.raises(PermissionError):
        await require_monitor_read('other', {'action': 'list', 'device_id': 'old'}, 'old-session')
