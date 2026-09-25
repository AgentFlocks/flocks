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
from flocks.monitoring import automatic as a, disposition as d, mailflow as m
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
                await m.authorized(policy, self.revision)
                state.raw['dealStatus'] = 30 if params['deal_status'] == 70 else params['deal_status']
                if state.lose_write: raise TimeoutError('SECRET_BODY')
                return {'code': 'Success', 'data': {}}
            if action == 'get_entities':
                return copy.deepcopy(state.responses[params['entity_type']])
            if state.leave_scope and params['white_status']:
                return {'code': 'Success', 'data': {'item': [], 'total': 0}}
            return {'code': 'Success', 'data': {'item': [copy.deepcopy(state.raw)], 'total': 1}}
    state.adapter = Fake
    await write('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?)', ('owner', policy.scope, project.id, 'person@example.com', '值班', 1, 'revision', 'mailbox', d.stamp()))
    monkeypatch.setattr(m, '_locks', {})
    monkeypatch.setattr(m, '_round_locks', {})
    state.send = AsyncMock(return_value=True)
    monkeypatch.setattr(m.transport, 'send', state.send)
    return state


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


class Recorder:
    async def call(self, name, params, operation):
        return (await operation('msg'))[0]


async def notice(auto):
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    return (await rows('SELECT * FROM monitor_mail_notices'))[0]


async def reply(auto, n, text='文件已清理，复查正常。', *, message_id='<reply@example.com>', headers=True, authenticated=True):
    from flocks.channel.base import InboundMessage
    msg = InboundMessage(channel_id='email', account_id='default', message_id=message_id,
                         sender_id='person@example.com', chat_id='person@example.com', text=text,
                         reply_to_id=n['message_id'] if headers else None,
                         raw={'mailbox_key': 'mailbox', 'authenticated_sender': authenticated, 'subject': '回复'})
    assert await m.receive(msg)
    return msg


def interpretation(n, text, outcome='completed', **kw):
    return {'classification': 'feedback', 'items': [{'notice_id': n['id'], 'outcome': outcome,
            'evidence': text, 'reason': text, 'ambiguous': False, **kw}]}


async def consume(auto, result, high=None, adapter=None):
    from flocks.monitoring.mail_interpreter import validate
    async def parse(*_): return result
    with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
        return await m.process_replies(auto.policy, auto.session.id, high if high is not None else await m.cutoff('owner', auto.policy.project),
                                       Recorder(), adapter or auto.adapter, parse)


def writes(auto):
    return [x for x in auto.calls if x['action']=='update_status']


async def test_no_reply_only_sends_once_and_never_marks(auto):
    n = await notice(auto)
    assert n['state'] == 'sent' and '事件编号：event' in n['body']
    await notice(auto)
    assert auto.send.await_count == 1 and not writes(auto)
    assert len(await rows('SELECT * FROM monitor_mail_notices')) == 1


@pytest.mark.parametrize('outcome,target', [('in_progress',10),('contained',70),('completed',40),('false_positive',60)])
async def test_feedback_next_round_marks_and_reads_back(auto, outcome, target):
    n = await notice(auto)
    msg = await reply(auto, n)
    result = await consume(auto, interpretation(n, msg.text, outcome))
    assert result['verified'] == 1
    assert len(writes(auto)) == 1 and writes(auto)[0]['deal_status'] == target
    records = await rows('SELECT * FROM monitor_dispositions')
    assert records[0]['mode'] == 'mail' and records[0]['status']=='verified'


async def test_new_message_with_identifier_is_supported(auto):
    n = await notice(auto)
    msg = await reply(auto, n, '事件 event 的文件已删除并复查正常。', headers=False)
    assert (await consume(auto, interpretation(n, msg.text)))['verified'] == 1


async def test_receipt_after_cutoff_is_deferred(auto):
    n = await notice(auto)
    high = await m.cutoff('owner', auto.policy.project)
    msg = await reply(auto, n)
    assert (await consume(auto, interpretation(n, msg.text), high))['processed'] == 0
    assert not writes(auto)
    assert (await consume(auto, interpretation(n, msg.text)))['verified'] == 1


