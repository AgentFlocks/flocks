"""Real persisted target configuration and mail routing; no external mail/XDR."""
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from flocks.config.config import Config
from flocks.monitoring import configuration as c, lifecycle, mailflow as m
from flocks.monitoring.adapter import normalize
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.sessions import ensure_daily
from flocks.monitoring.store import encode, rows, write
from flocks.project.project import Project
from flocks.task.manager import TaskManager
from flocks.task.models import SchedulerMode, SchedulerStatus, TaskTrigger
from flocks.task.store import TaskStore


def request(*, email_a='a@example.com', devices=('a', 'b'), enabled=True):
    return c.ConfigurationRequest(enabled=enabled, targets=[{
        'device_id': device, 'responsible_name': '负责人' + device,
        'recipient_email': email_a if device == 'a' else 'b@example.com',
    } for device in devices])


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    directory = tmp_path / '.flocks/workspace/monitor'
    directory.mkdir(parents=True)
    project = await Project.create(owner_id='owner', name='monitor', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['a'])
    scheduler = await TaskManager.create_scheduler(title='monitor', mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron='*/10 * * * *'), context={'monitoring': policy.model_dump()})
    await TaskManager.disable_scheduler(scheduler.id)
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready,scheduler_id) VALUES(?,?,?,?,1,?)',
                ('owner', policy.scope, project.id, encode(policy.model_dump()), scheduler.id))
    await write('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?)',
                ('owner', policy.scope, project.id, 'a@example.com', '旧负责人', 1, 'legacy-revision', 'mailbox', m.d.stamp()))
    catalog = [{'id': device, 'name': '机房' + device, 'available': True, 'reason': None, 'tool': 'tool-' + device} for device in ('a', 'b', 'c')]
    monkeypatch.setattr(c.adapter, 'device_catalog', AsyncMock(side_effect=lambda: copy.deepcopy(catalog)))
    monkeypatch.setattr(Config, 'resolve_default_llm', AsyncMock(return_value={'model_id': 'fixture'}))
    monkeypatch.setattr(m.transport, 'transport', lambda recipient: (None, {}))
    monkeypatch.setattr(m.transport, 'mailbox_key', lambda cfg: 'mailbox')
    schema = SimpleNamespace(to_json_schema=lambda: {'properties': {'uuids': {}, 'deal_status': {}, 'deal_comment': {}}})
    monkeypatch.setattr('flocks.tool.registry.ToolRegistry.get', lambda name: SimpleNamespace(info=SimpleNamespace(get_schema=lambda: schema)))
    monkeypatch.setattr('flocks.hub.local.get_record', lambda kind, _id: SimpleNamespace(enabled=True) if kind == 'component' else None)
    monkeypatch.setattr(m, '_locks', {})
    monkeypatch.setattr(m, '_round_locks', {})
    monkeypatch.setattr(m.transport, 'send', AsyncMock(return_value=True))
    return SimpleNamespace(policy=policy, scheduler=scheduler, catalog=catalog)


async def saved_policy():
    return MonitoringPolicy.model_validate_json((await rows('SELECT policy FROM monitor_installations'))[0]['policy'])


async def activate(setup):
    scheduler = await TaskStore.get_scheduler(setup.scheduler.id)
    scheduler.status = SchedulerStatus.ACTIVE
    await TaskStore.update_scheduler(scheduler)
    return await saved_policy()


class Recorder:
    async def call(self, name, params, operation):
        return (await operation('msg'))[0]


