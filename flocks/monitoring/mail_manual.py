"""Explicit human fallback on a single notice; mail delivery is never replayed.

Only failed delivery or absence of a reply permits a new human decision. A
completion is durable before XDR I/O and becomes handled only after readback.
"""
import asyncio
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from flocks.session.interaction_policy import require_interactive
from flocks.session.session import Session
from . import disposition as d, diagnostics as diag
from .adapter import ContractError
from .automatic import read_event
from .models import COMPONENT_ID
from .store import connection, rows, write, encode

_UNSET = object()


class ManualMailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    state: Literal['handled', 'unhandled']


def explicit_reference(notice, payload):
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    hints = ' '.join(str(payload.get(k) or '') for k in ('text', 'subject', 'reply_to_id', 'thread_id', 'references'))
    return (notice['message_id'] in hints or notice['id'] in hints or
            bool(re.search(r'(?<![\w-])' + re.escape(event['id']) + r'(?![\w-])', hints)))


async def has_reply(notice):
    linked = await rows('SELECT 1 FROM monitor_mail_items WHERE owner=? AND notice_id=? LIMIT 1',
                        (notice['owner'], notice['id']))
    if linked:
        return True
    replies = await rows("SELECT id,payload FROM monitor_mail_replies WHERE owner=? AND project=? AND mailbox=? "
                         "AND sender=? AND state NOT IN ('unrelated','forwarding') AND received_at>=?",
                         (notice['owner'], notice['project'], notice['mailbox'], notice['recipient'], notice['created_at']))
    if not replies:
        return False
    peers = await rows('SELECT id,event,message_id FROM monitor_mail_notices WHERE owner=? AND project=? AND mailbox=? AND recipient=?',
                       (notice['owner'], notice['project'], notice['mailbox'], notice['recipient']))
    for reply in replies:
        resolved = await rows('SELECT notice_id FROM monitor_mail_items WHERE owner=? AND reply_id=?',
                              (notice['owner'], reply['id']))
        if resolved:
            if any(item['notice_id'] == notice['id'] for item in resolved):
                return True
            continue
        payload = json.loads(reply['payload'])
        # New ingress records preserve the candidate set before interpretation;
        # old records are resolved conservatively using the same references.
        candidates = payload.get('notice_ids')
        if isinstance(candidates, list):
            if notice['id'] in candidates:
                return True
            continue
        exact = [n['id'] for n in peers if explicit_reference(n, payload)]
        if not exact or notice['id'] in exact:
            return True
    return False


async def latest(notice):
    found = await rows('SELECT m.*,d.status,d.error,d.updated_at FROM monitor_mail_manual m '
                       'LEFT JOIN monitor_dispositions d ON d.id=m.id AND d.owner=m.owner AND d.scope=? '
                       'WHERE m.owner=? AND m.notice_id=? ORDER BY m.created_at DESC,m.rowid DESC LIMIT 1',
                       (COMPONENT_ID, notice['owner'], notice['id']))
    return found[0] if found else None


async def completion_claimed(owner, notice_id):
    return bool(await rows("SELECT 1 FROM monitor_mail_manual m JOIN monitor_dispositions d "
                           "ON d.id=m.id AND d.owner=m.owner AND d.scope=? "
                           "WHERE m.owner=? AND m.notice_id=? AND m.state='handled' "
                           "AND d.status IN ('writing','pending','mismatch','verified') LIMIT 1",
                           (COMPONENT_ID, owner, notice_id)))


async def snapshot(notice, *, previous=_UNSET, reply_present=None):
    if previous is _UNSET:
        previous = await latest(notice)
    result = {'available': False, 'reason': '', 'state': None, 'request_id': None, 'error': None, 'updated_at': None}
    if previous:
        state = ('unhandled' if previous['state'] == 'unhandled' else
                 'handled' if previous['status'] == 'verified' else
                 'failed' if previous['status'] == 'failed' else 'pending')
        result.update(state=state, request_id=previous['id'], error=previous['error'],
                      updated_at=previous['updated_at'] or previous['created_at'])
        if state in ('handled', 'pending'):
            result['reason'] = '已处置并经 XDR 回查确认' if state == 'handled' else '处置已提交，等待回查；不会重复写回'
            return result
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    if event.get('development_sample'):
        result['reason'] = '历史联调通知不支持正式处置'
    elif (await has_reply(notice) if reply_present is None else reply_present):
        result['reason'] = '已收到回信，由程序自动解读并跟进'
    elif notice['state'] == 'send_unknown' or notice['state'] in ('queued', 'failed') and notice.get('error'):
        result.update(available=True, reason='发信失败或投递结果未确认，可记录人工处理结果')
    elif notice['state'] == 'sent':
        result.update(available=True, reason='尚未收到对应回信，可记录人工处理结果')
    else:
        result['reason'] = '已收到回信，由程序自动解读并跟进' if notice['state'] == 'sent' else '仅发信失败或尚无回信时可人工处理'
    return result


async def bound_notice(owner, notice_id):
    found = await rows('SELECT n.* FROM monitor_mail_notices n JOIN monitor_installations i '
                       'ON i.owner=n.owner AND i.scope=n.scope AND i.project=n.project AND i.installed=1 '
                       'WHERE n.owner=? AND n.scope=? AND n.id=?', (owner, COMPONENT_ID, notice_id))
    if not found:
        raise FileNotFoundError('未找到当前监测项目的发信记录')
    notice = found[0]
    policy, event = await d.target(owner, notice['event_key'])
    original = json.loads(notice['event'])
    if (original['device'], original['id']) != (event['device'], event['id']):
        raise ValueError('通知与监测事件不一致，不能标记状态')
    return notice, policy, original


