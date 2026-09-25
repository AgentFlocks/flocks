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
             'host': '192.0.2.1', 'endTime': 100, 'dealStatus': 40, 'development_sample': True,
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
    assert saved['state'] == 'pending' and len(json.loads(saved['evidence'])) == 1
    assert len(await i.pending(case.policy)) == 1
    monkeypatch.setattr(i, 'choose', decide)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'ready' and case.query.await_count == 1


async def test_repeated_cancellation_retains_evidence_without_endless_automatic_retry(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', AsyncMock(side_effect=asyncio.CancelledError))
    for _ in range(3):
        with pytest.raises(asyncio.CancelledError):
            await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'needs_review'
    assert not await i.pending(case.policy)


async def test_forged_reference_or_target_cannot_execute_or_complete(case, monkeypatch):
    monkeypatch.setattr(i, 'choose', AsyncMock(return_value=i.Choice(action='query', capability='not-present', reason='invalid')))
    await i.investigate(case.policy, case.event, case.recorder)
    assert not case.query.called
    monkeypatch.setattr(i, 'choose', AsyncMock(return_value=i.Choice(action='finish', verdict='benign', evidence_ids=['invented'], reason='invalid')))
    await i.investigate(case.policy, case.event, case.recorder)
    await i.investigate(case.policy, case.event, case.recorder)
    assert case.event['investigation']['state'] == 'needs_review'
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
    assert not await i.pending(case.policy.model_copy(update={'development_sample': False}))


async def test_pending_batch_filters_old_device_and_mode_before_limiting(case):
    async with connection() as db:
        for index in range(25):
            event = {**case.event, 'key': f'old-{index}', 'device': 'removed-device'}
            await i.enqueue(db, case.policy, event, '2000-01-01T00:00:00+00:00')
            event = {**case.event, 'key': f'normal-{index}', 'development_sample': False}
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
