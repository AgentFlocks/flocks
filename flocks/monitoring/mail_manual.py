"""Explicit human fallback on a single notice; mail delivery is never replayed.

Only failed delivery or absence of a reply permits a new human decision. A
completion is durable before XDR I/O and becomes handled only after readback.
"""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flocks.session.interaction_policy import require_interactive
from . import disposition as d, diagnostics as diag
from .adapter import ContractError
from .automatic import read_event
from .followup import event_version, is_monitoring_ended
from .models import COMPONENT_ID, MonitoringPolicy
from .store import connection, rows, write, encode

_UNSET = object()


class ManualMailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    state: Literal['handled', 'ignored', 'unhandled']
    notes: str = Field(default='', max_length=2000)

    @field_validator('notes')
    @classmethod
    def clean_notes(cls, value):
        return value.strip()


def explicit_reference(notice, payload):
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    hints = ' '.join(str(payload.get(k) or '') for k in ('text', 'subject', 'reply_to_id', 'thread_id', 'references'))
    return (any(mid in hints for mid in [notice['message_id'], *notice.get('delivery_message_ids', [])] if mid) or notice['id'] in hints or
            bool(re.search(r'(?<![\w-])' + re.escape(event['id']) + r'(?![\w-])', hints)))


async def delivery_references(notices):
    """Retain reply association to every explicit resend, including old mail."""
    by_id = {n['id']: n for n in notices}
    identifiers = list(by_id)
    for index in range(0, len(identifiers), 200):
        batch = identifiers[index:index+200]
        marks = ','.join('?' for _ in batch)
        for attempt in await rows(f'SELECT owner,project,notice_id,message_id FROM monitor_mail_retries WHERE notice_id IN ({marks}) AND message_id IS NOT NULL', batch):
            notice = by_id[attempt['notice_id']]
            if (not notice.get('owner') or notice['owner'] == attempt['owner']) and (not notice.get('project') or notice['project'] == attempt['project']):
                notice.setdefault('delivery_message_ids', []).append(attempt['message_id'])
    return notices


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
    peers = await rows('SELECT id,owner,project,event,message_id FROM monitor_mail_notices WHERE owner=? AND project=? AND mailbox=? AND recipient=?',
                       (notice['owner'], notice['project'], notice['mailbox'], notice['recipient']))
    await delivery_references(peers)
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
    found = await rows('SELECT m.*,d.status,d.error,d.updated_at,d.target_status FROM monitor_mail_manual m '
                       'LEFT JOIN monitor_dispositions d ON d.id=m.id AND d.owner=m.owner AND d.scope=? '
                       'WHERE m.owner=? AND m.notice_id=? ORDER BY m.created_at DESC,m.rowid DESC LIMIT 1',
                       (COMPONENT_ID, notice['owner'], notice['id']))
    return found[0] if found else None


async def completion_claimed(owner, notice_id):
    return bool(await rows("SELECT 1 FROM monitor_mail_manual m JOIN monitor_dispositions d "
                           "ON d.id=m.id AND d.owner=m.owner AND d.scope=? "
                           "WHERE m.owner=? AND m.notice_id=? AND m.state IN ('handled','ignored') "
                           "AND d.status IN ('queued','writing','pending','mismatch','verified') LIMIT 1",
                           (COMPONENT_ID, owner, notice_id)))


