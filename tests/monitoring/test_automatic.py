import asyncio
import copy
import inspect
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from flocks.monitoring import automatic as a, disposition as d
from flocks.monitoring.adapter import normalize, ContractError
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.reports import snapshot
from flocks.monitoring.sessions import ensure_daily
from flocks.monitoring.status_rules import ENTITY_TYPES
from flocks.monitoring.store import rows, write, encode
from flocks.project.project import Project
from flocks.session.interaction_policy import unattended_scope, monitoring_read_scope
from flocks.task.manager import TaskManager
from flocks.task.models import SchedulerMode, SchedulerStatus, TaskTrigger
from flocks.tool.registry import ToolRegistry, ToolResult
from flocks.tool.tool_loader import yaml_to_tool


@pytest.fixture
async def auto(tmp_path, monkeypatch):
    directory = tmp_path / '.flocks/workspace/monitor'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='monitor', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['device'])
    scheduler = await TaskManager.create_scheduler(title='monitor', mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron='*/10 * * * *'), context={'monitoring': policy.model_dump()})
    raw = {'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 4, 'dealStatus': 0,
           'whiteStatus': '未加白', 'gptResult': 170, 'endTime': 100}
    event = normalize('device', raw)
    session, day = await ensure_daily(policy, datetime.now(timezone.utc))
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready,scheduler_id) VALUES(?,?,?,?,1,?)',
                ('owner', policy.scope, project.id, encode(policy.model_dump()), scheduler.id))
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('attempt', 'execution', 'owner', project.id, policy.scope, day, session.id, 'message', d.stamp(), 'completed'))
    await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('attempt', event['key'], encode(event)))
    await write('INSERT INTO monitor_auto_settings VALUES(?,?,?,?,?,?)', ('owner', policy.scope, project.id, 1, 'revision', d.stamp()))
    await write('INSERT INTO monitor_auto_queue(owner,scope,project,event_key) VALUES(?,?,?,?)', ('owner', policy.scope, project.id, event['key']))
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *_: SimpleNamespace(enabled=True))
    state = SimpleNamespace(policy=policy, event=event, raw=raw, session=session, day=day, calls=[], scheduler=scheduler,
                            responses={kind: {'data': {'item': []}} for kind in ENTITY_TYPES}, lose_write=False,
                            leave_scope=False, before_write=None)
    class Fake:
        def __init__(self, policy, session_id, revision=None):
            self.policy, self.session_id, self.revision = policy, session_id, revision
        async def call(self, device, params, message):
            state.calls.append(copy.deepcopy(params))
            action = params['action']
            if action == 'update_status':
                if state.before_write: await state.before_write()
                await a.authorized(policy, self.revision)
                state.raw['dealStatus'] = 30 if params['deal_status'] == 70 else params['deal_status']
                if state.lose_write: raise TimeoutError('SECRET_BODY')
                return {'code': 'Success', 'data': {}}
            if action == 'get_entities':
                return copy.deepcopy(state.responses[params['entity_type']])
            if state.leave_scope and params['white_status']:
                return {'code': 'Success', 'data': {'item': [], 'total': 0}}
            return {'code': 'Success', 'data': {'item': [copy.deepcopy(state.raw)], 'total': 1}}
    state.adapter = Fake
    return state


async def execute(state):
    with unattended_scope(), monitoring_read_scope(state.policy.tool, state.policy.devices):
        return await a.process_event(state.policy, state.event['key'], state.session.id, 'revision', state.adapter)


@pytest.mark.parametrize('target', [10, 40, 60, 70])
async def test_automatic_selects_marks_and_reads_back(auto, target):
    if target == 40:
        auto.raw['gptResult'] = 120
        auto.responses['file']['data']['item'] = [{'threatLevel': 3, 'edrDealStatusInfo': {'status': 'DEAL_SUCCESS'}}]
    elif target == 60:
        auto.raw.update(gptResult=160, threatDefineName=['业务行为'])
    elif target == 70:
        auto.responses['ip']['data']['item'] = [{'threatLevel': 3, 'ndrDealStatusInfo': {'status': 'BLOCK_SUCCESS', 'isPermanent': True}}]
    result = await execute(auto)
    assert result['state'] == 'verified' and result['target'] == target
    writes = [call for call in auto.calls if call['action'] == 'update_status']
    assert len(writes) == 1 and writes[0]['deal_status'] == target and writes[0]['uuids'] == ['event']
    item = (await rows('SELECT * FROM monitor_dispositions'))[0]
    assert item['mode'] == 'automatic' and item['target_status'] == target
    assert json.loads(item['decision'])['target'] == target
    assert item['session_id'] == auto.session.id
    data = await snapshot('owner', auto.policy.scope, auto.day)
    assert data['metrics']['closed'] == int(target == 40)
    assert data['metrics']['contained'] == int(target == 70)
    assert data['metrics']['ignored'] == int(target == 60)


