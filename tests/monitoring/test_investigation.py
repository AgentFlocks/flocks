import asyncio
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from flocks.monitoring import investigation as i, capabilities as c
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import connection, rows, write
from flocks.session.interaction_policy import monitoring_read_scope, investigation_call_scope, require_monitor_read

discover_capabilities = c.discover
query_capability = c.query


class Recorder:
    session_id = 'test-session'
    def __init__(self):
        self.calls = []
    async def call(self, name, params, op):
        self.calls.append((name, params))
        return (await op('test-message'))[0]


@pytest.fixture
async def case(tmp_path, monkeypatch):
    policy = MonitoringPolicy(owner='owner', project='project', directory=str(tmp_path), devices=['xdr'],
                              investigation_engine='agent-v1', timeout_seconds=1200)
    event = {'key': 'xdr:incident:original', 'id': 'original', 'device': 'xdr', 'name': 'Synthetic',
             'host': '192.0.2.1', 'endTime': 100, 'dealStatus': 0, 'development_sample': False,
             'investigationWindow': {'start': 1, 'end': 101}}
    async with connection() as db:
        await i.enqueue(db, policy, event, datetime.now(timezone.utc).isoformat())
    catalog = [c.Capability('cap-1', 'xdr', policy.tool, 'xdr', 'Synthetic XDR', 'unknown'),
               c.Capability('cap-2', 'tdp', 'tdp_log_search', 'tdp', 'Synthetic TDP', 'unknown')]
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=(catalog, [])))
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[]))
    query = AsyncMock(return_value=({'data': {'item': [{'hostIp': '192.0.2.1', 'threatLevel': 3}]}}, {'action': 'get_entities'}))
    monkeypatch.setattr(c, 'query', query)
    return SimpleNamespace(policy=policy, event=event, recorder=Recorder(), query=query)


async def decide(agent, data):
    if not data['evidence']:
        return i.Choice(action='query', capability='cap-1', entity='host', reason='核查原主机')
    return i.Choice(action='finish', verdict='risk', evidence_ids=['evidence-1'], reason='取得主机证据，仍需负责人核查')


async def test_model_selects_query_then_cited_conclusion_and_reuses_saved_result(case, monkeypatch):
    model = AsyncMock(side_effect=decide)
    monkeypatch.setattr(i, 'choose', model)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert case.query.await_count == 1 and model.await_count == 2
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 1 and model.await_count == 2
    assert not await rows('SELECT * FROM monitor_mail_notices')
    assert not await rows('SELECT * FROM monitor_dispositions')


async def test_cancel_after_evidence_resumes_without_repeating_query(case, monkeypatch):
    async def interrupted(agent, data):
        if data['evidence']:
            raise asyncio.CancelledError()
        return await decide(agent, data)
    monkeypatch.setattr(i, 'choose', interrupted)
    with pytest.raises(asyncio.CancelledError):
        await i.investigate(case.policy, case.event, case.recorder)
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['state'] == 'deferred' and len(json.loads(saved['evidence'])) == 1
    assert not await i.pending(case.policy)
    future = i.now() + timedelta(minutes=11)
    monkeypatch.setattr(i, 'now', lambda: future)
    assert len(await i.pending(case.policy)) == 1
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready' and case.query.await_count == 1


async def test_repeated_cancellation_defers_without_exhausting_system_retries(case, monkeypatch):
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    model = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(i, 'choose', model)
    for _ in range(3):
        with pytest.raises(asyncio.CancelledError):
            await i.investigate(case.policy, case.event, case.recorder)
        await i.investigate(case.policy, case.event, case.recorder)
        clock[0] += timedelta(minutes=11)
    assert model.await_count == 3
    assert case.event['investigation']['state'] == 'deferred'
    assert len(await i.pending(case.policy)) == 1
    assert (await rows('SELECT failure_count FROM monitor_investigations'))[0]['failure_count'] == 0


