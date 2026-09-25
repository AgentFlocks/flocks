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
from flocks.monitoring.mail_transport import send as send_through_email_channel
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
    policy = MonitoringPolicy(development_sample=False, owner='owner', project=project.id, directory=str(directory), devices=['device'])
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
    monkeypatch.setattr('flocks.hub.local.get_record', lambda kind, _id: SimpleNamespace(enabled=True) if kind == 'component' else None)
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
    failed = await consume(auto, interpretation(n, msg.text))
    assert failed['pending'] == 1 and failed['errors']
    assert (await rows('SELECT status FROM monitor_dispositions'))[0]['status'] == 'pending'
    assert (await rows('SELECT state FROM monitor_mail_items'))[0]['state'] == 'pending'
    await m.recover()
    recovered = await consume(auto, interpretation(n, msg.text))
    assert recovered['verified'] == 1 and not recovered['errors']
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
@pytest.mark.parametrize('outcome,target', [('completed', 40), ('false_positive', 60)])
async def test_native_two_round_mail_feedback_flow(auto, actual_tool, connected_email, monkeypatch, authenticated, outcome, target):
    from email.mime.text import MIMEText
    from flocks.channel.inbound.dispatcher import InboundDispatcher
    from flocks.config.config import ChannelConfig
    from flocks.identity.entry import mint_channel_ingress_provenance
    from flocks.monitoring.runtime import run
    from flocks.monitoring import mail_interpreter
    from flocks.task.models import ExecutionTriggerType
    outbound = []
    smtp = SimpleNamespace(send_message=outbound.append, quit=lambda: None, close=lambda: None)
    monkeypatch.setattr(connected_email, '_connect_smtp', lambda: smtp)
    monkeypatch.setattr(connected_email, '_authenticate_smtp', lambda _: None)
    auto.send = AsyncMock(wraps=send_through_email_channel)
    monkeypatch.setattr(m.transport, 'send', auto.send)
    from flocks.monitoring import investigation, capabilities
    from flocks.task.store import TaskStore
    auto.policy.investigation_engine = 'agent-v1'
    auto.policy.timeout_seconds = 1200
    auto.scheduler.context['monitoring'] = auto.policy.model_dump()
    await TaskStore.update_scheduler(auto.scheduler)
    await write('UPDATE monitor_installations SET policy=? WHERE owner=?', (encode(auto.policy.model_dump()), 'owner'))
    catalog = [capabilities.Capability('cap-1', 'device', auto.policy.tool, 'xdr', 'Synthetic XDR', 'unknown')]
    monkeypatch.setattr(capabilities, 'discover', AsyncMock(return_value=(catalog, [])))
    monkeypatch.setattr(investigation.Agent, 'list', AsyncMock(return_value=[]))
    async def choose(agent, data):
        if not data['evidence']:
            return investigation.Choice(action='query', capability='cap-1', entity='host', reason='核对事件关联主机')
        return investigation.Choice(action='finish', verdict='unknown', evidence_ids=['evidence-1'], reason='保留主机查询事实，仍需负责人核查')
    monkeypatch.setattr(investigation, 'choose', choose)
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
    assert (await rows('SELECT state FROM monitor_investigations'))[0]['state'] == 'ready'
    assert '智能体调查参考' in notices[0]['body']
    assert len(outbound) == 1 and outbound[0]['Message-ID'] == notices[0]['message_id']
    assert outbound[0]['To'] == 'person@example.com'
    assert not outbound[0]['In-Reply-To']
    assert '[开发联调]' not in notices[0]['subject']
    assert '测试邮件' not in notices[0]['body']
    from flocks.session.message import Message, MessageRole
    messages = await Message.list_with_parts(auto.session.id)
    assert all(message.info.parentID for message in messages if message.info.role == MessageRole.ASSISTANT)
    summaries = [part for message in messages for part in message.parts if part.type == 'text' and (part.metadata or {}).get('monitoringSummary')]
    assert any('ID：event' in part.text for part in summaries)
    assert not any('本次为邮件链路测试' in part.text for part in summaries)
    assert all('重新核对事件及自动标记范围' not in part.text for part in summaries)
    assert any((part.metadata or {}).get('monitoringRoundEnd') and part.metadata['roundStatus'] == 'completed' for part in messages[-1].parts if part.type == 'text')
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
    monkeypatch.setattr(mail_interpreter, 'interpret', AsyncMock(return_value=interpretation(notices[0], msg.text, outcome)))
    await tick()
    assert auto.send.await_count == 1
    assert len(outbound) == 1
    updates = [data for path, data in actual_tool if path.endswith('/dealstatus')]
    assert len(updates) == 1 and updates[0]['uuIds'] == ['event']
    assert updates[0]['dealStatus'] == target
    listed = [data for path, data in actual_tool if path.endswith('/list')]
    queries = [data for data in listed if not data.get('uuIds')]
    assert queries and all(data['dealStatus'] == [0, 10] and data['whiteStatus'] == ['未加白', '部分加白'] for data in queries)
    assert all(not data.get('severities') and data['pageSize'] == 100 for data in queries)
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'verified'
    messages = await Message.list_with_parts(auto.session.id)
    assert all(message.info.parentID for message in messages if message.info.role == MessageRole.ASSISTANT)
    assert any((part.metadata or {}).get('monitoringRoundEnd') and part.metadata['roundStatus'] == 'completed' for part in messages[-1].parts if part.type == 'text')
    report = (await rows('SELECT * FROM monitor_reports'))[0]
    assert '当日告警总结' in report['summary_content'] and '执行时间线' in report['content']
    await tick()
    current = await snapshot('owner', auto.policy.scope, auto.day)
    assert current['developmentSample'] is False
    assert auto.send.await_count == 1
    assert len([1 for path, _ in actual_tool if path.endswith('/dealstatus')]) == 1