async def test_duplicate_round_never_repeats_write(auto):
    await asyncio.gather(execute(auto), execute(auto))
    assert len([c for c in auto.calls if c['action'] == 'update_status']) == 1


async def test_uncertain_write_recovers_without_new_write(auto):
    auto.lose_write = True
    assert (await execute(auto))['state'] == 'pending'
    result = await execute(auto)
    assert result['state'] == 'verified'
    assert len([c for c in auto.calls if c['action'] == 'update_status']) == 1
    assert 'SECRET_BODY' not in str(await rows('SELECT * FROM monitor_dispositions'))


async def test_unchanged_in_progress_still_allows_later_evidence(auto):
    assert (await execute(auto))['target'] == 10
    auto.raw['gptResult'] = 120
    auto.responses['file']['data']['item'] = [{'threatLevel': 3, 'edrDealStatusInfo': {'status': 'DEAL_SUCCESS'}}]
    assert (await execute(auto))['target'] == 40
    assert [c['deal_status'] for c in auto.calls if c['action'] == 'update_status'] == [10, 40]


async def test_out_of_scope_or_new_white_event_never_written(auto):
    auto.leave_scope = True
    assert (await execute(auto))['state'] == 'skipped'
    assert not await rows('SELECT * FROM monitor_dispositions')
    assert not await rows('SELECT * FROM monitor_auto_queue')


async def test_disabling_before_write_prevents_mutation(auto):
    async def disable():
        await write('UPDATE monitor_auto_settings SET enabled=0')
    auto.before_write = disable
    result = await execute(auto)
    assert result['state'] == 'pending' and auto.raw['dealStatus'] == 0
    with pytest.raises(ContractError): await execute(auto)


async def test_owner_and_binding_are_not_interchangeable(auto):
    changed = auto.policy.model_copy(update={'owner': 'other'})
    with pytest.raises(ContractError):
        await a.process_event(changed, auto.event['key'], auto.session.id, 'revision', auto.adapter)
    await write('UPDATE monitor_installations SET policy=?', (encode(auto.policy.model_copy(update={'devices': ['other']}).model_dump()),))
    with pytest.raises(ContractError): await execute(auto)
    assert not auto.calls


async def test_existing_manual_intent_blocks_automatic_write(auto):
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                ('manual', 'owner', auto.policy.scope, auto.event['key'], 'confirmed', 'pending', d.stamp(), d.stamp()))
    assert (await execute(auto))['state'] == 'waiting'
    assert not auto.calls


async def test_disabled_batch_does_not_call_devices(auto):
    await write('UPDATE monitor_auto_settings SET enabled=0')
    assert not (await a.process_batch(auto.policy, auto.session.id, [auto.event], None))['enabled']
    assert not auto.calls


async def test_queue_recovers_pending_without_rediscovered_event(auto):
    auto.lose_write = True
    await execute(auto)
    class Recorder:
        async def call(self, name, params, operation): return (await operation('msg'))[0]
    result = await a.process_batch(auto.policy, auto.session.id, [], Recorder(), auto.adapter)
    assert result['verified'] == 1
    assert len([c for c in auto.calls if c['action'] == 'update_status']) == 1


