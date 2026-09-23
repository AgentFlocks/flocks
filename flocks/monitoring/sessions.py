"""Recoverable cross-database daily session reservation."""
import hashlib
from zoneinfo import ZoneInfo
from flocks.session.session import Session
from .store import connection


async def ensure_daily(policy, started):
    day = started.astimezone(ZoneInfo(policy.timezone)).date().isoformat()
    key = (policy.owner, policy.project, policy.scope, day)
    reserved = 'ses_monitor_' + hashlib.sha256('\0'.join(key).encode()).hexdigest()[:32]
    async with connection() as db:
        await db.execute('INSERT OR IGNORE INTO monitor_daily_sessions(owner,project,scope,business_date,session_id,timezone) VALUES(?,?,?,?,?,?)', (*key, reserved, policy.timezone))
        cur = await db.execute('SELECT session_id FROM monitor_daily_sessions WHERE owner=? AND project=? AND scope=? AND business_date=?', key)
        reserved = (await cur.fetchone())['session_id']
    session = await Session.ensure_daily_session(
        session_id=reserved, project_id=policy.project, directory=policy.directory,
        owner=policy.owner, scope=policy.scope, business_date=day,
        read_only_tools={"tool": policy.tool, "devices": policy.devices},
    )
    async with connection() as db:
        await db.execute("UPDATE monitor_daily_sessions SET state='ready' WHERE session_id=?", (reserved,))
    return session, day


async def assert_history_mutable(session_id):
    """Protect today's bound session while its scheduler is active."""
    from datetime import datetime, timezone
    from .store import rows
    records = await rows('SELECT d.business_date,d.timezone,s.status FROM monitor_daily_sessions d JOIN monitor_installations i ON i.owner=d.owner AND i.scope=d.scope JOIN task_schedulers s ON s.id=i.scheduler_id WHERE d.session_id=? AND i.installed=1', (session_id,))
    if records:
        item = records[0]
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(item['timezone'])).date().isoformat()
        running = await rows("SELECT id FROM monitor_attempts WHERE session_id=? AND status='running'", (session_id,))
        if running or (item['status'] == 'active' and item['business_date'] == today):
            raise RuntimeError('请先停用安全运营监测，再归档或删除当天会话')
