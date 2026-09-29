"""Human fallback is notice-scoped, durable, and never repeats an XDR write."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from flocks.monitoring import mail_manual as manual, mailflow, disposition as d
from flocks.monitoring.adapter import normalize
from flocks.monitoring.models import MonitoringPolicy
from flocks.monitoring.store import rows, write, encode
from flocks.project.project import Project
from flocks.session.interaction_policy import unattended_scope


@pytest.fixture
async def case(tmp_path, monkeypatch):
    directory = tmp_path / 'monitor'
    directory.mkdir()
    project = await Project.create(owner_id='owner', name='monitor', worktree=str(directory))
    policy = MonitoringPolicy(owner='owner', project=project.id, directory=str(directory), devices=['device'])
    event = normalize('device', {'uuId': 'event', 'name': 'fixture', 'incidentSeverity': 4, 'dealStatus': 0, 'endTime': 100})
    await write('INSERT INTO monitor_installations(owner,scope,project,policy,ready) VALUES(?,?,?,?,1)',
                ('owner', policy.scope, project.id, encode(policy.model_dump())))
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('attempt', 'execution', 'owner', project.id, policy.scope, datetime.now(timezone.utc).date().isoformat(), 'daily', 'message', d.stamp(), 'completed'))
    await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('attempt', event['key'], encode(event)))
    await write('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?)',
                ('owner', policy.scope, project.id, 'person@example.com', 'person', 1, 'rev', 'mailbox', d.stamp()))
    nid = str(uuid4())
    await write('INSERT INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (nid, 'owner', policy.scope, project.id, event['key'], 'person@example.com', 'rev', 'mailbox', '<notice@example.com>', 'notice', 'body', encode(event), 'sent', d.stamp(), d.stamp()))
    monkeypatch.setattr('flocks.hub.local.get_record', lambda *_: SimpleNamespace(enabled=True))
    monkeypatch.setattr(mailflow, '_locks', {})
    state = SimpleNamespace(policy=policy, event=event, notice=nid, calls=[], statuses=[0, 40], fail=False)
    class Adapter:
        def __init__(self, policy, session_id): self.policy, self.session_id = policy, session_id
        async def call(self, device, params, message):
            state.calls.append((device, params))
            if params['action'] == 'update_status':
                if state.fail: raise TimeoutError('PRIVATE')
                return {'code': 'Success', 'data': {}}
            current = state.statuses.pop(0)
            if isinstance(current, Exception): raise current
            raw = current if isinstance(current, dict) else {'uuId': 'event', 'dealStatus': current, 'endTime': 100}
            return {'code': 'Success', 'data': {'item': [raw], 'total': 1}}
    state.adapter = Adapter
    return state


def request(state='handled', **changes):
    return manual.ManualMailRequest(**{'request_id': uuid4(), 'state': state, **changes})


async def notice(case):
    return (await rows('SELECT * FROM monitor_mail_notices WHERE id=?', (case.notice,)))[0]


async def change(case, req=None):
    return await manual.change('owner', case.notice, req or request(), case.adapter)


async def received(case, payload=None):
    rid = str(uuid4())
    await write('INSERT INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,received_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
        (rid, 'owner', case.policy.project, 'mailbox', '<reply@example.com>', 'person@example.com', encode(payload or {'text': '处理好了', 'notice_ids': [case.notice]}), 'pending', d.stamp(), d.stamp()))
    return rid


async def test_unhandled_is_local_then_handled_writes_and_readbacks_once(case):
    local = await change(case, request('unhandled'))
    assert local['state'] == 'unhandled' and local['available'] and not case.calls
    req = request()
    a, b = await asyncio.gather(change(case, req), change(case, req))
    assert a == b and a['state'] == 'handled' and not a['available']
    assert [p['action'] for _, p in case.calls] == ['list', 'update_status', 'list']
    mutation = case.calls[1][1]
    assert mutation['uuids'] == ['event'] and mutation['deal_status'] == 40
    assert (await notice(case))['state'] == 'sent'
    history = await mailflow.history('owner')
    n = history['notices'][0]
    assert n['disposition_state'] == 'handled' and n['disposition_source'] == 'manual'
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 1


@pytest.mark.parametrize('state,error,allowed', [('sent',None,True), ('send_unknown','unknown',True), ('queued','failed',True),
    ('queued',None,False), ('sending',None,False), ('skipped',None,False)])
async def test_manual_button_only_for_delivery_failure_or_no_reply(case, state, error, allowed):
    await write('UPDATE monitor_mail_notices SET state=?,error=? WHERE id=?', (state,error,case.notice))
    assert (await manual.snapshot(await notice(case)))['available'] is allowed
    if not allowed:
        with pytest.raises(ValueError): await change(case)
        assert not case.calls


@pytest.mark.parametrize('state', ['sent','send_unknown','queued'])
async def test_reply_saved_before_interpretation_removes_manual_entry(case, state):
    await write('UPDATE monitor_mail_notices SET state=?,error=?', (state,'delivery issue'))
    await received(case)
    with pytest.raises(ValueError, match='已收到回信'): await change(case)
    assert not case.calls
    history = await mailflow.history('owner')
    n = history['notices'][0]
    assert n['reply_received'] and len(n['replies']) == 1
    assert n['replies'][0]['payload']['text'] == '处理好了'
    assert not n['manual']['available'] and n['disposition_state'] == 'unhandled'
    assert not history['replies']


async def test_unknown_write_only_rechecks_even_after_reply_arrives(case):
    case.fail = True
    req = request()
    result = await change(case, req)
    assert result['state'] == 'pending' and not result['available'] and 'PRIVATE' not in str(result)
    assert (await change(case, req))['state'] == 'pending'
    with pytest.raises(ValueError): await change(case, request('unhandled'))
    await received(case)
    result = await manual.recheck('owner',case.notice,case.adapter)
    assert result['state'] == 'handled'
    assert len([p for _,p in case.calls if p['action']=='update_status']) == 1


async def test_late_reply_and_send_retry_cannot_override_manual_completion(case):
    await change(case)
    before = list(case.calls)
    with pytest.raises(ValueError, match='人工处置'):
        await mailflow.mark_item(case.policy, {'notice_id':case.notice}, 'session')
    assert await mailflow.notify_one(case.policy,await notice(case),'session') == 'already_attempted'
    assert case.calls == before


async def test_wrong_owner_removed_device_and_unattended_cannot_mark(case):
    with pytest.raises(FileNotFoundError): await manual.change('other',case.notice,request(),case.adapter)
    with unattended_scope():
        with pytest.raises(PermissionError): await change(case)
    await write('UPDATE monitor_installations SET policy=?', (encode(case.policy.model_copy(update={'devices':['other']}).model_dump()),))
    with pytest.raises(ValueError, match='监测范围'): await change(case)
    assert not case.calls


async def test_notice_from_other_project_cannot_be_changed(case):
    await write('UPDATE monitor_mail_notices SET project=?', ('other-project',))
    with pytest.raises(FileNotFoundError): await change(case)
    assert not case.calls


async def test_changed_event_activity_does_not_close_new_alarm(case):
    event = {**case.event, 'notified_end_time': 100}
    await write('UPDATE monitor_mail_notices SET event=?', (encode(event),))
    case.statuses = [{'uuId':'event','dealStatus':0,'endTime':200}]
    result = await change(case)
    assert result['state'] == 'failed' and '新活动' in result['error']
    assert all(p['action']!='update_status' for _,p in case.calls)


async def test_request_id_cannot_be_reused_for_other_decision(case):
    req = request('unhandled')
    await change(case,req)
    with pytest.raises(ValueError, match='其他操作'):
        await change(case,request(request_id=req.request_id))


async def test_automatic_reply_status_is_merged_into_notice(case):
    rid = await received(case)
    iid = str(uuid4())
    await write('INSERT INTO monitor_mail_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (iid,rid,case.notice,'owner',case.policy.project,40,'已修复','verified',None,d.stamp(),d.stamp()))
    await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at,mode,target_status,project) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (iid,'owner',case.policy.scope,case.event['key'],'已修复','verified',d.stamp(),d.stamp(),'mail',40,case.policy.project))
    n = (await mailflow.history('owner'))['notices'][0]
    assert n['disposition_state']=='handled' and n['disposition_source']=='reply'
    assert n['replies'][0]['targets'][0]['event_id']=='event'
    assert not n['manual']['available']


async def test_route_uses_authenticated_owner_and_notice_id(monkeypatch):
    from flocks.server.routes import security_monitoring as api
    notice_id = uuid4(); req=request('unhandled')
    action=AsyncMock(return_value={'state':'unhandled'})
    monkeypatch.setattr(manual,'change',action)
    monkeypatch.setattr('flocks.server.routes.event.publish_event',AsyncMock())
    result=await api.manual_mail_status(notice_id,req,user=SimpleNamespace(id='owner'))
    assert result['state']=='unhandled'
    action.assert_awaited_once_with('owner',str(notice_id),req)


@pytest.mark.parametrize('fields',[{'state':'ignored'},{'state':'handled','device':'other'},{'state':'handled','target':60}])
def test_manual_payload_does_not_accept_target_override(fields):
    with pytest.raises(ValidationError): request(**fields)


async def test_legacy_free_text_reply_never_exposes_manual_button(case):
    await received(case, {'text':'已处理'})
    row = (await mailflow.history('owner'))['notices'][0]
    assert not row['manual']['available']
    assert (await manual.snapshot(await notice(case)))['available'] is False
    assert not row['reply_received']  # Saved but not yet uniquely associated.


async def test_failed_notice_uses_observation_snapshot_before_completion(case):
    await write("UPDATE monitor_mail_notices SET state='queued',error='发送失败'")
    case.statuses = [{'uuId':'event','dealStatus':0,'endTime':200}]
    result = await change(case)
    assert result['state']=='failed' and '新活动' in result['error']
    assert all(p['action']!='update_status' for _,p in case.calls)


async def test_completion_is_not_downgraded_by_report_export_failure(case, monkeypatch):
    export = AsyncMock(side_effect=OSError('report unavailable'))
    monkeypatch.setattr('flocks.monitoring.reports.export_report',export)
    assert (await change(case))['state']=='handled'
    assert not export.called
    assert (await rows('SELECT status FROM monitor_reports'))[0]['status']=='pending'


async def test_concurrent_send_skip_keeps_human_notice_visible(case, monkeypatch):
    await write("UPDATE monitor_mail_notices SET state='queued',error='发送失败'")
    monkeypatch.setattr(mailflow,'authorized',AsyncMock(return_value={'enabled':1}))
    async def closed_by_human(*args, **kwargs):
        assert (await change(case))['state']=='handled'
        return None
    monkeypatch.setattr(mailflow,'read_event',closed_by_human)
    result=await mailflow.notify_one(case.policy,await notice(case),'session',lambda *a:None)
    assert result=='already_attempted'
    row=(await mailflow.history('owner'))['notices'][0]
    assert row['manual']['state']=='handled' and row['state']=='queued'


async def test_new_manual_endpoint_validates_identity_and_payload(case, monkeypatch):
    from fastapi import FastAPI
    from flocks.server.routes import security_monitoring as api
    import httpx
    app=FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.require_user]=lambda:SimpleNamespace(id='owner')
    path=f'/monitoring/host-security-monitor/mail/notices/{case.notice}/manual-status'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
        body=request('unhandled').model_dump(mode='json')
        assert (await client.post(path,json={**body,'owner':'other'})).status_code==422
        assert (await client.post(path,json={**body,'state':'ignored'})).status_code==422
        response=await client.post(path,json=body)
        assert response.status_code==200 and response.json()['state']=='unhandled'
        assert (await client.post(path.replace(case.notice,str(uuid4())),json=body)).status_code==404


async def test_two_devices_with_same_event_id_write_only_notice_device(case):
    case.policy = case.policy.model_copy(update={'devices':['device','second'],
        'device_tools':{'device':'tool_a','second':'tool_b'}})
    await write('UPDATE monitor_installations SET policy=?', (encode(case.policy.model_dump()),))
    second = normalize('second', {'uuId':'event','endTime':100,'dealStatus':0})
    await write('INSERT INTO monitor_observations VALUES(?,?,?)', ('attempt',second['key'],encode(second)))
    original=await notice(case); case.notice=str(uuid4())
    await write('INSERT INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (case.notice,'owner',case.policy.scope,case.policy.project,second['key'],'second@example.com','rev','mailbox','<second@example.com>','second','body',encode(second),'sent',d.stamp(),d.stamp()))
    assert (await change(case))['state']=='handled'
    assert {device for device,_ in case.calls}=={'second'}
    records=await rows('SELECT * FROM monitor_dispositions')
    assert len(records)==1 and records[0]['event_key']==second['key']
    assert not await manual.completion_claimed('owner',original['id'])


async def test_rebound_project_does_not_inherit_manual_closure(case):
    from flocks.monitoring.reports import snapshot
    await change(case)
    await write('UPDATE monitor_attempts SET project=?', ('new-project',))
    day=(await rows('SELECT business_date FROM monitor_attempts'))[0]['business_date']
    result=await snapshot('owner',case.policy.scope,day)
    assert result['events'][0]['closure']=='open'
    assert not result['events'][0].get('dispositionRecord')
