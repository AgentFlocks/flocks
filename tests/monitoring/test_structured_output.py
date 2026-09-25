import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookPipeline
from flocks.monitoring.adapter import XdrAdapter, ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.runtime import run
from flocks.monitoring.store import rows, write
from flocks.project.project import Project
from flocks.session.interaction_policy import monitoring_read_scope, unattended_scope
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType
from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolRegistry, ToolResult, ToolParameter, ParameterType
from flocks.tool import structured_output, truncation


@pytest.fixture
async def setup(monkeypatch, tmp_path):
    state = SimpleNamespace(calls=[], display=[], payload={'code': 'Success', 'data': {'list': [], 'total': 0}}, enabled=True)
    async def handler(ctx, action, **kwargs):
        state.calls.append({'action': action, **kwargs})
        value = state.payload(action, kwargs) if callable(state.payload) else state.payload
        if isinstance(value, ToolResult):
            return value
        return ToolResult(success=True, output=value)
    params = [ToolParameter(name='action', type=ParameterType.STRING)] + [
        ToolParameter(name=n, type=ParameterType.INTEGER, required=False)
        for n in ('start_time', 'end_time', 'page_num', 'page_size')
    ] + [ToolParameter(name=n, type=ParameterType.STRING, required=False) for n in ('uuid', 'entity_type')]
    params += [ToolParameter(name='time_field', type=ParameterType.STRING, required=False)] + [ToolParameter(name=n, type=ParameterType.ARRAY, required=False) for n in ('white_status', 'deal_statuses')]
    tool = Tool(ToolInfo(name='fixture_xdr', source='device', provider='fixture', description='Fixture', parameters=params), handler)
    execute = tool.execute
    async def observed_execute(*args, **kwargs):
        value = await execute(*args, **kwargs)
        state.display.append(value)
        return value
    monkeypatch.setattr(tool, 'execute', observed_execute)
    monkeypatch.setattr(ToolRegistry, '_tools', {'fixture_xdr': tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    monkeypatch.setattr(ToolRegistry, '_sync_configured_enabled_states', lambda: None)
    monkeypatch.setattr(ToolRegistry, '_resolve_device_target', AsyncMock(return_value=('device-1', None)))
    async def enabled(*args):
        return state.enabled
    monkeypatch.setattr('flocks.tool.device.store.get_device_tool_enabled', enabled)
    @asynccontextmanager
    async def credentials(device):
        assert device == 'device-1'
        yield True
    monkeypatch.setattr('flocks.tool.credential_context.activate_device_credentials', credentials)
    output_dir = tmp_path / 'tool-output'
    output_dir.mkdir()
    monkeypatch.setattr(truncation, '_OUTPUT_DIR', output_dir)
    monkeypatch.setattr(truncation, '_last_cleanup_ts', 0)
    directory = tmp_path / '.flocks/workspace/structured'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='fixture', worktree=str(directory))
    state.policy = MonitoringPolicy(development_sample=False, owner='owner', project=project.id, directory=str(directory), devices=['device-1'], tool=tool.info.name)
    state.tool = tool
    state.after = AsyncMock()
    monkeypatch.setattr(HookPipeline, 'run_tool_after', state.after)
    # Model reasoning is isolated; entity lookups still exercise the actual
    # ToolRegistry, hooks, permission gate and structured capture under test.
    async def investigate(policy, event, recorder, budget):
        await XdrAdapter(policy, recorder.session_id).call(event['device'],
            {'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'host'}, 'fixture')
        event['investigation'] = {'state': 'ready', 'verdict': 'unknown', 'reason': 'Synthetic read complete'}
        await write("UPDATE monitor_investigations SET state='ready' WHERE owner=? AND project=? AND event_key=?",
                    (policy.owner, policy.project, event['key']))
    monkeypatch.setattr('flocks.monitoring.investigation.investigate', investigate)
    return state


async def query(state):
    with unattended_scope(), monitoring_read_scope(state.policy.tool, state.policy.devices):
        return await XdrAdapter(state.policy, 'fixture').call('device-1', {'action': 'list'}, 'message')


async def test_old_tool_cannot_silently_drop_required_filters(setup):
    setup.tool.info.parameters = [p for p in setup.tool.info.parameters if p.name not in {'deal_statuses', 'white_status', 'time_field'}]
    with pytest.raises(ContractError, match='请更新 XDR API 工具'):
        await XdrAdapter(setup.policy, 'fixture').call('device-1', {
            'action': 'list', 'deal_statuses': [0, 10], 'white_status': ['未加白', '部分加白'], 'time_field': 'endTime',
        }, 'message')
    assert setup.calls == []


@pytest.mark.parametrize('list_field', ['list', 'item'])
async def test_large_pages_real_registry_complete_round_and_cursor(setup, list_field):
    def payload(action, params):
        if action == 'get_entities':
            return {'code': 'Success', 'data': {list_field: []}}
        ids = range(100) if params['page_num'] == 1 else range(100, 105)
        return {'code': 'Success', 'data': {list_field: [
            {'uuId': str(i), 'name': 'BODY_SECRET' * 200, 'riskLevel': 1} for i in ids
        ], 'total': 105}}
    setup.payload = payload
    scheduler = await TaskManager.create_scheduler(title='fixture', context={'monitoring': setup.policy.model_dump()})
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    with unattended_scope(), monitoring_read_scope(setup.policy.tool, setup.policy.devices):
        result = await run(execution, setup.policy)
    assert result.action == 'stop'
    assert len(await rows('SELECT * FROM monitor_observations')) == 105
    assert len(await rows('SELECT * FROM monitor_cursors')) == 1
    assert (await rows('SELECT status FROM monitor_attempts'))[0]['status'] == 'completed'
    assert [p['page_num'] for p in setup.calls if p['action'] == 'list'] == [1, 2]
    assert all(p['deal_statuses'] == [0, 10] and p['white_status'] == ['未加白', '部分加白']
               and p['time_field'] == 'endTime' for p in setup.calls if p['action'] == 'list')
    assert len(setup.calls) == 22 and setup.after.await_count == 22
    # Full collection commits progress; uninvestigated events are durable work,
    # not a failed query or falsely completed investigation.
    pending = await rows("SELECT * FROM monitor_investigations WHERE state='pending'")
    assert len(pending) == 85
    assert {json.loads(row['event'])['id'] for row in pending} == {str(i) for i in range(20, 105)}
    outcome = json.loads((await rows('SELECT result FROM monitor_attempts'))[0]['result'])
    assert outcome['analyzed'] == 20 and outcome['deferred'] == 85 and outcome['errors'] == []
    first = setup.display[0]
    assert first.truncated and isinstance(first.output, str)
    with pytest.raises(json.JSONDecodeError):
        json.loads(first.output)
    assert 'structured_output' not in first.model_dump()
    assert set(first.model_dump()) == {'success', 'output', 'error', 'metadata', 'title', 'truncated', 'attachments'}


@pytest.mark.parametrize('mode', ['mixed', 'all_excluded', 'unknown'])
async def test_business_selection_after_real_tool_capture(setup, monkeypatch, mode):
    from flocks.monitoring import runtime
    from flocks.monitoring.reports import snapshot
    from flocks.monitoring.selection import Disposition, IncidentState, monitoring_selection, FILTER_EXPRESSION
    from flocks.session.message import Message
    # Domain states are fixture metadata, deliberately not claimed to be XDR
    # fields or enums. The actual ToolRegistry/Tool/Hook/capture path is used.
    domain = {}
    cases = [(Disposition.PENDING, False), (Disposition.IN_PROGRESS, False),
             (Disposition.OTHER, False), (Disposition.PENDING, True),
             (Disposition.IN_PROGRESS, True), (Disposition.OTHER, True)]
    for i in range(201):
        disposition, whitelisted = cases[i % 6] if i < 100 else (Disposition.PENDING, True)
        if i == 200:
            disposition, whitelisted = Disposition.IN_PROGRESS, False
        if mode == 'all_excluded':
            whitelisted = True
        if mode == 'unknown' and i == 200:
            whitelisted = None
        domain[str(i)] = IncidentState(disposition, whitelisted)
    rule = monitoring_selection(lambda raw: domain[raw['uuId']])
    query_device = runtime.query_device
    async def selected_query(*args):
        return await query_device(*args, selection=rule)
    monkeypatch.setattr(runtime, 'query_device', selected_query)
    def payload(action, params):
        if action == 'get_entities':
            return {'code': 'Success', 'data': {'item': []}}
        start = (params['page_num'] - 1) * 100
        return {'code': 'Success', 'data': {'item': [
            {'uuId': str(i), 'name': 'synthetic-name-' + 'x' * 3000, 'riskLevel': 1}
            for i in range(start, min(start + 100, 201))], 'total': 201}}
    setup.payload = payload
    scheduler = await TaskManager.create_scheduler(title='selection fixture', context={'monitoring': setup.policy.model_dump()})
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    with unattended_scope(), monitoring_read_scope(setup.policy.tool, setup.policy.devices):
        result = await run(execution, setup.policy)
    attempt = (await rows('SELECT * FROM monitor_attempts'))[0]
    projection = await snapshot(setup.policy.owner, setup.policy.scope, attempt['business_date'])
    expected_ids = {str(i) for i in range(100) if i % 6 in {0, 1}} | {'200'} if mode == 'mixed' else set()
    assert {event['id'] for event in projection['events']} == expected_ids
    assert projection['metrics']['events'] == len(expected_ids)
    admitted_ids = {str(i) for i in range(100) if i % 6 in {0, 1}}
    admitted_ids = set(sorted(admitted_ids, key=int)[:20]) if expected_ids else set()
    assert {call['uuid'] for call in setup.calls if call['action'] == 'get_entities'} == admitted_ids
    assert [call['page_num'] for call in setup.calls if call['action'] == 'list'] == [1, 2, 3]
    assert setup.after.await_count == len(setup.calls) == 3 + len(admitted_ids)
    assert setup.display[0].truncated and setup.display[1].truncated
    assert result.action == ('error' if mode == 'unknown' else 'stop')
    assert bool(await rows('SELECT * FROM monitor_cursors')) is (mode != 'unknown')
    messages = await Message.list_with_parts(attempt['session_id'])
    summaries = [part.text for message in messages for part in message.parts
                 if part.type == 'text' and part.metadata and part.metadata.get('monitoringSummary')]
    assert any(FILTER_EXPRESSION in text and '原始返回 100 条，符合条件 0 条，排除 100 条' in text for text in summaries)
    if mode != 'unknown':
        assert any(f'本轮 XDR 安全事件符合条件 {len(expected_ids)} 条' in text for text in summaries)
        last_query = [step for step in projection['runs'][0]['steps'] if step['tool'] == '查询 XDR 事件'][-1]
        counts = json.loads(last_query['output'])['selection']['cumulative']
        assert counts['source_unique'] == 201 and counts['matched_unique'] == len(expected_ids)
    else:
        assert attempt['status'] == 'failed'
        assert any('加白状态缺失或未识别' in text and '上次查询进度保持不变' in text for text in summaries)
        assert not any('本轮 XDR 安全事件符合条件' in text for text in summaries)


@pytest.mark.parametrize('code', [None, 0, '0', 200, '200', 'Success'])
async def test_valid_small_structures_and_success_codes(setup, code):
    setup.payload = {'code': code, 'data': {'list': [], 'total': 0}}
    assert await query(setup) == setup.payload
    assert not setup.display[0].truncated
    assert setup.after.await_count == 1


@pytest.mark.parametrize('payload', [
    {'code': 'Failure', 'data': {'list': [], 'total': 0}},
    {'code': 'Success', 'success': False},
    {'code': False, 'data': {'list': []}},
    {'code': 401, 'data': {'list': []}},
])
async def test_business_failures_stay_failures(setup, payload):
    setup.payload = payload
    with pytest.raises(ContractError, match='业务查询失败'):
        await query(setup)


@pytest.mark.parametrize('failure', ['business', 'budget', 'pretruncated', 'missing_page'])
async def test_incomplete_round_never_commits_cursor(setup, monkeypatch, failure):
    if failure == 'business':
        setup.payload = {'code': 'Failure', 'data': {'list': []}}
    elif failure == 'budget':
        monkeypatch.setattr(structured_output, 'MAX_BYTES', 100)
        setup.payload = {'code': 'Success', 'data': {'list': [{'uuId': 'x', 'name': 'x' * 200}]}}
    elif failure == 'pretruncated':
        setup.payload = ToolResult(success=True, truncated=True, output='{"data":{"list":[]}}', metadata={'output_path': '/should/not/be/read'})
    else:
        setup.payload = {'code': 'Success', 'data': {'list': [{'uuId': 'first'}], 'total': 2}}
    scheduler = await TaskManager.create_scheduler(title='fixture', context={'monitoring': setup.policy.model_dump()})
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    with unattended_scope(), monitoring_read_scope(setup.policy.tool, setup.policy.devices):
        result = await run(execution, setup.policy)
    assert result.action == 'error'
    assert not await rows('SELECT * FROM monitor_cursors')
    assert not await rows('SELECT * FROM monitor_observations')
    assert (await rows('SELECT status FROM monitor_attempts'))[0]['status'] == 'failed'


async def test_device_and_hook_gates_precede_capture(setup, monkeypatch):
    setup.enabled = False
    with pytest.raises(ContractError, match='设备查询失败'):
        await query(setup)
    assert not setup.calls
    setup.enabled = True
    async def patched(_):
        return SimpleNamespace(output={'decision': {'validated_input_patch': {'action': 'update_status'}}}, execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline, 'run_tool_before', patched)
    with pytest.raises(ContractError, match='设备查询失败'):
        await query(setup)
    assert not setup.calls and setup.after.await_count == 1


async def test_ordinary_calls_keep_display_only_and_capture_is_cleared(setup):
    setup.payload = {'data': {'list': [{'uuId': str(i), 'name': 'x' * 2000} for i in range(100)]}}
    ctx = ToolContext(session_id='ordinary', message_id='message')
    result = await ToolRegistry.execute(setup.policy.tool, ctx, action='list', device_id='device-1')
    assert result.success and result.truncated
    assert not hasattr(ctx, '_output_capture')
    capture = structured_output.OutputCapture('tool')
    source = ToolResult(success=True, output={'x': [1]})
    capture.accept('tool', source)
    source.output['x'].append(2)
    assert capture.take(source) == {'x': [1]}
    assert capture.result is None and capture.value is None


@pytest.mark.parametrize('value', [object(), {1: 'non-string-key'}, float('nan')])
def test_unsupported_machine_data_is_rejected(value):
    with pytest.raises(structured_output.StructuredOutputError):
        structured_output.bounded_copy(value)


def test_depth_node_and_byte_budgets_and_cycles(monkeypatch):
    for key, value, limit in [('MAX_BYTES', {'x': '界' * 20}, 32), ('MAX_NODES', list(range(10)), 5), ('MAX_DEPTH', [[[[]]]], 2)]:
        with monkeypatch.context() as patch:
            patch.setattr(structured_output, key, limit)
            with pytest.raises(structured_output.StructuredOutputError):
                structured_output.bounded_copy(value)
    value = []
    value.append(value)
    with pytest.raises(structured_output.StructuredOutputError):
        structured_output.bounded_copy(value)


@pytest.mark.parametrize('data', [{}, {'list': None, 'item': []}, {'list': [], 'item': [{'uuId': 'x'}]}, {'item': 'not-a-list'}])
def test_ambiguous_or_unknown_list_contract_is_rejected(data):
    from flocks.monitoring.adapter import page_items
    with pytest.raises(ContractError):
        page_items({'data': data})