async def legacy_notice(auto, state='queued'):
    """Seed a persisted pre-upgrade notification, not a current test-mode policy."""
    await m.queue_notices(auto.policy, [auto.event], auto.session.id)
    n = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    old_event = {**json.loads(n['event']), 'development_sample': True,
                 'notified_end_time': auto.raw['endTime'], 'notified_status': auto.raw['dealStatus'],
                 'notified_host': auto.raw.get('hostIp')}
    await write('UPDATE monitor_mail_notices SET state=?,event=?,subject=? WHERE id=?',
                (state, encode(old_event), '[开发联调] 历史邮件', n['id']))
    return (await rows('SELECT * FROM monitor_mail_notices WHERE id=?', (n['id'],)))[0]


@pytest.mark.parametrize('state', ['queued', 'skipped', 'sent', 'send_unknown', 'needs_review'])
async def test_retired_sample_notices_never_send_again(auto, state):
    n = await legacy_notice(auto, state)
    result = await m.notify_batch(auto.policy, auto.session.id, [], Recorder(), auto.adapter)
    saved = (await rows('SELECT * FROM monitor_mail_notices WHERE id=?', (n['id'],)))[0]
    assert result['sent'] == 0 and not auto.send.called and not writes(auto)
    assert saved['state'] == ('skipped' if state == 'queued' else state)
    assert json.loads(saved['event'])['development_sample'] is True


async def test_historical_sample_observation_never_creates_notice(auto):
    event = {**auto.event, 'development_sample': True}
    await m.notify_batch(auto.policy, auto.session.id, [event], Recorder(), auto.adapter)
    assert not await rows('SELECT * FROM monitor_mail_notices')
    assert not auto.send.called and not writes(auto)


@pytest.mark.parametrize('state', ['queued', 'skipped', 'sent', 'send_unknown'])
async def test_current_event_dedup_is_separate_from_retired_sample_notice(auto, state):
    old = await legacy_notice(auto, state)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    notices = await rows('SELECT * FROM monitor_mail_notices ORDER BY created_at,id')
    assert len(notices) == 2 and auto.send.await_count == 1
    legacy = next(n for n in notices if n['id'] == old['id'])
    current = next(n for n in notices if n['id'] != old['id'])
    assert legacy['event_key'] == 'legacy-sample:' + old['id']
    assert legacy['state'] == ('skipped' if state == 'queued' else state)
    assert current['event_key'] == auto.event['key'] and current['state'] == 'sent'
    assert '[开发联调]' not in current['subject'] and not writes(auto)


@pytest.mark.parametrize('initial', [0, 40, 60])
async def test_historical_test_feedback_does_not_create_new_write(auto, initial):
    auto.raw['dealStatus'] = initial
    n = await legacy_notice(auto, 'sent')
    msg = await reply(auto, n, text='本次联调处理已完成，请标记忽略。', authenticated=False)
    await consume(auto, interpretation(n, msg.text))
    await consume(auto, interpretation(n, msg.text))
    assert not auto.send.called and not writes(auto)
    assert auto.raw['dealStatus'] == initial
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'
    assert not await rows('SELECT * FROM monitor_dispositions')


@pytest.mark.parametrize('migrated', [False, True])
async def test_historical_attempted_write_only_finishes_readback(auto, migrated):
    n = await notice(auto)
    msg = await reply(auto, n)
    auto.lose_write = True
    assert (await consume(auto, interpretation(n, msg.text)))['pending'] == 1
    current = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    old_event = {**json.loads(current['event']), 'development_sample': True}
    await write('UPDATE monitor_mail_notices SET event=? WHERE id=?', (encode(old_event), n['id']))
    if migrated:
        await m.queue_notices(auto.policy, [auto.event], auto.session.id)
        assert (await rows('SELECT event_key FROM monitor_mail_notices WHERE id=?', (n['id'],)))[0]['event_key'].startswith('legacy-sample:')
    await m.recover()
    assert (await consume(auto, interpretation(n, msg.text)))['verified'] == 1
    assert len(writes(auto)) == 1 and auto.send.await_count == 1