async def test_forged_reference_or_target_cannot_execute_or_complete(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', AsyncMock(return_value=i.Choice(action='query', capability='not-present', reason='invalid')))
    await i.investigate(case.policy, case.event, case.recorder)
    assert not case.query.called
    monkeypatch.setattr(i, 'choose', AsyncMock(return_value=i.Choice(action='finish', verdict='benign', evidence_ids=['invented'], reason='invalid')))
    await i.investigate(case.policy, case.event, case.recorder)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'system_wait'
    assert not case.event['investigation']['retry_exhausted']
    assert not await i.pending(case.policy)


async def test_null_or_large_evidence_cannot_be_all_clear(case, monkeypatch):
    case.query.return_value = ({'data': {'item': None}}, {})
    async def benign(agent, data):
        if not data['evidence']:
            return await decide(agent, data)
        return i.Choice(action='finish', verdict='benign', evidence_ids=['evidence-1'], reason='no risk')
    monkeypatch.setattr(i, 'choose', benign)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['verdict'] == 'unknown'
    safe, partial = c.facts({'data': {'item': [{'name': str(n), 'Authorization': 'private'} for n in range(707)]}})
    assert partial and 'private' not in json.dumps(safe) and len(safe['data']['item']) <= 20


async def test_case_change_invalidates_evidence_but_project_and_mode_remain_scoped(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    changed = {**case.event, 'endTime': 101}
    async with connection() as db:
        await i.enqueue(db, case.policy, changed, '2026-09-25T00:00:00+00:00')
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['state'] == 'pending' and saved['evidence'] == '[]'
    assert not await i.pending(case.policy.model_copy(update={'project': 'other'}))
    assert not await i.pending(case.policy.model_copy(update={'development_sample': True}))


async def test_pending_batch_filters_old_device_and_mode_before_limiting(case):
    async with connection() as db:
        for index in range(25):
            event = {**case.event, 'key': f'old-{index}', 'device': 'removed-device'}
            await i.enqueue(db, case.policy, event, '2000-01-01T00:00:00+00:00')
            event = {**case.event, 'key': f'legacy-sample-{index}', 'development_sample': True}
            await i.enqueue(db, case.policy, event, '2000-01-01T00:00:00+00:00')
    assert [event['key'] for event in await i.pending(case.policy)] == [case.event['key']]
    assert not await i.pending(case.policy.model_copy(update={'devices': []}))


@pytest.mark.parametrize('kind,tool', [('tdp', 'tdp_log_search'), ('sig', 'onesig_strategy_api_query')])
async def test_cross_device_query_pins_original_ip_window_and_read_action(case, monkeypatch, kind, tool):
    from flocks.tool.registry import ToolResult
    from flocks.tool.structured_output import capture_output
    capability = c.Capability('cap-2', kind, tool, kind, 'Synthetic secondary device', 'unknown')
    monkeypatch.setattr(c, 'assert_active', AsyncMock())
    monkeypatch.setattr(c, 'discover', AsyncMock(return_value=([capability], [])))
    skill = AsyncMock(return_value='loaded')
    monkeypatch.setattr(c, 'tdp_skill', skill)
    monkeypatch.setattr(c, 'tdp_timestamp', AsyncMock(return_value=99))
    async def execute(name, ctx, **params):
        await require_monitor_read(name, params, ctx.session_id)
        assert params['device_id'] == kind
        result = ToolResult(success=True, output={'data': {'items': [{'hostIp': '192.0.2.1'}]}})
        capture_output(ctx, name, result)
        return result
    call = AsyncMock(side_effect=execute)
    monkeypatch.setattr(c.ToolRegistry, 'execute', call)
    with monitoring_read_scope(case.policy.tool, case.policy.devices):
        value, params = await query_capability(case.policy, 'session', 'message', capability, case.event, 'related')
    assert value['data']['items'][0]['hostIp'] == case.event['host']
    if kind == 'tdp':
        assert params == {'action': 'search', 'time_from': 1, 'time_to': 99,
            'net_data_type': ['attack', 'risk', 'action'], 'size': 50,
            'sql': "net.src_ip = '192.0.2.1' OR net.dest_ip = '192.0.2.1'"}
        assert skill.await_count == 1
    else:
        assert params == {'action': 'asset_list', 'body': {'pageNo': 1, 'pageSize': 50, 'search': '192.0.2.1'}}
    for host in ("192.0.2.1' OR 1=1", 'fe80::1%interface'):
        with pytest.raises(ContractError, match='有效主机 IP'):
            await query_capability(case.policy, 'session', 'message', capability, {**case.event, 'host': host}, 'related')
    assert call.await_count == 1


async def test_scope_pins_target_params_session_and_child_cannot_widen():
    with monitoring_read_scope('xdr', ['xdr-device']):
        with pytest.raises(PermissionError):
            await require_monitor_read('tdp', {'device_id': 'tdp-device', 'action': 'search'}, 'session')
        with investigation_call_scope('tdp', 'tdp-device', {'action': 'search', 'size': 50}, 'session'):
            await require_monitor_read('tdp', {'device_id': 'tdp-device', 'action': 'search', 'size': 50}, 'session')
            await require_monitor_read('tdp', {'action': 'search', 'size': 50}, 'session', resolved_device='tdp-device')
            for tool, device, action, size, session in [('tdp', 'other', 'search', 50, 'session'),
                    ('tdp', 'tdp-device', 'delete', 50, 'session'), ('tdp', 'tdp-device', 'search', 5000, 'session'),
                    ('bash', 'tdp-device', 'search', 50, 'session'), ('tdp', 'tdp-device', 'search', 50, 'other')]:
                with pytest.raises(PermissionError):
                    await require_monitor_read(tool, {'device_id': device, 'action': action, 'size': size}, session)
            async def child():
                with investigation_call_scope('tdp', 'other', {'action': 'search'}, 'session'):
                    pass
            with pytest.raises(PermissionError):
                await asyncio.create_task(child())
        with pytest.raises(PermissionError):
            await require_monitor_read('tdp', {'device_id': 'tdp-device', 'action': 'search', 'size': 50}, 'session')


async def test_bounded_query_retries_and_no_nested_delegation(case, monkeypatch):
    case.query.side_effect = ContractError('synthetic query failed')
    monkeypatch.setattr(i, 'choose', AsyncMock(return_value=i.Choice(action='query', capability='cap-1', entity='host', reason='retry')))
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 2
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 2


async def test_twenty_minute_round_is_allowed_but_unbounded_timeout_is_rejected(case):
    assert case.policy.timeout_seconds > 600
    with pytest.raises(ValueError):
        MonitoringPolicy(**{**case.policy.model_dump(), 'timeout_seconds': 3600})


async def test_overrun_coalesces_ticks_and_old_backlog_without_touching_running(case):
    from flocks.task.manager import TaskManager
    from flocks.task.models import TaskTrigger, SchedulerMode, ExecutionTriggerType
    from flocks.task.store import TaskStore
    from flocks.task.queue import TaskQueue
    from flocks.monitoring.scheduling import admit_slots
    scheduler = await TaskManager.create_scheduler(title='long investigation', mode=SchedulerMode.CRON,
                trigger=TaskTrigger(cron='*/10 * * * *'), context={'monitoring': case.policy.model_dump()})
    stamp = datetime(2026, 9, 25, 1, tzinfo=timezone.utc)
    scheduler.trigger.next_run = stamp
    await TaskStore.update_scheduler(scheduler)
    await admit_slots(scheduler, stamp)
    queue = TaskQueue()
    running = await queue.dequeue()
    assert running
    for _ in range(4):
        await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=True)
    await admit_slots(scheduler, stamp + timedelta(minutes=30))
    assert len(await rows("SELECT * FROM task_execution_queue_refs WHERE status='running'")) == 1
    assert len(await rows("SELECT * FROM task_execution_queue_refs WHERE status='queued'")) == 1
    assert len(await rows("SELECT * FROM monitor_slots WHERE status='coalesced'")) == 3
    assert not await queue.dequeue()
    await TaskStore.finish_queue_ref(running.id)
    queue.mark_finished(running.id)
    assert await queue.dequeue()
    assert not await queue.dequeue()


async def test_catalog_uses_bound_devices_current_tools_and_skips_unsafe_overrides(case, monkeypatch):
    from flocks.tool.device import store
    from flocks.tool.registry import ToolRegistry
    devices = [SimpleNamespace(id=name, enabled=True, storage_key=name, service_id=service,
                               name=name, status='unknown', fields_set=dict.fromkeys(['host', 'auth_code', 'base_url', 'api_key', 'secret'], True)) for name, service in
               [('xdr', 'sangfor_xdr'), ('tdp', 'tdp_api'), ('new-device', 'tdp_api'), ('edr', 'sangfor_edr')]]
    def info(name, provider, fields):
        return SimpleNamespace(name=name, provider=provider, source='device', enabled=True, requires_confirmation=False,
            get_schema=lambda: SimpleNamespace(to_json_schema=lambda: {'properties': dict.fromkeys(fields, {})}))
    tools = [info(case.policy.tool, 'xdr', ['action', 'uuid', 'entity_type']),
             info('tdp_log_search', 'tdp', ['action', 'time_from', 'time_to', 'sql', 'size']),
             info('tdp_log_search', 'new-device', ['action', 'time_from', 'time_to', 'sql', 'size'])]
    monkeypatch.setattr(store, 'list_devices', AsyncMock(return_value=devices))
    monkeypatch.setattr(store, 'get_device_tool_enabled', AsyncMock(return_value=None))
    monkeypatch.setattr(ToolRegistry, 'init_async', AsyncMock())
    monkeypatch.setattr(ToolRegistry, 'list_tools', lambda: tools)
    monkeypatch.setattr(c, 'tdp_skill', AsyncMock(return_value='loaded'))
    policy = case.policy.model_copy(update={'correlation_devices': ['tdp', 'edr']})
    found, unavailable = await discover_capabilities(policy)
    assert {cap.device for cap in found} == {'xdr', 'tdp'}
    assert any('受控设备绑定' in reason for reason in unavailable)
    tools[1] = info('tdp_log_search', 'tdp', ['action', 'time_from', 'time_to', 'sql', 'size', 'base_url'])
    assert [cap.kind for cap in (await discover_capabilities(policy))[0]] == ['xdr']


async def test_specialist_shares_query_budget_and_only_uses_declared_tools(case, monkeypatch):
    specialist = SimpleNamespace(name='security-specialist', description='correlate', delegatable=True,
                                hidden=False, tags=['security'], tools=['tdp_log_search'])
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[specialist]))
    monkeypatch.setattr(i.Agent, 'get', AsyncMock(return_value=specialist))
    async def planner(agent, data):
        if agent == 'security-monitor' and not data['specialist_assessments']:
            return i.Choice(action='consult', agent='security-specialist', reason='核对同一时间的网络候选')
        if not data['evidence']:
            assert [cap['kind'] for cap in data['capabilities']] == ['tdp']
            assert data['agents'] == []
            return i.Choice(action='query', capability='cap-2', entity='related', reason='查原主机的网络告警')
        return i.Choice(action='finish', verdict='unknown', evidence_ids=['evidence-1'], reason='仅为同 IP 候选，身份尚未核对')
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready' and case.query.await_count == 1
    assert case.query.call_args.args[3].kind == 'tdp'


