"""Round completion is a persisted fact, separate from incident closure."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.monitoring import capabilities, investigation, recovery, rounds, runtime
from flocks.monitoring.adapter import ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.sessions import ensure_daily
from flocks.monitoring.store import encode, rows, write
from flocks.monitoring.summaries import Summary
from flocks.project.project import Project
from flocks.session.message import Message, MessageRole, ToolPart, ToolStateRunning
from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionTriggerType, TaskStatus, SchedulerStatus
from flocks.task.store import TaskStore


@pytest.fixture
async def context(tmp_path, monkeypatch):
    directory = tmp_path / '.flocks/workspace/round-endings'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='Round ending fixture', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['xdr'])
    scheduler = await TaskManager.create_scheduler(title='round-endings', context={'monitoring': policy.model_dump()})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready,scheduler_id) VALUES(?,?,?,?,1,?)',
                (policy.owner, policy.scope, policy.project, encode(policy.model_dump()), scheduler.id))
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    events = [{'uuId': 'event-1', 'name': 'Fixture incident', 'incidentSeverity': 2, 'hostIp': '192.0.2.1', 'dealStatus': 0}]
    published = []
    async def publish(kind, payload):
        published.append((kind, payload))
    monkeypatch.setattr(runtime, 'publish', publish)
    # This fixture simulates a healthy mailbox; failure cases override explicitly.
    monkeypatch.setattr('flocks.monitoring.mailflow.health_snapshot', AsyncMock(return_value={
        'enabled': True, 'receive': {'state': 'healthy'}, 'send': {'state': 'healthy'}, 'errors': [],
    }))
    monkeypatch.setattr(capabilities, 'discover', AsyncMock(return_value=([
        capabilities.Capability('fixture-cap', 'xdr', policy.tool, 'xdr', 'Fixture XDR', 'unknown'),
    ], [])))
    monkeypatch.setattr(investigation.Agent, 'list', AsyncMock(return_value=[]))
    monkeypatch.setattr(capabilities, 'query', AsyncMock(return_value=(
        {'data': {'item': [{'hostIp': '192.0.2.1', 'gptResult': 40, 'threatLevel': 1}]}}, {'action': 'get_entities'},
    )))
    async def choose(agent, data):
        if not data['evidence']:
            return investigation.Choice(action='query', capability='fixture-cap', entity='host', reason='核验主机证据')
        return investigation.Choice(action='finish', verdict='benign', evidence_ids=['evidence-1'], reason='取得明确误报及安全实体证据')
    monkeypatch.setattr(investigation, 'choose', choose)
    class Adapter:
        def __init__(self, policy, session):
            self.policy = policy
        async def call(self, device, params, message):
            assert params['action'] == 'list'
            return {'data': {'list': events, 'total': len(events)}}
    return SimpleNamespace(policy=policy, scheduler=scheduler, execution=execution, events=events, adapter=Adapter, published=published)


async def ending(attempt_id=None):
    attempt = (await rows('SELECT * FROM monitor_attempts' + (' WHERE id=?' if attempt_id else ' ORDER BY sequence DESC LIMIT 1'),
                          (attempt_id,) if attempt_id else ()))[0]
    # Test durable reload, not the Message class's in-memory cache.
    Message._messages_cache.pop(attempt['session_id'], None)
    Message._parts_cache.pop(attempt['session_id'], None)
    messages = await Message.list_with_parts(attempt['session_id'])
    cards = [(message, part) for message in messages for part in message.parts
             if part.type == 'text' and (part.metadata or {}).get('monitoringRoundEnd') is True
             and part.metadata['roundId'] == attempt['id']]
    assert len(cards) == 1
    message, card = cards[0]
    assert message.info.id == attempt['end_message_id']
    assert message.info.finish in ('stop', 'error') and message.info.time.get('completed')
    assert card.text == attempt['summary']
    assert card.metadata == {'monitoringRoundEnd': True, 'roundStatus': attempt['status'],
                             'roundId': attempt['id'], 'nextStep': attempt['next_step']}
    assert attempt['next_step'] and attempt['end_published'] == 1
    return attempt, message, card


@pytest.mark.parametrize('scenario', ['empty', 'no_action', 'await_reply', 'deduplicated'])
async def test_normal_business_outcomes_finish_without_partial_status(context, monkeypatch, scenario):
    from flocks.monitoring import mailflow
    if scenario == 'empty':
        context.events.clear()
    if scenario in ('await_reply', 'deduplicated'):
        # Only the mailbox service boundary is simulated. Query/agent decisions,
        # evidence checkpoints, step Recorder and final session storage are real.
        monkeypatch.setattr(mailflow, 'settings', AsyncMock(return_value={'enabled': True, 'project': context.policy.project}))
        monkeypatch.setattr(mailflow, 'process_replies', AsyncMock(return_value={'processed': 0, 'verified': 0, 'pending': 0}))
        monkeypatch.setattr(mailflow, 'notify_batch', AsyncMock(return_value={
            'sent': int(scenario == 'await_reply'), 'pending': 0,
            'explanations': ['等待责任人回信' if scenario == 'await_reply' else '同设备、同事件编号已有通知，本轮去重，不重复发信'],
        }))
    result = await runtime.run(context.execution, context.policy, context.adapter)
    assert result.action == 'stop'
    attempt, message, card = await ending()
    assert attempt['status'] == 'completed' and not attempt['error']
    assert '部分完成' not in card.text and '监测继续运行' in card.metadata['nextStep']
    facts = json.loads(attempt['result'])
    assert facts['events'] == int(scenario != 'empty')
    assert facts['closure'] == 'open'  # task completion is not incident closure
    if scenario != 'empty':
        case = (await rows('SELECT * FROM monitor_investigations'))[0]
        assert case['state'] == 'ready' and json.loads(case['result'])['verdict'] == 'benign'
        assert len(json.loads(case['evidence'])) == 1
    assert all(step['status'] == 'completed' for step in await rows('SELECT * FROM monitor_steps'))
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize('failure', ['api', 'model'])
async def test_failed_query_or_incomplete_model_produces_failed_card(context, monkeypatch, failure):
    factory = context.adapter
    if failure == 'api':
        class Failed(context.adapter):
            async def call(self, *args):
                raise ContractError('设备查询失败')
        factory = Failed
    else:
        monkeypatch.setattr(investigation, 'choose', AsyncMock(side_effect=ContractError('调查模型没有返回完整决策')))
    result = await runtime.run(context.execution, context.policy, factory)
    assert result.action == 'error'
    attempt, _, card = await ending()
    assert attempt['status'] == 'failed' and attempt['error']
    failed_steps = await rows("SELECT * FROM monitor_steps WHERE status='failed'")
    assert failed_steps
    if failure == 'model':
        assert any(step['tool'] == '智能体调查结果' for step in failed_steps)
        case = (await rows('SELECT * FROM monitor_investigations'))[0]
        assert case['state'] == 'system_wait'
    else:
        assert not await rows('SELECT * FROM monitor_cursors')
    for step in failed_steps:
        info = await Message.get(attempt['session_id'], step['message_id'])
        tool = next(part for part in await Message.parts(info.id, attempt['session_id']) if part.id == step['part_id'])
        assert tool.state.status == 'error' and info.finish == 'error'
    assert card.metadata['roundStatus'] == 'failed'


async def test_cancelled_runtime_preserves_failed_step_and_round_card(context):
    entered = asyncio.Event()
    class Hanging(context.adapter):
        async def call(self, *args):
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(runtime.run(context.execution, context.policy, Hanging))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    attempt, _, card = await ending()
    assert attempt['status'] == 'failed' and attempt['finished_at']
    assert not await rows("SELECT * FROM monitor_steps WHERE status='running'")
    assert not await rows('SELECT * FROM monitor_cursors')
    assert '中断' in card.text and '无风险' in card.text


async def seed_attempt(context, status='running'):
    session, day = await ensure_daily(context.policy, runtime.now())
    trigger = await runtime.emit_message(session.id, '系统自动监测', role=MessageRole.USER)
    anchor = await runtime.emit_message(session.id, '本轮开始', parent_id=trigger.id)
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('attempt-fixture', context.execution.id, context.policy.owner, context.policy.project, context.policy.scope, day,
                 session.id, anchor.id, runtime.now().isoformat(), status))
    return SimpleNamespace(id='attempt-fixture', session=session.id, anchor=anchor.id, trigger=trigger.id)


async def test_recorder_marks_saved_incomplete_analysis_as_error(context):
    attempt = await seed_attempt(context)
    recorder = runtime.Recorder(attempt.session, attempt.id, parent_id=attempt.trigger)
    async def operation(_):
        return {'state': 'pending'}, {'state': 'pending'}, Summary('调查未完成，已保存待续查', '模型未完整返回', success=False)
    result = await recorder.call('智能体调查结果', {'event': 'event-1'}, operation)
    assert result == {'state': 'pending'}
    step = (await rows('SELECT * FROM monitor_steps'))[0]
    assert step['status'] == 'failed' and step['error'] == '调查未完成，已保存待续查'
    info = await Message.get(attempt.session, step['message_id'])
    parts = await Message.parts(info.id, attempt.session)
    assert info.finish == 'error'
    assert next(p for p in parts if p.id == step['part_id']).state.status == 'error'
    assert parts[-1].metadata['monitoringSummary'] is True
    assert parts[-1].text == step['error']


async def test_restart_closes_inflight_steps_and_emits_one_failed_card(context):
    attempt = await seed_attempt(context)
    info = await runtime.emit_message(attempt.session, '查询中', finished=False, parent_id=attempt.trigger)
    part = ToolPart(sessionID=attempt.session, messageID=info.id, callID='fixture-call', tool='查询 XDR 事件',
                    state=ToolStateRunning(input={'action': 'list'}, time={'start': 1}))
    await Message.add_part(attempt.session, info.id, part)
    await write("INSERT INTO monitor_steps(id,attempt_id,message_id,part_id,tool,input,status,started_at) VALUES(?,?,?,?,?,?,'running',?)",
                (part.id, attempt.id, info.id, part.id, part.tool, '{}', runtime.now().isoformat()))
    await recovery.recover()
    saved, message, card = await ending(attempt.id)
    assert saved['status'] == 'failed' and '服务重启' in saved['error']
    assert message.info.parentID == attempt.trigger
    step = (await rows('SELECT * FROM monitor_steps'))[0]
    assert step['status'] == 'failed'
    assert (await Message.get(attempt.session, info.id)).finish == 'error'
    assert next(p for p in await Message.parts(info.id, attempt.session) if p.id == part.id).state.status == 'error'
    await recovery.recover()
    assert (await ending(attempt.id))[0]['end_message_id'] == saved['end_message_id']


@pytest.mark.parametrize('pause_before_recovery', [False, True])
async def test_end_marker_is_idempotent_and_recovery_reuses_persisted_message(context, monkeypatch, pause_before_recovery):
    attempt = await seed_attempt(context, 'completed')
    published = runtime.publish
    monkeypatch.setattr(runtime, 'publish', AsyncMock(side_effect=RuntimeError('crash before publish')))
    with pytest.raises(RuntimeError, match='crash before publish'):
        await rounds.finish_round(attempt.id, '本轮完成，等待回信')
    saved = (await rows('SELECT * FROM monitor_attempts'))[0]
    assert saved['summary'] and saved['end_message_id'] and not saved['end_published']
    assert await Message.get(attempt.session, saved['end_message_id']) is not None
    if pause_before_recovery:
        context.scheduler.status = SchedulerStatus.DISABLED
        await TaskStore.update_scheduler(context.scheduler)
    monkeypatch.setattr(runtime, 'publish', published)
    await recovery.recover()
    first, _, _ = await ending(attempt.id)
    await rounds.finish_round(attempt.id, '不能覆盖已发布结果')
    second, _, _ = await ending(attempt.id)
    assert first == second
    assert second['end_message_id'] == saved['end_message_id']


async def test_concurrent_finish_calls_do_not_duplicate_end_marker(context):
    attempt = await seed_attempt(context, 'completed')
    await asyncio.gather(*(rounds.finish_round(attempt.id, '本轮完成') for _ in range(6)))
    saved, _, _ = await ending(attempt.id)
    assert saved['status'] == 'completed'


async def test_recovery_finishes_message_created_before_completion_marker_crash(context, monkeypatch):
    attempt = await seed_attempt(context, 'completed')
    update = Message.update
    monkeypatch.setattr(Message, 'update', AsyncMock(side_effect=RuntimeError('crash after message insert')))
    with pytest.raises(RuntimeError, match='crash after message insert'):
        await rounds.finish_round(attempt.id, '本轮完成')
    saved = (await rows('SELECT * FROM monitor_attempts'))[0]
    assert not (await Message.get(attempt.session, saved['end_message_id'])).finish
    monkeypatch.setattr(Message, 'update', update)
    await recovery.recover()
    assert (await ending(attempt.id))[0]['end_message_id'] == saved['end_message_id']


async def test_timeout_cancels_real_round_before_returning_failed_execution(context, monkeypatch):
    from flocks.auth.context import AuthUser
    from flocks.hub import local
    monkeypatch.setattr(local, 'get_record', lambda kind, key: SimpleNamespace(enabled=True) if kind == 'component' else None)
    monkeypatch.setattr('flocks.monitoring.agent_component.resolve', AsyncMock())
    monkeypatch.setattr(runtime.AuthService, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(
        status='active', to_auth_user=lambda: AuthUser(id='owner', username='owner', role='admin'))))
    entered = asyncio.Event()
    class Hanging(context.adapter):
        async def call(self, *args):
            entered.set()
            await asyncio.Event().wait()
    manager = SimpleNamespace(_task_handles={})
    async def launch(**kwargs):
        manager._task_handles['fixture-background'] = asyncio.create_task(kwargs['runner']())
        return SimpleNamespace(id='fixture-background')
    async def wait_for(*args, **kwargs):
        await asyncio.wait_for(entered.wait(), 3)
        return None  # watchdog expired; do not sleep for twenty real minutes
    manager.run_existing_session = launch
    manager.wait_for = wait_for
    manager.cancel = lambda key: manager._task_handles[key].cancel()
    monkeypatch.setattr(runtime, 'get_background_manager', lambda: manager)
    result = await runtime.dispatch(context.execution, context.scheduler, adapter_factory=Hanging)
    assert result.status == TaskStatus.FAILED
    assert manager._task_handles['fixture-background'].done()
    assert context.execution.id not in runtime._running
    attempt, _, card = await ending()
    assert attempt['status'] == 'failed' and '执行上限' in card.text
    persisted = await TaskStore.get_execution(result.id)
    assert persisted.status == TaskStatus.FAILED


def enable_fixture_mail(context, monkeypatch):
    from flocks.monitoring import mailflow
    monkeypatch.setattr(mailflow, 'settings', AsyncMock(return_value={
        'enabled': True, 'project': context.policy.project, 'revision': 'fixture-revision',
        'recipient': 'responsible@example.invalid', 'mailbox': 'fixture-mailbox',
    }))


async def assert_recorded_step(attempt, tool_name, *, failed):
    step = (await rows('SELECT * FROM monitor_steps WHERE attempt_id=? AND tool=?', (attempt['id'], tool_name)))[0]
    assert step['status'] == ('failed' if failed else 'completed')
    info = await Message.get(attempt['session_id'], step['message_id'])
    parts = await Message.parts(info.id, attempt['session_id'])
    tool = next(part for part in parts if part.id == step['part_id'])
    assert tool.state.status == ('error' if failed else 'completed')
    assert info.finish == ('error' if failed else 'stop')
    if failed:
        assert step['error'] and tool.state.error
    assert any(part.type == 'text' and (part.metadata or {}).get('monitoringSummary') is True for part in parts)


@pytest.mark.parametrize('outcome,failed', [
    ('queued', True), ('pending', True), ('send_unknown', True), ('exception', True),
    ('sent', False), ('skipped', False), ('already_attempted', False),
])
async def test_notification_outcome_distinguishes_transport_failure_from_business_completion(context, monkeypatch, outcome, failed):
    from flocks.monitoring import mailflow
    enable_fixture_mail(context, monkeypatch)
    context.events.clear()
    event = {'key': 'xdr:incident:old', 'id': 'old', 'device': 'xdr', 'name': 'Earlier incident'}
    await mailflow.queue_notices(context.policy, [event], 'earlier-session')
    # The notification batch and Recorder persist actual state. Only the
    # network-facing one-notice service is simulated for each transport result.
    async def notify(policy, notice, session, adapter_factory):
        if outcome == 'exception':
            raise RuntimeError('fixture transport failure')
        state = 'sent' if outcome == 'already_attempted' else outcome
        await write('UPDATE monitor_mail_notices SET state=?,error=? WHERE id=?',
                    (state, '邮件投递未完成' if failed else None, notice['id']))
        return outcome
    call = AsyncMock(side_effect=notify)
    monkeypatch.setattr(mailflow, 'notify_one', call)
    result = await runtime.run(context.execution, context.policy, context.adapter)
    assert result.action == ('error' if failed else 'stop')
    attempt, _, card = await ending()
    assert attempt['status'] == ('failed' if failed else 'completed')
    facts = json.loads(attempt['result'])
    assert bool(facts['mail']['notification'].get('errors')) is failed
    assert bool(facts['errors']) is failed
    assert call.await_count == 1
    await assert_recorded_step(attempt, '邮件通知结果', failed=failed)
    assert card.metadata['roundStatus'] == attempt['status']


async def test_existing_sent_notification_deduplicates_without_marking_round_failed(context, monkeypatch):
    from flocks.monitoring import mailflow
    from flocks.monitoring.adapter import normalize
    enable_fixture_mail(context, monkeypatch)
    event = normalize('xdr', context.events[0])
    await mailflow.queue_notices(context.policy, [event], 'earlier-session')
    await write("UPDATE monitor_mail_notices SET state='sent'")
    send = AsyncMock(side_effect=AssertionError('must not re-send'))
    monkeypatch.setattr(mailflow, 'notify_one', send)
    result = await runtime.run(context.execution, context.policy, context.adapter)
    assert result.action == 'stop'
    attempt, _, _ = await ending()
    assert attempt['status'] == 'completed' and not send.called
    facts = json.loads(attempt['result'])
    assert facts['mail']['notification']['sent'] == 0 and not facts['mail']['notification'].get('errors')
    await assert_recorded_step(attempt, '核对已有通知，避免重复发信', failed=False)


@pytest.mark.parametrize('error,failed,state', [
    (ContractError('核验 API 失败'), True, 'pending'),
    (TimeoutError('模型超时'), True, 'pending'),
    (RuntimeError('模型服务不可用'), True, 'pending'),
    (OSError('transport unavailable'), True, 'pending'),
    (ValueError('回信涉及多条候选，需人工确认'), False, 'needs_review'),
    (None, False, 'pending'),
])
async def test_reply_failures_are_not_confused_with_normal_waiting_or_human_review(context, monkeypatch, error, failed, state):
    from flocks.monitoring import mailflow
    enable_fixture_mail(context, monkeypatch)
    context.events.clear()
    await write('INSERT INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,received_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('reply-fixture', context.policy.owner, context.policy.project, 'fixture-mailbox', '<fixture@example.invalid>',
                 'responsible@example.invalid', '{}', 'pending', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'))
    process = AsyncMock(side_effect=error, return_value=None)
    monkeypatch.setattr(mailflow, 'process_reply', process)
    result = await runtime.run(context.execution, context.policy, context.adapter)
    assert result.action == ('error' if failed else 'stop')
    attempt, _, _ = await ending()
    assert attempt['status'] == ('failed' if failed else 'completed')
    facts = json.loads(attempt['result'])
    assert bool(facts['mail']['feedback'].get('errors')) is failed
    assert bool(facts['errors']) is failed
    reply = (await rows('SELECT * FROM monitor_mail_replies'))[0]
    assert reply['state'] == state and process.await_count == 1
    assert facts['mail']['feedback']['pending'] == 1
    await assert_recorded_step(attempt, '解读回信并跟进状态', failed=failed)


async def test_shared_query_budget_defers_unstarted_cases_without_spending_retries(context):
    context.events[:] = [dict(context.events[0], uuId=f'event-{index:02d}') for index in range(20)]
    for round_index in range(3):
        execution = context.execution if round_index == 0 else await TaskManager.create_execution_from_scheduler(
            context.scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        result = await runtime.run(execution, context.policy, context.adapter)
        assert result.action == 'stop'
        attempt, _, _ = await ending()
        assert attempt['status'] == 'completed'
        facts = json.loads(attempt['result'])
        cases = await rows('SELECT * FROM monitor_investigations ORDER BY event_key')
        assert len(cases) == 20
        assert all(case['state'] != 'needs_review' for case in cases)
        if round_index == 0:
            assert facts['analyzed'] == 12 and facts['deferred'] == 8
            assert sum(case['attempts'] == 0 and case['state'] == 'pending' for case in cases) == 8
        else:
            assert facts['analyzed'] == 20 and facts['deferred'] == 0
            assert all(case['state'] == 'ready' and case['attempts'] == 1 for case in cases)


async def test_steps_persist_readable_evidence_sections_without_model_reasoning(context):
    await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, _ = await ending()
    messages = await Message.list_with_parts(attempt['session_id'])
    summaries = [p for message in messages for p in message.parts
                 if p.type == 'text' and (p.metadata or {}).get('monitoringSummary') is True]
    assert summaries and all(p.metadata['monitoringSections'] for p in summaries)
    labels = {section['label'] for p in summaries for section in p.metadata['monitoringSections']}
    assert {'实际发现', '下一步', '证据与缺口', '调查对象', '采用的方法'} <= labels
    sections = [section for p in summaries for section in p.metadata['monitoringSections']]
    assert any('主机：192.0.2.1' in section['text'] for section in sections)
    assert any('evidence-1' in section['text'] for section in sections)
    assert not any('思考过程' in section['label'] for section in sections)


async def test_zero_events_with_unavailable_receiver_is_failure_not_no_reply(context, monkeypatch):
    from flocks.monitoring import mailflow
    enable_fixture_mail(context, monkeypatch)
    context.events.clear()
    monkeypatch.setattr(mailflow, 'health_snapshot', AsyncMock(return_value={
        'enabled': True, 'receive': {'state': 'unavailable', 'stage': 'search'},
        'send': {'state': 'healthy'}, 'errors': ['邮件收取失败：搜索阶段异常'],
    }))
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    facts = json.loads(attempt['result'])
    assert outcome.action == 'error' and facts['query_complete'] is True
    assert facts['events'] == 0 and facts['mail']['health']['receive']['stage'] == 'search'
    assert '当前不能把处理回信 0 封解释为没有新回信' in card.text
    assert '查询未完整成功' not in card.text


async def test_zero_event_round_exposes_old_system_failure_and_retry_plan(context, monkeypatch):
    monkeypatch.setattr(investigation, 'choose', AsyncMock(side_effect=ContractError('调查模型没有返回完整决策')))
    await runtime.run(context.execution, context.policy, context.adapter)
    context.events.clear()
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    facts = json.loads(attempt['result'])
    assert outcome.action == 'stop'
    assert facts['events'] == 0  # cooldown prevents a repeated model call
    assert facts['investigation_backlog']['system_wait'] == 1
    assert facts['errors'] == [] and not attempt['error']
    assert facts['query_events'] == 0 and facts['resumed_events'] == 0
    assert '系统故障 1 条' in card.text
    assert '历史故障待恢复，不代表本轮再次失败' in card.text
    assert '不早于' in attempt['next_step']


@pytest.mark.parametrize('query_hits_waiting_event', [False, True])
async def test_system_failure_only_fails_round_when_retry_actually_runs(context, monkeypatch, query_hits_waiting_event):
    from datetime import timedelta
    clock = runtime.now()
    monkeypatch.setattr(investigation, 'now', lambda: clock)
    monkeypatch.setattr(runtime, 'now', lambda: clock)
    choose = AsyncMock(side_effect=ContractError('调查模型请求失败'))
    monkeypatch.setattr(investigation, 'choose', choose)

    first = await runtime.run(context.execution, context.policy, context.adapter)
    first_attempt, _, _ = await ending()
    assert first.action == 'error' and first_attempt['status'] == 'failed'
    case = (await rows('SELECT * FROM monitor_investigations'))[0]
    retry_at = case['next_retry_at']
    assert case['attempts'] == 1 and case['failure_count'] == 1
    if not query_hits_waiting_event:
        context.events.clear()

    # Restart recovery must retain the incident cooldown without relabeling
    # the already failed round or creating another failed round.
    await recovery.recover()
    assert (await rows('SELECT next_retry_at FROM monitor_investigations'))[0]['next_retry_at'] == retry_at
    clock += timedelta(minutes=5)
    second = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    result = json.loads(attempt['result'])
    assert second.action == 'stop' and attempt['status'] == 'completed'
    assert result['errors'] == [] and result['investigation_backlog']['system_wait'] == 1
    assert choose.await_count == 1
    assert result['query_events'] == int(query_hits_waiting_event)
    assert result['resumed_events'] == result['query_resumed_overlap'] == 0
    assert '历史故障待恢复，不代表本轮再次失败' in card.text
    assert not await rows("SELECT * FROM monitor_steps WHERE attempt_id=? AND status='failed'", (attempt['id'],))
    if query_hits_waiting_event:
        snapshot = json.loads((await rows('SELECT data FROM monitor_observations WHERE attempt_id=?', (attempt['id'],)))[0]['data'])
        assert snapshot['investigation']['state'] == 'system_wait'
        assert snapshot['investigation']['attempted_this_round'] is False
        step = (await rows("SELECT * FROM monitor_steps WHERE attempt_id=? AND tool='调查等待重试'", (attempt['id'],)))[0]
        assert step['status'] == 'completed'
        assert '本轮未执行此事件调查' in str(await Message.parts(step['message_id'], attempt['session_id']))

    clock += timedelta(minutes=6)
    third = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, _ = await ending()
    result = json.loads(attempt['result'])
    assert third.action == 'error' and attempt['status'] == 'failed'
    assert choose.await_count == 2 and len(result['errors']) == 1
    assert result['query_events'] == int(query_hits_waiting_event)
    assert result['resumed_events'] == 1
    assert result['query_resumed_overlap'] == int(query_hits_waiting_event)
    assert result['events'] == result['query_events'] + result['resumed_events'] - result['query_resumed_overlap'] == 1
    case = (await rows('SELECT * FROM monitor_investigations'))[0]
    assert case['failure_count'] == case['attempts'] == 2


@pytest.mark.parametrize('query_hits_waiting_event', [False, True])
async def test_exhausted_history_remains_visible_without_failing_new_round(context, monkeypatch, query_hits_waiting_event):
    choose = AsyncMock(side_effect=ContractError('调查模型请求失败'))
    monkeypatch.setattr(investigation, 'choose', choose)
    await runtime.run(context.execution, context.policy, context.adapter)
    await write('UPDATE monitor_investigations SET retry_exhausted=1,next_retry_at=NULL,failure_count=3')
    if not query_hits_waiting_event:
        context.events.clear()
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    assert outcome.action == 'stop' and attempt['status'] == 'completed'
    assert choose.await_count == 1
    assert '已停止自动恢复' in card.text and '暂停并重新启动' in attempt['next_step']
    assert not await rows("SELECT * FROM monitor_steps WHERE attempt_id=? AND status='failed'", (attempt['id'],))


@pytest.mark.parametrize('query_hits_history', [False, True])
async def test_resumed_work_counts_are_separate_from_current_query(context, monkeypatch, query_hits_history):
    # Preserve a real historical case, then let a later round complete it.
    original_choose = investigation.choose
    monkeypatch.setattr(investigation, 'choose', AsyncMock(side_effect=ContractError('暂时失败')))
    await runtime.run(context.execution, context.policy, context.adapter)
    await investigation.retry_waiting(context.policy)
    monkeypatch.setattr(investigation, 'choose', original_choose)
    if not query_hits_history:
        context.events.clear()
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    result = json.loads(attempt['result'])
    assert outcome.action == 'stop'
    assert result['events'] == result['analyzed'] == result['resumed_events'] == 1
    assert result['query_events'] == result['query_resumed_overlap'] == int(query_hits_history)
    assert f'本轮查询返回 {int(query_hits_history)} 条，纳入历史待办 1 条' in card.text
    assert '本轮共纳入 1 条事件' in card.text
    assert '读取 1 条事件' not in card.text
    if query_hits_history:
        assert '已合并去重' in card.text


async def test_manual_review_is_not_promised_automatic_resumption(context):
    await runtime.run(context.execution, context.policy, context.adapter)
    await write("UPDATE monitor_investigations SET state='needs_review'")
    context.events.clear()
    outcome = await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, card = await ending()
    assert outcome.action == 'stop'
    assert '业务待人工核对 1 条' in card.text
    assert '不再自动续查' in attempt['next_step']
    assert '优先处理已收到的回信和未完成调查' not in attempt['next_step']


async def test_monitor_migration_disables_generic_retry_but_preserves_other_tasks(context):
    from datetime import datetime, timedelta, timezone
    from flocks.monitoring.lifecycle import _disable_round_retries
    other = await TaskManager.create_scheduler(title='unrelated')
    context.execution.status = TaskStatus.FAILED
    context.execution.retry.retry_count = 2
    context.execution.retry.retry_after = datetime.now(timezone.utc) + timedelta(seconds=60)
    await TaskStore.update_execution(context.execution)
    await _disable_round_retries(context.scheduler)
    saved = await TaskStore.get_execution(context.execution.id)
    assert saved.retry.max_retries == 0 and saved.retry.retry_after is None
    assert saved.retry.retry_count == 2 and saved.status == TaskStatus.FAILED
    assert (await TaskStore.get_scheduler(context.scheduler.id)).retry.max_retries == 0
    assert (await TaskStore.get_scheduler(other.id)).retry.max_retries == 3


async def test_next_step_excludes_old_devices_and_development_cases(context):
    await runtime.run(context.execution, context.policy, context.adapter)
    original = (await rows('SELECT * FROM monitor_investigations'))[0]
    event = json.loads(original['event'])
    event['device'] = 'removed-device'
    await write("UPDATE monitor_investigations SET event=?,state='system_wait',retry_exhausted=1", (encode(event),))
    context.events.clear()
    await runtime.run(context.execution, context.policy, context.adapter)
    attempt, _, _ = await ending()
    assert '系统故障调查' not in attempt['next_step']
    assert '修复依赖后' not in attempt['next_step']


async def test_history_snapshot_reports_current_run_without_switching_session(context):
    from flocks.monitoring.reports import snapshot
    attempt = await seed_attempt(context)
    view = await snapshot(context.policy.owner, context.policy.scope, '2000-01-01')
    assert view['businessDate'] == '2000-01-01' and not view['runs']
    assert view['sessionID'] is None
    assert view['currentRun']['id'] == attempt.id
    assert view['metrics']['investigationCompletionRate'] is None


@pytest.mark.parametrize('stage', ['query', 'model'])
async def test_real_background_timeout_marks_reason_before_runner_cleanup(context, monkeypatch, stage):
    from flocks.auth.context import AuthUser
    from flocks.hub import local
    from flocks.task.background import BackgroundManager
    from flocks.session.core.status import SessionStatus
    monkeypatch.setattr(local, 'get_record', lambda kind, key: SimpleNamespace(enabled=True) if kind == 'component' else None)
    monkeypatch.setattr('flocks.monitoring.agent_component.resolve', AsyncMock())
    monkeypatch.setattr(runtime.AuthService, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(
        status='active', to_auth_user=lambda: AuthUser(id='owner', username='owner', role='admin'))))
    context.policy = context.policy.model_copy(update={'timeout_seconds': 1})
    context.execution.execution_input_snapshot['context']['monitoring'] = context.policy.model_dump()
    context.scheduler.context = {'monitoring': context.policy.model_dump()}
    await TaskStore.update_scheduler(context.scheduler)
    await write('UPDATE monitor_installations SET policy=?', (encode(context.policy.model_dump()),))
    entered = asyncio.Event()
    factory = context.adapter
    if stage == 'query':
        class Hanging(context.adapter):
            async def call(self, *args):
                entered.set()
                await asyncio.Event().wait()
        factory = Hanging
    else:
        original = investigation.choose
        async def hang_after_evidence(agent, data):
            if data['evidence']:
                entered.set()
                await asyncio.Event().wait()
            return await original(agent, data)
        monkeypatch.setattr(investigation, 'choose', hang_after_evidence)
    manager = BackgroundManager()
    waiting = AsyncMock(wraps=manager.wait_for)
    monkeypatch.setattr(manager, 'wait_for', waiting)
    monkeypatch.setattr(runtime, 'get_background_manager', lambda: manager)
    outcome = await runtime.dispatch(context.execution, context.scheduler, adapter_factory=factory)
    assert entered.is_set() and outcome.status == TaskStatus.FAILED
    assert waiting.call_args.kwargs == {}  # Monitoring owns the deadline, not generic wait_for.
    assert all(task.done() for task in manager._task_handles.values())
    assert all(task.status == 'cancelled' for task in manager.list_tasks())
    attempt, _, card = await ending()
    assert '达到本轮 1 秒执行上限' in card.text
    assert '运行中断或内部错误' not in card.text
    assert attempt['status'] == 'failed' and attempt['end_published'] == 1
    assert SessionStatus.get(attempt['session_id']).type == 'idle'
    assert context.execution.id not in runtime._running
    if stage == 'model':
        saved = (await rows('SELECT * FROM monitor_investigations'))[0]
        assert saved['state'] == 'deferred' and saved['error_kind'] == 'cancelled'
        assert len(json.loads(saved['evidence'])) == 1
        assert saved['failure_count'] == 0


async def test_real_background_manual_cancel_drains_runner_and_waiter(context, monkeypatch):
    from flocks.auth.context import AuthUser
    from flocks.hub import local
    from flocks.task.background import BackgroundManager
    from flocks.session.core.status import SessionStatus
    monkeypatch.setattr(local, 'get_record', lambda kind, key: SimpleNamespace(enabled=True) if kind == 'component' else None)
    monkeypatch.setattr('flocks.monitoring.agent_component.resolve', AsyncMock())
    monkeypatch.setattr(runtime.AuthService, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(
        status='active', to_auth_user=lambda: AuthUser(id='owner', username='owner', role='admin'))))
    entered = asyncio.Event()
    class Hanging(context.adapter):
        async def call(self, *args):
            entered.set()
            await asyncio.Event().wait()
    manager = BackgroundManager()
    monkeypatch.setattr(runtime, 'get_background_manager', lambda: manager)
    dispatched = asyncio.create_task(runtime.dispatch(context.execution, context.scheduler, adapter_factory=Hanging))
    await asyncio.wait_for(entered.wait(), 3)
    cancelled = await TaskManager.cancel_execution(context.execution.id)
    result = await asyncio.gather(dispatched, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert cancelled.status == TaskStatus.CANCELLED
    assert all(task.done() for task in manager._task_handles.values())
    attempt, _, card = await ending()
    assert attempt['status'] == 'failed' and '执行上限' not in card.text
    assert SessionStatus.get(attempt['session_id']).type == 'idle'
    assert context.execution.id not in runtime._running


def real_dispatch_manager(monkeypatch):
    from flocks.auth.context import AuthUser
    from flocks.hub import local
    from flocks.task.background import BackgroundManager
    monkeypatch.setattr(local, 'get_record', lambda kind, key: SimpleNamespace(enabled=True) if kind == 'component' else None)
    monkeypatch.setattr('flocks.monitoring.agent_component.resolve', AsyncMock())
    monkeypatch.setattr(runtime.AuthService, 'get_user_by_id', AsyncMock(return_value=SimpleNamespace(
        status='active', to_auth_user=lambda: AuthUser(id='owner', username='owner', role='admin'))))
    manager = BackgroundManager()
    monkeypatch.setattr(runtime, 'get_background_manager', lambda: manager)
    return manager


async def test_end_message_storage_failure_clears_busy_and_keeps_terminal_states_consistent(context, monkeypatch):
    from flocks.session.core.status import SessionStatus
    manager = real_dispatch_manager(monkeypatch)
    original = Message.create
    async def broken_ending(*args, **kwargs):
        if (kwargs.get('part_metadata') or {}).get('monitoringRoundEnd'):
            report = (await rows('SELECT * FROM monitor_reports'))[0]
            assert report['status'] == 'pending'  # scheduled before message publication
            raise RuntimeError('synthetic session storage failure')
        return await original(*args, **kwargs)
    monkeypatch.setattr(Message, 'create', broken_ending)
    result = await runtime.dispatch(context.execution, context.scheduler, adapter_factory=context.adapter)
    attempt = (await rows('SELECT * FROM monitor_attempts'))[0]
    assert result.status == TaskStatus.FAILED and attempt['status'] == 'failed'
    assert '结束总结发布失败' in attempt['error'] and attempt['summary']
    assert attempt['end_published'] == 0 and attempt['end_message_id']
    assert SessionStatus.get(attempt['session_id']).type == 'idle'
    assert any(kind == 'monitor.execution.finished' and item['status'] == 'failed' for kind, item in context.published)
    assert all(task.done() for task in manager._task_handles.values())
    monkeypatch.setattr(Message, 'create', original)
    await recovery.recover()
    repaired, _, card = await ending(attempt['id'])
    assert repaired['end_published'] == 1 and card.metadata['roundStatus'] == 'failed'
    assert '收尾失败' in card.text


@pytest.mark.parametrize('where', ['render', 'export'])
async def test_report_failure_is_independently_retryable_without_busy_or_false_task_failure(context, monkeypatch, where):
    from flocks.monitoring import reports
    from flocks.session.core.status import SessionStatus
    manager = real_dispatch_manager(monkeypatch)
    original = getattr(reports, 'render' if where == 'render' else 'export_report')
    if where == 'render':
        def broken_render(*args):
            raise RuntimeError('synthetic renderer failure')
        monkeypatch.setattr(reports, 'render', broken_render)
    else:
        monkeypatch.setattr(reports, 'export_report', AsyncMock(side_effect=RuntimeError('synthetic report I/O failure')))
    outcome = await runtime.dispatch(context.execution, context.scheduler, adapter_factory=context.adapter)
    attempt, _, card = await ending()
    report = (await rows('SELECT * FROM monitor_reports'))[0]
    assert outcome.status == TaskStatus.COMPLETED and attempt['status'] == 'completed'
    assert card.metadata['roundStatus'] == 'completed' and report['status'] == 'failed'
    assert SessionStatus.get(attempt['session_id']).type == 'idle'
    assert all(task.done() for task in manager._task_handles.values())
    monkeypatch.setattr(reports, 'render' if where == 'render' else 'export_report', original)
    await reports.retry_exports()
    assert (await rows('SELECT * FROM monitor_reports'))[0]['status'] == 'updated'