@pytest.mark.parametrize('gpt,definition,threat,sent', [
    (170, [], None, True),  # Unknown evidence still needs the responsible person.
    (40, ['业务行为'], 1, False),
    (160, ['业务行为'], 1, False),
    (10, [], 3, True),
    (20, [], 3, True),
])
async def test_formal_mail_only_notifies_when_followup_needed(auto, gpt, definition, threat, sent):
    auto.raw.update(gptResult=gpt, threatDefineName=definition)
    if threat is not None:
        auto.responses['file']['data']['item'] = [{'threatLevel': threat}]
    n = await notice(auto)
    assert n['state'] == ('sent' if sent else 'skipped')
    assert auto.send.await_count == int(sent)
    assert '即使无需处置或证据不足也发送' not in n['body']
    assert not writes(auto)


async def test_partial_evidence_can_request_investigation_without_claiming_safety(auto):
    auto.raw['gptResult'] = 10
    auto.responses['file'] = {'data': {}}
    result = await m.notify_batch(auto.policy, auto.session.id, [auto.event], Recorder(), auto.adapter)
    assert result['sent'] == 1 and result['pending'] == 0 and auto.send.called
    n = (await rows('SELECT * FROM monitor_mail_notices'))[0]
    assert '证据不完整' in n['body'] and '文件：证据未取得' in n['body']
    assert '[开发联调]' not in n['subject'] and not writes(auto)


async def test_proven_no_smtp_attempt_is_retryable_but_unknown_send_is_not(auto):
    auto.send.side_effect = m.transport.TransportNotReady('请先连接 Flocks 邮件通道')
    n = await notice(auto)
    assert n['state'] == 'queued' and '连接' in n['error']
    auto.send.side_effect = None
    assert (await notice(auto))['state'] == 'sent'
    await notice(auto)
    assert auto.send.await_count == 2 and not writes(auto)


async def test_empty_scope_never_sends(auto):
    auto.leave_scope = True
    n = await notice(auto)
    assert n['state'] == 'skipped' and n['error'] == '事件已离开通知查询范围'
    assert not auto.send.called and not writes(auto)


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


async def test_old_benign_investigation_cannot_hide_fresh_malicious_evidence(auto):
    auto.event['investigation'] = {'verdict': 'benign', 'state': 'ready', 'reason': '之前实体为安全', 'evidence_ids': []}
    auto.raw['gptResult'] = 10
    auto.responses['file']['data']['item'] = [{'threatLevel': 3}]
    n = await notice(auto)
    assert n['state'] == 'sent' and auto.send.await_count == 1
    assert json.loads(n['event'])['assessment']['malicious'] is True
    assert not writes(auto)


async def test_mail_history_filters_sent_before_pagination_and_uses_actual_send_time(auto):
    states = ['sent', 'queued', 'sent', 'skipped', 'send_unknown', 'sent']
    sent_ids = []
    for index, state in enumerate(states):
        event = {**auto.event, 'key': f'device:incident:event-{index}', 'id': f'event-{index}'}
        await m.queue_notices(auto.policy, [event], auto.session.id)
        n = (await rows('SELECT * FROM monitor_mail_notices WHERE event_key=?', (event['key'],)))[0]
        stamp = f'2026-09-25T{index:02d}:00:00+00:00'
        await write('UPDATE monitor_mail_notices SET state=?,created_at=?,updated_at=?,sent_at=? WHERE id=?',
                    (state, '2026-09-24T00:00:00+00:00', stamp, stamp if state == 'sent' else None, n['id']))
        if state == 'sent': sent_ids.append(n['id'])
    first = await m.history('owner', limit=2, tab='sent')
    second = await m.history('owner', limit=2, offset=2, tab='sent')
    assert [n['id'] for n in first['notices']] == sent_ids[::-1][:2]
    assert [n['id'] for n in second['notices']] == sent_ids[::-1][2:]
    assert first['has_more'] is True and second['has_more'] is False
    assert first['notices'][0]['sent_at'] == '2026-09-25T05:00:00+00:00'
    assert first['replies'] == second['replies'] == []
    assert first['counts']['sent'] == 3
    assert not auto.send.called and not writes(auto)