async def test_model_uses_configured_provider_and_installed_agent_contract(monkeypatch):
    from flocks.hub.installer import install_plugin
    from flocks.monitoring.agent_component import resolve
    await install_plugin('agent', 'security-monitor')
    agent = await resolve()
    assert agent.prompt and '同 IP' in agent.prompt and agent.tools == []
    monkeypatch.setattr(i.Config, 'resolve_default_llm', AsyncMock(return_value={'provider_id': 'fixture', 'model_id': 'fixture-model'}))
    monkeypatch.setattr(i.Provider, 'apply_config', AsyncMock())
    chat = AsyncMock(return_value=SimpleNamespace(tool_calls=[], finish_reason='stop', content='```json\n' +
        i.Choice(action='query', capability='cap-1', reason='read original event').model_dump_json() + '\n```'))
    monkeypatch.setattr(i.Provider, 'get', lambda _: SimpleNamespace(chat=chat))
    assert (await i.choose('security-monitor', {'event': {'id': 'original'}})).action == 'query'
    assert chat.call_args.args[0] == 'fixture-model' and 'tools' not in chat.call_args.kwargs
    chat.return_value.content = '{"action":"query","reason":"bad","shell":"execute"}'
    with pytest.raises(ContractError):
        await i.choose('security-monitor', {})


