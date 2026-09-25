import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from flocks.monitoring.models import MonitoringPolicy, COMPONENT_ID
from flocks.monitoring.store import rows, write, encode
from flocks.monitoring.sessions import ensure_daily
from flocks.monitoring.scheduling import admit_slots
from flocks.monitoring.runtime import run, query_device, Recorder
from flocks.monitoring.adapter import ContractError, page_items, normalize
from flocks.monitoring.reports import snapshot, export_report, retry_exports
from flocks.task.manager import TaskManager
from flocks.task.models import TaskScheduler, TaskTrigger, SchedulerMode, TaskStatus, ExecutionTriggerType
from flocks.task.store import TaskStore
from flocks.task.queue import TaskQueue
from flocks.session.message import Message, MessageRole
from flocks.project.project import Project

@pytest.fixture
async def policy(tmp_path, monkeypatch):
    directory = tmp_path / '.flocks/workspace/monitor'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='安全运营监测', worktree=str(directory))
    # Exercise the real checkpointed investigator with deterministic model and
    # device responses; no external provider or device is contacted.
    from flocks.monitoring import capabilities as c, investigation as i
    async def discover(policy):
        return [c.Capability(device, device, policy.tool, 'xdr', 'Fixture XDR', 'unknown') for device in policy.devices], []
    async def choose(agent, data):
        if not data['evidence']:
            return i.Choice(action='query', capability=data['event']['device'], reason='核对主机', entity='host')
        return i.Choice(action='finish', verdict='risk', evidence_ids=['evidence-1'], reason='有主机证据，需负责人跟进')
    monkeypatch.setattr(c, 'discover', discover)
    monkeypatch.setattr(i.Agent, 'list', AsyncMock(return_value=[]))
    monkeypatch.setattr(i, 'choose', choose)
    monkeypatch.setattr(c, 'query', AsyncMock(return_value=({'data': {'item': [{'hostIp': '192.0.2.1', 'threatLevel': 3}]}}, {'action': 'get_entities'})))
    return MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['xdr-1'])

async def scheduler_for(policy):
    return await TaskManager.create_scheduler(title='安全运营监测', mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron='*/10 * * * *'), context={'monitoring': policy.model_dump()})

class FixtureAdapter:
    calls = []
    def __init__(self, policy, session_id): self.policy = policy
    async def call(self, device, params, message):
        self.calls.append(dict(params))
        assert params['action'] in {'list', 'get_entities'}
        if params['action'] == 'get_entities':
            return {'code': 0, 'data': {'list': [{'hostId': 'host-1', 'ip': '192.0.2.1'}]}}
        return {'code': 0, 'data': {'list': [{'uuId': 'event-1', 'name': 'Synthetic event', 'incidentSeverity': 4, 'hostIp': '192.0.2.1'}], 'total': 1}}


@pytest.mark.parametrize('has_cursor', [False, True])
async def test_legacy_development_policy_queries_all_devices_with_production_filters(policy, has_cursor):
    policy = MonitoringPolicy.model_validate({**policy.model_dump(), 'development_sample': True, 'devices': ['xdr-1', 'xdr-2']})
    assert policy.development_sample is False
    if has_cursor:
        await write('INSERT INTO monitor_cursors VALUES(?,?,?,?)', (policy.owner, policy.scope, 'xdr-1', 123456))
    FixtureAdapter.calls = []
    scheduler = await scheduler_for(policy)
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    await run(execution, policy, FixtureAdapter)
    cursors = await rows('SELECT * FROM monitor_cursors')
    assert len(cursors) == 2 and all(row['through_time'] > 123456 for row in cursors)
    observations = await rows('SELECT * FROM monitor_observations')
    assert len(observations) == 2
    assert {json.loads(row['data'])['device'] for row in observations} == {'xdr-1', 'xdr-2'}
    calls = [c for c in FixtureAdapter.calls if c['action'] == 'list']
    assert len(calls) == 2
    assert calls[0]['start_time'] == (123456 - 600 if has_cursor else calls[0]['end_time'] - 86400)
    assert all(call['deal_statuses'] == [0, 10] and call['white_status'] == ['未加白', '部分加白'] for call in calls)
    assert all(call['page_size'] == 100 for call in calls)
    report = (await rows('SELECT * FROM monitor_reports'))[0]
    assert '仅统计样本' not in report['content'] and '联调抽样轮次' not in report['summary_content']