async def snapshot(notice, *, previous=_UNSET, reply_present=None, monitoring_ended=None, source_closed=None):
    if previous is _UNSET:
        previous = await latest(notice)
    result = {'available': False, 'reason': '', 'state': None, 'request_id': None, 'error': None, 'updated_at': None, 'notes': '', 'target_status': None}
    lifecycle = []
    if type(monitoring_ended) is not bool or type(source_closed) is not bool:
        lifecycle = await rows('SELECT state FROM monitor_event_followup WHERE owner=? AND scope=? AND project=? AND event_key=?',
                               (notice['owner'], notice['scope'], notice['project'], notice['event_key']))
    state = lifecycle[0]['state'] if lifecycle else None
    ended = monitoring_ended if type(monitoring_ended) is bool else state == 'monitoring_ended'
    closed = source_closed if type(source_closed) is bool else state in ('verified', 'external_closed')
    result['source_closed'] = closed
    if previous:
        state = ('unhandled' if previous['state'] == 'unhandled' else
                 previous['state'] if previous['status'] == 'verified' else
                 'failed' if previous['status'] == 'failed' else 'pending')
        result.update(state=state, request_id=previous['id'], error=previous['error'],
                      updated_at=previous['updated_at'] or previous['created_at'], notes=previous.get('notes', ''),
                      target_status=previous.get('target_status'))
        if not ended and state in ('handled', 'ignored', 'pending'):
            result['reason'] = ('已忽略并经 XDR 回查确认' if state == 'ignored' else '已处置并经 XDR 回查确认') if state in ('handled', 'ignored') else '已保存处理反馈，等待下一轮写回并回查' if previous['status'] == 'queued' else '处置已提交，等待回查；不会重复写回'
            return result
    if ended:
        result.update(monitoring_ended=True, reason='XDR 本轮未返回此事件，事件未再检出，已停止跟进；历史邮件和处置审计保留，不再提交状态标记')
        return result
    if closed:
        result['reason'] = 'XDR 已确认处置状态，不再重复通知或标记'
        return result
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    if event.get('development_sample'):
        result['reason'] = '历史联调通知不支持正式处置'
    elif (await has_reply(notice) if reply_present is None else reply_present):
        result['reason'] = '已收到回信，由程序自动解读并跟进'
    elif notice['state'] in ('sent', 'blocked', 'send_unknown', 'queued', 'failed'):
        result.update(available=True)
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
                if existing[0]['notice_id'] != notice_id or existing[0]['state'] != request.state or existing[0].get('notes', '') != request.notes:
                    raise ValueError('请求标识已用于其他操作')
                return await snapshot(notice)
            eligibility = await snapshot(notice)
            if not eligibility['available']:
                raise ValueError(eligibility['reason'])
            stamp = d.stamp()
            target = {'handled': 40, 'ignored': 60}.get(request.state)
            comment = f'Flocks 用户人工标记{"忽略" if target == 60 else "已处置"}（通知 {notice_id}）' + (f'；备注：{request.notes}' if request.notes else '')
            comment = comment[:2048]  # XDR contract limit; the full notes remain in the local audit.
            async with connection() as db:
                await db.execute('BEGIN IMMEDIATE')
                unresolved = await db.execute("SELECT 1 FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? "
                                              "AND status IN ('queued','writing','pending','mismatch') LIMIT 1",
                                              (owner, COMPONENT_ID, notice['event_key']))
                if await unresolved.fetchone():
                    raise ValueError('此告警已有处置待回查，不能再次提交')
                await db.execute('INSERT INTO monitor_mail_manual(id,notice_id,owner,project,state,created_at,notes) VALUES(?,?,?,?,?,?,?)',
                                 (request_id, notice_id, owner, policy.project, request.state, stamp, request.notes))
                if target is not None:
                    await db.execute('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at,mode,target_status,project,decision) '
                                     'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (request_id, owner, COMPONENT_ID, notice['event_key'], comment, 'queued', stamp, stamp,
                                      'mail_manual', target, policy.project, encode({'notice': notice_id, 'actor': owner, 'notes': request.notes, 'policy': policy.model_dump(mode='json'), 'event_version': event_version(event)})))
        if request.state == 'unhandled':
            return await snapshot(notice)
        return await snapshot(notice)


