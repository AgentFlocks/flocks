"""Project-scoped mail follow-up: persist first, process on the next monitor tick."""
import asyncio
import json
import re
from time import monotonic
from uuid import uuid4, uuid5, NAMESPACE_URL
from pydantic import BaseModel, ConfigDict, StrictBool, Field, field_validator
from . import disposition as d, mail_transport as transport, diagnostics as diag
from .adapter import XdrAdapter, ContractError
from .automatic import read_event
from .models import COMPONENT_ID, MonitoringPolicy
from .store import connection, rows, write, encode
from .status_rules import ENTITY_TYPES, select_status, malicious_evidence
from . import sampling
from .summaries import Summary, event_label, label, evidence_summary, ENTITY_LABELS, STATUS_LABELS
from flocks.session.interaction_policy import require_interactive, automatic_mark_scope

_locks = {}
_round_locks = {}
TARGETS = {'in_progress': 10, 'contained': 70, 'completed': 40, 'false_positive': 60}
STATE_LABELS = {'sent': '已发送', 'queued': '尚未发送，等待重试', 'sending': '发送中', 'skipped': '未发送', 'already_attempted': '已尝试发送',
                'send_unknown': '发送结果待核对', 'pending': '等待下轮处理', 'interpreted': '已解读，等待回查',
                'verified': '已回查确认', 'needs_review': '待人工核对', 'unrelated': '普通邮件已转交'}


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


async def settings(owner):
    found = await rows('SELECT s.* FROM monitor_mail_settings s JOIN monitor_installations i ON '
                       'i.owner=s.owner AND i.scope=s.scope AND i.project=s.project '
                       'WHERE s.owner=? AND s.scope=? AND i.installed=1', (owner, COMPONENT_ID))
    return found[0] if found else {'enabled': False, 'recipient': '', 'responsible_name': '', 'revision': '', 'project': None, 'mailbox': ''}


async def configure(owner, body):
    await require_interactive()
    from .lifecycle import _owned_installation
    async with lock(owner):
        entry, _, policy = await _owned_installation(owner)
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
                                         'UNION SELECT 1 FROM monitor_mail_notices WHERE mailbox=? AND recipient=? AND owner!=? LIMIT 1',
                                         (key, body.recipient_email, owner, key, body.recipient_email, owner))
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


async def authorized(policy, revision):
    config = await settings(policy.owner)
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
            await authorized(self.policy, self.revision)
            ids = params.get('uuids')
            if not isinstance(ids, list) or len(ids) != 1:
                raise PermissionError('邮件反馈每次只允许标记一个事件')
            with automatic_mark_scope(self.policy.tool, device, ids[0], params.get('deal_status'), self.session_id):
                return await super().call(device, params, message_id)