async def test_empty_lookup_cannot_establish_benign_case(case, monkeypatch):
    case.query.return_value = ({'data': {'item': []}}, {})
    async def planner(agent, data):
        if not data['evidence']:
            return await decide(agent, data)
        return i.Choice(action='finish', verdict='benign', evidence_ids=['evidence-1'], reason='nothing found')
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert case.event['investigation']['verdict'] == 'unknown'
    assert '保留待判定' in case.event['investigation']['reason']


@pytest.fixture
async def configured_model(monkeypatch):
    from flocks.hub.installer import install_plugin
    await install_plugin('agent', 'security-monitor')
    monkeypatch.setattr(i.Config, 'resolve_default_llm', AsyncMock(return_value={'provider_id': 'fixture', 'model_id': 'fixture-model'}))
    monkeypatch.setattr(i.Provider, 'apply_config', AsyncMock())
    response = SimpleNamespace(tool_calls=[], finish_reason='stop', content=i.Choice(action='query', capability='cap-1', reason='read original event').model_dump_json())
    chat = AsyncMock(return_value=response)
    monkeypatch.setattr(i.Provider, 'get', lambda _: SimpleNamespace(chat=chat))
    diagnostics = []
    monkeypatch.setattr(i.diag, 'event', lambda stage, **fields: diagnostics.append({'stage': stage, **fields}))
    return response, diagnostics


@pytest.mark.parametrize('finish', ['stop', 'end_turn', 'completed'])
async def test_supported_provider_completion_reasons_are_validated(configured_model, finish):
    response, diagnostics = configured_model
    response.finish_reason = finish
    choice = await i.choose('security-monitor', {})
    assert choice.action == 'query' and choice.capability == 'cap-1'
    assert len(diagnostics) == 1
    assert diagnostics[0] == {'stage': 'investigation.model', 'model_stop': finish,
        'has_tool_calls': False, 'length': len(response.content), 'model_provider': 'fixture',
        'model_id': 'fixture-model', 'request_max_tokens': 2500, 'correction_attempt': 0,
        'elapsed_ms': diagnostics[0]['elapsed_ms']}


