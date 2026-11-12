"""Bounded, original-device alert reads; no network or credentials are used."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import adapter, capabilities as c
from flocks.monitoring.models import MonitoringPolicy
from flocks.session.interaction_policy import monitoring_read_scope, require_monitor_read
from flocks.tool.registry import ParameterType, ToolInfo, ToolParameter, ToolRegistry, ToolResult
from flocks.tool.structured_output import capture_output


INCIDENT_FIELDS = {'action', 'uuid', 'entity_type'}
ALERT_FIELDS = {'action', 'uuid', 'uuids', 'start_time', 'end_time', 'time_field', 'page_num', 'page_size'}


def device(identifier='xdr', **updates):
    return SimpleNamespace(**{'id': identifier, 'name': f'Fixture {identifier}', 'storage_key': identifier,
                              'service_id': 'sangfor_xdr', 'enabled': True, 'status': 'unknown',
                              'fields_set': {'host': True, 'auth_code': True}, **updates})


def tool(name, provider='xdr', *, fields=ALERT_FIELDS, actions=None, **updates):
    parameters = [ToolParameter(name=field, type=ParameterType.STRING,
                                enum=(actions or ['list', 'get_proof']) if field == 'action' else None)
                  for field in fields]
    return ToolInfo(name=name, description='Fixture tool', source='device', provider=provider,
                    parameters=parameters, **updates)


def setup_registry(monkeypatch, devices, tools, *, enabled=True):
    monkeypatch.setattr('flocks.tool.device.store.list_devices', AsyncMock(return_value=devices))
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', AsyncMock(return_value=enabled))
    monkeypatch.setattr(ToolRegistry, 'init_async', AsyncMock())
    monkeypatch.setattr(ToolRegistry, 'list_tools', lambda: tools)


def policy(tmp_path, **updates):
    return MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'],
                            tool='sangfor_xdr_incidents__xdr', **updates)


async def test_discovery_advertises_alert_reads_only_on_bound_xdr(monkeypatch, tmp_path):
    setup_registry(monkeypatch, [device(), device('other')], [
        tool('sangfor_xdr_incidents__xdr', fields=INCIDENT_FIELDS), tool('sangfor_xdr_alerts__xdr'),
        tool('sangfor_xdr_alerts__other', provider='other')])
    catalog, unavailable = await c.discover(policy(tmp_path))
    assert [(cap.kind, cap.device) for cap in catalog] == [('xdr', 'xdr'), ('xdr_alerts', 'xdr')]
    assert catalog[1].entities == ('alerts', 'alert_proof')
    assert unavailable == []
    assert c.for_event(catalog, {'device': 'other'}) == []


@pytest.mark.parametrize('case', ['missing', 'disabled', 'confirmation', 'schema', 'actions', 'credentials', 'ambiguous', 'override'])
async def test_old_or_unsafe_alert_tool_does_not_block_incident_capability(monkeypatch, tmp_path, case):
    target = device()
    candidate = tool('sangfor_xdr_alerts__xdr')
    candidates = [candidate]
    if case == 'missing':
        candidates = []
    elif case == 'disabled':
        candidate.enabled = False
    elif case == 'confirmation':
        candidate.requires_confirmation = True
    elif case == 'schema':
        candidates = [tool('sangfor_xdr_alerts__xdr', fields=ALERT_FIELDS - {'uuids'})]
    elif case == 'actions':
        candidates = [tool('sangfor_xdr_alerts__xdr', actions=['list', 'update_status'])]
    elif case == 'credentials':
        target.fields_set = {'host': True, 'auth_code': False}
    elif case == 'ambiguous':
        candidates.append(tool('sangfor_xdr_alerts__duplicate'))
    elif case == 'override':
        candidates = [tool('sangfor_xdr_alerts__xdr', fields=ALERT_FIELDS | {'url'})]
    setup_registry(monkeypatch, [target], [tool('sangfor_xdr_incidents__xdr', fields=INCIDENT_FIELDS), *candidates])
    catalog, unavailable = await c.discover(policy(tmp_path))
    assert all(cap.kind != 'xdr_alerts' for cap in catalog)
    assert unavailable and any('HTTP' in reason for reason in unavailable)
    if case != 'credentials':
        assert [cap.kind for cap in catalog] == ['xdr']


@pytest.fixture
def query_case(tmp_path, monkeypatch):
    bound = policy(tmp_path)
    capability = c.Capability('alerts-cap', 'xdr', 'sangfor_xdr_alerts__xdr', 'xdr_alerts', 'Fixture XDR', 'unknown')
    event = {'id': 'incident-original', 'device': 'xdr', 'host': '192.0.2.1',
             'startTime': 1, 'alertIds': ['a-1', 'a-2'], 'investigationWindow': {'start': 1, 'end': 101}}
    monkeypatch.setattr(c, 'assert_active', AsyncMock())
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([capability], [])))
    seen = []

    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        seen.append(params)
        output = ({'data': {'uuId': params['uuid'], 'proof': {'requestBody': 'id=1'}}}
                  if params['action'] == 'get_proof' else
                  {'data': {'item': [{'uuId': value} for value in params['uuids']], 'total': len(params['uuids'])}})
        result = ToolResult(success=True, output=output)
        capture_output(ctx, name, result)
        return result

    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    return SimpleNamespace(policy=bound, capability=capability, event=event, seen=seen)


async def test_alert_list_pins_ids_time_and_read_parameters(query_case):
    case = query_case
    with monitoring_read_scope(case.policy.tool, case.policy.devices):
        value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')
    assert params == {'action': 'list', 'uuids': ['a-1', 'a-2'], 'start_time': 1, 'end_time': 101,
                      'time_field': 'lastTime', 'page_num': 1, 'page_size': 5}
    assert case.seen == [{**params, 'device_id': 'xdr'}]
    assert value['_monitor_partial'] is False


async def test_alert_list_limits_five_and_marks_missing_records(query_case, monkeypatch):
    case = query_case
    case.event['alertIds'] = [f'a-{n}' for n in range(8)]
    value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')
    assert params['uuids'] == ['a-0', 'a-1', 'a-2', 'a-3', 'a-4']
    assert len(value['data']['item']) == 5 and value['_monitor_partial']
    safe, partial = c.facts(value)
    assert partial and '_monitor_partial' not in safe
    value = c._bound_alert_response({'data': {'item': [], 'total': 0}}, {'action': 'list', 'uuids': ['a-1']})
    assert value['_monitor_partial']


async def test_alert_list_includes_incident_start_before_current_day(query_case):
    case = query_case
    case.event.update(startTime=10, investigationWindow={'start': 200000, 'end': 200100})
    value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')
    assert params['start_time'] == 10 and params['end_time'] == 200100
    assert value['_monitor_partial'] is False
    del case.event['startTime']
    value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')
    assert params['start_time'] == 200000 and value['_monitor_partial']


async def test_alert_proof_uses_only_original_device_related_id(query_case):
    case = query_case
    with monitoring_read_scope(case.policy.tool, case.policy.devices):
        value, params = await c.query(case.policy, 'session', 'message', case.capability, case.event,
                                      'alert_proof', alert_id='a-2')
    assert params == {'action': 'get_proof', 'uuid': 'a-2'}
    assert value['data']['uuId'] == 'a-2'
    for event, kind, identifier in [({**case.event, 'device': 'other'}, 'alert_proof', 'a-2'),
                                    (case.event, 'alert_proof', 'unrelated'),
                                    (case.event, 'update_status', 'a-2'),
                                    (case.event, 'alerts', 'a-2'),
                                    ({**case.event, 'alertIds': []}, 'alerts', '')]:
        with pytest.raises(adapter.ContractError):
            await c.query(case.policy, 'session', 'message', case.capability, event, kind, alert_id=identifier)
    assert len(case.seen) == 1


@pytest.mark.parametrize('body', [{'data': {'uuId': 'another-alert', 'proof': {}}},
                                  {'data': {'proof': {}}}])
async def test_alert_proof_rejects_response_for_unrelated_or_missing_id(query_case, monkeypatch, body):
    case = query_case
    async def execute(name, ctx, **params):
        result = ToolResult(success=True, output=body)
        capture_output(ctx, name, result)
        return result
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    with pytest.raises(adapter.ContractError, match='编号'):
        await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alert_proof', alert_id='a-1')


@pytest.mark.parametrize('items', [[{'uuId': 'another-alert'}], [{'name': 'no-id'}],
                                  [{'uuId': ['a-1']}], [{'uuId': {'id': 'a-1'}}], ['not-an-object']])
async def test_alert_list_rejects_response_cross_talk(query_case, monkeypatch, items):
    case = query_case
    async def execute(name, ctx, **params):
        result = ToolResult(success=True, output={'data': {'item': items, 'total': 1}})
        capture_output(ctx, name, result)
        return result
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    with pytest.raises(adapter.ContractError, match='原事件之外'):
        await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')


async def test_alert_exact_call_scope_rejects_hook_target_action_and_parameter_changes(query_case, monkeypatch):
    case = query_case
    async def execute(name, ctx, **params):
        for changed in ({**params, 'device_id': 'other'}, {**params, 'action': 'update_status'},
                        {**params, 'uuid': 'unrelated'}, {**params, 'url': 'https://unrelated.invalid'}):
            with pytest.raises(PermissionError):
                await require_monitor_read(name, changed, ctx.session_id)
        await require_monitor_read(name, params, ctx.session_id)
        result = ToolResult(success=True, output={'data': {'uuId': 'a-1'}})
        capture_output(ctx, name, result)
        return result
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alert_proof', alert_id='a-1')


async def test_alert_capability_revoked_since_discovery_cannot_execute(query_case, monkeypatch):
    case = query_case
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([], ['disabled'])))
    with pytest.raises(adapter.ContractError, match='已停用'):
        await c.query(case.policy, 'session', 'message', case.capability, case.event, 'alerts')
    assert case.seen == []


@pytest.mark.parametrize('data', [[], {}, None, [{'alertTimeLine': [{'alertId': 'unbound'}]}],
                                 [{'uuId': 'incident-other', 'alertTimeLine': [{'alertId': 'unbound'}]}],
                                 [{'uuId': 'incident-original'}, {'uuId': 'incident-other'}]])
async def test_incident_proof_cannot_promote_unbound_timeline_alerts(query_case, monkeypatch, data):
    case = query_case
    capability = c.Capability('incident-cap', 'xdr', case.policy.tool, 'xdr', 'Fixture XDR', 'unknown')
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([capability], [])))
    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        result = ToolResult(success=True, output={'code': 'Success', 'data': data})
        capture_output(ctx, name, result)
        return result
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    with pytest.raises(adapter.ContractError, match='原事件编号'):
        await c.query(case.policy, 'session', 'message', capability, case.event, 'proof')


async def test_incident_proof_accepts_documented_original_event_id(query_case, monkeypatch):
    case = query_case
    capability = c.Capability('incident-cap', 'xdr', case.policy.tool, 'xdr', 'Fixture XDR', 'unknown')
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([capability], [])))
    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        assert params == {'action': 'get_proof', 'uuid': 'incident-original', 'device_id': 'xdr'}
        result = ToolResult(success=True, output={'code': 'Success', 'data': [
            {'uuId': 'incident-original', 'alertTimeLine': [{'alertId': 'a-1', 'lastTime': 90}]}]})
        capture_output(ctx, name, result)
        return result
    monkeypatch.setattr(ToolRegistry, 'execute', execute)
    value, params = await c.query(case.policy, 'session', 'message', capability, case.event, 'proof')
    assert value['data'][0]['alertTimeLine'][0]['alertId'] == 'a-1'
    assert params == {'action': 'get_proof', 'uuid': 'incident-original'}


def test_normalize_preserves_basics_times_and_only_exact_string_alert_ids():
    result = adapter.normalize('xdr', {'uuId': 'incident-original', 'name': 'Web攻击', 'hostIp': '192.0.2.1',
                                     'incidentSeverity': 3, 'startTime': 1, 'endTime': 100,
                                     'alertIds': ['a-1', 'a-1', 5, {'uuId': 'forged'}, 'x' * 201]})
    assert result['startTime'] == 1 and result['endTime'] == 100
    assert result['alertIds'] == ['a-1'] and isinstance(result['basics'], dict)


def test_related_alert_id_validation_does_not_coerce_or_change_ids():
    assert c.related_alert_ids({'alertIds': ['a-1', 'a-1', 3, {}, '.', '..', '../bad', 'x' * 201]}) == ['a-1']