async def receive(msg):
    """Only called inside the gateway provenance/ingress and allowlist boundary."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    mailbox = raw.get('mailbox_key')
    if not mailbox:
        return False
    candidates = await rows("SELECT * FROM monitor_mail_notices WHERE mailbox=? AND recipient=? AND state IN ('sending','sent','send_unknown')", (mailbox, msg.sender_id))
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
    exact = [n for n in candidates if n['message_id'] in hints or n['id'] in hints or
             re.search(r'(?<![\w-])'+re.escape(str(json.loads(n['event'])['id']))+r'(?![\w-])', hints)]
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
    authenticated = raw.get('authenticated_sender') is True
    bypassed = not authenticated and not transport.REQUIRE_AUTHENTICATED_FEEDBACK
    payload.update(authenticated_sender=authenticated, sender_verification_bypassed=bypassed)
    stamp = d.stamp()
    state, error = ('pending', None) if authenticated or bypassed else ('needs_review', '回信身份未通过邮件通道核验')
    if ambiguous_project:
        state, error = 'needs_review', '回信涉及多个监测项目，需人工核对'
    if len(msg.text) > 20000:
        state, error = 'needs_review', '邮件超过解读预算，需人工核对'
    # Bounded original mail is business data, never diagnostic text.
    payload['text'] = msg.text[:50000]
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


async def queue_notices(policy, observed, session_id):
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return
    stamp = d.stamp()
    async with connection() as db:
        for event in observed:
            if event.get('development_sample') is True:
                continue  # Retained historical samples cannot issue new mail.
            await db.execute("UPDATE monitor_mail_notices SET event_key='legacy-sample:' || id, "
                             "state=CASE WHEN state='queued' THEN 'skipped' ELSE state END, "
                             "error=CASE WHEN state='queued' THEN '已退出联调，历史测试通知不再发送' ELSE error END "
                             "WHERE owner=? AND project=? AND event_key=? AND json_extract(event,'$.development_sample')=1",
                             (policy.owner, policy.project, event['key']))
            nid = str(uuid4())
            await db.execute('INSERT OR IGNORE INTO monitor_mail_notices(id,owner,scope,project,event_key,recipient,revision,mailbox,message_id,subject,body,event,state,error,session_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (nid, policy.owner, policy.scope, policy.project, event['key'], config['recipient'], config['revision'], config['mailbox'],
                              f'<flocks-monitor-{nid}@flocks.local>', '', '', encode(event), 'queued', None, session_id, stamp, stamp))


async def notify_one(policy, notice, session_id, adapter_factory=MailAdapter):
    config = await authorized(policy, notice['revision'])
    event = json.loads(notice['event'])
    if event.get('development_sample') is True:
        await write("UPDATE monitor_mail_notices SET state='skipped',error='已退出联调，历史测试通知不再发送',updated_at=? WHERE id=? AND state='queued'", (d.stamp(), notice['id']))
        return 'skipped'
    if event['device'] not in policy.devices:
        raise ContractError('事件设备不在当前项目中')
    adapter = adapter_factory(policy, session_id, notice['revision'])
    raw = await read_event(adapter, event, eligible=True)
    if raw is None:
        await write("UPDATE monitor_mail_notices SET state='skipped',error='事件已离开通知查询范围',updated_at=? WHERE id=?", (d.stamp(), notice['id']))
        return 'skipped'
    responses, failures, failure_reasons = {}, [], {}
    for kind in ENTITY_TYPES:
        try:
            responses[kind] = await d.operation(adapter, event, {'action': 'get_entities', 'uuid': event['id'], 'entity_type': kind}, f'核对关联{ENTITY_LABELS[kind]}证据')
        except ContractError as exc:
            failures.append(kind)
            failure_reasons[kind] = str(exc)
        except TimeoutError:
            failures.append(kind)
            failure_reasons[kind] = '实体查询超时，未取得可用结果'
    decision = select_status(raw, responses, failures=failures)
    decision.evidence['failedReasons'].update(failure_reasons)
    development = False
    malicious = malicious_evidence(decision)
    diag.event('mail.analyzed', notice=diag.opaque(notice['id']), development_sample=development,
               malicious=malicious, errors=len(decision.evidence['failedQueries']))
    assessment = evidence_summary(event, decision, development=development)
    event['assessment'] = {'text': assessment.text, 'details': assessment.details, 'evidence': decision.evidence,
                           'malicious': malicious, 'purpose': 'development_mail_loop' if development else 'followup'}
    for kind in ENTITY_TYPES:
        data = decision.evidence['entities'].get(kind, {})
        diag.event('mail.evidence', entity_type=kind, success=kind not in decision.evidence['failedQueries'],
                   items=data.get('count'), notice=diag.opaque(notice['id']))
    recorder = d.operation_recorder.get()
    if recorder:
        async def explain(_):
            return None, {'malicious': malicious, 'failed_entities': decision.evidence['failedQueries'],
                          'development_sample': development, 'writes': 0}, assessment
        await recorder.call('说明分析结论与发信原因', {'event': event['id']}, explain)
    # Recheck current XDR evidence: an older model opinion must not hide a new
    # malicious finding or incomplete query returned immediately before sending.
    if decision.target in (40, 60):
        reason = '分析未发现需要责任人继续处置的事项；不发送通知，不自动修改 XDR 状态。' + assessment.text
        await write("UPDATE monitor_mail_notices SET state='skipped',event=?,error=?,updated_at=? WHERE id=? AND state='queued'",
                    (encode(event), reason, d.stamp(), notice['id']))
        return 'skipped'
    event['notified_end_time'] = raw.get('endTime')
    advice = assessment.text + '\n' + assessment.details
    if event.get('investigation'):
        investigation = event['investigation']
        advice += ('\n智能体调查参考（不等于已处置）：' + str(investigation.get('reason', '待续查'))[:1200]
                   + '\n调查记录状态：' + str(investigation.get('state', 'pending'))
                   + '\n证据引用：' + ', '.join(investigation.get('evidence_ids', [])))
    event['analysis'] = advice
    event['notified_status'] = raw.get('dealStatus')
    event['notified_host'] = raw.get('hostIp')
    subject = re.sub(r'[\r\n]', ' ', f"安全告警处理通知 · {event['id']} · {event['name']}")[:240]
    body = (f"{config['responsible_name'] or '责任人'}：\n请核查以下安全告警并处置。\n\n"
            f"事件编号：{event['id']}\n事件名称：{event['name']}\n主机：{event['host'] or '未知'}\n"
            f"最近发生时间（XDR）：{raw.get('endTime', '未知')}\n风险：{event['reason']}\n分析参考：{advice}\n\n"
            "请核查并处理这条告警。完成后用自己的话说明处理结果和措施；可以回复本邮件，也可以新写邮件提供上述事件编号。\n"
            "Flocks 会在下一轮监测中读取反馈，核对后标记事件状态并回查。\n"
            f"通知编号：{notice['id']}")
    async with lock(policy.owner):
        await authorized(policy, notice['revision'])
        # Commit the intent before network I/O. Unknown outcomes never auto-resend.
        async with connection() as db:
            cur = await db.execute("UPDATE monitor_mail_notices SET subject=?,body=?,event=?,state='sending',session_id=?,updated_at=? WHERE id=? AND state='queued'",
                                   (subject, body, encode(event), session_id, d.stamp(), notice['id']))
            if cur.rowcount != 1:
                return 'already_attempted'
        notice.update(subject=subject, body=body)
        async def send_once(_):
            state, error = 'send_unknown', '发件结果未知，请核对；不会自动重发'
            try:
                with diag.span('mail.send', notice=diag.opaque(notice['id'])):
                    if await asyncio.wait_for(transport.send(notice, session_id), timeout=45):
                        state, error = 'sent', None
            except transport.TransportNotReady as exc:
                # Proven preflight failure before SMTP: safe to retry later.
                state, error = 'queued', str(exc)
            finally:
                diag.event('mail.result', notice=diag.opaque(notice['id']), mail_state=state, failure=state!='sent', reason='unavailable' if state=='queued' else 'send_unknown' if state!='sent' else None)
                await write("UPDATE monitor_mail_notices SET state=?,error=?,updated_at=?,sent_at=CASE WHEN ?='sent' THEN COALESCE(sent_at,?) ELSE sent_at END WHERE id=?", (state, error, d.stamp(), state, d.stamp(), notice['id']))
            return state, {'state': state, 'reason': error}, Summary(error or '邮件服务已接受这一封通知，等待责任人回信；当前没有修改 XDR 状态。', success=state == 'sent')
        state = (await recorder.call('发送告警通知', {'notice': notice['id'], 'event': event['id']}, send_once)
                 if recorder else (await send_once(None))[0])
    return state


async def notify_batch(policy, session_id, observed, recorder, adapter_factory=MailAdapter):
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return {'sent': 0, 'pending': 0}
    await queue_notices(policy, observed, session_id)
    pending = await rows("SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND state='queued' ORDER BY created_at LIMIT 20", (policy.owner, policy.project))
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
        if notice['revision'] != config['revision']:
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


async def mark_item(policy, item, session_id, adapter_factory=MailAdapter):
    notice = (await rows('SELECT * FROM monitor_mail_notices WHERE id=? AND owner=? AND project=?', (item['notice_id'], policy.owner, policy.project)))[0]
    async with d._locks.setdefault((policy.owner, notice['event_key']), asyncio.Lock()):
        await authorized(policy, notice['revision'])
        event = json.loads(notice['event'])
        # The current observed object and device must still belong to this installation.
        actual, _ = await d.target(policy.owner, event['key'])
        if actual != policy:
            raise ContractError('监测安装已变化')
        adapter = adapter_factory(policy, session_id, notice['revision'])
        previous = await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND id=?', (policy.owner, policy.scope, item['id']))
        if previous:
            with diag.span('mail.readback', notice=diag.opaque(notice['id'])):
                current = await d.read_status(adapter, event)
            state = 'verified' if d.matches_status(item['target'], current) else 'mismatch'
            await d.finish(policy.owner, item['id'], state, current, defer_exports=True)
            return state
        if event.get('development_sample') is True:
            raise ValueError('已退出联调模式，旧测试回信不能发起新的状态写入')
        unresolved = await rows("SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? AND status IN ('writing','pending','mismatch')",
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
                current = await d.read_status(adapter, event)
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
    result = {'processed': 0, 'verified': 0, 'pending': 0, 'errors': []}
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
                    await write("UPDATE monitor_mail_replies SET state='needs_review',error=?,updated_at=? WHERE id=?", ('解读或事件核验不明确，请人工核对', d.stamp(), reply['id']))
                except Exception as exc:
                    success = False
                    diag.event('mail.result', failure=True, reply=diag.opaque(reply['id']), mail_state='pending', reason='model_failed', error_type=type(exc).__name__)
                    await write('UPDATE monitor_mail_replies SET error=?,updated_at=? WHERE id=?', ('邮件处理服务暂不可用，下轮重试', d.stamp(), reply['id']))
                data = (await rows('SELECT state,error FROM monitor_mail_replies WHERE id=?', (reply['id'],)))[0]
                if not success:
                    result['errors'].append('责任人回信处理失败：' + data['error'])
                items = await rows('SELECT i.target,i.state,i.reason,n.event FROM monitor_mail_items i JOIN monitor_mail_notices n ON n.id=i.notice_id WHERE i.reply_id=?', (reply['id'],))
                details = '\n'.join(f"{event_label(json.loads(item['event']))}；反馈依据：{label(item['reason'], limit=500)}；目标状态：{STATUS_LABELS.get(item['target'], '未知')}；结果：{STATE_LABELS.get(item['state'], item['state'])}。" for item in items[:20])
                return data, data, Summary(f"已读取责任人回信：{STATE_LABELS.get(data['state'], data['state'])}。{data['error'] or ''}" + ('已重新查询 XDR 确认目标状态。' if data['state'] == 'verified' else '未确认前保留待跟进，不把收到回信直接当作闭环。'), details, success=success)
            data = await recorder.call('解读回信并跟进状态', {'reply': reply['id']}, perform)
            result['processed'] += 1
            result['verified' if data['state'] == 'verified' else 'pending'] += 1
    return result


async def process_reply(policy, reply, session_id, adapter_factory, interpreter, validator):
    payload = json.loads(reply['payload'])
    config = await authorized(policy, (await settings(policy.owner))['revision'])
    if transport.REQUIRE_AUTHENTICATED_FEEDBACK and payload.get('authenticated_sender') is not True:
        raise ValueError('回信身份未核验，不能在严格模式下自动处置')
    notices = await rows("SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND mailbox=? AND recipient=? AND state IN ('sent','send_unknown','sending') ORDER BY created_at DESC",
                        (policy.owner, policy.project, reply['mailbox'], reply['sender']))
    if reply['state'] == 'pending':
        # Explicit identifiers and transport references narrow large histories before the LLM.
        hints = ' '.join(str(payload.get(k) or '') for k in ('text', 'subject', 'reply_to_id', 'thread_id', 'references'))
        exact = [n for n in notices if n['message_id'] in hints or n['id'] in hints or json.loads(n['event'])['id'] in hints]
        if exact:
            notices = exact
        if not notices:
            raise ValueError('没有对应通知')
        candidates = [{'notice_id': n['id'], 'message_id': n['message_id'], 'event': json.loads(n['event'])} for n in notices]
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
        async with connection() as db:
            for notice, item in accepted:
                if notice['revision'] != config['revision']:
                    raise ValueError('通知配置已变化')
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
        if item['state'] in ('verified', 'needs_review', 'failed'):
            continue
        try:
            with diag.span('mail.mark', notice=diag.opaque(item['notice_id']), reply=diag.opaque(reply['id'])):
                state = await mark_item(policy, item, session_id, adapter_factory)
            diag.event('mail.result', mail_state=state, notice=diag.opaque(item['notice_id']), failure=state!='verified')
            error = None
        except ValueError:
            state, error = 'needs_review', '事件、配置或处理依据变化，需要人工核对'
        await write('UPDATE monitor_mail_items SET state=?,error=?,updated_at=? WHERE id=?', (state, error, d.stamp(), item['id']))
    states = await rows('SELECT state FROM monitor_mail_items WHERE reply_id=?', (reply['id'],))
    state = ('interpreted' if any(x['state'] not in ('verified', 'needs_review', 'failed') for x in states)
             else 'verified' if states and all(x['state'] == 'verified' for x in states) else 'needs_review')
    await write('UPDATE monitor_mail_replies SET state=?,updated_at=? WHERE id=?', (state, d.stamp(), reply['id']))


async def history(owner, limit=100, offset=0, tab=None):
    config = await settings(owner)
    project = config['project']
    notices = await rows("SELECT * FROM monitor_mail_notices WHERE owner=? AND project=? AND state='sent' ORDER BY COALESCE(sent_at,updated_at) DESC,id DESC LIMIT ? OFFSET ?", (owner, project, limit + 1, offset)) if tab != 'received' else []
    replies = await rows('SELECT * FROM monitor_mail_replies WHERE owner=? AND project=? ORDER BY received_at DESC,sequence DESC LIMIT ? OFFSET ?', (owner, project, limit + 1, offset)) if tab != 'sent' else []
    has_more = len(notices) > limit or len(replies) > limit
    notices, replies = notices[:limit], replies[:limit]
    for n in notices:
        n['sent_at'] = n['sent_at'] or n['updated_at']
        n['event'] = json.loads(n['event'])
        n['items'] = await rows('SELECT i.*,r.payload AS reply_payload FROM monitor_mail_items i JOIN monitor_mail_replies r ON r.id=i.reply_id WHERE i.notice_id=? AND i.owner=? ORDER BY i.created_at LIMIT 100', (n['id'], owner))
        for item in n['items']:
            item['reply_excerpt'] = json.loads(item.pop('reply_payload'))['text'][:2000]
    for r in replies:
        r['payload'] = json.loads(r['payload'])
        r['result'] = json.loads(r['result']) if r['result'] else None
        r['targets'] = await rows('SELECT n.event,i.state,i.target FROM monitor_mail_items i JOIN monitor_mail_notices n ON n.id=i.notice_id WHERE i.reply_id=? AND i.owner=?', (r['id'], owner))
        for target in r['targets']:
            event = json.loads(target.pop('event'))
            target.update(event_id=event['id'], name=event['name'])
    counts = await rows('SELECT state,COUNT(*) AS count FROM monitor_mail_notices WHERE owner=? AND project=? GROUP BY state', (owner, project))
    reply_counts = await rows('SELECT state,COUNT(*) AS count FROM monitor_mail_replies WHERE owner=? AND project=? GROUP BY state', (owner, project))
    unparsed = await rows('SELECT COUNT(*) AS count FROM monitor_mail_unparsed WHERE mailbox=?', (config['mailbox'],))
    return {'settings': {**config, 'recipient_email': config['recipient']}, 'notices': notices, 'replies': replies,
            'sender_verification_required': transport.REQUIRE_AUTHENTICATED_FEEDBACK,
            'unparsed_count': unparsed[0]['count'],
            'counts': {x['state']: x['count'] for x in counts}, 'reply_counts': {x['state']: x['count'] for x in reply_counts},
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
        result['investigation_engine'] = policy.investigation_engine
        result['round_timeout_seconds'] = policy.timeout_seconds
        investigations = await rows('SELECT state,COUNT(*) AS count FROM monitor_investigations WHERE owner=? AND project=? GROUP BY state', (owner, config['project']))
        result['investigations'] = {r['state']: r['count'] for r in investigations if r['state'] in {'pending', 'ready', 'needs_review'}}
    for name, table in (('notices', 'monitor_mail_notices'), ('replies', 'monitor_mail_replies')):
        counts = await rows(f'SELECT state,COUNT(*) AS count FROM {table} WHERE owner=? AND project=? GROUP BY state', (owner, config['project']))
        result[name] = {r['state']: r['count'] for r in counts if r['state'] in diag._ENUMS['mail_state']}
    result['unparsed_messages'] = (await rows('SELECT COUNT(*) AS count FROM monitor_mail_unparsed WHERE mailbox=?', (config['mailbox'],)))[0]['count']
    return result


async def recover():
    await write("UPDATE monitor_mail_notices SET state='send_unknown',error='服务重启，发件结果待核对，不自动重发' WHERE state='sending'")
    await write("UPDATE monitor_mail_replies SET state='needs_review',error='普通邮件转交中断，请人工核对' WHERE state='forwarding'")