async def _apply_queued(policy, candidate, session_id, adapter_factory):
    """Apply a durable prior-round human feedback; unknown writes only recheck."""
    from .mailflow import lock
    owner, request_id, notice_id = policy.owner, candidate['id'], candidate['notice_id']
    notice, actual, event = await bound_notice(owner, notice_id)
    record = (await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND id=?', (owner, request_id)))[0]
    if await is_monitoring_ended(owner, policy.project, notice['event_key'], scope=policy.scope):
        return {**record, 'monitoring_ended': True}
    if record['status'] != 'queued':
        return record
    if actual != policy or json.loads(record['decision']).get('policy') != policy.model_dump(mode='json'):
        await d.finish(owner, request_id, 'failed', error='监测配置已变化，旧人工反馈未写回', defer_exports=True)
        return {'status': 'failed'}
    comment = record['comment']
    target = record['target_status']
    if target not in {40, 60}:
        raise ContractError('人工处置目标状态不支持')
    attempted = False
    async with diag.trace_scope(owner, COMPONENT_ID, request_id):
        try:
            await write("UPDATE monitor_dispositions SET status='writing',session_id=?,updated_at=? WHERE owner=? AND id=? AND status='queued'", (session_id, d.stamp(), owner, request_id))
            adapter = adapter_factory(policy, session_id)
            raw = await read_event(adapter, event)
            current = raw.get('dealStatus')
            if type(current) is not int or current not in d.LIST_RESPONSE_STATUSES:
                raise ContractError('XDR 当前处置状态无法识别')
            if raw.get('endTime') != event.get('notified_end_time', event.get('endTime')) or (raw.get('hostIp') or '') != (event.get('notified_host', event.get('host')) or ''):
                raise ContractError('通知后事件活动变化，旧反馈不能关闭新活动')
            from .mailflow import feedback_version_matches
            if not feedback_version_matches(event, raw):
                raise ContractError('事件活动版本与原通知不一致，旧反馈未覆盖新活动')
            if current == target:
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
                    if actual != policy or not await _active_policy(policy):
                        raise ContractError('监测设备配置已变化，本次未写回状态')
                    attempted = True
                    with diag.span('mail.manual.mark', notice=diag.opaque(notice_id)):
                        await d.operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']],
                            'deal_status': target, 'deal_comment': comment}, '人工处理：将原告警标记为' + ('忽略' if target == 60 else '已处置'))
                checked = await read_event(adapter, event)
                from .mailflow import feedback_version_matches
                if not feedback_version_matches(event, checked):
                    raise ContractError('写后回查出现新活动，旧反馈未确认闭环')
                current = checked.get('dealStatus')
                await d.finish(owner, request_id, 'verified' if current == target else 'mismatch', current, defer_exports=True)
        except BaseException as exc:
            error = str(exc) if isinstance(exc, ContractError) else '处置未完成，请回查 XDR 状态；不会重复写入'
            await d.finish(owner, request_id, 'pending' if attempted else 'failed', error=error, defer_exports=True)
            if not isinstance(exc, Exception):
                raise
    return (await rows('SELECT status,error FROM monitor_dispositions WHERE owner=? AND id=?', (owner, request_id)))[0]


