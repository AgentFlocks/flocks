"""Project-scoped mail follow-up: persist first, process on the next monitor tick."""
import asyncio
import json
import re
from time import monotonic
from uuid import uuid4, uuid5, NAMESPACE_URL
from pydantic import BaseModel, ConfigDict, StrictBool, Field, field_validator
from . import disposition as d, mail_transport as transport, diagnostics as diag
from .adapter import XdrAdapter, ContractError, normalize
from .automatic import read_event
from .models import COMPONENT_ID, MonitoringPolicy
from .store import connection, rows, write, encode
from . import sampling
from .summaries import Summary, event_label, label, STATUS_LABELS
from .event_details import build_story
from .mail_template import TEMPLATE_VERSION, render_notification
from .assessment import assess_event, same_evidence_version, apply_followup
from .followup import is_monitoring_ended, feedback_expired
from flocks.session.interaction_policy import require_interactive, automatic_mark_scope

_locks = {}
_round_locks = {}
TARGETS = {'in_progress': 10, 'contained': 70, 'completed': 40, 'false_positive': 60}
STATE_LABELS = {'sent': '已发送', 'queued': '尚未发送，失败时可手动重试', 'sending': '发送中', 'skipped': '未发送', 'already_attempted': '已尝试发送',
                'blocked': '邮件通道异常，未发送',
                'send_unknown': '发送结果待核对', 'pending': '等待下轮处理', 'interpreted': '已解读，等待回查',
                'verified': '已回查确认', 'needs_review': '反馈待核验', 'unrelated': '普通邮件已转交',
                'monitoring_ended': '事件未再检出，已停止跟进，回信已归档'}


def lock(owner):
    return _locks.setdefault(owner, asyncio.Lock())


class MailSettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient_email: str = Field(default='', max_length=254)
    responsible_name: str = Field(default='', max_length=80)
    enabled: StrictBool = False

    @field_validator('recipient_email')
    @classmethod
    def email(cls, value):
        value = value.strip().lower()
        if value and (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
                      or '..' in value):
            raise ValueError('请填写单个有效邮箱地址')
        return value


async def settings(owner, device=None):
    found = await rows('SELECT s.* FROM monitor_mail_settings s JOIN monitor_installations i ON '
                       'i.owner=s.owner AND i.scope=s.scope AND i.project=s.project '
                       'WHERE s.owner=? AND s.scope=? AND i.installed=1', (owner, COMPONENT_ID))
    config = found[0] if found else {'enabled': False, 'recipient': '', 'responsible_name': '', 'revision': '', 'project': None, 'mailbox': ''}
    if device is None:
        return config
    installations = await rows('SELECT policy FROM monitor_installations WHERE owner=? AND scope=? AND installed=1', (owner, COMPONENT_ID))
    policy = MonitoringPolicy.model_validate_json(installations[0]['policy']) if installations else None
    if not policy or device not in policy.devices:
        return {**config, 'enabled': False, 'recipient': '', 'revision': ''}
    if policy.targets_configured:
        targets = await rows('SELECT * FROM monitor_device_targets WHERE owner=? AND scope=? AND project=? AND device=?',
                             (owner, COMPONENT_ID, policy.project, device))
        if not targets:
            return {**config, 'enabled': False, 'recipient': '', 'revision': ''}
        return {**config, **targets[0], 'enabled': config['enabled'],
                'device_name': policy.device_names.get(device, targets[0]['device_name'])}
    # Legacy recipients are valid only for the original single monitored target.
    return {**config, 'enabled': bool(config['enabled'] and len(policy.devices) == 1),
            'device_name': policy.device_names.get(device, device)}


async def health_snapshot(owner, project=None):
    """Read health facts without probing the network or exposing mailbox data.

    A successful SMTP submission says nothing about receiving replies. Unknown
    after process restart stays unknown until the channel verifies connectivity.
    """
    config = await settings(owner)
    enabled = bool(config['enabled'] and (project is None or config['project'] == project))
    empty = {'state': 'unknown' if enabled else 'disabled', 'stage': 'unknown',
             'last_success_at': None, 'last_success_stage': None, 'last_error_at': None, 'last_error_stage': None,
             'error_type': None, 'consecutive_failures': 0, 'next_retry_at': None}
    result = {'enabled': enabled, 'receive': dict(empty), 'send': dict(empty), 'errors': []}
    if not enabled:
        return result
    plugin = transport.default_registry.get('email')
    cfg = getattr(plugin, '_resolved', {})
    if plugin and callable(getattr(plugin, 'health_snapshot', None)):
        health = plugin.health_snapshot()
        for direction in ('receive', 'send'):
            result[direction].update({key: health.get(direction, {}).get(key, value)
                                      for key, value in empty.items()})
    if not cfg or config['mailbox'] != transport.mailbox_key(cfg):
        for direction in ('receive', 'send'):
            result[direction].update(state='unavailable', stage='configuration')
        result['errors'].append('邮件通道尚未配置或账号已变化，请核对邮件跟进配置；不能将当前状态视为没有回信。')
        return result
    if not plugin.status.connected and result['receive']['state'] == 'healthy':
        result['receive'].update(state='unavailable', stage='stopped')
    labels = {'connect': '连接', 'tls': '加密连接', 'authenticate': '认证', 'select': '打开收件箱',
              'search': '检索邮件', 'fetch': '读取邮件', 'dispatch': '保存或转交回信',
              'checkpoint': '保存收信进度', 'poll': '收信轮询', 'probe': '连接检查',
              'send': '投递邮件', 'stopped': '连接未启动或已停止', 'stale': '长时间未完成收信检查',
              'configuration': '配置', 'unknown': '连接尚未验证'}
    for direction, name in (('receive', '收信（IMAP）'), ('send', '发信（SMTP）')):
        item = result[direction]
        if item['state'] != 'healthy':
            stage = labels.get(item['stage'], '连接检查')
            # No probe after restart is an unverified dependency, not evidence
            # that a message was sent and rejected. Keep the dependency visible
            # until verified, without fabricating a delivery failure.
            text = (f'{name}尚未完成连接验证：{stage}。' if item['state'] == 'unknown'
                    else f'{name}暂不可用：{stage}。')
            if direction == 'receive':
                text += '本轮无法确认是否有新回信；已保存回信和收信进度保留，连接恢复后继续补收。'
            elif item['state'] == 'unknown':
                text += '当前没有可用的连接验证结果，不代表已经发生邮件投递失败；实际发送结果请查看邮件记录。'
            else:
                text += '投递结果未知的通知不会自动重发，请在邮件记录中核对。'
            result['errors'].append(text)
    return result