async def notify(setup):
    await c.configure('owner', request())
    policy = await activate(setup)
    session, day = await ensure_daily(policy, datetime.now(timezone.utc))
    raw = {'uuId': 'same-event', 'name': '同名告警', 'incidentSeverity': 4, 'dealStatus': 0,
           'whiteStatus': '未加白', 'endTime': 100, 'hostIp': '192.0.2.10'}
    actual = {device: copy.deepcopy(raw) for device in ('a', 'b')}
    calls = []

    class Adapter:
        def __init__(self, policy, session_id, revision=None):
            self.policy, self.session_id, self.revision = policy, session_id, revision

        async def call(self, device, params, message):
            calls.append((device, copy.deepcopy(params)))
            if params['action'] == 'update_status':
                await m.authorized(self.policy, self.revision, device)
                actual[device]['dealStatus'] = params['deal_status']
                return {'data': {}}
            if params['action'] == 'get_entities':
                return {'data': {'item': []}}
            return {'data': {'item': [copy.deepcopy(actual[device])], 'total': 1}}

    events = [normalize(device, raw) for device in ('a', 'b')]
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('attempt', 'exec', 'owner', policy.project, policy.scope, day, session.id, 'message', m.d.stamp(), 'completed'))
    for event in events:
        await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('attempt', event['key'], encode(event)))
    result = await m.notify_batch(policy, session.id, events, Recorder(), Adapter)
    assert result['sent'] == 2 and not result['errors']
    notices = {json.loads(n['event'])['device']: n for n in await rows('SELECT * FROM monitor_mail_notices')}
    return SimpleNamespace(policy=policy, session=session, notices=notices, calls=calls, adapter=Adapter, events=events)


async def receive(state, *, sender='b@example.com', notice_device='b', message_id='<reply@test.example>'):
    from flocks.channel.base import InboundMessage
    notice = state.notices[notice_device]
    text = '本次已经完成处理，文件已清理，复查正常。'
    msg = InboundMessage(channel_id='email', account_id='default', message_id=message_id,
                         sender_id=sender, chat_id=sender, text=text, reply_to_id=notice['message_id'],
                         raw={'mailbox_key': 'mailbox', 'authenticated_sender': True, 'subject': '回复'})
    assert await m.receive(msg)
    async def interpreter(payload, candidates):
        return {'classification': 'feedback', 'items': [{'notice_id': notice['id'], 'outcome': 'completed',
                'evidence': text, 'reason': text, 'ambiguous': False}]}
    await m.process_replies(state.policy, state.session.id, await m.cutoff('owner', state.policy.project), Recorder(), state.adapter, interpreter)


async def test_save_two_targets_atomically_and_migrate_single_recipient(setup):
    result = await c.configure('owner', request())
    assert result['enabled'] and not result['running']
    assert [(row['device_name'], row['recipient_email']) for row in result['targets']] == [('机房a', 'a@example.com'), ('机房b', 'b@example.com')]
    policy = await saved_policy()
    scheduler = await TaskStore.get_scheduler(setup.scheduler.id)
    assert policy.devices == ['a', 'b'] and policy.targets_configured
    assert policy.device_tools == {'a': 'tool-a', 'b': 'tool-b'}
    assert scheduler.context['monitoring'] == policy.model_dump()
    assert (await m.settings('owner', 'a'))['revision'] == 'legacy-revision'
    assert not (await m.settings('owner', 'c'))['enabled']
    assert not (await m.settings('owner'))['recipient']  # Never fall back to A for another device.
    setup.catalog[0]['name'] = '机房A新名称'
    assert (await c.snapshot('owner'))['targets'][0]['device_name'] == '机房A新名称'
    assert await c.resolve_targets(policy) is None
    assert policy.device_names['a'] == '机房A新名称'


@pytest.mark.parametrize('change', ['duplicate', 'blank_name', 'blank_email', 'bad_email', 'spoof_name'])
def test_reject_invalid_configuration_input(change):
    body = request().model_dump()
    if change == 'duplicate':
        body['targets'][1]['device_id'] = 'a'
    if change == 'blank_name':
        body['targets'][0]['responsible_name'] = ' '
    if change == 'blank_email':
        body['targets'][0]['recipient_email'] = ' '
    if change == 'bad_email':
        body['targets'][0]['recipient_email'] = 'a@example.com,b@example.com'
    if change == 'spoof_name':
        body['targets'][0]['device_name'] = '伪造设备名'
    with pytest.raises(ValidationError):
        c.ConfigurationRequest.model_validate(body)


async def test_running_or_unavailable_cannot_mutate_configuration(setup):
    await activate(setup)
    with pytest.raises(ValueError, match='暂停'):
        await c.configure('owner', request())
    assert not await rows('SELECT * FROM monitor_device_targets')
    await TaskManager.disable_scheduler(setup.scheduler.id)
    setup.catalog[1].update(available=False, reason='已停用')
    with pytest.raises(ValueError, match='已停用'):
        await c.configure('owner', request())
    assert not await rows('SELECT * FROM monitor_device_targets')
    assert (await saved_policy()).devices == ['a']