@pytest.mark.parametrize('kind,message', [
    ('length', '长度上限'), ('max_tokens', '长度上限'),
    ('tool_calls', '未开放的工具调用'), ('tool_stop', '未正常结束'),
    ('empty', '空决策'), ('null', '空决策'), ('empty_json', '格式无效'),
    ('malformed', '格式无效'), ('unknown_stop', '未正常结束'),
])
async def test_incomplete_decisions_fail_with_safe_diagnostics(configured_model, kind, message):
    response, diagnostics = configured_model
    if kind in {'length', 'max_tokens'}:
        response.finish_reason = kind
    elif kind == 'tool_calls':
        response.tool_calls = [{'name': 'unauthorized', 'arguments': {'private': 'sensitive'}}]
        response.finish_reason = 'tool_calls'
    elif kind == 'tool_stop':
        response.finish_reason = 'tool_calls'
    elif kind == 'empty':
        response.content = '  '
    elif kind == 'null':
        response.content = None
    elif kind == 'empty_json':
        response.content = '{}'
    elif kind == 'malformed':
        response.content = 'sensitive: malformed response'
    else:
        response.finish_reason = 'sensitive: provider detail'
    with pytest.raises(ContractError, match=message) as failure:
        await i.choose('security-monitor', {'input': 'sensitive'})
    assert 'sensitive' not in str(failure.value)
    calls = [record for record in diagnostics if record['stage'] == 'investigation.model']
    assert len(calls) == (1 if kind in {'tool_calls', 'tool_stop', 'unknown_stop'} else 2)
    assert all(record['model_id'] == 'fixture-model' for record in calls)
    assert all('content' not in record and 'reasoning' not in record for record in diagnostics)
    assert 'sensitive' not in json.dumps(diagnostics)
    assert not await rows('SELECT * FROM monitor_dispositions')


def test_capability_alias_is_device_bound_and_ambiguous_alias_is_rejected():
    catalog = [c.Capability('cap-1', 'other-xdr', 'sangfor_xdr_incidents', 'xdr', 'Other', 'unknown'),
               c.Capability('cap-6', 'original-xdr', 'sangfor_xdr_incidents', 'xdr', 'Original', 'unknown'),
               c.Capability('cap-7', 'network-a', 'network_search', 'sig', 'A', 'unknown'),
               c.Capability('cap-8', 'network-b', 'network_search', 'sig', 'B', 'unknown')]
    event = {'device': 'original-xdr'}
    cap, error = c.resolve_choice(catalog, event, 'sangfor_xdr_incidents')
    assert not error and cap.id == 'cap-6'
    assert c.resolve_choice(catalog, event, 'cap-1') == (None, 'wrong_device')
    assert c.resolve_choice(catalog, event, 'network_search') == (None, 'ambiguous_capability')
    assert c.resolve_choice(catalog, event, 'sangfor') == (None, 'unknown_capability')
    assert 'related' not in cap.json()['entities']


async def test_tool_name_alias_from_reported_failure_completes_bound_investigation(case, monkeypatch):
    async def planner(agent, data):
        if not data['evidence']:
            return i.Choice(action='query', capability=case.policy.tool, entity='host', reason='核对原事件主机')
        return await decide(agent, data)
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert case.query.await_args.args[3].device == 'xdr'
    assert case.query.await_count == 1


@pytest.mark.parametrize('bad,expected_field,expected_code', [
    ({'action': 'query', 'reason': 'test', 'capability': 'cap-1', 'entity': 'related'}, 'entity', 'unsupported_entity'),
    ({'action': 'query', 'reason': 'test', 'capability': 'does-not-exist'}, 'capability', 'unknown_capability'),
    ({'action': 'finish', 'reason': 'test', 'evidence_ids': ['invented']}, 'evidence_ids', 'invalid_reference'),
    ({'action': 'query', 'reason': 'test', 'capability': 'cap-1', 'untrusted_password': 'secret'}, 'document', 'extra_field'),
    ({'action': 'query'}, 'reason', 'missing'),
])
async def test_one_local_correction_repairs_schema_or_action_before_any_tool_call(case, configured_model, bad, expected_field, expected_code):
    response, diagnostics = configured_model
    chat = i.Provider.get('fixture').chat
    invalid = SimpleNamespace(tool_calls=[], finish_reason='stop', content=json.dumps(bad))
    valid = SimpleNamespace(tool_calls=[], finish_reason='stop', content=i.Choice(
        action='query', capability='cap-1', entity='host', reason='核对绑定的事件').model_dump_json())
    chat.side_effect = [invalid, valid]
    catalog, _ = await c.discover(case.policy)
    data = {'event': case.event, 'capabilities': [cap.json() for cap in catalog], 'evidence': [], 'agents': []}
    choice = await i.choose('security-monitor', data)
    assert choice.capability == 'cap-1' and choice.entity == 'host'
    assert chat.await_count == 2 and not case.query.called
    repair = json.loads(chat.await_args.args[1][-1].content)
    assert {'field': expected_field, 'code': expected_code} in repair['correction']
    assert 'secret' not in json.dumps(repair) and 'secret' not in json.dumps(diagnostics)
    assert 'query_options' in chat.await_args.args[1][0].content
    assert 'response_format' not in chat.await_args.kwargs


