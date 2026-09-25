"""Durable, idempotent end-of-round markers, independent of incident closure."""
from datetime import datetime, timezone
from flocks.session.message import Message, TextPart
from flocks.utils.id import Identifier
from .store import rows, write


async def finish_round(attempt_id, summary=None):
    from .runtime import emit_message, publish
    attempt = (await rows('SELECT * FROM monitor_attempts WHERE id=?', (attempt_id,)))[0]
    if attempt['end_published']:
        return
    if summary is None:
        summary = attempt['summary'] or ('本轮执行中断。' + (attempt['error'] or '') + '。已有证据与回信保留，不能据此判断无风险。')
    scheduler = await rows('SELECT s.status FROM task_schedulers s JOIN monitor_installations i ON i.scheduler_id=s.id WHERE i.owner=? AND i.scope=?', (attempt['owner'], attempt['scope']))
    next_step = ('监测继续运行，下一轮优先处理已收到的回信和未完成调查。' if scheduler and scheduler[0]['status'] == 'active'
                 else '监测当前未运行；请检查失败原因或配置，点击“检查接入并启动”后继续。')
    await write('UPDATE monitor_attempts SET summary=?,next_step=?,end_message_id=COALESCE(end_message_id,?) WHERE id=?',
                (summary, next_step, Identifier.ascending('message'), attempt_id))
    attempt = (await rows('SELECT * FROM monitor_attempts WHERE id=?', (attempt_id,)))[0]
    status = 'completed' if attempt['status'] in ('completed', 'partial') else 'failed'
    metadata = {'monitoringRoundEnd': True, 'roundStatus': status, 'roundId': attempt_id, 'nextStep': next_step}
    message = await Message.get(attempt['session_id'], attempt['end_message_id'])
    if message is None:
        anchor = await Message.get(attempt['session_id'], attempt['message_id'])
        await emit_message(attempt['session_id'], summary, message_id=attempt['end_message_id'],
                           parent_id=getattr(anchor, 'parentID', ''), agent='security-monitor', metadata=metadata)
    else:
        # Message creation and completion are separate writes. Repair both the
        # terminal marker and visible snapshot before replaying after a crash.
        parts = await Message.parts(message.id, attempt['session_id'])
        ending = next((part for part in parts if isinstance(part, TextPart)), None)
        if ending:
            await Message.update_part(attempt['session_id'], message.id, ending.id,
                                      text=summary, metadata=metadata)
        else:
            await Message.add_part(attempt['session_id'], message.id, TextPart(
                sessionID=attempt['session_id'], messageID=message.id, text=summary, metadata=metadata))
        message = await Message.update(attempt['session_id'], message.id, finish='stop',
            time={**message.time, 'completed': message.time.get('completed') or int(datetime.now(timezone.utc).timestamp() * 1000)})
        await publish('message.updated', {'sessionID': attempt['session_id'], 'info': message.model_dump(mode='json', by_alias=True)})
        for part in await Message.parts(message.id, attempt['session_id']):
            await publish('message.part.updated', {'sessionID': attempt['session_id'], 'part': part.model_dump(mode='json', by_alias=True)})
    await write('UPDATE monitor_attempts SET end_published=1 WHERE id=?', (attempt_id,))