async def test_two_notices_and_reply_only_write_original_device(setup):
    state = await notify(setup)
    assert len({n['message_id'] for n in state.notices.values()}) == 2
    for device, notice in state.notices.items():
        assert notice['recipient'] == device + '@example.com'
        assert '监测设备：机房' + device in notice['body']
        assert notice['event_key'] == f'{device}:incident:same-event'
    again = await m.notify_batch(state.policy, state.session.id, state.events, Recorder(), state.adapter)
    assert again['sent'] == 0 and m.transport.send.await_count == 2
    await receive(state)
    writes = [(device, params) for device, params in state.calls if params['action'] == 'update_status']
    assert len(writes) == 1 and writes[0][0] == 'b' and writes[0][1]['deal_status'] == 40
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'verified'


async def test_reply_from_b_cannot_select_a_notice(setup):
    state = await notify(setup)
    await receive(state, notice_device='a')
    assert not any(params['action'] == 'update_status' for _, params in state.calls)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


async def test_change_a_recipient_preserves_b_feedback(setup):
    state = await notify(setup)
    await TaskManager.disable_scheduler(setup.scheduler.id)
    await c.configure('owner', request(email_a='new-a@example.com'))
    state.policy = await activate(setup)
    await receive(state)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'verified'
    await receive(state, sender='a@example.com', notice_device='a', message_id='<old-a@test.example>')
    assert (await rows('SELECT state FROM monitor_mail_replies ORDER BY sequence DESC'))[0]['state'] == 'needs_review'
    assert all(device == 'b' for device, params in state.calls if params['action'] == 'update_status')


async def test_removed_target_keeps_history_but_cannot_mark(setup):
    state = await notify(setup)
    await TaskManager.disable_scheduler(setup.scheduler.id)
    await c.configure('owner', request(devices=('a',)))
    state.policy = await activate(setup)
    await receive(state)
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 2
    assert not any(params['action'] == 'update_status' for _, params in state.calls)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


async def test_legacy_endpoint_cannot_override_device_routes(setup):
    await c.configure('owner', request())
    with pytest.raises(ValueError, match='监测配置页面'):
        await m.configure('owner', m.MailSettingsRequest(enabled=False))
    assert (await m.settings('owner'))['enabled']


async def test_save_is_all_or_nothing_when_recipient_conflicts(setup):
    await write('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?)',
                ('other', setup.policy.scope, 'other-project', 'b@example.com', 'other', 1, 'other-rev', 'mailbox', m.d.stamp()))
    with pytest.raises(ValueError, match='其他拥有者'):
        await c.configure('owner', request())
    assert not await rows('SELECT * FROM monitor_device_targets')
    assert (await saved_policy()).devices == ['a']


async def test_start_uses_selected_devices_and_reports_missing_device(setup, monkeypatch):
    from flocks.hub import catalog
    await c.configure('owner', request())
    monkeypatch.setattr(lifecycle, 'validate_manifest', lambda *args: None)
    monkeypatch.setattr(catalog, 'load_manifest', lambda *args: None)
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *args: SimpleNamespace(enabled=True, installPath=setup.policy.directory))
    monkeypatch.setattr('flocks.monitoring.agent_component.unavailable_reason', AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, 'start_immediately', AsyncMock())
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(side_effect=AssertionError('must not rediscover all devices')))
    await lifecycle.start_monitoring('owner')
    assert (await saved_policy()).devices == ['a', 'b']
    lifecycle.start_immediately.assert_awaited_once()
    setup.catalog.pop(1)
    with pytest.raises(ValueError, match='机房b'):
        await lifecycle.start_monitoring('owner')
    assert not (await rows('SELECT ready FROM monitor_installations'))[0]['ready']