async def test_truncated_response_is_not_executed_and_one_short_correction_succeeds(case, configured_model):
    _, diagnostics = configured_model
    chat = i.Provider.get('fixture').chat
    chat.side_effect = [SimpleNamespace(tool_calls=[], finish_reason='length', content='',
        usage={'prompt_tokens': 1500, 'completion_tokens': 2500, 'total_tokens': 4000}),
        SimpleNamespace(tool_calls=[], finish_reason='stop', content=i.Choice(action='query', capability='cap-1', reason='核对').model_dump_json(),
        usage={'prompt_tokens': 1550, 'completion_tokens': 120, 'total_tokens': 1670})]
    choice = await i.choose('security-monitor', {})
    assert choice.action == 'query' and chat.await_count == 2
    calls = [record for record in diagnostics if record['stage'] == 'investigation.model']
    assert calls[0]['output_tokens'] == 2500 and calls[1]['output_tokens'] == 120
    assert calls[0]['model_provider'] == 'fixture' and calls[0]['model_id'] == 'fixture-model'
    assert not case.query.called


async def test_correction_is_counted_in_shared_budget_and_cannot_exceed_it(configured_model):
    response, _ = configured_model
    response.content = '{}'
    chat = i.Provider.get('fixture').chat
    budget = i.Budget()
    token = i._MODEL_BUDGET.set((budget, 1))
    try:
        with pytest.raises(i.InvestigationError) as failure:
            await i.choose('security-monitor', {})
    finally:
        i._MODEL_BUDGET.reset(token)
    assert failure.value.kind == 'budget'
    assert budget.models == chat.await_count == 1


async def test_model_options_respect_configured_limits_without_forcing_api_extensions(monkeypatch):
    import flocks.provider.options as options
    monkeypatch.setattr(options, 'build_provider_options', lambda *args, **kwargs: {'max_tokens': 6000})
    provider = SimpleNamespace(get_model_definitions=lambda: [SimpleNamespace(id='configured',
        limits=SimpleNamespace(max_output_tokens=3200))])
    assert i.model_options(provider, {'provider_id': 'custom', 'model_id': 'configured'}) == {'max_tokens': 3200}
    monkeypatch.setattr(options, 'build_provider_options', lambda *args, **kwargs: {'max_tokens': 600,
        'thinking': {'type': 'enabled', 'budget_tokens': 1024}})
    with pytest.raises(i.InvestigationError) as failure:
        i.model_options(provider, {'provider_id': 'custom', 'model_id': 'configured'})
    assert failure.value.kind == 'dependency'


async def test_system_errors_have_durable_backoff_and_explicit_recovery(case, monkeypatch):
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    model = AsyncMock(side_effect=i.InvestigationError('模型不可用', 'dependency'))
    monkeypatch.setattr(i, 'choose', model)
    for attempt, delay in [(1, 10), (2, 20), (3, None)]:
        await i.investigate(case.policy, case.event, case.recorder)
        state = case.event['investigation']
        assert state['state'] == 'system_wait' and state['error_kind'] == 'dependency'
        assert state['retry_exhausted'] == (delay is None)
        assert not await i.pending(case.policy)
        # Repeated list-query observations/restarts inside cooldown do not call the model.
        await i.investigate(case.policy, case.event, case.recorder)
        assert model.await_count == attempt
        if delay:
            assert i.parse_time(state['next_retry_at']) == clock[0] + timedelta(minutes=delay)
            clock[0] += timedelta(minutes=delay, seconds=1)
            assert len(await i.pending(case.policy)) == 1
    summary = await i.status_summary(case.policy)
    assert summary['system_wait'] == summary['retry_exhausted'] == 1
    assert summary['needs_review'] == 0 and summary['earliest_retry_at'] is None
    assert await i.retry_waiting(case.policy.model_copy(update={'project': 'other'})) == 0
    assert await i.retry_waiting(case.policy, 'missing') == 0
    assert await i.retry_waiting(case.policy) == 1
    assert len(await i.pending(case.policy)) == 1
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert (await i.status_summary(case.policy))['system_wait'] == 0