@pytest.mark.asyncio
async def test_daily_reservation_concurrent_recovery_midnight(policy):
    start = datetime(2026, 9, 23, 15, 59, tzinfo=timezone.utc)
    results = await asyncio.gather(*(ensure_daily(policy, start) for _ in range(6)))
    assert len({s.id for s, _ in results}) == 1
    session, day = results[0]
    await Message.create(session_id=session.id, role=MessageRole.ASSISTANT, content='preserve')
    await write("UPDATE monitor_daily_sessions SET state='creating'")
    again, _ = await ensure_daily(policy, start)
    assert again.id == session.id
    assert len(await Message.list_with_parts(session.id)) == 1
    tomorrow, newday = await ensure_daily(policy, start + timedelta(minutes=2))
    assert newday != day and tomorrow.id != session.id

@pytest.mark.asyncio
async def test_atomic_slots_queue_and_serial_scope(policy):
    scheduler = await scheduler_for(policy)
    stamp = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    stamp = stamp.replace(minute=stamp.minute // 10 * 10)
    scheduler.trigger.next_run = stamp
    await TaskStore.update_scheduler(scheduler)
    await asyncio.gather(admit_slots(scheduler, stamp), admit_slots(scheduler, stamp))
    assert len(await rows('SELECT * FROM monitor_slots')) == 1
    await admit_slots(scheduler, stamp + timedelta(minutes=20))
    assert len(await rows('SELECT * FROM monitor_slots')) == 3
    assert len(await rows("SELECT * FROM monitor_slots WHERE status='coalesced'")) == 2
    queue = TaskQueue()
    first = await queue.dequeue()
    assert first and await queue.dequeue() is None
    await admit_slots(scheduler, stamp + timedelta(minutes=40))
    assert len(await rows("SELECT * FROM task_execution_queue_refs WHERE status='queued'")) == 1
    assert await queue.dequeue() is None
    await TaskStore.finish_queue_ref(first.id)
    queue.mark_finished(first.id)
    assert await queue.dequeue()

@pytest.mark.asyncio
async def test_native_rounds_dashboard_report_idempotence(policy):
    scheduler = await scheduler_for(policy)
    for _ in range(2):
        execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        result = await run(execution, policy, FixtureAdapter)
        assert result.action == 'stop'
    attempts = await rows('SELECT * FROM monitor_attempts')
    assert len(attempts) == 2 and len({x['session_id'] for x in attempts}) == 1
    day = attempts[0]['business_date']
    data = await snapshot(policy.owner, policy.scope, day)
    assert data['metrics']['events'] == 1 and data['metrics']['started'] == 2
    assert data['metrics']['risk'] == 1 and data['events'][0]['incidentSeverity'] == 4
    assert data['events'][0]['closure'] == 'open'
    messages = await Message.list_with_parts(attempts[0]['session_id'])
    tools = [p for m in messages for p in m.parts if p.type == 'tool']
    assert len(tools) == 10 and all(p.state.status == 'completed' for p in tools)
    assert len(await rows("SELECT * FROM monitor_investigations WHERE state='ready'")) == 1
    await export_report(policy.owner, policy.scope, day)
    first = (await rows('SELECT * FROM monitor_reports'))[0]
    await export_report(policy.owner, policy.scope, day)
    second = (await rows('SELECT * FROM monitor_reports'))[0]
    assert first['version'] == second['version']
    assert second['content'].count('## 轮次') == 2
    assert len(list((Path.home() / '.flocks/workspace/outputs' / day).glob('*.md'))) == 2
    foreign = await snapshot('other', policy.scope, day)
    assert not foreign['runs'] and not foreign['events']


@pytest.mark.asyncio
async def test_failed_round_diagnostic_status_matches_persisted_facts(policy, monkeypatch):
    from flocks.monitoring import diagnostics as diag
    captured = []
    monkeypatch.setattr(diag._sink, 'submit', lambda fields: captured.append(diag.safe_record(fields)))
    class Failed(FixtureAdapter):
        async def call(self, device, params, message):
            raise ContractError('XDR 返回非 JSON 数据')
    scheduler = await scheduler_for(policy)
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    result = await run(execution, policy, Failed)
    assert result.action == 'error'
    assert (await rows('SELECT status FROM monitor_attempts'))[0]['status'] == 'failed'
    assert not await rows('SELECT * FROM monitor_cursors')
    assert captured[0]['event'] == 'trace.start'
    assert captured[-1]['event'] == 'trace.end' and captured[-1]['outcome'] == 'failed'
    assert any(r.get('stage') == 'query.events' and r.get('outcome') == 'error' for r in captured)
    assert any(r['event'] == 'run.result' and r['events'] == 0 and r['errors'] == 1 for r in captured)
    assert {r['execution'] for r in captured} == {diag.opaque(execution.id)}

@pytest.mark.asyncio
async def test_export_failure_retry_does_not_query(policy, monkeypatch):
    scheduler = await scheduler_for(policy)
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    import flocks.monitoring.reports as report_module
    original = report_module.os.replace
    monkeypatch.setattr(report_module.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    await run(execution, policy, FixtureAdapter)
    count = len(FixtureAdapter.calls)
    assert (await rows('SELECT status FROM monitor_reports'))[0]['status'] == 'failed'
    monkeypatch.setattr(report_module.os, 'replace', original)
    await retry_exports()
    assert len(FixtureAdapter.calls) == count
    assert (await rows('SELECT status FROM monitor_reports'))[0]['status'] == 'updated'

@pytest.mark.parametrize('payload', [{}, {'data': {}}, {'data': {'list': [{'name': 'missing ID'}]}}, {'data': {'list': [], 'total': '5'}}])
def test_unknown_contract_never_becomes_no_events(payload):
    with pytest.raises(ContractError): page_items(payload)

@pytest.mark.parametrize('risk', [3, 4, None, '1', 99])
def test_unknown_not_ignored(risk):
    assert normalize('xdr', {'uuId': 'id', 'riskLevel': risk})['risk'] == 'unknown'


@pytest.mark.parametrize('severity,risk', [(-1, 'unknown'), (1, 'unknown'), (2, 'risk'), (3, 'risk'), (4, 'risk'),
                                         (0, 'unknown'), (5, 'unknown'), (None, 'unknown'), ('4', 'unknown'), (True, 'unknown')])
def test_native_incident_severity_takes_precedence(severity, risk):
    event = normalize('xdr', {'uuId': 'id', 'incidentSeverity': severity, 'riskLevel': 0,
                              'incidentThreatClass': '恶意软件', 'incidentThreatType': '木马'})
    assert event['risk'] == risk
    assert event['riskLevel'] is None
    assert event['incidentSeverity'] == (severity if type(severity) is int and severity in {-1, 1, 2, 3, 4} else None)
    assert event['incidentThreatClass'] == '恶意软件' and event['incidentThreatType'] == '木马'
    if risk == 'risk': assert 'incidentSeverity' in event['reason']


@pytest.mark.parametrize('level,risk', [(0,'risk'), (1,'risk'), (2,'risk'), (3,'unknown'), (4,'unknown'), (5,'unknown')])
def test_legacy_risk_level_keeps_its_own_enum(level, risk):
    event = normalize('xdr', {'uuId': 'id', 'riskLevel': level})
    assert event['risk'] == risk and event['severitySource'] == 'riskLevel'
    assert event['riskLevel'] == level and event['incidentSeverity'] is None

@pytest.mark.asyncio
async def test_full_pagination_missing_page_does_not_advance_cursor(policy):
    class Paged(FixtureAdapter):
        fail = False
        async def call(self, device, params, message):
            if params['action'] != 'list': return await super().call(device, params, message)
            page = params['page_num']
            if page == 2 and self.fail: raise ContractError('fixture page unavailable')
            items = [{'uuId': str(i), 'riskLevel': 1} for i in (range(100) if page == 1 else range(100, 105))]
            return {'data': {'list': items, 'total': 105}}
    class QuietRecorder:
        async def call(self, name, params, operation): return (await operation('fixture'))[0]
    adapter = Paged(policy, 'fixture')
    assert len(await query_device(adapter, 'xdr-1', 0, 100, QuietRecorder())) == 105
    adapter.fail = True
    with pytest.raises(ContractError): await query_device(adapter, 'xdr-1', 0, 100, QuietRecorder())
    class Failed(Paged): fail = True
    scheduler = await scheduler_for(policy)
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    result = await run(execution, policy, Failed)
    assert result.action == 'error'
    assert not await rows('SELECT * FROM monitor_cursors')
    assert not await rows('SELECT * FROM monitor_observations')

@pytest.mark.asyncio
async def test_empty_events_complete_but_entity_failure_fails(policy, monkeypatch):
    from flocks.monitoring import capabilities
    class Empty(FixtureAdapter):
        async def call(self, *args): return {'data': {'list': [], 'total': 0}}
    scheduler = await scheduler_for(policy)
    monkeypatch.setattr(capabilities, 'query', AsyncMock(side_effect=ContractError('entity unavailable')))
    for factory, expected in ((Empty, 'stop'), (FixtureAdapter, 'error')):
        execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        assert (await run(execution, policy, factory)).action == expected
    attempts = await rows('SELECT status,result FROM monitor_attempts ORDER BY sequence')
    assert [r['status'] for r in attempts] == ['completed','failed']
    assert json.loads(attempts[0]['result'])['events'] == 0

@pytest.mark.asyncio
async def test_144_online_slots_and_downtime_missing(policy):
    scheduler = await scheduler_for(policy)
    stamp = datetime(2026, 9, 22, 16, tzinfo=timezone.utc)
    scheduler.trigger.next_run = stamp
    await TaskStore.update_scheduler(scheduler)
    for n in range(144): await admit_slots(scheduler, stamp + timedelta(minutes=10*n))
    assert len(await rows('SELECT * FROM monitor_slots')) == 144
    assert len(await rows('SELECT * FROM task_execution_queue_refs')) == 1
    assert len(await rows("SELECT * FROM monitor_slots WHERE status='coalesced'")) == 143
    await admit_slots(scheduler, stamp + timedelta(days=2))
    missed = await rows("SELECT * FROM monitor_slots WHERE status='missed'")
    assert missed
    for row in missed:
        execution = await TaskStore.get_execution(row['execution_id'])
        assert execution.status == TaskStatus.CANCELLED and not execution.session_id

@pytest.mark.asyncio
async def test_recovery_closes_running_steps_preserves_attempt(policy):
    from flocks.monitoring.recovery import recover
    scheduler = await scheduler_for(policy)
    execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    await run(execution, policy, FixtureAdapter)
    await write("UPDATE monitor_attempts SET status='running'")
    await write("UPDATE monitor_steps SET status='running'")
    await recover()
    assert (await rows('SELECT status FROM monitor_attempts'))[0]['status'] == 'failed'
    assert all(r['status'] == 'failed' for r in await rows('SELECT status FROM monitor_steps'))
    await run(execution, policy, FixtureAdapter)
    attempts = await rows('SELECT * FROM monitor_attempts ORDER BY sequence')
    assert len(attempts) == 2 and attempts[0]['session_id'] == attempts[1]['session_id']
