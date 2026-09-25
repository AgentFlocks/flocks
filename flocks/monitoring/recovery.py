"""Recover interrupted facts without fabricating completion."""
from datetime import datetime, timezone
from flocks.session.message import Message, ToolStateError
from .store import rows, write
from .reports import retry_exports


async def recover():
    from .mailflow import recover as recover_mail
    await recover_mail()
    stamp = datetime.now(timezone.utc).isoformat()
    for attempt in await rows("SELECT * FROM monitor_attempts WHERE status='running'"):
        for step in await rows("SELECT * FROM monitor_steps WHERE attempt_id=? AND status='running'", (attempt['id'],)):
            await Message.update_part(attempt['session_id'], step['message_id'], step['part_id'],
                                      state=ToolStateError(input={}, error='服务重启，步骤已中断', time={}).model_dump())
            message = await Message.get(attempt['session_id'], step['message_id'])
            if message:
                await Message.update(attempt['session_id'], step['message_id'], finish='error', time={**message.time, 'completed': int(datetime.now(timezone.utc).timestamp() * 1000)})
        await write("UPDATE monitor_steps SET status='failed',error='服务重启，步骤已中断',finished_at=? WHERE attempt_id=? AND status='running'", (stamp, attempt['id']))
        await write("UPDATE monitor_attempts SET status='failed',error='服务重启中断本轮，保留原尝试',finished_at=? WHERE id=?", (stamp, attempt['id']))
        await write("INSERT INTO monitor_reports(owner,scope,business_date,status) VALUES(?,?,?,'pending') ON CONFLICT(owner,scope,business_date) DO UPDATE SET status='pending'", (attempt['owner'], attempt['scope'], attempt['business_date']))
    from .rounds import finish_round
    for attempt in await rows("SELECT id FROM monitor_attempts WHERE status='failed' AND end_published=0 AND (summary IS NOT NULL OR error LIKE '服务重启%')"):
        await finish_round(attempt['id'])
    # Resume a persisted ending interrupted between reservation and publication.
    for attempt in await rows("SELECT id FROM monitor_attempts WHERE summary IS NOT NULL AND end_published=0 AND status='completed'"):
        await finish_round(attempt['id'])
    await retry_exports()
