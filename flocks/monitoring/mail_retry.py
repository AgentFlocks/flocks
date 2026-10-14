"""Explicit delivery or reminder for one canonical incident notification.

Keep the notice, recipient and prepared content. Accepted-mail reminders get a
new delivery Message-ID; every alias still identifies the original notice.
Persist request identity before I/O so HTTP replay never repeats delivery.
"""
import asyncio
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flocks.session.interaction_policy import require_interactive
from flocks.session.session import Session
from . import disposition as d, diagnostics as diag, mail_manual as manual
from .adapter import ContractError
from .models import COMPONENT_ID
from .store import connection, rows, write


class RetryMailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    expected_updated_at: str | None = Field(default=None, min_length=1, max_length=80)


async def snapshot(notice, *, reply_present=None, manual=None, monitoring_ended=None):
    from . import mail_manual
    if manual is None:
        manual = await mail_manual.snapshot(notice, reply_present=reply_present, monitoring_ended=monitoring_ended)
    result = {'available': False, 'reason': ''}
    from .followup import is_monitoring_ended
    ended = (monitoring_ended if type(monitoring_ended) is bool else
             manual.get('monitoring_ended') or await is_monitoring_ended(notice['owner'], notice['project'], notice['event_key'], scope=notice['scope']))
    if ended:
        result['reason'] = 'XDR 本轮未返回此事件，事件未再检出，已停止跟进，不再重发历史通知'
        return result
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    if event.get('development_sample'):
        result['reason'] = '历史联调通知不再发送'
    elif manual.get('source_closed') or manual['state'] in ('handled', 'ignored', 'pending'):
        result['reason'] = '已有处置反馈，不再重发通知'
    elif (await mail_manual.has_reply(notice) if reply_present is None else reply_present):
        result['reason'] = '已收到反馈，等待程序处理，不再重发通知'
    elif notice['state'] == 'sent':
        result.update(available=True)
    elif notice['state'] in ('sending', 'send_unknown'):
        result['reason'] = '投递正在进行或结果未知，为避免重复邮件暂不重发；告警保留待处理'
    elif notice['state'] in ('queued', 'blocked', 'failed'):
        result.update(available=True)
    else:
        result['reason'] = '仅发送失败的通知可以重试'
    return result


async def authorized(policy, revision, device):
    """An explicit click may retry while paused, never after ownership changes."""
    await require_interactive()
    from .mailflow import settings
    from .lifecycle import _owned_installation
    entry, _, actual = await _owned_installation(policy.owner)
    if not entry['ready'] or actual != policy:
        raise ValueError('监测配置已变化或接入未就绪，未发送')
    config = await settings(policy.owner, device)
    if not config['enabled'] or config['project'] != policy.project or config['revision'] != revision:
        raise ValueError('责任人配置已变化或邮件跟进已关闭，未发送')
    return config


async def response(owner, notice_id):
    found = await rows('SELECT * FROM monitor_mail_notices WHERE owner=? AND scope=? AND id=?',
                       (owner, COMPONENT_ID, notice_id))
    notice = found[0]
    return {'state': notice['state'], 'error': notice['error'], 'updated_at': notice['updated_at'], 'retry': await snapshot(notice)}


async def retry(owner, notice_id, request: RetryMailRequest, adapter_factory=None):
    await require_interactive()
    from . import mailflow
    notice, _, _ = await manual.bound_notice(owner, notice_id)
    expected_updated_at = request.expected_updated_at or notice['updated_at']
    request_id = str(request.request_id)
    # Same order as manual disposition; tagging handled and retrying cannot race.
    async with d._locks.setdefault((owner, notice['event_key']), asyncio.Lock()):
        async with mailflow.lock(owner):
            notice, policy, event = await manual.bound_notice(owner, notice_id)
            existing = await rows('SELECT * FROM monitor_mail_retries WHERE id=?', (request_id,))
            if existing:
                if existing[0]['owner'] != owner or existing[0]['notice_id'] != notice_id:
                    raise ValueError('请求标识已用于其他发信操作')
                return await response(owner, notice_id)
            if notice['updated_at'] != expected_updated_at:
                raise ValueError('通知状态已更新，请刷新后再发送')
            eligibility = await snapshot(notice)
            if not eligibility['available']:
                raise ValueError(eligibility['reason'])
            config = await authorized(policy, notice['revision'], event['device'])
            if config['recipient'] != notice['recipient'] or config['mailbox'] != notice['mailbox']:
                raise ValueError('原通知邮箱配置已变化，不能向另一责任人重发')
            async with connection() as db:
                await db.execute('BEGIN IMMEDIATE')
                active = await db.execute("SELECT 1 FROM monitor_mail_retries WHERE owner=? AND notice_id=? AND state='running'",
                                          (owner, notice_id))
                if await active.fetchone():
                    raise ValueError('该通知正在重试发送，请等待结果')
                delivery_id = (f'<flocks-monitor-{notice_id}-resend-{request_id}@flocks.local>'
                               if notice.get('sent_at') or notice['state'] == 'sent' else notice['message_id'])
                await db.execute('INSERT INTO monitor_mail_retries(id,notice_id,owner,project,state,mail_state,created_at,updated_at,message_id) VALUES(?,?,?,?,?,?,?,?,?)',
                    (request_id, notice_id, owner, policy.project, 'running', notice['state'], d.stamp(), d.stamp(), delivery_id))
        async with diag.trace_scope(owner, COMPONENT_ID, request_id):
            try:
                session_id = notice['session_id']
                if not session_id:
                    session = await Session.create(project_id=policy.project, directory=policy.directory,
                        title='安全运营监测 · 重试告警通知', owner_user_id=owner,
                        metadata={'monitorNotice': notice_id, 'monitorMailRetry': request_id})
                    session_id = session.id
                # All retries recheck the current incident and confirmed risk.
                # notify_one preserves prepared v2 content after that check;
                # a legacy unsupported notification cannot bypass the gate.
                await write("UPDATE monitor_mail_notices SET state='queued' WHERE id=?", (notice_id,))
                await asyncio.wait_for(mailflow.notify_one(policy, notice, session_id,
                    adapter_factory or mailflow.MailAdapter, manual_retry=True, delivery_message_id=delivery_id), timeout=120)
            except BaseException as exc:
                # An SMTP exception is already persisted as unknown by deliver;
                # do not relabel it as known failure. Unsent failures stay visible.
                error = str(exc) if isinstance(exc, (ContractError, ValueError)) else '重试尚未完成，告警保留待处理'
                await write("UPDATE monitor_mail_notices SET error=?,updated_at=? WHERE id=? AND state IN ('queued','failed')",
                            (error, d.stamp(), notice_id))
                if not isinstance(exc, Exception):
                    raise
            finally:
                await write("UPDATE monitor_mail_retries SET state='finished',mail_state=(SELECT state FROM monitor_mail_notices WHERE id=?),updated_at=? WHERE id=?",
                            (notice_id, d.stamp(), request_id))
        return await response(owner, notice_id)