async def test_start_cannot_transfer_legacy_recipient_to_replacement_device(setup, monkeypatch):
    from flocks.hub import catalog
    monkeypatch.setattr(lifecycle, 'validate_manifest', lambda *args: None)
    monkeypatch.setattr(catalog, 'load_manifest', lambda *args: None)
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *args: SimpleNamespace(enabled=True, installPath=setup.policy.directory))
    monkeypatch.setattr('flocks.monitoring.agent_component.unavailable_reason', AsyncMock(return_value=None))
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=(['b'], 'tool-b', None)))
    with pytest.raises(ValueError, match='对应关系'):
        await lifecycle.start_monitoring('owner')
    assert (await saved_policy()).devices == ['a']
    assert not (await m.settings('owner', 'b'))['enabled']


async def test_config_survives_real_hub_reinstall_and_startup(monkeypatch):
    from flocks.hub import installer
    from flocks.monitoring.models import COMPONENT_ID
    monkeypatch.setattr(lifecycle, 'discover', AsyncMock(return_value=([], None, '请选择监测设备')))
    monkeypatch.setattr('flocks.monitoring.capabilities.discover', AsyncMock(return_value=([], [])))
    catalog = [{'id': device, 'name': '机房' + device, 'available': True, 'reason': None, 'tool': 'tool-' + device} for device in ('a', 'b')]
    monkeypatch.setattr(c.adapter, 'device_catalog', AsyncMock(return_value=catalog))
    await installer.install_plugin('component', COMPONENT_ID)
    result = await c.configure('owner', request(enabled=False))
    assert len(result['targets']) == 2 and not result['running']
    original = await saved_policy()
    original_targets = await rows('SELECT * FROM monitor_device_targets')
    await installer.install_plugin('component', COMPONENT_ID)
    await lifecycle.reconcile()
    current = await saved_policy()
    assert current.devices == original.devices and current.device_tools == original.device_tools
    assert current.project == original.project
    assert await rows('SELECT * FROM monitor_device_targets') == original_targets
    assert not (await c.snapshot('owner'))['running']
    assert (await rows('SELECT ready FROM monitor_installations'))[0]['ready']


def test_reply_headers_disambiguate_shared_incident_id_but_new_mail_does_not():
    from flocks.monitoring.mail_interpreter import validate
    notices = [{'id': f'notice-{device}', 'message_id': f'<notice-{device}@example.com>',
                'event': encode({'id': 'shared-event', 'device': f'xdr-{device}', 'alertIds': []})} for device in ('a', 'b')]
    text = 'shared-event 已完成处理。'
    result = {'classification': 'feedback', 'items': [{'notice_id': 'notice-a', 'outcome': 'completed',
              'evidence': '已完成处理', 'reason': '处理完成', 'ambiguous': False}]}
    payload = {'text': text, 'reply_to_id': '<notice-a@example.com>'}
    assert len(validate(result, payload, notices)) == 1
    with pytest.raises(ValueError):
        validate(result, {'text': text}, notices)
    for extra in (' notice-b', ' xdr-b'):
        with pytest.raises(ValueError, match='冲突'):
            validate(result, {**payload, 'text': text + extra}, notices)


async def test_configuration_http_schema_and_current_user_scope(setup):
    import httpx
    from fastapi import FastAPI
    from flocks.server.auth import require_user
    from flocks.server.routes.security_monitoring import router
    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(id='owner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = '/api/monitoring/host-security-monitor/configuration'
        response = await client.put(url, json=request().model_dump())
        assert response.status_code == 200 and len(response.json()['targets']) == 2
        response = await client.get(url)
        assert response.status_code == 200 and response.json()['targets'][1]['device_name'] == '机房b'
        response = await client.put(url, json={**request().model_dump(), 'owner': 'other'})
        assert response.status_code == 422
        app.dependency_overrides[require_user] = lambda: SimpleNamespace(id='other')
        assert (await client.get(url)).status_code == 404


async def test_diagnostic_export_contains_target_counts_without_recipient_data(setup):
    await c.configure('owner', request())
    result = await m.diagnostic_state('owner')
    assert result['monitoring_devices'] == 2 and result['recipient_configured']
    assert len(result['device_targets']) == 2
    serialized = encode(result)
    assert 'a@example.com' not in serialized and 'b@example.com' not in serialized
    assert '负责人' not in serialized and '机房' not in serialized
