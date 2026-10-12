"""Explicit retry of a notice known not to have been delivered.

Reuse the original notice, recipient, Message-ID and prepared content. Persist
request identity before I/O so a repeated HTTP request never repeats delivery.
"""
import asyncio
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from flocks.session.interaction_policy import require_interactive
from flocks.session.session import Session
from . import disposition as d, diagnostics as diag, mail_manual as manual
from .adapter import ContractError
from .models import COMPONENT_ID
from .store import connection, rows, write


class RetryMailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID


async def snapshot(notice, *, reply_present=None, manual=None):
    from . import mail_manual
    if manual is None:
        manual = await mail_manual.snapshot(notice, reply_present=reply_present)
    result = {'available': False, 'reason': ''}
    event = notice['event'] if isinstance(notice['event'], dict) else json.loads(notice['event'])
    if event.get('development_sample'):
        result['reason'] = '历史联调通知不再发送'
    elif manual['state'] in ('handled', 'pending'):
        result['reason'] = '已有处置反馈，不再重发通知'
    elif (await mail_manual.has_reply(notice) if reply_present is None else reply_present):
        result['reason'] = '已收到反馈，等待程序处理，不再重发通知'
    elif notice['state'] == 'sent':
        result['reason'] = '已发送，告警保留未处置，等待责任人反馈'
    elif notice['state'] in ('sending', 'send_unknown'):
        result['reason'] = '投递正在进行或结果未知，为避免重复邮件暂不重发；告警保留待处理'
    elif notice['state'] in ('queued', 'failed') and notice.get('error'):
        result.update(available=True, reason='发送未成功，可重试原告警通知；不修改处置状态')
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
    return {'state': notice['state'], 'error': notice['error'], 'retry': await snapshot(notice)}


async def retry(owner, notice_id, request: RetryMailRequest, adapter_factory=None):
    await require_interactive()
    from . import mailflow
    notice, _, _ = await manual.bound_notice(owner, notice_id)
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
                await db.execute('INSERT INTO monitor_mail_retries VALUES(?,?,?,?,?,?,?,?)',
                    (request_id, notice_id, owner, policy.project, 'running', notice['state'], d.stamp(), d.stamp()))
        async with diag.trace_scope(owner, COMPONENT_ID, request_id):
            try:
                session_id = notice['session_id']
                if not session_id:
                    session = await Session.create(project_id=policy.project, directory=policy.directory,
                        title='安全运营监测 · 重试告警通知', owner_user_id=owner,
                        metadata={'monitorNotice': notice_id, 'monitorMailRetry': request_id})
                    session_id = session.id
                if notice['subject'] and notice['body']:
                    async with mailflow.lock(owner):
                        # A reply may arrive while opening the session. Recheck
                        # both the recipient binding and feedback before SMTP.
                        current, actual, _ = await manual.bound_notice(owner, notice_id)
                        if actual != policy:
                            raise ContractError('监测配置已变化，未发送')
                        await authorized(policy, notice['revision'], event['device'])
                        eligibility = await snapshot(current)
                        if not eligibility['available']:
                            raise ValueError(eligibility['reason'])
                        await write("UPDATE monitor_mail_notices SET state='sending',session_id=?,updated_at=? WHERE id=?",
                                    (session_id, d.stamp(), notice_id))
                        with diag.span('mail.retry', notice=diag.opaque(notice_id)):
                            await mailflow.deliver_notice(notice, session_id)
                else:
                    # Failure before content preparation: redo only read queries
                    # needed to prepare the single original notice, never mark XDR.
                    await write("UPDATE monitor_mail_notices SET state='queued' WHERE id=?", (notice_id,))
                    await asyncio.wait_for(mailflow.notify_one(policy, notice, session_id,
                        adapter_factory or mailflow.MailAdapter, manual_retry=True), timeout=120)
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