async def test_duplicate_delivery_and_round_do_not_repeat_write(auto):
    n = await notice(auto); msg = await reply(auto, n)
    await m.receive(msg)
    parsed = interpretation(n, msg.text)
    await consume(auto, parsed); await consume(auto, parsed)
    assert len(await rows('SELECT * FROM monitor_mail_replies')) == 1
    assert len(writes(auto)) == 1


async def test_unknown_smtp_result_not_resent_after_restart(auto):
    auto.send.side_effect = TimeoutError('private smtp error')
    n = await notice(auto)
    assert n['state'] == 'send_unknown'
    await m.recover(); await notice(auto)
    assert auto.send.await_count == 1


async def test_uncertain_write_only_readback_next_round(auto):
    n = await notice(auto); msg = await reply(auto, n)
    auto.lose_write = True
    assert (await consume(auto, interpretation(n, msg.text)))['pending'] == 1
    await m.recover()
    assert (await consume(auto, interpretation(n, msg.text)))['verified'] == 1
    assert len(writes(auto)) == 1


async def test_progress_can_be_followed_by_completion(auto):
    n = await notice(auto); first = await reply(auto,n,'正在排查。')
    await consume(auto, interpretation(n,first.text,'in_progress'))
    second = await reply(auto,n,'已处理完并复查。', message_id='<reply2@example.com>')
    await consume(auto, interpretation(n,second.text))
    assert [x['deal_status'] for x in writes(auto)] == [10,40]


async def test_completed_not_downgraded_by_late_progress(auto):
    n = await notice(auto); first = await reply(auto,n)
    await consume(auto, interpretation(n,first.text))
    second = await reply(auto,n,'还在处理中。', message_id='<old@example.com>')
    await consume(auto, interpretation(n,second.text,'in_progress'))
    assert len(writes(auto))==1
    assert (await rows("SELECT state FROM monitor_mail_replies WHERE message_id='<old@example.com>'"))[0]['state']=='needs_review'


@pytest.mark.parametrize('change', ['unauthenticated','ambiguous','unknown','quoted','wrong_id','new_activity','new_config','paused'])
async def test_uncertain_or_revoked_feedback_never_writes(auto, change, monkeypatch):
    if change == 'unauthenticated':
        monkeypatch.setattr(m.transport, 'REQUIRE_AUTHENTICATED_FEEDBACK', True)
    n = await notice(auto)
    text = '文件已清理，复查正常。'
    msg = await reply(auto,n, '还未完成。\n> '+text if change=='quoted' else text, authenticated=change!='unauthenticated')
    parsed = interpretation(n,text)
    if change=='ambiguous': parsed['items'][0]['ambiguous']=True
    if change=='unknown': parsed['items'][0]['outcome']='unknown'
    if change=='wrong_id': parsed['items'][0]['notice_id']='invented'
    if change=='new_activity': auto.raw['endTime']=200
    if change=='new_config': await write("UPDATE monitor_mail_settings SET revision='changed'")
    if change=='paused': await TaskManager.disable_scheduler(auto.scheduler.id)
    await consume(auto,parsed)
    assert not writes(auto)


async def test_legacy_opt_in_does_not_authorize_mail_or_direct_marking(auto):
    await write('DELETE FROM monitor_mail_settings')
    with pytest.raises(ContractError, match='旧版'): await a.process_event(auto.policy,auto.event['key'],auto.session.id,'revision')
    with pytest.raises(ValueError, match='邮件'): await a.configure('owner',True)
    await m.notify_batch(auto.policy,auto.session.id,[auto.event],Recorder(),auto.adapter)
    assert auto.send.await_count==0 and not writes(auto)
    assert not (await a.settings('owner'))['enabled']


async def test_mail_history_is_owner_scoped(auto):
    n=await notice(auto); await reply(auto,n)
    other=await m.history('other')
    assert other['notices']==[] and other['replies']==[]
    assert len((await m.history('owner'))['notices'])==1