async def test_event_updates_preserve_history_and_cannot_bypass_system_cooldown(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', AsyncMock(side_effect=i.InvestigationError('暂时不可用', 'dependency')))
    await i.investigate(case.policy, case.event, case.recorder)
    original = (await rows('SELECT * FROM monitor_investigations'))[0]
    changed = {**case.event, 'endTime': 120}
    async with connection() as db:
        await i.enqueue(db, case.policy, changed, i.now().isoformat())
    current = (await rows('SELECT * FROM monitor_investigations'))[0]
    history = (await rows('SELECT * FROM monitor_investigation_versions'))[0]
    assert current['next_retry_at'] == original['next_retry_at']
    assert current['failure_count'] == original['failure_count']
    assert current['state'] == 'system_wait' and current['revision'] == 2
    assert json.loads(history['event'])['endTime'] == 100
    assert history['result'] == original['result']
    assert not await i.pending(case.policy)


async def test_stale_or_updated_event_evidence_is_archived_and_requeried(case, monkeypatch):
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    clock[0] += timedelta(minutes=31)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 2
    archived = await rows('SELECT * FROM monitor_investigation_versions')
    assert len(archived) == 1 and len(json.loads(archived[0]['evidence'])) == 1
    changed = {**case.event, 'endTime': 130}
    async with connection() as db:
        await i.enqueue(db, case.policy, changed, clock[0].isoformat())
    await i.investigate(case.policy, changed, case.recorder)
    assert case.query.await_count == 3
    archived = await rows('SELECT * FROM monitor_investigation_versions ORDER BY revision')
    assert [record['revision'] for record in archived] == [1, 2]
    assert all(json.loads(record['evidence'])[0]['event_revision'] == i.fingerprint(case.event) for record in archived)


async def test_exhausted_round_budget_defers_without_consuming_recovery_attempt(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', decide)
    budget = i.Budget()
    budget.calls = case.policy.investigation_calls
    await i.investigate(case.policy, case.event, case.recorder, budget)
    assert case.event['investigation']['state'] == 'deferred'
    assert case.event['investigation']['error_kind'] == 'budget'
    assert not case.query.called
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['failure_count'] == 0 and not saved['retry_exhausted']


async def test_legacy_system_error_migration_keeps_business_review_and_evidence(case):
    # Build an actual old schema to exercise the additive migration once.
    from flocks.task.store import TaskStore
    import aiosqlite
    async with aiosqlite.connect(TaskStore.get_db_path()) as db:
        await db.execute('DROP TABLE monitor_investigations')
        await db.execute("CREATE TABLE monitor_investigations (owner TEXT,project TEXT,event_key TEXT,event TEXT,"
            "evidence TEXT,result TEXT,state TEXT,attempts INTEGER,updated_at TEXT,PRIMARY KEY(owner,project,event_key))")
        for key, reason in [('system', '调查模型输出达到长度上限，决策被截断'), ('business', '无法确认关联的是哪台资产')]:
            await db.execute('INSERT INTO monitor_investigations VALUES(?,?,?,?,?,?,?,?,?)',
                ('owner', 'project', key, json.dumps(case.event), '[{"saved":true}]',
                 json.dumps({'reason': reason}), 'needs_review', 3, i.now().isoformat()))
        for key, value in [('invalid', 'legacy malformed data'), ('null', None)]:
            await db.execute('INSERT INTO monitor_investigations VALUES(?,?,?,?,?,?,?,?,?)',
                ('owner', 'project', key, json.dumps(case.event), '[]', value,
                 'needs_review', 3, i.now().isoformat()))
        await db.commit()
    migrated = {row['event_key']: row for row in await rows('SELECT * FROM monitor_investigations')}
    assert migrated['system']['state'] == 'system_wait' and migrated['system']['retry_exhausted'] == 1
    assert migrated['system']['evidence'] == '[{"saved":true}]'
    assert migrated['business']['state'] == 'needs_review'
    assert migrated['invalid']['state'] == migrated['null']['state'] == 'needs_review'
    assert await i.retry_waiting(case.policy) == 1


async def test_failed_query_cannot_be_hidden_as_normal_budget_deferral(case, monkeypatch):
    case.policy = case.policy.model_copy(update={'investigation_calls': 1})
    case.query.side_effect = ContractError('查询服务暂时不可用')
    async def planner(agent, data):
        return i.Choice(action='query', capability='cap-1', entity='file' if data['evidence'] else 'host', reason='补查')
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 1
    assert case.event['investigation']['state'] == 'system_wait'
    assert case.event['investigation']['error_kind'] == 'query_transient'
    assert '关联查询失败' in case.event['investigation']['reason']


async def test_alternate_success_keeps_failed_source_gap_and_stable_evidence_ids(case, monkeypatch):
    case.query.side_effect = [ContractError('原主机来源暂时不可用'), ({'data': {'item': [{'fileName': 'synthetic'}]}}, {})]
    async def planner(agent, data):
        if not data['evidence']:
            return i.Choice(action='query', capability='cap-1', entity='host', reason='核对主机')
        if len(data['evidence']) == 1:
            return i.Choice(action='query', capability='cap-1', entity='file', reason='改查文件证据')
        return i.Choice(action='finish', verdict='risk', evidence_ids=['evidence-2'], reason='文件证据支持风险')
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready'
    assert any('查询失败的来源' in gap for gap in case.event['investigation']['gaps'])
    # Expire only the failed proof, retaining evidence-2. A further query must
    # allocate evidence-3 instead of silently reusing evidence-2.
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    evidence = json.loads(saved['evidence'])
    evidence[0]['retrieved_at'] = (i.now() - timedelta(hours=1)).isoformat()
    await write("UPDATE monitor_investigations SET state='pending',evidence=?", (json.dumps(evidence),))
    case.query.side_effect = None
    async def continued(agent, data):
        if len(data['evidence']) == 1:
            return i.Choice(action='query', capability='cap-1', entity='host', reason='恢复后重查')
        return i.Choice(action='finish', verdict='risk', evidence_ids=['evidence-2', 'evidence-3'], reason='已取得两项证据')
    monkeypatch.setattr(i, 'choose', continued)
    await i.investigate(case.policy, case.event, case.recorder)
    evidence = json.loads((await rows('SELECT evidence FROM monitor_investigations'))[0]['evidence'])
    assert [proof['id'] for proof in evidence] == ['evidence-2', 'evidence-3']
    assert case.event['investigation']['state'] == 'ready'


async def test_failed_query_can_resume_after_cooldown_without_old_failures_consuming_new_budget(case, monkeypatch):
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    case.query.side_effect = ContractError('服务断连')
    async def planner(agent, data):
        successful = [proof for proof in data['evidence'] if proof['success']]
        if successful:
            return i.Choice(action='finish', verdict='risk', evidence_ids=[successful[0]['id']], reason='已恢复取证')
        return i.Choice(action='query', capability='cap-1', entity='host', reason='查询原主机')
    monkeypatch.setattr(i, 'choose', planner)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 2 and case.event['investigation']['state'] == 'system_wait'
    clock[0] += timedelta(minutes=11)
    case.query.side_effect = None
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.query.await_count == 3 and case.event['investigation']['state'] == 'ready'
    assert not case.event['investigation']['gaps']


@pytest.mark.parametrize('error', [TimeoutError('private endpoint'), OSError('private credential'), RuntimeError('private provider response')])
async def test_provider_failure_records_actual_model_budget_and_safe_type(configured_model, error):
    _, diagnostics = configured_model
    chat = i.Provider.get('fixture').chat
    chat.side_effect = error
    with pytest.raises(i.InvestigationError) as failed:
        await i.choose('security-monitor', {})
    assert failed.value.kind == 'dependency'
    assert chat.await_count == 1  # Do not combine immediate connection retry with persistent retry.
    assert diagnostics[0]['model_provider'] == 'fixture'
    assert diagnostics[0]['model_id'] == 'fixture-model'
    assert diagnostics[0]['request_max_tokens'] == 2500
    assert diagnostics[0]['error_type'] == type(error).__name__
    assert diagnostics[0]['success'] is False
    assert 'private' not in str(failed.value) and 'private' not in json.dumps(diagnostics)


async def test_expired_ready_result_is_atomically_invalidated_before_a_crash(case, monkeypatch):
    class SimulatedCrash(BaseException):
        pass
    clock = [i.now()]
    monkeypatch.setattr(i, 'now', lambda: clock[0])
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    clock[0] += timedelta(minutes=31)
    original = i.Investigator.drive
    monkeypatch.setattr(i.Investigator, 'drive', AsyncMock(side_effect=SimulatedCrash))
    with pytest.raises(SimulatedCrash):
        await i.investigate(case.policy, case.event, case.recorder)
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert saved['state'] == 'pending' and saved['result'] == '{}' and saved['evidence'] == '[]'
    assert saved['revision'] == 2
    history = (await rows('SELECT * FROM monitor_investigation_versions'))[0]
    assert history['state'] == 'ready' and json.loads(history['result'])['verdict'] == 'risk'
    monkeypatch.setattr(i.Investigator, 'drive', original)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready' and case.query.await_count == 2


@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
@pytest.mark.parametrize('outcome', ['system_error', 'cancel'])
async def test_terminal_result_and_recovery_schedule_commit_together(case, monkeypatch, phase, outcome):
    class SimulatedCrash(BaseException):
        pass
    original = i.write
    async def crash_at_terminal_commit(sql, args=()):
        if 'failure_count=?' in sql:
            if phase == 'after_commit':
                await original(sql, args)
            raise SimulatedCrash()
        return await original(sql, args)
    async def planner(agent, data):
        if data['evidence']:
            if outcome == 'cancel':
                raise asyncio.CancelledError()
            raise i.InvestigationError('模型依赖异常', 'dependency')
        return await decide(agent, data)
    monkeypatch.setattr(i, 'choose', planner)
    monkeypatch.setattr(i, 'write', crash_at_terminal_commit)
    with pytest.raises(SimulatedCrash):
        await i.investigate(case.policy, case.event, case.recorder)
    saved = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert len(json.loads(saved['evidence'])) == 1
    if phase == 'before_commit':
        assert saved['state'] == 'pending' and saved['next_retry_at'] is None
        assert saved['failure_count'] == 0 and saved['error_kind'] is None
    else:
        assert saved['state'] == ('deferred' if outcome == 'cancel' else 'system_wait')
        assert saved['error_kind'] == ('cancelled' if outcome == 'cancel' else 'dependency')
        assert saved['failure_count'] == (0 if outcome == 'cancel' else 1)
        assert saved['next_retry_at'] and json.loads(saved['result'])['reason']