async def configure(owner, body):
    await require_interactive()
    from .lifecycle import _owned_installation
    async with lock(owner):
        entry, _, policy = await _owned_installation(owner)
        if policy.targets_configured:
            raise ValueError('请在监测配置页面按设备修改责任人和邮箱')
        current = await settings(owner)
        key = current['mailbox']
        if body.enabled:
            if not body.recipient_email or not entry['ready']:
                raise ValueError('请填写责任人邮箱并完成 XDR 接入检查')
            from flocks.config.config import Config
            if not await Config.resolve_default_llm():
                raise ValueError('请先配置自然语言解读模型')
            _, cfg = transport.transport(body.recipient_email)
            key = transport.mailbox_key(cfg)
            from flocks.tool.registry import ToolRegistry
            from .adapter import require_query_contract
            tool = ToolRegistry.get(policy.tool)
            if tool is None:
                raise ValueError('XDR 工具不可用')
            require_query_contract(tool.info)
            if not {'uuids', 'deal_status', 'deal_comment'} <= tool.info.get_schema().to_json_schema().get('properties', {}).keys():
                raise ValueError('XDR 工具缺少状态标记字段')
        stamp = d.stamp()
        # A recipient cannot silently route the same mailbox feedback to two owners.
        async with connection() as db:
            await db.execute('BEGIN IMMEDIATE')
            conflicts = await db.execute('SELECT 1 FROM monitor_mail_settings WHERE mailbox=? AND recipient=? AND owner!=? '
                                         'UNION SELECT 1 FROM monitor_device_targets WHERE mailbox=? AND recipient=? AND owner!=? '
                                         'UNION SELECT 1 FROM monitor_mail_notices WHERE mailbox=? AND recipient=? AND owner!=? LIMIT 1',
                                         (key, body.recipient_email, owner) * 3)
            if key and await conflicts.fetchone():
                raise ValueError('该邮箱已关联其他拥有者的监测，请使用独立责任人邮箱')
            unchanged = current['project'] == policy.project and current['mailbox'] == key and current['recipient'] == body.recipient_email
            revision = current['revision'] if unchanged else str(uuid4())
            await db.execute('INSERT INTO monitor_mail_settings VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(owner,scope) DO UPDATE SET '
                             'project=excluded.project,recipient=excluded.recipient,responsible_name=excluded.responsible_name,'
                             'enabled=excluded.enabled,revision=excluded.revision,mailbox=excluded.mailbox,updated_at=excluded.updated_at',
                             (owner, COMPONENT_ID, policy.project, body.recipient_email, body.responsible_name, int(body.enabled), revision, key, stamp))
            await db.execute('UPDATE monitor_auto_settings SET enabled=0 WHERE owner=? AND scope=?', (owner, COMPONENT_ID))
    return await settings(owner)


async def authorized(policy, revision, device=None):
    config = await settings(policy.owner, device)
    if not config['enabled'] or config['project'] != policy.project or config['revision'] != revision:
        raise ContractError('邮件跟进已关闭或配置已变化')
    from .lifecycle import _owned_installation
    from flocks.hub import local
    from flocks.project.project import Project
    entry, scheduler, actual = await _owned_installation(policy.owner)
    record = local.get_record('component', policy.scope)
    if not record or not record.enabled or not entry['ready'] or actual != policy or scheduler.status.value != 'active':
        raise ContractError('监测已暂停、停用或绑定变化')
    if Project.registry_state(policy.project, owner_id=policy.owner) != 'active':
        raise ContractError('监测项目不可用')
    return config


class MailAdapter(XdrAdapter):
    allowed_actions = frozenset({'list', 'get_entities', 'get_proof', 'update_status'})

    def __init__(self, policy, session_id, revision):
        super().__init__(policy, session_id)
        self.revision = revision

    async def call(self, device, params, message_id):
        if params.get('action') != 'update_status':
            return await super().call(device, params, message_id)
        async with lock(self.policy.owner):
            await authorized(self.policy, self.revision, device)
            ids = params.get('uuids')
            if not isinstance(ids, list) or len(ids) != 1:
                raise PermissionError('邮件反馈每次只允许标记一个事件')
            if await is_monitoring_ended(self.policy.owner, self.policy.project, f'{device}:incident:{ids[0]}', scope=self.policy.scope):
                raise ContractError('事件未再检出，已停止跟进，回信保留，不再修改此事件的 XDR 状态')
            with automatic_mark_scope(self.policy.tool_for(device), device, ids[0], params.get('deal_status'), self.session_id):
                return await super().call(device, params, message_id)