async def test_native_round_reports_timeline_and_daily_summary_without_direct_write(auto,actual_tool):
    from flocks.monitoring.runtime import run
    from flocks.task.models import ExecutionTriggerType
    await write('DELETE FROM monitor_mail_settings')
    execution=await TaskManager.create_execution_from_scheduler(auto.scheduler,trigger_type=ExecutionTriggerType.RUN_ONCE,enqueue=False)
    with unattended_scope(),monitoring_read_scope(auto.policy.tool,auto.policy.devices):
        await run(execution,auto.policy)
    report=(await rows('SELECT * FROM monitor_reports'))[0]
    assert '执行时间线' in report['content'] and '当日告警总结' in report['summary_content']
    assert not any(path.endswith('/dealstatus') for path,_ in actual_tool)


@pytest.mark.parametrize('patch', [{'uuids':['victim']},{'deal_status':60},{'api_params':{'dealStatus':40}}])
async def test_mail_write_hooks_cannot_change_target(auto,actual_tool,monkeypatch,patch):
    n=await notice(auto); msg=await reply(auto,n)
    from flocks.hooks.pipeline import HookPipeline
    async def hook(payload):
        changing=payload['tool_execution']['tool']['validated_input'].get('action')=='update_status'
        return SimpleNamespace(output={'decision':{'validated_input_patch':patch}} if changing else {},execution_stop_requested=False)
    monkeypatch.setattr(HookPipeline,'run_tool_before',hook)
    await consume(auto,interpretation(n,msg.text),adapter=m.MailAdapter)
    assert not any(path.endswith('/dealstatus') for path,_ in actual_tool)


async def test_full_device_tool_contract_writes_single_incident(auto,actual_tool):
    n=await notice(auto); msg=await reply(auto,n)
    assert (await consume(auto,interpretation(n,msg.text),adapter=m.MailAdapter))['verified']==1
    updates=[data for path,data in actual_tool if path.endswith('/dealstatus')]
    assert len(updates)==1 and updates[0]['uuIds']==['event'] and updates[0]['dealStatus']==40


@pytest.mark.parametrize('authenticated', [True, False])
async def test_diagnostic_export_has_mail_stages_not_mail_text(auto, authenticated):
    from flocks.monitoring import diagnostics as diag
    captured=[]
    original=diag._sink.submit
    diag._sink.submit=lambda fields: captured.append(diag.safe_record(fields))
    try:
        async with diag.trace_scope('owner',auto.policy.scope,'test'):
            n=await notice(auto); msg=await reply(auto,n,authenticated=authenticated)
            await consume(auto,interpretation(n,msg.text))
    finally:
        diag._sink.submit=original
    raw=json.dumps(captured,ensure_ascii=False)
    assert 'mail.received' in raw and 'mail.interpret' in raw and 'mail.mark' in raw
    assert 'person@example.com' not in raw and msg.text not in raw and n['id'] not in raw
    received = next(r for r in captured if r['event'] == 'mail.received')
    assert received['authenticated_sender'] is authenticated
    assert received['sender_verification_bypassed'] is (not authenticated)


async def test_ingress_requires_gateway_capability_before_receipt(auto,monkeypatch):
    from flocks.channel.base import InboundMessage
    from flocks.channel.inbound.dispatcher import InboundDispatcher
    from flocks.identity.entry import mint_channel_ingress_provenance
    n=await notice(auto)
    msg=InboundMessage(channel_id='email',account_id='default',message_id='<ingress@example.com>',sender_id='person@example.com',text='已处理',
                       raw={'mailbox_key':'mailbox','authenticated_sender':True})
    dispatcher=InboundDispatcher()
    monkeypatch.setattr(dispatcher,'_dispatch',AsyncMock())
    monkeypatch.setattr('flocks.channel.inbound.dispatcher._check_allowlist',lambda *_:True)
    await dispatcher.dispatch(msg)
    assert not await rows('SELECT * FROM monitor_mail_replies')
    capability=mint_channel_ingress_provenance(channel_id='email',account_id='default',message_id=msg.message_id,sender_id=msg.sender_id,chat_type=msg.chat_type.value,message=msg,evidence=msg.raw)
    await dispatcher.dispatch(msg,provenance=capability)
    assert len(await rows('SELECT * FROM monitor_mail_replies'))==1