@pytest.fixture
async def actual_tool(auto, monkeypatch):
    path = Path(__file__).resolve().parents[2] / '.flocks/plugins/tools/device/sangfor_xdr_v2_2/sangfor_xdr_incidents.yaml'
    tool = yaml_to_tool(yaml.safe_load(path.read_text()), path)
    fn = inspect.getclosurevars(tool.handler).nonlocals['fn']
    calls = []
    async def send(method, path, data=None, params=None):
        calls.append((path, data))
        if path.endswith('/dealstatus'):
            auto.raw['dealStatus'] = data['dealStatus']
            return ToolResult(success=True, output={'code': 'Success'})
        if '/entities/' in path:
            return ToolResult(success=True, output={'code': 'Success', **copy.deepcopy(auto.responses[path.rsplit('/', 1)[-1]])})
        return ToolResult(success=True, output={'code': 'Success', 'data': {'item': [copy.deepcopy(auto.raw)], 'total': 1}})
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


async def test_real_registry_allows_only_scoped_automatic_status(auto, actual_tool):
    with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
        result = await a.process_event(auto.policy, auto.event['key'], auto.session.id, 'revision')
    assert result['state'] == 'verified'
    writes = [data for path, data in actual_tool if path.endswith('/dealstatus')]
    assert len(writes) == 1 and writes[0]['dealStatus'] == 10
    assert writes[0]['uuIds'] == ['event']


async def test_hook_cannot_change_automatic_target(auto, actual_tool, monkeypatch):
    from flocks.hooks.pipeline import HookPipeline
    async def patch(payload):
        return SimpleNamespace(output={'decision': {'validated_input_patch': {'uuids': ['other']}}}, execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline, 'run_tool_before', patch)
    with pytest.raises(ContractError):
        with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
            await a.process_event(auto.policy, auto.event['key'], auto.session.id, 'revision')
    assert actual_tool == []