async def receive(msg):
    """Only called inside the gateway provenance/ingress and allowlist boundary."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    mailbox = raw.get('mailbox_key')
    if not mailbox:
        return False
    candidates = await rows("SELECT * FROM monitor_mail_notices WHERE mailbox=? AND recipient=? AND (state IN ('sending','sent','send_unknown') OR sent_at IS NOT NULL)", (mailbox, msg.sender_id))
    if not candidates:
        return False
    from flocks.hooks.pipeline import HookPipeline
    hook = await HookPipeline.run_channel_inbound_before({
        'channel_id': msg.channel_id, 'sender_id': msg.sender_id, 'chat_id': msg.chat_id,
        'chat_type': msg.chat_type.value, 'text': msg.text, 'message_id': msg.message_id,
    })
    if hook.output.get('blocked'):
        return True
    # Preserve the received original as feedback evidence. Hook rewrites
    # belong to the generic chat flow, not evidence authorizing an XDR write.
    # Route across reinstallations only by known references; quarantine ambiguous
    # replies in the same owner's current project without stalling the mailbox.
    hints = ' '.join(str(v or '') for v in (msg.text, msg.reply_to_id, msg.thread_id, raw.get('subject'), raw.get('references')))
    from .mail_manual import delivery_references, explicit_reference
    await delivery_references(candidates)
    exact = [n for n in candidates if explicit_reference(n, {'text': hints})]
    if exact:
        candidates = exact
    ambiguous_project = len({c['project'] for c in candidates}) != 1
    if len({c['owner'] for c in candidates}) != 1:
        raise ValueError('邮件拥有者不唯一，未消费')
    owner, project = candidates[0]['owner'], candidates[0]['project']
    if ambiguous_project:
        project = (await settings(owner))['project'] or project
    identity = msg.message_id
    if not identity or identity.startswith('<imap-'):
        identity = encode([raw.get('uidvalidity'), raw.get('uid')])
    payload = {k: getattr(msg, k) for k in ('text', 'reply_to_id', 'thread_id', 'account_id', 'sender_id', 'chat_id')}
    payload.update({k: raw.get(k) for k in ('subject', 'references', 'date')})
    payload['notice_ids'] = [notice['id'] for notice in candidates]
    authenticated = raw.get('authenticated_sender') is True
    bypassed = not authenticated and not transport.REQUIRE_AUTHENTICATED_FEEDBACK
    payload.update(authenticated_sender=authenticated, sender_verification_bypassed=bypassed)
    stamp = d.stamp()
    state, error = ('pending', None) if authenticated or bypassed else ('needs_review', '回信身份未通过邮件通道核验')
    if ambiguous_project:
        state, error = 'needs_review', '回信涉及多个监测项目，暂无法唯一关联'
    if len(msg.text) > 20000:
        state, error = 'needs_review', '邮件超过解读预算，反馈待核验'
    # Bounded original mail is business data, never diagnostic text.
    payload['text'] = msg.text[:50000]
    async with lock(owner):
        await write('INSERT OR IGNORE INTO monitor_mail_replies(id,owner,project,mailbox,message_id,sender,payload,state,error,received_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (str(uuid4()), owner, project, mailbox, identity, msg.sender_id,
                                                     encode(payload), state, error, stamp, stamp))
    async with diag.trace_scope(owner, COMPONENT_ID, 'mail:' + identity):
        diag.event('mail.received', project=diag.opaque(project), reply=diag.opaque(identity), mail_state=state,
                   success=state=='pending', authenticated_sender=authenticated, sender_verification_bypassed=bypassed)
    return True


async def cutoff(owner, project, before=None):
    data = await rows('SELECT COALESCE(MAX(sequence),0) AS high FROM monitor_mail_replies WHERE owner=? AND project=? AND (? IS NULL OR received_at<=?)', (owner, project, before, before))
    return data[0]['high']


async def notification_assessment(policy, event, *, with_case=False):
    """Use the same verified facts as the event list, never incident severity."""
    found = await rows('SELECT * FROM monitor_investigations WHERE owner=? AND project=? AND event_key=?',
                       (policy.owner, policy.project, event['key']))
    case = found[0] if found else event.get('investigation', {})
    assessment = assess_event(event, case)
    from .followup import snapshot as followup_snapshot
    following = (await followup_snapshot(policy, event['key'])).get(event['key'])
    assessment = apply_followup(assessment, event, following)
    return (assessment, case) if with_case else assessment


async def queue_notices(policy, observed, session_id):
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return
    stamp = d.stamp()
    routes = {device: await settings(policy.owner, device) for device in {event['device'] for event in observed}}
    # Unknown/benign events remain in the incident list, not the outbound queue.
    # A later confirmed risk can still create its first notification.
    eligible = {event['key']: await notification_assessment(policy, event, with_case=True) for event in observed}
    async with connection() as db:
        for event in observed:
            config = routes[event['device']]
            if not config['enabled'] or not config['recipient']:
                continue
            event['device_name'] = config.get('device_name', event['device'])
            assessment, case = eligible[event['key']]
            if event.get('development_sample') is True or not assessment['notify_eligible']:
                continue  # Historical tests and uncertain decisions never send.
            result = json.loads(case['result']) if isinstance(case.get('result'), str) else case.get('result', case)
            proofs = json.loads(case['evidence']) if isinstance(case.get('evidence'), str) else case.get('evidence', [])
            prepared = {**event, 'assessment': assessment}
            story = build_story(prepared, proofs, result)
            presentation = render_notification(prepared, story, config['responsible_name'], timezone_name=policy.timezone)
            prepared.update(story=story, mail_presentation={'version': TEMPLATE_VERSION, 'html_body': presentation.html})
            await db.execute("UPDATE monitor_mail_notices SET event_key='legacy-sample:' || id, "
                             "state=CASE WHEN state='queued' THEN 'skipped' ELSE state END, "
                             "error=CASE WHEN state='queued' THEN '已退出联调，历史测试通知不再发送' ELSE error END "
                             "WHERE owner=? AND project=? AND event_key=? AND json_extract(event,'$.development_sample')=1",
                             (policy.owner, policy.project, event['key']))
            # A pre-send version change may have skipped an otherwise eligible
            # intent. Reuse it only after a new current risk decision; delivered
            # and ambiguous messages never enter this path.
            await db.execute("UPDATE monitor_mail_notices SET state='queued',error=NULL,event=?,updated_at=? "
                "WHERE owner=? AND project=? AND event_key=? AND state='skipped' AND sent_at IS NULL "
                "AND revision=? AND recipient=? AND mailbox=?",
                (encode(prepared), stamp, policy.owner, policy.project, event['key'], config['revision'], config['recipient'], config['mailbox']))
            nid = str(uuid4())
            await db.execute('INSERT OR IGNORE INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,error,session_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (nid, policy.owner, policy.scope, policy.project, event['key'], config['recipient'], config['revision'], config['mailbox'],
                              f'<flocks-monitor-{nid}@flocks.local>', presentation.subject, presentation.text, encode(prepared), 'queued', None, session_id, stamp, stamp))
            await db.execute("UPDATE monitor_mail_notices SET subject=?,body=?,event=? WHERE owner=? AND project=? AND event_key=? AND state='queued' AND sent_at IS NULL",
                             (presentation.subject, presentation.text, encode(prepared), policy.owner, policy.project, event['key']))


async def notify_one(policy, notice, session_id, adapter_factory=MailAdapter, *, manual_retry=False, delivery_message_id=None):
    if not manual_retry and notice.get('sent_at'):
        return 'already_attempted'  # Accepted notifications are never background reminders.
    authorize = authorized
    if manual_retry:
        await require_interactive()
        from .mail_retry import authorized as authorize
    from .mail_manual import completion_claimed
    if await completion_claimed(policy.owner, notice['id']):
        return 'already_attempted'
    async def skip(reason, updated_event=None):
        async with lock(policy.owner):
            if await completion_claimed(policy.owner, notice['id']):
                return 'already_attempted'
            state = 'queued' if manual_retry else 'skipped'
            async with connection() as db:
                changed = await db.execute("UPDATE monitor_mail_notices SET state=?,error=?,event=COALESCE(?,event),updated_at=? "
                    "WHERE id=? AND state='queued'", (state, reason, encode(updated_event) if updated_event else None, d.stamp(), notice['id']))
            return state if changed.rowcount else 'already_attempted'
    if await is_monitoring_ended(policy.owner, policy.project, notice['event_key'], scope=policy.scope):
        return await skip('XDR 本轮未返回此事件，事件未再检出，已停止跟进，不再发送历史通知')
    event = json.loads(notice['event'])
    config = await authorize(policy, notice['revision'], event['device'])
    if event.get('development_sample') is True:
        await write("UPDATE monitor_mail_notices SET state='skipped',error='已退出联调，历史测试通知不再发送',updated_at=? WHERE id=? AND state='queued'", (d.stamp(), notice['id']))
        return 'skipped'
    if event['device'] not in policy.devices:
        raise ContractError('事件设备不在当前项目中')
    adapter = adapter_factory(policy, session_id, notice['revision'])
    raw = await read_event(adapter, event, eligible=True)
    if raw is None:
        return await skip('事件已离开通知查询范围')
    # Recheck the original event, including its activity/host/status fingerprint.
    # A stored risk opinion must never authorize a new version of the event.
    current = {**event, **normalize(event['device'], raw),
               'device_name': config.get('device_name', event['device'])}
    assessment, case = await notification_assessment(policy, current, with_case=True)
    current['assessment'] = {'text': assessment['reason'], 'details': '', **assessment}
    diag.event('mail.analyzed', notice=diag.opaque(notice['id']), development_sample=False,
               malicious=assessment['notify_eligible'], errors=0)
    recorder = d.operation_recorder.get()
    if recorder:
        async def explain(_):
            return None, {'notify_eligible': assessment['notify_eligible'],
                          'verdict': assessment['verdict'], 'writes': 0}, Summary(assessment['reason'])
        await recorder.call('核对调查结论与通知条件', {'event': event['id']}, explain)
    if not assessment['notify_eligible']:
        return await skip('未达到已确认风险的通知条件：' + assessment['reason'] + '；不发信，不修改 XDR 状态。', current)
    same_version = same_evidence_version(event, current)
    event = current
    # A queue intent can outlive its investigation version. Always render the
    # currently accepted evidence, never a stale story saved in that intent.
    result = json.loads(case['result']) if isinstance(case.get('result'), str) else case.get('result', case)
    proofs = json.loads(case['evidence']) if isinstance(case.get('evidence'), str) else case.get('evidence', [])
    story = build_story(event, proofs, result)
    event['story'] = story
    saved_presentation = event.get('mail_presentation', {})
    prepared = (manual_retry and same_version and notice.get('subject') and notice.get('body')
                and isinstance(saved_presentation, dict)
                and saved_presentation.get('version') == TEMPLATE_VERSION
                and isinstance(saved_presentation.get('html_body'), str))
    if prepared:
        # Only a revalidated, same-version risk notice can reuse prepared mail.
        # Its Message-ID, subject and both bodies remain stable on retry.
        subject, body = notice['subject'], notice['body']
    else:
        presentation = render_notification(event, story, config['responsible_name'], timezone_name=policy.timezone)
        subject, body = presentation.subject, presentation.text
        saved_presentation = {'version': TEMPLATE_VERSION, 'html_body': presentation.html}
    event.update(notified_end_time=raw.get('endTime'), notified_status=raw.get('dealStatus'),
                 notified_host=raw.get('hostIp'), analysis=body, mail_presentation=saved_presentation)
    async with lock(policy.owner):
        if await is_monitoring_ended(policy.owner, policy.project, notice['event_key'], scope=policy.scope):
            await write("UPDATE monitor_mail_notices SET state='skipped',error=?,updated_at=? WHERE id=? AND state='queued'",
                ('事件未再检出，已停止跟进，不再发送历史通知', d.stamp(), notice['id']))
            return 'skipped'
        if await completion_claimed(policy.owner, notice['id']):
            return 'already_attempted'
        await authorize(policy, notice['revision'], event['device'])
        from .mail_manual import has_reply
        if await has_reply(notice):
            return 'already_attempted'
        # Commit the intent before network I/O. Unknown outcomes never auto-resend.
        async with connection() as db:
            cur = await db.execute("UPDATE monitor_mail_notices SET subject=?,body=?,event=?,state='sending',session_id=?,updated_at=? WHERE id=? AND state='queued'",
                                   (subject, body, encode(event), session_id, d.stamp(), notice['id']))
            if cur.rowcount != 1:
                return 'already_attempted'
        notice.update(subject=subject, body=body, event=encode(event))
        if delivery_message_id:
            notice['message_id'] = delivery_message_id
        return await deliver_notice(notice, session_id, recorder)


async def deliver_notice(notice, session_id, recorder=None):
    """Send one prepared notice after a persisted sending intent, under mail lock."""
    async def send_once(_):
        if await is_monitoring_ended(notice['owner'], notice['project'], notice['event_key'], scope=notice['scope']):
            await write("UPDATE monitor_mail_notices SET state='skipped',error=?,updated_at=? WHERE id=? AND state IN ('queued','sending')",
                ('事件未再检出，已停止跟进，未继续发信', d.stamp(), notice['id']))
            return 'skipped', {'state': 'skipped'}, Summary('事件未再检出，已停止跟进，历史通知保留，未继续发信。')
        state, error = 'send_unknown', '发件结果未知，保留待处理；不会重复投递'
        try:
            with diag.span('mail.send', notice=diag.opaque(notice['id'])):
                if await asyncio.wait_for(transport.send(notice, session_id), timeout=45):
                    state, error = 'sent', None
        except transport.TransportNotReady as exc:
            # Only a transport fact that SMTP did not accept permits retry.
            state, error = 'queued', str(exc)
        finally:
            diag.event('mail.result', notice=diag.opaque(notice['id']), mail_state=state,
                failure=state != 'sent', reason='unavailable' if state == 'queued' else 'send_unknown' if state != 'sent' else None)
            await write("UPDATE monitor_mail_notices SET state=?,error=?,updated_at=?,sent_at=CASE WHEN ?='sent' THEN COALESCE(sent_at,?) ELSE sent_at END WHERE id=?",
                (state, error, d.stamp(), state, d.stamp(), notice['id']))
        return state, {'state': state, 'reason': error}, Summary(error or '邮件服务已接受这一封通知，等待责任人回信；当前没有修改 XDR 状态。', success=state == 'sent')
    return (await recorder.call('发送告警通知', {'notice': notice['id']}, send_once)
            if recorder else (await send_once(None))[0])


async def notify_batch(policy, session_id, observed, recorder, adapter_factory=MailAdapter):
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return {'sent': 0, 'pending': 0}
    await queue_notices(policy, observed, session_id)
    health = await health_snapshot(policy.owner, policy.project)
    if health.get('send', {}).get('state') == 'unavailable':
        reason = '邮件通道异常，未发送；通知正文已保存，可在邮件跟进中手动重发'
        await write("UPDATE monitor_mail_notices SET state='blocked',error=?,updated_at=? WHERE owner=? AND project=? AND state='queued' AND error IS NULL AND sent_at IS NULL",
                    (reason, d.stamp(), policy.owner, policy.project))
    pending = await rows("SELECT n.* FROM monitor_mail_notices n WHERE n.owner=? AND n.project=? AND n.state='queued' AND n.error IS NULL AND n.sent_at IS NULL "
                         "AND NOT EXISTS(SELECT 1 FROM monitor_mail_retries r WHERE r.owner=n.owner AND r.notice_id=n.id AND r.state='running') ORDER BY n.created_at LIMIT 20", (policy.owner, policy.project))
    result = {'sent': 0, 'pending': 0, 'explanations': [], 'errors': []}
    pending_ids = {n['id'] for n in pending}
    for event in observed[:20]:
        previous = await rows('SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND event_key=?',
                              (policy.owner, policy.project, event['key']))
        if not previous or previous[0]['id'] in pending_ids:
            continue
        n = previous[0]
        text = (f"{event_label(event)}：与本项目同设备、同事件编号的通知记录去重。"
                f"已有通知 {n['id']}，创建于 {n['created_at']}，状态为 {STATE_LABELS.get(n['state'], n['state'])}；本轮不重复发信。"
                f"{n['error'] or ''}这不是无风险判断。")
        async def duplicate(_, text=text, n=n):
            return None, {'notice': n['id'], 'state': n['state'], 'duplicate': True}, Summary(text)
        await recorder.call('核对已有通知，避免重复发信', {'event': event['id']}, duplicate)
        result['explanations'].append(text)
    deadline = monotonic() + 120
    for notice in pending:
        if monotonic() >= deadline:
            break
        route = await settings(policy.owner, json.loads(notice['event'])['device'])
        if not route['enabled'] or notice['revision'] != route['revision'] or notice['mailbox'] != route['mailbox'] or notice['recipient'] != route['recipient']:
            await write("UPDATE monitor_mail_notices SET state='needs_review',error='通知配置已变化，未发送',updated_at=? WHERE id=?", (d.stamp(), notice['id']))
            result['pending'] += 1
            result['explanations'].append('通知配置已变化，未发送；请在邮件跟进中核对原通知。')
            continue
        async def perform(_):
            error = None
            try:
                state = await asyncio.wait_for(notify_one(policy, notice, session_id, adapter_factory), max(1, deadline-monotonic()))
            except Exception as exc:
                diag.event('mail.result', failure=True, notice=diag.opaque(notice['id']), mail_state='pending', error_type=type(exc).__name__)
                state = 'pending'
                error = str(exc) if isinstance(exc, ContractError) else '发信流程未完成，请检查前面的失败步骤或诊断日志'
                await write("UPDATE monitor_mail_notices SET error=?,updated_at=? WHERE id=? AND state='queued'",
                            (error, d.stamp(), notice['id']))
            current = (await rows('SELECT * FROM monitor_mail_notices WHERE id=?', (notice['id'],)))[0]
            if state == 'pending' and current['state'] == 'send_unknown':
                state = 'send_unknown'
            stored_event = json.loads(current['event'])
            reason = current['error'] or error or ('邮件服务已接受发送，等待责任人回信；尚未修改 XDR。' if state == 'sent' else '请在邮件跟进中核对记录。')
            text = f"邮件通知：{event_label(stored_event)} · {STATE_LABELS.get(state, state)}。{reason}"
            result['explanations'].append(stored_event.get('assessment', {}).get('text', '') + '\n' + text)
            success = state not in ('pending', 'queued', 'send_unknown')
            if not success:
                result['errors'].append(text)
            return state, {'notice': notice['id'], 'state': state, 'reason': reason}, Summary(text, stored_event.get('assessment', {}).get('details', ''), success=success)
        # Evidence calls finish in chronological order before the mail result.
        # All messages share this round's parent instead of becoming detached
        # assistant groups after the final round summary.
        token = d.operation_recorder.set(recorder)
        try:
            outcome = await perform(None)
        finally:
            d.operation_recorder.reset(token)
        async def completed(_, outcome=outcome):
            return outcome
        state = await recorder.call('邮件通知结果', {'notice': notice['id']}, completed)
        if state not in ('skipped', 'already_attempted'):
            result['sent' if state == 'sent' else 'pending'] += 1
    return result


def feedback_version_matches(event, raw):
    from .adapter import normalize
    from .followup import event_version
    expected_end = event.get('notified_end_time', event.get('endTime'))
    expected_host = event.get('notified_host', event.get('host'))
    return (raw.get('uuId') == event['id'] and expected_end is not None and raw.get('endTime') == expected_end
            and (raw.get('hostIp') or '') == (expected_host or '')
            and (not isinstance(event.get('basics'), dict) or event_version({**normalize(event['device'], raw), 'development_sample': False}) == event_version({**event, 'development_sample': False})))


async def mark_item(policy, item, session_id, adapter_factory=MailAdapter):
    notice = (await rows('SELECT * FROM monitor_mail_notices WHERE id=? AND owner=? AND project=?', (item['notice_id'], policy.owner, policy.project)))[0]
    async with d._locks.setdefault((policy.owner, notice['event_key']), asyncio.Lock()):
        if await is_monitoring_ended(policy.owner, policy.project, notice['event_key'], scope=policy.scope):
            return 'monitoring_ended'
        received = await rows('SELECT received_at FROM monitor_mail_replies WHERE id=? AND owner=? AND project=?',
                              (item.get('reply_id'), policy.owner, policy.project))
        if await feedback_expired(policy.owner, policy.project, notice['event_key'],
                received[0]['received_at'] if received else None, scope=policy.scope):
            return 'monitoring_ended'
        from .mail_manual import completion_claimed
        if await completion_claimed(policy.owner, notice['id']):
            raise ValueError('此告警已有人工处置记录，回信保留，不重复写回')
        event = json.loads(notice['event'])
        route = await settings(policy.owner, event['device'])
        if not route['enabled'] or notice['revision'] != route['revision'] or notice['recipient'] != route['recipient'] or notice['mailbox'] != route['mailbox']:
            raise ValueError('设备责任人配置已变化，请人工核对旧回信')
        await authorized(policy, notice['revision'], event['device'])
        # The current observed object and device must still belong to this installation.
        actual, _ = await d.target(policy.owner, event['key'])
        if actual != policy:
            raise ContractError('监测安装已变化')
        adapter = adapter_factory(policy, session_id, notice['revision'])
        previous = await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND id=?', (policy.owner, policy.scope, item['id']))
        if previous:
            with diag.span('mail.readback', notice=diag.opaque(notice['id'])):
                checked = await read_event(adapter, event)
                current = checked.get('dealStatus')
                if not feedback_version_matches(event, checked):
                    await d.finish(policy.owner, item['id'], 'pending', current, error='回查出现新活动，旧反馈未确认闭环', defer_exports=True)
                    return 'pending'
            state = 'verified' if d.matches_status(item['target'], current) else 'mismatch'
            await d.finish(policy.owner, item['id'], state, current, defer_exports=True)
            return state
        if event.get('development_sample') is True:
            raise ValueError('已退出联调模式，旧测试回信不能发起新的状态写入')
        unresolved = await rows("SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? AND status IN ('queued','writing','pending','mismatch')",
                                (policy.owner, policy.scope, notice['event_key']))
        if unresolved:
            raise ContractError('已有处置待回查，暂不发起新写入')
        raw = await read_event(adapter, event)
        current = raw.get('dealStatus')
        if type(current) is not int or current not in {0, 10, 30, 40, 50, 60, 70}:
            raise ValueError('XDR 当前状态未知')
        notified = event.get('notified_end_time')
        if notified is None or raw.get('endTime') != notified:
            raise ValueError('通知后出现新的事件活动，需要重新核对')
        if raw.get('hostIp') != event.get('notified_host'):
            raise ValueError('通知后事件主机变化，需要重新核对')
        if current in {40, 60} and current != item['target'] or current in {30, 70} and item['target'] == 10:
            raise ValueError('迟到反馈不能覆盖已有处置结果')
        stamp = d.stamp()
        comment = f"Flocks 责任人邮件反馈：{item['reason'][:500]}（通知 {notice['id']}）"
        await write('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,session_id,created_at,updated_at,mode,target_status,project,decision) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (item['id'], policy.owner, policy.scope, notice['event_key'], comment, 'writing', session_id,
                     stamp, stamp, 'mail', item['target'], policy.project, encode({'notice': notice['id'], 'reply': item['reply_id'],
                         'development_sample': False, 'observed_end_time': notified, 'observed_host': raw.get('hostIp')})))
        attempted = False
        try:
            if not d.matches_status(item['target'], current):
                fresh = await read_event(adapter, event)
                if any(fresh.get(k) != raw.get(k) for k in ('dealStatus', 'endTime', 'whiteStatus', 'hostIp')):
                    raise ValueError('写入前事件变化，保留待确认')
                attempted = True
                await d.operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']], 'deal_status': item['target'], 'deal_comment': comment}, '按责任人回信标记 XDR 状态')
            with diag.span('mail.readback', notice=diag.opaque(notice['id'])):
                checked = await read_event(adapter, event)
                current = checked.get('dealStatus')
                if not feedback_version_matches(event, checked):
                    await d.finish(policy.owner, item['id'], 'pending', current, error='回查出现新活动，旧反馈未确认闭环', defer_exports=True)
                    return 'pending'
            state = 'verified' if d.matches_status(item['target'], current) else 'mismatch'
            await d.finish(policy.owner, item['id'], state, current, defer_exports=True)
        except (Exception, asyncio.CancelledError) as exc:
            state = 'pending' if attempted else 'failed'
            await d.finish(policy.owner, item['id'], state, error='回信处置待回查' if attempted else '写入前校验未通过', defer_exports=True)
            if isinstance(exc, (asyncio.CancelledError, ValueError)):
                raise
            # Preserve the write intent for readback, but report this attempt's
            # transport failure separately from ordinary feedback waiting.
            raise ContractError('XDR 状态写入或回查未完成，已保存执行记录；后续只回查，不重复写入') from None
        return state


async def process_replies(policy, session_id, high, recorder, adapter_factory=MailAdapter, interpreter=None):
    from .mail_interpreter import interpret, validate
    interpreter = interpreter or interpret
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return {'processed': 0, 'verified': 0, 'pending': 0}
    result = {'processed': 0, 'verified': 0, 'pending': 0, 'ended': 0, 'errors': []}
    async with _round_locks.setdefault(policy.owner, asyncio.Lock()):
        replies = await rows("SELECT * FROM monitor_mail_replies WHERE owner=? AND project=? AND sequence<=? AND state IN ('pending','interpreted') ORDER BY sequence LIMIT 20", (policy.owner, policy.project, high))
        deadline = monotonic()+120
        for reply in replies:
            if monotonic() >= deadline:
                break
            async def perform(_):
                success = True
                try:
                    await asyncio.wait_for(process_reply(policy, reply, session_id, adapter_factory, interpreter, validate), max(1, deadline-monotonic()))
                except (ContractError, TimeoutError, RuntimeError):
                    success = False
                    await write('UPDATE monitor_mail_replies SET error=?,updated_at=? WHERE id=?', ('暂未处理完成，下一轮重试或回查', d.stamp(), reply['id']))
                except ValueError as exc:
                    diag.event('mail.result', failure=True, reply=diag.opaque(reply['id']), mail_state='needs_review', reason='ambiguous_reply', error_type=type(exc).__name__)
                    await write("UPDATE monitor_mail_replies SET state='needs_review',error=?,updated_at=? WHERE id=?", ('回信解读或事件关联尚不明确，反馈已保存，未改写告警', d.stamp(), reply['id']))
                except Exception as exc:
                    success = False
                    diag.event('mail.result', failure=True, reply=diag.opaque(reply['id']), mail_state='pending', reason='model_failed', error_type=type(exc).__name__)
                    await write('UPDATE monitor_mail_replies SET error=?,updated_at=? WHERE id=?', ('邮件处理服务暂不可用，下轮重试', d.stamp(), reply['id']))
                data = (await rows('SELECT state,error FROM monitor_mail_replies WHERE id=?', (reply['id'],)))[0]
                if not success:
                    result['errors'].append('责任人回信处理失败：' + data['error'])
                items = await rows('SELECT i.target,i.state,i.reason,n.event FROM monitor_mail_items i JOIN monitor_mail_notices n ON n.id=i.notice_id WHERE i.reply_id=?', (reply['id'],))
                details = '\n'.join(f"{event_label(json.loads(item['event']))}；反馈依据：{label(item['reason'], limit=500)}；目标状态：{STATUS_LABELS.get(item['target'], '未知')}；结果：{STATE_LABELS.get(item['state'], item['state'])}。" for item in items[:20])
                return data, data, Summary(f"已读取责任人回信：{STATE_LABELS.get(data['state'], data['state'])}。{data['error'] or ''}" + ('已重新查询 XDR 确认目标状态。' if data['state'] == 'verified' else '该事件未再检出，已停止跟进，保留回信记录，未修改 XDR。' if data['state'] == 'monitoring_ended' else '未确认前保留待跟进，不把收到回信直接当作闭环。'), details, success=success)
            data = await recorder.call('解读回信并跟进状态', {'reply': reply['id']}, perform)
            result['processed'] += 1
            result['verified' if data['state'] == 'verified' else 'ended' if data['state'] == 'monitoring_ended' else 'pending'] += 1
    return result


async def process_reply(policy, reply, session_id, adapter_factory, interpreter, validator):
    payload = json.loads(reply['payload'])
    await authorized(policy, (await settings(policy.owner))['revision'])
    if transport.REQUIRE_AUTHENTICATED_FEEDBACK and payload.get('authenticated_sender') is not True:
        raise ValueError('回信身份未核验，不能在严格模式下自动处置')
    notices = await rows("SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND mailbox=? AND recipient=? AND (state IN ('sent','send_unknown','sending') OR sent_at IS NOT NULL) ORDER BY created_at DESC",
                        (policy.owner, policy.project, reply['mailbox'], reply['sender']))
    if reply['state'] == 'pending':
        # Explicit identifiers and transport references narrow large histories before the LLM.
        hints = ' '.join(str(payload.get(k) or '') for k in ('text', 'subject', 'reply_to_id', 'thread_id', 'references'))
        from .mail_manual import delivery_references, explicit_reference
        await delivery_references(notices)
        exact = [n for n in notices if explicit_reference(n, {'text': hints})]
        if exact:
            notices = exact
        if not notices:
            raise ValueError('没有对应通知')
        candidates = [{'notice_id': n['id'], 'message_id': n['message_id'], 'delivery_message_ids': n.get('delivery_message_ids', []), 'event': json.loads(n['event'])} for n in notices]
        with diag.span('mail.interpret', reply=diag.opaque(reply['id'])):
            parsed = await interpreter(payload, candidates)
        with diag.span('mail.resolve', reply=diag.opaque(reply['id'])):
            accepted = validator(parsed, payload, notices)
        diag.event('mail.interpreted', reply=diag.opaque(reply['id']), items=len(accepted))
        if parsed['classification'] == 'unrelated':
            if payload.get('sender_verification_bypassed'):
                raise ValueError('未认证的联调回信不转交普通 Agent，请人工核对')
            # Durable handoff intention prevents repeated generic agent dispatch after crashes.
            await write("UPDATE monitor_mail_replies SET state='forwarding',result=?,updated_at=? WHERE id=?", (encode(parsed), d.stamp(), reply['id']))
            from flocks.channel.inbound.dispatcher import InboundDispatcher
            from flocks.channel.base import InboundMessage
            msg = InboundMessage(channel_id='email', account_id=payload['account_id'], message_id=reply['message_id'], sender_id=reply['sender'],
                                 chat_id=payload['chat_id'], text=payload['text'], reply_to_id=payload['reply_to_id'], thread_id=payload['thread_id'])
            await InboundDispatcher()._dispatch(msg)
            await write("UPDATE monitor_mail_replies SET state='unrelated',updated_at=? WHERE id=?", (d.stamp(), reply['id']))
            return
        if not accepted:
            raise ValueError('无法明确理解回信')
        for notice, item in accepted:
            route = await settings(policy.owner, json.loads(notice['event'])['device'])
            if not route['enabled'] or notice['revision'] != route['revision'] or notice['recipient'] != route['recipient'] or notice['mailbox'] != route['mailbox']:
                raise ValueError('设备责任人配置已变化，请人工核对旧回信')
        async with connection() as db:
            for notice, item in accepted:
                identity = str(uuid5(NAMESPACE_URL, reply['id']+notice['id']))
                target = TARGETS[item.outcome]
                if json.loads(notice['event']).get('development_sample') is True:
                    raise ValueError('已退出联调，旧测试回信保留人工核对，不发起状态写入')
                await db.execute('INSERT OR IGNORE INTO monitor_mail_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                 (identity, reply['id'], notice['id'], policy.owner, policy.project, target,
                                  item.reason, 'pending', None, d.stamp(), d.stamp()))
            await db.execute("UPDATE monitor_mail_replies SET state='interpreted',result=?,error=NULL,updated_at=? WHERE id=?", (encode(parsed), d.stamp(), reply['id']))
    items = await rows('SELECT * FROM monitor_mail_items WHERE reply_id=? ORDER BY created_at', (reply['id'],))
    for item in items:
        if item['state'] in ('verified', 'needs_review', 'failed', 'monitoring_ended'):
            continue
        try:
            with diag.span('mail.mark', notice=diag.opaque(item['notice_id']), reply=diag.opaque(reply['id'])):
                state = await mark_item(policy, item, session_id, adapter_factory)
            diag.event('mail.result', mail_state=state, notice=diag.opaque(item['notice_id']), failure=state not in {'verified', 'monitoring_ended'})
            error = None
        except ValueError:
            state, error = 'needs_review', '事件、配置或处理依据变化，反馈已保存，本次未改写告警'
        await write('UPDATE monitor_mail_items SET state=?,error=?,updated_at=? WHERE id=?', (state, error, d.stamp(), item['id']))
    states = await rows('SELECT state FROM monitor_mail_items WHERE reply_id=?', (reply['id'],))
    state = ('interpreted' if any(x['state'] not in ('verified', 'needs_review', 'failed', 'monitoring_ended') for x in states)
             else 'verified' if states and all(x['state'] == 'verified' for x in states)
             else 'monitoring_ended' if states and all(x['state'] in {'verified', 'monitoring_ended'} for x in states)
             else 'needs_review')
    await write('UPDATE monitor_mail_replies SET state=?,updated_at=? WHERE id=?', (state, d.stamp(), reply['id']))


async def history(owner, limit=100, offset=0, tab=None):
    from .mail_manual import snapshot as manual_snapshot, explicit_reference
    from .mail_retry import snapshot as retry_snapshot
    config = await settings(owner)
    project = config['project']
    # One connection for this projection. Only the current page's related mail
    # bodies are loaded; background polling must not rescan all historical mail.
    async with connection() as db:
        async def select(sql, args=()):
            cursor = await db.execute(sql, args)
            return [dict(row) for row in await cursor.fetchall()]
        notices = await select("SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND "
            "state IN ('sent','send_unknown','sending','queued','failed','blocked') "
            "ORDER BY COALESCE(sent_at,updated_at) DESC,id DESC LIMIT ? OFFSET ?",
            (owner, project, limit + 1, offset)) if tab != 'received' else []
        has_more = len(notices) > limit
        notices = notices[:limit]
        ids = [n['id'] for n in notices]
        placeholders = ','.join('?' for _ in ids) or 'NULL'
        references = await select("SELECT id,owner,project,message_id,mailbox,recipient,created_at,json_extract(event,'$.id') AS event_id, "
                                  "json_extract(event,'$.name') AS event_name,json_extract(event,'$.device') AS device, "
                                  "json_extract(event,'$.device_name') AS device_name "
                                  'FROM monitor_mail_notices WHERE owner=? AND project=?', (owner, project))
        for ref in references:
            ref['event'] = {'id': ref.pop('event_id'), 'name': ref.pop('event_name'),
                            'device': ref.pop('device'), 'device_name': ref.pop('device_name')}
        # Conservative candidates also keep an ambiguous incoming reply from
        # exposing the manual button before interpretation has resolved it.
        legacy = []
        legacy_args = []
        for n in notices:
            legacy.append('(r.mailbox=? AND r.sender=? AND r.received_at>=?)')
            legacy_args.extend((n['mailbox'], n['recipient'], n['created_at']))
        candidate_sql = (f"EXISTS(SELECT 1 FROM json_each(r.payload,'$.notice_ids') j WHERE j.value IN ({placeholders})) "
            f"OR EXISTS(SELECT 1 FROM monitor_mail_items i WHERE i.owner=r.owner AND i.reply_id=r.id AND i.notice_id IN ({placeholders})) "
            + ("OR (json_type(r.payload,'$.notice_ids') IS NULL AND (" + ' OR '.join(legacy) + '))' if legacy else ''))
        candidates = await select(f'SELECT r.* FROM monitor_mail_replies r WHERE r.owner=? AND r.project=? AND ({candidate_sql}) '
            'ORDER BY r.received_at DESC,r.sequence DESC', (owner, project, *ids, *ids, *legacy_args)) if ids else []
        if tab == 'received':
            standalone = await select('SELECT * FROM monitor_mail_replies WHERE owner=? AND project=? ORDER BY received_at DESC,sequence DESC LIMIT ? OFFSET ?',
                                      (owner, project, limit + 1, offset))
        else:
            standalone = await select("SELECT r.* FROM monitor_mail_replies r WHERE r.owner=? AND r.project=? "
                "AND r.state NOT IN ('unrelated','forwarding') AND COALESCE(json_array_length(r.payload,'$.notice_ids'),0)!=1 "
                "AND NOT EXISTS(SELECT 1 FROM monitor_mail_items i WHERE i.owner=r.owner AND i.reply_id=r.id) "
                "ORDER BY r.received_at DESC,r.sequence DESC LIMIT ? OFFSET ?", (owner, project, limit + 1, offset))
        has_more = has_more or len(standalone) > limit
        reply_rows = {r['id']: r for r in candidates + standalone}
        reply_ids = list(reply_rows)
        reply_placeholders = ','.join('?' for _ in reply_ids) or 'NULL'
        links = await select(f'SELECT * FROM monitor_mail_items WHERE owner=? AND project=? AND '
            f'(notice_id IN ({placeholders}) OR reply_id IN ({reply_placeholders})) ORDER BY created_at DESC,id DESC',
            (owner, project, *ids, *reply_ids))
        manual_rows = await select('SELECT m.*,d.status,d.error,d.updated_at,d.target_status FROM monitor_mail_manual m '
            'LEFT JOIN monitor_dispositions d ON d.id=m.id AND d.owner=m.owner AND d.scope=? '
            f'WHERE m.owner=? AND m.project=? AND m.notice_id IN ({placeholders}) ORDER BY m.created_at DESC,m.rowid DESC',
            (COMPONENT_ID, owner, project, *ids))
        event_keys = [n['event_key'] for n in notices]
        lifecycle_rows = await select('SELECT event_key,state,result FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? '
            f"AND event_key IN ({placeholders}) AND state IN ('monitoring_ended','verified','external_closed')", (owner, COMPONENT_ID, project, *event_keys))
        dispositions = await select('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND project=? '
            f'AND event_key IN ({placeholders}) ORDER BY created_at DESC,rowid DESC', (owner, COMPONENT_ID, project, *event_keys))
        counts = await select('SELECT state,COUNT(*) AS count FROM monitor_mail_notices WHERE owner=? AND project=? GROUP BY state', (owner, project))
        accepted_count = await select("SELECT COUNT(*) AS count FROM monitor_mail_notices WHERE owner=? AND project=? AND (sent_at IS NOT NULL OR state='sent')", (owner, project))
        reply_counts = await select('SELECT state,COUNT(*) AS count FROM monitor_mail_replies WHERE owner=? AND project=? GROUP BY state', (owner, project))
        unparsed = await select('SELECT COUNT(*) AS count FROM monitor_mail_unparsed WHERE mailbox=?', (config['mailbox'],))
    reference_map = {n['id']: n for n in references}
    from .mail_manual import delivery_references
    await delivery_references(references)
    items_by_reply, items_by_notice = {}, {}
    for item in links:
        items_by_reply.setdefault(item['reply_id'], []).append(item)
        items_by_notice.setdefault(item['notice_id'], []).append(item)
    manual_by_notice, disposition_by_event = {}, {}
    ended_keys = {item['event_key'] for item in lifecycle_rows if item['state'] == 'monitoring_ended'}
    closed_states = {item['event_key']: json.loads(item['result']).get('observed_status') for item in lifecycle_rows if item['state'] in ('verified','external_closed')}
    for item in manual_rows:
        manual_by_notice.setdefault(item['notice_id'], item)
    for item in dispositions:
        disposition_by_event.setdefault(item['event_key'], item)
    by_notice, ambiguous, assigned = {}, set(), set()
    for r in reply_rows.values():
        r['payload'] = json.loads(r['payload'])
        r['result'] = json.loads(r['result']) if r['result'] else None
        r['targets'] = []
        items = items_by_reply.get(r['id'], [])
        linked_ids = {i['notice_id'] for i in items}
        if not linked_ids and r['state'] not in ('unrelated', 'forwarding'):
            candidates = r['payload'].get('notice_ids')
            if not isinstance(candidates, list):
                peers = [n for n in references if n['mailbox'] == r['mailbox'] and n['recipient'] == r['sender']]
                candidates = [n['id'] for n in peers if explicit_reference(n, r['payload'])]
                if not candidates:
                    # An old free-form reply is still feedback; do not offer
                    # a manual decision while its association is unresolved.
                    ambiguous.update(n['id'] for n in peers if r['received_at'] >= n['created_at'])
            if len(candidates) == 1:
                linked_ids = set(candidates)
            else:
                ambiguous.update(candidates)
        for item in items:
            # Notice body and host are already on its own row. Replies only need
            # identifiers here, and never borrow another device's event data.
            n = next((n for n in notices if n['id'] == item['notice_id']), None)
            event = json.loads(n['event']) if n else reference_map.get(item['notice_id'], {}).get('event', {})
            r['targets'].append({'event_id': event.get('id', ''), 'name': event.get('name', ''),
                'device': event.get('device', ''), 'device_name': event.get('device_name', event.get('device', '')),
                'state': item['state'], 'target': item['target']})
        for notice_id in linked_ids:
            by_notice.setdefault(notice_id, []).append(r)
        if linked_ids:
            assigned.add(r['id'])
    for n in notices:
        n['sent_at'] = (n['sent_at'] or n['updated_at']) if n['state'] == 'sent' else n['sent_at']
        n['event'] = json.loads(n['event'])
        n['replies'] = sorted(by_notice.get(n['id'], []), key=lambda r: r['received_at'], reverse=True)
        n['reply_received'] = bool(n['replies'])
        n['manual'] = await manual_snapshot(n, previous=manual_by_notice.get(n['id']),
            reply_present=n['reply_received'] or n['id'] in ambiguous, monitoring_ended=n['event_key'] in ended_keys,
            source_closed=n['event_key'] in closed_states)
        n['retry'] = await retry_snapshot(n, reply_present=n['reply_received'] or n['id'] in ambiguous,
            manual=n['manual'], monitoring_ended=n['event_key'] in ended_keys)
        n['items'] = [dict(i) for i in items_by_notice.get(n['id'], [])][:100]
        for item in n['items']:
            item['reply_excerpt'] = reply_rows.get(item['reply_id'], {}).get('payload', {}).get('text', '')[:2000]
        disposition = disposition_by_event.get(n['event_key'], {})
        n['disposition_state'] = ({40: 'handled', 60: 'ignored'}.get(disposition.get('target_status'), 'unhandled')
                                  if disposition.get('status') == 'verified' else 'unhandled')
        n['disposition_source'] = ('reply' if disposition.get('mode') == 'mail' else 'manual' if disposition else
                                  'manual' if n['manual']['state'] == 'unhandled' else None)
        if n['event_key'] in closed_states and n['disposition_state'] == 'unhandled':
            n['disposition_state'] = {40: 'handled', 60: 'ignored'}.get(closed_states[n['event_key']], 'unhandled')
            n['disposition_source'] = 'xdr'
    replies = [reply_rows[r['id']] for r in standalone[:limit] if tab == 'received' or r['id'] not in assigned]
    return {'settings': {**config, 'recipient_email': config['recipient']}, 'notices': notices, 'replies': replies,
            'health': await health_snapshot(owner, project),
            'sender_verification_required': transport.REQUIRE_AUTHENTICATED_FEEDBACK,
            'unparsed_count': unparsed[0]['count'],
            'counts': {**{x['state']: x['count'] for x in counts}, 'sent': accepted_count[0]['count']}, 'reply_counts': {x['state']: x['count'] for x in reply_counts},
            'has_more': has_more}


async def diagnostic_state(owner):
    """Content-free configuration/queue snapshot for target-machine support."""
    from flocks.channel.registry import default_registry
    from flocks.config.config import Config
    config = await settings(owner)
    plugin = default_registry.get('email')
    cfg = getattr(plugin, '_resolved', {})
    installed = await rows('SELECT policy FROM monitor_installations WHERE owner=? AND scope=?', (owner, COMPONENT_ID))
    result = {'enabled': bool(config['enabled']), 'recipient_configured': bool(config['recipient']),
              'health': await health_snapshot(owner, config['project']),
              'channel_connected': bool(plugin and plugin.status.connected),
              'sender_verification_required': transport.REQUIRE_AUTHENTICATED_FEEDBACK,
              'channel_requires_authenticated_sender': bool(cfg.get('requireAuthenticatedSender')),
              'sender_verification_configured': bool(cfg.get('authservId')),
              'mailbox_matches': bool(cfg and config['mailbox'] == transport.mailbox_key(cfg)),
              'model_configured': bool(await Config.resolve_default_llm()),
              'development_sample': bool(installed and sampling.enabled(MonitoringPolicy.model_validate_json(installed[0]['policy'])))}
    if installed:
        from .agent_component import diagnostic_state as agent_state
        result['agent_component'] = agent_state()
        policy = MonitoringPolicy.model_validate_json(installed[0]['policy'])
        targets = await rows('SELECT device,recipient FROM monitor_device_targets WHERE owner=? AND scope=? AND project=?',
                             (owner, COMPONENT_ID, policy.project))
        result['monitoring_devices'] = len(policy.devices)
        result['device_targets_configured'] = policy.targets_configured
        result['recipient_configured'] = (bool(targets) and all(row['recipient'] for row in targets)
                                          if policy.targets_configured else bool(config['recipient']))
        result['device_targets'] = [{'device': diag.opaque(row['device']), 'recipient_configured': bool(row['recipient'])}
                                    for row in targets]
        result['investigation_engine'] = policy.investigation_engine
        result['round_timeout_seconds'] = policy.timeout_seconds
        from .investigation import status_summary
        backlog = await status_summary(policy)
        result['investigations'] = {state: backlog[state] for state in
            ('pending', 'ready', 'needs_review', 'deferred', 'system_wait')}
        result['investigation_recovery'] = {key: backlog[key] for key in
            ('retry_exhausted', 'earliest_retry_at', 'oldest_updated_at')}
    for name, table in (('notices', 'monitor_mail_notices'), ('replies', 'monitor_mail_replies')):
        counts = await rows(f'SELECT state,COUNT(*) AS count FROM {table} WHERE owner=? AND project=? GROUP BY state', (owner, config['project']))
        result[name] = {r['state']: r['count'] for r in counts if r['state'] in diag._ENUMS['mail_state']}
    result['unparsed_messages'] = (await rows('SELECT COUNT(*) AS count FROM monitor_mail_unparsed WHERE mailbox=?', (config['mailbox'],)))[0]['count']
    return result


async def recover():
    await write("UPDATE monitor_mail_notices SET state='send_unknown',error='服务重启，发件结果待核对，不自动重发' WHERE state='sending'")
    await write("UPDATE monitor_mail_retries SET state='finished', mail_state=(SELECT state FROM monitor_mail_notices WHERE id=notice_id), updated_at=? WHERE state='running'", (d.stamp(),))
    await write("UPDATE monitor_mail_replies SET state='needs_review',error='普通邮件转交中断，请人工核对' WHERE state='forwarding'")