async def test_mail_reply_history_keeps_unmatched_feedback_and_does_not_mix_sent_pagination(auto):
    n = await notice(auto)
    for index in range(3):
        await reply(auto, n, text='处理结果待核对', headers=False, message_id=f'<history-{index}@example.com>')
        await write('UPDATE monitor_mail_replies SET received_at=? WHERE message_id=?',
                    (f'2026-09-25T0{index}:00:00+00:00', f'<history-{index}@example.com>'))
    first = await m.history('owner', limit=2, tab='received')
    second = await m.history('owner', limit=2, offset=2, tab='received')
    assert [r['message_id'] for r in first['replies']] == ['<history-2@example.com>', '<history-1@example.com>']
    assert [r['message_id'] for r in second['replies']] == ['<history-0@example.com>']
    assert all(r['targets'] == [] and r['state'] == 'pending' for r in first['replies'])
    assert first['has_more'] is True and second['has_more'] is False
    assert first['notices'] == second['notices'] == []
    # A full final page is not evidence of another page.
    assert (await m.history('owner', limit=3, tab='received'))['has_more'] is False
    assert (await m.history('owner', limit=1, tab='sent'))['has_more'] is False


@pytest.mark.parametrize('failure', ['truncated', 'tools', 'abnormal_stop', 'empty', 'invalid_json', 'invalid_schema'])
async def test_reply_model_contract_failure_is_retryable_service_error(auto, monkeypatch, failure):
    from flocks.config.config import Config
    from flocks.provider.provider import Provider
    n = await notice(auto)
    msg = await reply(auto, n)
    response = SimpleNamespace(content=json.dumps(interpretation(n, msg.text)), finish_reason='stop', tool_calls=[])
    if failure == 'truncated': response.finish_reason = 'length'
    if failure == 'tools': response.tool_calls = [{'name': 'not_allowed'}]
    if failure == 'abnormal_stop': response.finish_reason = 'content_filter'
    if failure == 'empty': response.content = None
    if failure == 'invalid_json': response.content = '{invalid'
    if failure == 'invalid_schema': response.content = '{"classification":"feedback","items":[{"unexpected":"field"}]}'
    chat = AsyncMock(return_value=response)
    monkeypatch.setattr(Config, 'resolve_default_llm', AsyncMock(return_value={'provider_id': 'fixture', 'model_id': 'fixture'}))
    monkeypatch.setattr(Provider, 'apply_config', AsyncMock())
    monkeypatch.setattr(Provider, 'get', lambda *_: SimpleNamespace(chat=chat))
    async def process():
        with unattended_scope(), monitoring_read_scope(auto.policy.tool, auto.policy.devices):
            return await m.process_replies(auto.policy, auto.session.id, await m.cutoff('owner', auto.policy.project), Recorder(), auto.adapter)
    failed = await process()
    assert failed['errors'] and failed['pending'] == 1
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'pending'
    assert not writes(auto) and not await rows('SELECT * FROM monitor_mail_items')
    response.content = json.dumps(interpretation(n, msg.text))
    response.finish_reason, response.tool_calls = 'completed', []
    recovered = await process()
    assert recovered['verified'] == 1 and not recovered['errors']
    assert len(writes(auto)) == 1


async def test_ambiguous_feedback_is_human_review_not_service_failure(auto):
    n = await notice(auto)
    msg = await reply(auto, n, '目前情况还不确定。')
    result = await consume(auto, interpretation(n, msg.text, 'unknown'))
    assert not result['errors'] and result['pending'] == 1
    assert (await rows('SELECT state FROM monitor_mail_replies'))[0]['state'] == 'needs_review'
    assert not writes(auto)


async def test_reply_body_budget_remains_business_review_before_model_call(auto, monkeypatch):
    from flocks.monitoring import mail_interpreter
    provider = AsyncMock()
    monkeypatch.setattr(mail_interpreter.Config, 'resolve_default_llm', provider)
    with pytest.raises(ValueError, match='超过解读预算'):
        await mail_interpreter.interpret({'text': 'x' * 20001}, [])
    provider.assert_not_called()


async def test_daily_sent_count_uses_delivery_date_not_later_record_update(auto):
    n = await notice(auto)
    await write('UPDATE monitor_mail_notices SET sent_at=?,updated_at=? WHERE id=?',
                ('2026-09-24T12:00:00+00:00', '2026-09-25T12:00:00+00:00', n['id']))
    previous = await snapshot('owner', auto.policy.scope, '2026-09-24')
    current = await snapshot('owner', auto.policy.scope, '2026-09-25')
    assert previous['mail']['sentToday'] == 1
    assert current['mail']['sentToday'] == 0
    # Pre-upgrade notices retain a documented fallback until a real sent_at exists.
    await write('UPDATE monitor_mail_notices SET sent_at=NULL WHERE id=?', (n['id'],))
    legacy = await snapshot('owner', auto.policy.scope, '2026-09-25')
    assert legacy['mail']['sentToday'] == 1