async def test_settings_validation_and_ownership(auto,actual_tool,monkeypatch):
    from flocks.config.config import Config
    from fastapi import FastAPI
    from flocks.server.routes import security_monitoring as api
    import httpx
    monkeypatch.setattr(Config,'resolve_default_llm',AsyncMock(return_value={'provider_id':'fixture','model_id':'fixture'}))
    monkeypatch.setattr(m.transport,'transport',lambda *_:(None,{}))
    monkeypatch.setattr(m.transport,'mailbox_key',lambda *_:'mailbox')
    app=FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.require_user]=lambda:SimpleNamespace(id='owner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        path='/monitoring/host-security-monitor/mail/settings'
        body={'enabled':True,'recipient_email':'person@example.com','responsible_name':'值班'}
        assert (await client.put(path,json=body)).status_code==200
        for invalid in ({**body,'owner':'other'},{**body,'enabled':'true'},{**body,'recipient_email':'a@b.com\nbcc:c@d.com'}):
            assert (await client.put(path,json=invalid)).status_code==422
        app.dependency_overrides[api.require_user]=lambda:SimpleNamespace(id='other')
        assert (await client.put(path,json=body)).status_code==404
        assert (await client.get('/monitoring/host-security-monitor/mail')).json()['notices']==[]
    assert actual_tool==[]


async def test_llm_uses_configured_provider_without_tools(auto,monkeypatch):
    from flocks.monitoring import mail_interpreter as interpreter
    from flocks.config.config import Config
    from flocks.provider.provider import Provider
    n=await notice(auto)
    text='编号 event 已处理，复查正常'
    response=SimpleNamespace(content=json.dumps(interpretation(n,text)),finish_reason='stop',tool_calls=[])
    chat=AsyncMock(return_value=response)
    monkeypatch.setattr(Config,'resolve_default_llm',AsyncMock(return_value={'provider_id':'fixture','model_id':'fixture'}))
    monkeypatch.setattr(Provider,'apply_config',AsyncMock())
    monkeypatch.setattr(Provider,'get',lambda *_:SimpleNamespace(chat=chat))
    parsed=await interpreter.interpret({'text':text},[{'notice_id':n['id']}])
    assert parsed['version']==interpreter.VERSION
    assert 'tools' not in chat.call_args.kwargs and len(chat.call_args.args[1])==2
    assert interpreter.validate(parsed,{'text':text},[n])[0][0]['id']==n['id']


async def test_durable_imap_cursor_includes_read_mail_and_resets_validity(auto):
    from flocks.monitoring import mail_transport
    cfg={'address':'agent@example.com','imapHost':'mail.example.com','imapPort':993}
    key=mail_transport.mailbox_key(cfg)
    await write('UPDATE monitor_mail_settings SET mailbox=?',(key,))
    imap=SimpleNamespace(response=lambda *_:('UIDVALIDITY',[b'99']))
    first=mail_transport.poll_range(cfg,imap)
    assert first[2][0]=='SINCE'
    mail_transport.checkpoint(first,123)
    assert mail_transport.poll_range(cfg,imap)[2]==['UID','124:*']
    imap.response=lambda *_:('UIDVALIDITY',[b'100'])
    assert mail_transport.poll_range(cfg,imap)[2][0]=='SINCE'


@pytest.mark.parametrize('style', ['quoted', 'subject'])
async def test_reply_reference_can_be_quoted_or_in_subject(auto, style):
    n = await notice(auto)
    text = '文件已清理，复查正常。'
    msg = await reply(auto, n, text=text + ('\n> 事件编号：event\n> 请处理' if style == 'quoted' else ''), headers=False)
    if style == 'subject':
        saved = (await rows('SELECT * FROM monitor_mail_replies'))[0]
        payload = json.loads(saved['payload']); payload['subject'] = '事件 event 的处理反馈'
        await write('UPDATE monitor_mail_replies SET payload=? WHERE id=?', (encode(payload), saved['id']))
    assert (await consume(auto, interpretation(n, text)))['verified'] == 1


async def test_quote_cannot_supply_completed_evidence(auto):
    n = await notice(auto)
    await reply(auto, n, text='尚未完成。\n> 文件已清理，复查正常。')
    await consume(auto, interpretation(n, '文件已清理，复查正常。'))
    assert not writes(auto)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


async def test_host_changed_after_notification_never_marks(auto):
    n = await notice(auto); msg = await reply(auto, n)
    auto.raw['hostIp'] = '192.0.2.10'
    await consume(auto, interpretation(n, msg.text))
    assert not writes(auto)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


async def test_retired_queued_configuration_is_quarantined(auto):
    await m.queue_notices(auto.policy, [auto.event], auto.session.id)
    await write("UPDATE monitor_mail_settings SET revision='new'")
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    assert not auto.send.called
    assert (await rows('SELECT state FROM monitor_mail_notices'))[0]['state'] == 'needs_review'


async def test_inbound_plugin_can_block_monitor_feedback(auto, monkeypatch):
    from flocks.hooks.pipeline import HookPipeline
    n = await notice(auto)
    monkeypatch.setattr(HookPipeline, 'run_channel_inbound_before', AsyncMock(return_value=SimpleNamespace(output={'blocked': True})))
    await reply(auto, n)
    assert not await rows('SELECT * FROM monitor_mail_replies')


async def test_reply_with_partial_review_keeps_other_item_readback_alive(auto, monkeypatch):
    n = await notice(auto); msg = await reply(auto, n)
    record = (await rows('SELECT * FROM monitor_mail_replies'))[0]
    await write("UPDATE monitor_mail_replies SET state='interpreted' WHERE id=?", (record['id'],))
    for identity, notice_id, state in [('review-item', 'old-notice', 'needs_review'), ('pending-item', n['id'], 'pending')]:
        await write('INSERT INTO monitor_mail_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (identity, record['id'], notice_id, 'owner', auto.policy.project, 40, 'fixture', state, None, d.stamp(), d.stamp()))
    monkeypatch.setattr(m, 'mark_item', AsyncMock(side_effect=['mismatch', 'verified']))
    await consume(auto, {})
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'interpreted'
    await consume(auto, {})
    assert m.mark_item.await_count == 2
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


@pytest.mark.parametrize('authenticated', [True, False])
async def test_native_two_round_mail_feedback_flow(auto, actual_tool, connected_email, monkeypatch, authenticated):
    from email.mime.text import MIMEText
    from flocks.channel.inbound.dispatcher import InboundDispatcher
    from flocks.config.config import ChannelConfig
    from flocks.identity.entry import mint_channel_ingress_provenance
    from flocks.monitoring.runtime import run
    from flocks.monitoring import mail_interpreter
    from flocks.task.models import ExecutionTriggerType
    if not authenticated:
        connected_email._resolved.update(authservId='', requireAuthenticatedSender=False)
    await m.configure('owner', m.MailSettingsRequest(enabled=True, recipient_email='person@example.com'))
    async def tick():
        execution = await TaskManager.create_execution_from_scheduler(auto.scheduler, trigger_type=ExecutionTriggerType.RUN_ONCE, enqueue=False)
        with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
            await run(execution, auto.policy)
    await tick()
    notices = await rows('SELECT * FROM monitor_mail_notices')
    assert len(notices) == 1 and notices[0]['state'] == 'sent'
    assert not any(path.endswith('/dealstatus') for path, _ in actual_tool)
    original = MIMEText('文件已清理，复查正常。', 'plain', 'utf-8')
    original['From'] = 'person@example.com'
    original['Subject'] = 'Re: 处置反馈'
    original['Message-ID'] = '<native-feedback@example.com>'
    original['In-Reply-To'] = notices[0]['message_id']
    if authenticated:
        original['Authentication-Results'] = 'mx.example.com; dmarc=pass header.from=example.com'
    msg = connected_email._parse_and_authorize(original, '1')
    assert msg is not None and msg.raw['authenticated_sender'] is authenticated
    dispatcher = InboundDispatcher()
    monkeypatch.setattr(dispatcher, '_get_channel_config', AsyncMock(return_value=ChannelConfig(enabled=True, allowFrom=['person@example.com'])))
    monkeypatch.setattr(dispatcher, '_dispatch', AsyncMock())
    provenance = mint_channel_ingress_provenance(channel_id='email', account_id=msg.account_id, message_id=msg.message_id,
        sender_id=msg.sender_id, chat_type=msg.chat_type.value, message=msg, evidence=msg.raw)
    await dispatcher.dispatch(msg, provenance=provenance)
    received = (await rows('SELECT * FROM monitor_mail_replies'))[0]
    assert received['state'] == 'pending'
    payload = json.loads(received['payload'])
    assert payload['authenticated_sender'] is authenticated
    assert payload['sender_verification_bypassed'] is (not authenticated)
    assert (await m.history('owner'))['sender_verification_required'] is False
    assert (await m.diagnostic_state('owner'))['sender_verification_required'] is False
    dispatcher._dispatch.assert_not_called()
    assert not any(path.endswith('/dealstatus') for path, _ in actual_tool)
    monkeypatch.setattr(mail_interpreter, 'interpret', AsyncMock(return_value=interpretation(notices[0], msg.text)))
    await tick()
    assert auto.send.await_count == 1
    updates = [data for path, data in actual_tool if path.endswith('/dealstatus')]
    assert len(updates) == 1 and updates[0]['uuIds'] == ['event']
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'verified'
    report = (await rows('SELECT * FROM monitor_reports'))[0]
    assert '当日告警总结' in report['summary_content'] and '执行时间线' in report['content']


async def test_development_feedback_cannot_escape_to_generic_agent(auto, monkeypatch):
    from flocks.channel.inbound.dispatcher import InboundDispatcher
    dispatch = AsyncMock()
    monkeypatch.setattr(InboundDispatcher, '_dispatch', dispatch)
    n = await notice(auto)
    await reply(auto, n, authenticated=False)
    await consume(auto, {'classification': 'unrelated', 'items': []})
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'
    dispatch.assert_not_called()
    assert not writes(auto)


async def test_restoring_authentication_blocks_pending_development_feedback(auto, monkeypatch):
    n = await notice(auto)
    msg = await reply(auto, n, authenticated=False)
    monkeypatch.setattr(m.transport, 'REQUIRE_AUTHENTICATED_FEEDBACK', True)
    await consume(auto, interpretation(n, msg.text))
    assert not writes(auto)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'


async def test_diagnostic_state_contains_only_flags_and_counts(auto, monkeypatch):
    from flocks.config.config import Config
    monkeypatch.setattr(Config, 'resolve_default_llm', AsyncMock(return_value={'provider_id': 'private', 'model_id': 'private'}))
    await notice(auto)
    data = await m.diagnostic_state('owner')
    assert data['enabled'] and data['model_configured'] and data['notices']['sent'] == 1
    serialized = json.dumps(data)
    assert 'person@example.com' not in serialized and auto.policy.project not in serialized and 'private' not in serialized
    assert (await m.diagnostic_state('other'))['notices'] == {}


@pytest.fixture
def connected_email(monkeypatch):
    from flocks.channel.builtin.email.channel import EmailChannel
    from flocks.channel.builtin.email.config import resolved_config
    from flocks.channel.registry import default_registry
    from flocks.config.config import Config
    plugin = EmailChannel()
    plugin._resolved = resolved_config({'address': 'agent@example.com', 'imapHost': 'imap.example.com',
        'authservId': 'mx.example.com', 'allowFrom': ['person@example.com']})
    plugin.mark_connected()
    monkeypatch.setattr(default_registry, '_channels', {'email': plugin})
    monkeypatch.setattr(Config, 'resolve_default_llm', AsyncMock(return_value={'model_id': 'fixture'}))
    return plugin


async def test_save_with_real_email_channel_and_export_snapshot(auto, actual_tool, connected_email):
    from flocks.server.routes import security_monitoring as api
    await api.mail_settings(m.MailSettingsRequest(enabled=True, recipient_email='person@example.com'),
                            user=SimpleNamespace(id='owner'))
    saved = await m.settings('owner')
    assert saved['enabled'] and saved['recipient'] == 'person@example.com'
    assert saved['mailbox'] == m.transport.mailbox_key(connected_email._resolved)
    response = await api.diagnostics(user=SimpleNamespace(id='owner'))
    data = json.loads(response.body)['mail']
    assert data['channel_connected'] and data['mailbox_matches'] and data['sender_verification_configured']
    assert 'example.com' not in json.dumps(data)
    assert not auto.send.called and not actual_tool


@pytest.mark.parametrize('condition,message', [
    ('disconnected', '请先连接 Flocks 邮件通道'),
    ('no_authserv', '可信 authservId'),
    ('not_allowed', '允许收件人列表'),
])
async def test_real_email_channel_validation_preserves_saved_settings(auto, actual_tool, connected_email, condition, message, monkeypatch):
    if condition == 'disconnected':
        connected_email.mark_disconnected()
    elif condition == 'no_authserv':
        monkeypatch.setattr(m.transport, 'REQUIRE_AUTHENTICATED_FEEDBACK', True)
        connected_email._resolved['authservId'] = ''
    else:
        connected_email._resolved['allowFrom'] = ['other@example.com']
    before = await m.settings('owner')
    with pytest.raises(ValueError, match=message):
        await m.configure('owner', m.MailSettingsRequest(enabled=True, recipient_email='person@example.com'))
    assert await m.settings('owner') == before
    assert not auto.send.called and not actual_tool


async def test_manual_readback_cannot_rebind_old_mail_write_to_new_project(auto):
    n = await notice(auto); msg = await reply(auto, n)
    await consume(auto, interpretation(n, msg.text))
    record = (await rows('SELECT * FROM monitor_dispositions'))[0]
    await write("UPDATE monitor_dispositions SET project='retired-project' WHERE id=?", (record['id'],))
    calls = len(auto.calls)
    with pytest.raises(ValueError, match='不属于当前监测项目'):
        await d.recheck('owner', record['id'], auto.adapter)
    assert len(auto.calls) == calls


async def test_unparsed_message_is_recorded_without_blocking_other_mail(auto, monkeypatch):
    from flocks.channel.builtin.email.channel import EmailChannel
    from flocks.channel.builtin.email.config import resolved_config
    plugin = EmailChannel(); plugin._resolved = resolved_config({'address': 'agent@example.com'})
    imap = SimpleNamespace(uid=lambda action,*_: ('OK', [b'1 2']) if action=='search' else ('OK', [(b'meta', b'From: x@example.com\n\nUnsupported')]), logout=lambda:None)
    for method in ('_authenticate_imap', '_identify_imap_client', '_select_inbox'):
        monkeypatch.setattr(plugin, method, lambda *_:None)
    monkeypatch.setattr(plugin, '_connect_imap', lambda:imap)
    monkeypatch.setattr(m.transport, 'poll_range', lambda *_:('mailbox','123',['UID','1:*'],0))
    monkeypatch.setattr(plugin, '_parse_and_authorize_with_tracking', lambda *_:(None,False))
    assert plugin._fetch_new_messages() == []
    assert plugin._monitor_high == 2 and plugin._monitor_unparsed == 2
    assert len(await rows('SELECT * FROM monitor_mail_unparsed')) == 2
    assert (await m.history('owner'))['unparsed_count'] == 2
    assert (await m.history('other'))['unparsed_count'] == 0
    assert (await m.diagnostic_state('owner'))['unparsed_messages'] == 2
    assert plugin._fetch_new_messages() == []
    assert len(await rows('SELECT * FROM monitor_mail_unparsed')) == 2
