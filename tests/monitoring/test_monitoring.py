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
async def policy(tmp_path):
    directory = tmp_path / '.flocks/workspace/monitor'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='安全运营监测', worktree=str(directory))
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
        return {'code': 0, 'data': {'list': [{'uuId': 'event-1', 'name': 'Synthetic event', 'riskLevel': 1, 'hostIp': '192.0.2.1'}], 'total': 1}}

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
    queue = TaskQueue()
    first = await queue.dequeue()
    assert first and await queue.dequeue() is None
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
    assert data['events'][0]['closure'] == 'open'
    messages = await Message.list_with_parts(attempts[0]['session_id'])
    tools = [p for m in messages for p in m.parts if p.type == 'tool']
    assert len(tools) == 6 and all(p.state.status == 'completed' for p in tools)
    await export_report(policy.owner, policy.scope, day)
    first = (await rows('SELECT * FROM monitor_reports'))[0]
    await export_report(policy.owner, policy.scope, day)
    second = (await rows('SELECT * FROM monitor_reports'))[0]
    assert first['version'] == second['version']
    assert second['content'].count('## 轮次') == 2
    assert len(list((Path.home() / '.flocks/workspace/outputs' / day).glob('*.md'))) == 1
    foreign = await snapshot('other', policy.scope, day)
    assert not foreign['runs'] and not foreign['events']

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
async def test_empty_events_success_and_entity_failure_partial(policy):
    class Empty(FixtureAdapter):
        async def call(self, *args): return {'data': {'list': [], 'total': 0}}
    class Partial(FixtureAdapter):
        async def call(self, device, params, message):
            if params['action'] == 'get_entities': raise ContractError('entity unavailable')
            return await super().call(device, params, message)
    scheduler = await scheduler_for(policy)
    for factory in (Empty, Partial):
        execution = await TaskManager.create_execution_from_scheduler(scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        assert (await run(execution, policy, factory)).action == 'stop'
    attempts = await rows('SELECT status,result FROM monitor_attempts ORDER BY sequence')
    assert [r['status'] for r in attempts] == ['completed','partial']
    assert json.loads(attempts[0]['result'])['events'] == 0

@pytest.mark.asyncio
async def test_144_online_slots_and_downtime_missing(policy):
    scheduler = await scheduler_for(policy)
    stamp = datetime(2026, 9, 22, 16, tzinfo=timezone.utc)
    scheduler.trigger.next_run = stamp
    await TaskStore.update_scheduler(scheduler)
    for n in range(144): await admit_slots(scheduler, stamp + timedelta(minutes=10*n))
    assert len(await rows('SELECT * FROM monitor_slots')) == 144
    assert len(await rows('SELECT * FROM task_execution_queue_refs')) == 144
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
    assert (await rows('SELECT status FROM monitor_attempts'))[0]['status'] == 'interrupted'
    assert all(r['status'] == 'failed' for r in await rows('SELECT status FROM monitor_steps'))
    await run(execution, policy, FixtureAdapter)
    attempts = await rows('SELECT * FROM monitor_attempts ORDER BY sequence')
    assert len(attempts) == 2 and attempts[0]['session_id'] == attempts[1]['session_id']