async def test_automatic_settings_are_owner_scoped_and_strict(auto, actual_tool, monkeypatch):
    from fastapi import FastAPI
    from flocks.server.routes import security_monitoring as api
    import httpx
    monkeypatch.setattr('flocks.monitoring.adapter.discover', AsyncMock(return_value=(['device'], auto.policy.tool, None)))
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.require_user] = lambda: SimpleNamespace(id='owner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        path = '/monitoring/host-security-monitor/automatic-status'
        for body in ({'enabled': 'true'}, {'enabled': True, 'owner': 'other'}, {'enabled': True, 'target': 40}):
            assert (await client.put(path, json=body)).status_code == 422
        assert (await client.put(path, json={'enabled': False})).status_code == 200
        assert not (await a.settings('owner'))['enabled']
        assert (await client.put(path, json={'enabled': True})).status_code == 200
        assert (await a.settings('owner'))['enabled']
        app.dependency_overrides[api.require_user] = lambda: SimpleNamespace(id='other')
        assert (await client.put(path, json={'enabled': True})).status_code == 404
    assert actual_tool == []  # enabling is local configuration, never a device mutation


async def test_full_native_round_marks_without_interactive_confirmation(auto, actual_tool):
    from flocks.monitoring.runtime import run
    from flocks.task.models import ExecutionTriggerType
    from flocks.session.message import Message
    execution = await TaskManager.create_execution_from_scheduler(auto.scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
    with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
        result = await run(execution, auto.policy)
    assert result.action == 'stop'
    attempts = await rows('SELECT result FROM monitor_attempts WHERE execution_id=?', (execution.id,))
    assert json.loads(attempts[0]['result'])['automatic']['verified'] == 1
    assert any(path.endswith('/dealstatus') for path, _ in actual_tool)
    data = await snapshot('owner', auto.policy.scope, auto.day)
    assert data['metrics']['closed'] == 0
    assert data['events'][0]['dispositionRecord']['target_status'] == 10
    report = (await rows('SELECT content FROM monitor_reports'))[0]['content']
    assert '自动标记：已启用' in report and '来源：automatic' in report


async def test_containment_recheck_uses_native_equivalence(auto, actual_tool):
    auto.responses['ip']['data']['item'] = [{'threatLevel': 3, 'ndrDealStatusInfo': {'status': 'BLOCK_SUCCESS', 'isPermanent': True}}]
    assert (await execute(auto))['target'] == 70
    item = (await rows('SELECT * FROM monitor_dispositions'))[0]
    result = await d.recheck('owner', item['id'])
    assert result['status'] == 'verified' and result['observed_status'] == 30
    assert not any(path.endswith('/dealstatus') for path, _ in actual_tool)


async def test_old_database_migration_preserves_manual_records(auto):
    from flocks.monitoring.store import connection
    async with connection() as db:
        await db.execute('DROP TABLE monitor_dispositions')
        await db.execute('CREATE TABLE monitor_dispositions (id TEXT, owner TEXT, scope TEXT, event_key TEXT, comment TEXT, '
                         'status TEXT, observed_status INTEGER, error TEXT, session_id TEXT, created_at TEXT, updated_at TEXT, PRIMARY KEY(owner,scope,id))')
        await db.execute('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                         ('legacy', 'owner', auto.policy.scope, auto.event['key'], 'previous', 'verified', d.stamp(), d.stamp()))
    # Startup readers may all discover the old schema at once.
    results = await asyncio.gather(*(rows('SELECT * FROM monitor_dispositions') for _ in range(5)))
    assert all(result[0]['comment'] == 'previous' and result[0]['mode'] == 'manual' and result[0]['target_status'] == 40 for result in results)


async def test_pause_and_reenable_revision_revoke_existing_write_scope(auto):
    await TaskManager.disable_scheduler(auto.scheduler.id)
    with pytest.raises(ContractError, match='暂停'): await execute(auto)
    await TaskManager.enable_scheduler(auto.scheduler.id)
    await write("UPDATE monitor_auto_settings SET revision='new-revision'")
    with pytest.raises(ContractError, match='配置已变化'): await execute(auto)
    assert not auto.calls


@pytest.mark.parametrize('patch', [{'uuids': ['victim']}, {'deal_status': 60}, {'api_params': {'dealStatus': 40}}])
async def test_write_hook_cannot_widen_rule_decision(auto, actual_tool, monkeypatch, patch):
    from flocks.hooks.pipeline import HookPipeline
    async def hook(payload):
        changing = payload['tool_execution']['tool']['validated_input'].get('action') == 'update_status'
        return SimpleNamespace(output={'decision': {'validated_input_patch': patch}} if changing else {}, execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline, 'run_tool_before', hook)
    with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
        result = await a.process_event(auto.policy, auto.event['key'], auto.session.id, 'revision')
    assert result['state'] == 'pending'
    assert not any(path.endswith('/dealstatus') for path, _ in actual_tool)


async def test_crashed_automatic_write_only_recovers_by_read(auto):
    await execute(auto)
    await write("UPDATE monitor_dispositions SET status='writing'")
    assert (await execute(auto))['state'] == 'waiting'
    await write('UPDATE monitor_dispositions SET updated_at=?', ((datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),))
    assert (await execute(auto))['state'] == 'verified'
    assert len([call for call in auto.calls if call['action'] == 'update_status']) == 1


async def test_batch_budget_leaves_durable_queue_for_next_round(auto, monkeypatch):
    monkeypatch.setattr(a, 'MAX_PER_ROUND', 1)
    await write('INSERT INTO monitor_auto_queue(owner,scope,project,event_key) VALUES(?,?,?,?)',
                ('owner', auto.policy.scope, auto.policy.project, 'device:incident:later'))
    class Recorder:
        async def call(self, name, params, operation): return (await operation('msg'))[0]
    result = await a.process_batch(auto.policy, auto.session.id, [], Recorder(), auto.adapter)
    assert result['processed'] == 1
    assert len(await rows("SELECT * FROM monitor_auto_queue WHERE checked_at=''")) == 1


async def test_unattended_cannot_enable_automatic_policy(auto):
    await write('UPDATE monitor_auto_settings SET enabled=0')
    with unattended_scope(), pytest.raises(PermissionError):
        await a.configure('owner', True)
    assert not (await a.settings('owner'))['enabled']


async def test_latest_mark_controls_projection_without_stale_ignore(auto):
    auto.raw.update(gptResult=160, threatDefineName=['业务行为'])
    await execute(auto)
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,observed_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                ('latest-manual', 'owner', auto.policy.scope, auto.event['key'], 'completed', 'verified', 40, d.stamp(), d.stamp()))
    data = await snapshot('owner', auto.policy.scope, auto.day)
    assert data['events'][0]['closure'] == 'closed'
    assert data['events'][0]['risk'] == 'risk'
    assert data['metrics']['ignored'] == 0 and data['metrics']['closed'] == 1