async def _readback_manual(owner, record_id, adapter_factory, *, session_id=None):
    record = (await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND id=?', (owner, record_id)))[0]
    links = await rows('SELECT notice_id FROM monitor_mail_manual WHERE owner=? AND project=? AND id=?', (owner, record['project'], record_id))
    if not links:
        raise ContractError('未找到原人工处置通知，保留待回查')
    notice_id = links[0]['notice_id']
    _, policy, event = await bound_notice(owner, notice_id)
    if await is_monitoring_ended(owner, policy.project, event['key'], scope=policy.scope):
        return {**record, 'monitoring_ended': True}
    adapter = adapter_factory(policy, session_id or record.get('session_id'))
    raw = await read_event(adapter, event)
    from .mailflow import feedback_version_matches
    state = ('verified' if raw.get('dealStatus') == record['target_status'] else 'mismatch') if feedback_version_matches(event, raw) else 'pending'
    await d.finish(owner, record_id, state, raw.get('dealStatus'),
                   error=None if state == 'verified' else '原事件版本或目标状态尚未回查确认；不重复写入', defer_exports=True)
    return {'status': state, 'error': None if state == 'verified' else '待回查'}


async def recheck(owner, notice_id, adapter_factory=d.DispositionAdapter):
    await require_interactive()
    notice, policy, _ = await bound_notice(owner, notice_id)
    previous = await latest(notice)
    if not previous or previous['state'] not in ('handled', 'ignored'):
        raise ValueError('尚无已提交的人工处置')
    if previous['project'] != policy.project:
        raise ValueError('处置记录不属于当前监测项目')
    if previous['status'] == 'queued':
        raise ValueError('处理反馈已保存，等待下一轮监测写回')
    await _readback_manual(owner, previous['id'], adapter_factory)
    return await snapshot(notice)


async def _active_policy(policy):
    """Readbacks run only as part of an active, currently bound monitor round."""
    from flocks.hub import local
    entry = local.get_record('component', policy.scope)
    if not entry or not entry.enabled:
        return False
    installed = await rows(
        "SELECT i.policy FROM monitor_installations i JOIN task_schedulers s ON s.id=i.scheduler_id "
        "WHERE i.owner=? AND i.scope=? AND i.project=? AND i.installed=1 AND i.ready=1 AND s.status='active'",
        (policy.owner, policy.scope, policy.project))
    return bool(installed and MonitoringPolicy.model_validate_json(installed[0]['policy']) == policy)


async def recheck_pending(policy, *, limit=10, timeout_seconds=60, adapter_factory=None, session_id=None, before=None):
    """Consume queued prior human feedback and verify existing uncertain writes.

    No reply/delivery status creates work here: only a durable prior human
    completion intent can be selected. Both the total round budget and each
    readback are bounded, leaving failed/unknown items for a later round.
    """
    result = {'checked': 0, 'verified': 0, 'pending': 0, 'errors': []}
    if not await _active_policy(policy):
        return result
    if adapter_factory is None:
        from .followup import FollowupAdapter
        adapter_factory = FollowupAdapter
    round_cutoff = before or d.stamp()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    candidates = await rows(
        "SELECT m.id,m.notice_id,n.event_key,d.status FROM monitor_mail_manual m "
        "JOIN monitor_mail_notices n ON n.id=m.notice_id AND n.owner=m.owner AND n.project=m.project "
        "JOIN monitor_dispositions d ON d.id=m.id AND d.owner=m.owner AND d.project=m.project "
        "AND d.event_key=n.event_key AND d.scope=n.scope "
        "WHERE m.owner=? AND m.project=? AND n.scope=? AND m.state IN ('handled','ignored') AND d.mode='mail_manual' "
        "AND ((d.status='queued' AND m.created_at<?) OR d.status IN ('pending','mismatch') OR (d.status='writing' AND d.updated_at<=?)) "
        "ORDER BY d.updated_at,m.created_at,m.id LIMIT ?",
        (policy.owner, policy.project, policy.scope, round_cutoff, cutoff, max(1, min(int(limit), 50))))
    deadline = asyncio.get_running_loop().time() + max(0, min(float(timeout_seconds), 120))
    warning = '人工处置状态回查暂未完成；保留待回查，后续监测轮次继续核验，不会重复写回。'
    for candidate in candidates:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or not await _active_policy(policy):
            break
        if await is_monitoring_ended(policy.owner, policy.project, candidate['event_key'], scope=policy.scope):
            continue  # Preserve an uncertain write audit; absence is not confirmation.
        result['checked'] += 1
        try:
            async def readback():
                _, actual, _ = await bound_notice(policy.owner, candidate['notice_id'])
                if actual != policy:
                    raise ContractError('监测绑定已变化，等待后续轮次核验')
                if candidate['status'] == 'queued':
                    if session_id is None:
                        return {'status': 'pending'}
                    async with d._locks.setdefault((policy.owner, candidate['event_key']), asyncio.Lock()):
                        return await _apply_queued(policy, candidate, session_id, adapter_factory)
                return await _readback_manual(policy.owner, candidate['id'], adapter_factory, session_id=session_id)
            updated = await asyncio.wait_for(readback(), timeout=min(15, remaining))
            if updated['status'] == 'verified':
                result['verified'] += 1
            else:
                result['pending'] += 1
                if updated.get('error'):
                    result['errors'].append(warning)
        except Exception:
            # Cancellation propagates to pause/stop the round. A per-item error
            # must not prevent another incident's readback or affect its state.
            # Rotate unsuccessful entries so a removed device cannot starve
            # later items; never overwrite a result already verified elsewhere.
            await write("UPDATE monitor_dispositions SET error=?,updated_at=? WHERE owner=? AND project=? AND id=? "
                        "AND status IN ('writing','pending','mismatch')",
                        (warning, d.stamp(), policy.owner, policy.project, candidate['id']))
            result['pending'] += 1
            result['errors'].append(warning)
    result['errors'] = list(dict.fromkeys(result['errors']))
    return result