async def change(owner, notice_id, request: ManualMailRequest, adapter_factory=d.DispositionAdapter):
    await require_interactive()
    from .mailflow import lock
    notice, _, _ = await bound_notice(owner, notice_id)
    request_id = str(request.request_id)
    async with d._locks.setdefault((owner, notice['event_key']), asyncio.Lock()):
        # Same lock as sending/ingress: a sent notification or a newly persisted
        # reply cannot race the human decision. Release it after durable intent.
        async with lock(owner):
            notice, policy, event = await bound_notice(owner, notice_id)
            existing = await rows('SELECT * FROM monitor_mail_manual WHERE id=? AND owner=?', (request_id, owner))
            if existing:
                if existing[0]['notice_id'] != notice_id or existing[0]['state'] != request.state:
                    raise ValueError('请求标识已用于其他操作')
                return await snapshot(notice)
            eligibility = await snapshot(notice)
            if not eligibility['available']:
                raise ValueError(eligibility['reason'])
            stamp = d.stamp()
            comment = f'Flocks 用户人工标记已处置（通知 {notice_id}）'
            async with connection() as db:
                await db.execute('BEGIN IMMEDIATE')
                unresolved = await db.execute("SELECT 1 FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? "
                                              "AND status IN ('writing','pending','mismatch') LIMIT 1",
                                              (owner, COMPONENT_ID, notice['event_key']))
                if await unresolved.fetchone():
                    raise ValueError('此告警已有处置待回查，不能再次提交')
                await db.execute('INSERT INTO monitor_mail_manual VALUES(?,?,?,?,?,?)',
                                 (request_id, notice_id, owner, policy.project, request.state, stamp))
                if request.state == 'handled':
                    await db.execute('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at,mode,target_status,project,decision) '
                                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (request_id, owner, COMPONENT_ID, notice['event_key'], comment, 'writing', stamp, stamp,
                                      'mail_manual', 40, policy.project, encode({'notice': notice_id, 'actor': owner})))
        if request.state == 'unhandled':
            return await snapshot(notice)
        attempted = False
        async with diag.trace_scope(owner, COMPONENT_ID, request_id):
            try:
                session = await Session.create(project_id=policy.project, directory=policy.directory,
                    title='安全运营监测 · 发信人工处置', owner_user_id=owner,
                    metadata={'monitorDisposition': request_id, 'monitorNotice': notice_id})
                await write('UPDATE monitor_dispositions SET session_id=? WHERE owner=? AND id=?', (session.id, owner, request_id))
                from .runtime import emit_message
                from flocks.session.message import MessageRole
                await emit_message(session.id, f'人工确认事件 {event["id"]} 已处置；写回 XDR 状态 40 并回查。通知：{notice_id}', role=MessageRole.USER)
                adapter = adapter_factory(policy, session.id)
                raw = await read_event(adapter, event)
                current = raw.get('dealStatus')
                if type(current) is not int or current not in d.LIST_RESPONSE_STATUSES:
                    raise ContractError('XDR 当前处置状态无法识别')
                if current == 40:
                    await d.finish(owner, request_id, 'verified', current, defer_exports=True)
                else:
                    if current not in (0, 10, 30, 70):
                        raise ContractError('XDR 状态已变化，不能覆盖已忽略或挂起的告警')
                    # Never close new activity based on an older notification.
                    expected_end = event.get('notified_end_time', event.get('endTime'))
                    expected_host = event.get('notified_host', event.get('host'))
                    if expected_end is None:
                        raise ContractError('原告警缺少时间快照，无法确认是否出现新活动，本次未写回')
                    if raw.get('endTime') != expected_end:
                        raise ContractError('通知后告警出现新活动，本次未写回状态')
                    if (raw.get('hostIp') or '') != (expected_host or ''):
                        raise ContractError('通知后告警主机变化，本次未写回状态')
                    async with lock(owner):
                        _, actual, _ = await bound_notice(owner, notice_id)
                        if actual != policy:
                            raise ContractError('监测设备配置已变化，本次未写回状态')
                        attempted = True
                        with diag.span('mail.manual.mark', notice=diag.opaque(notice_id)):
                            await d.operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']],
                                'deal_status': 40, 'deal_comment': comment}, '人工处理：将原告警标记为已处置')
                    current = await d.read_status(adapter, event)
                    await d.finish(owner, request_id, 'verified' if current == 40 else 'mismatch', current, defer_exports=True)
            except BaseException as exc:
                error = str(exc) if isinstance(exc, ContractError) else '处置未完成，请回查 XDR 状态；不会重复写入'
                await d.finish(owner, request_id, 'pending' if attempted else 'failed', error=error, defer_exports=True)
                if not isinstance(exc, Exception):
                    raise
        return await snapshot(notice)


async def recheck(owner, notice_id, adapter_factory=d.DispositionAdapter):
    await require_interactive()
    notice, policy, _ = await bound_notice(owner, notice_id)
    previous = await latest(notice)
    if not previous or previous['state'] != 'handled':
        raise ValueError('尚无已提交的人工处置')
    if previous['project'] != policy.project:
        raise ValueError('处置记录不属于当前监测项目')
    await d.recheck(owner, previous['id'], adapter_factory, defer_exports=True)
    return await snapshot(notice)
