"""Recoverable cross-database daily session reservation."""
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flocks.session.session import Session
from .models import MonitoringPolicy
from .store import connection, rows


def _read_only_tools(policy):
    return {'tool': policy.tool, 'devices': list(policy.devices), 'device_tools': dict(policy.device_tools)}


async def _assert_current_policy(policy):
    saved = await rows('SELECT policy,project,installed FROM monitor_installations WHERE owner=? AND scope=?',
                       (policy.owner, policy.scope))
    if saved and (not saved[0]['installed'] or saved[0]['project'] != policy.project
                  or MonitoringPolicy.model_validate_json(saved[0]['policy']) != policy):
        raise PermissionError('监测配置已变化，不能更新旧轮次的会话授权')


async def _sync_read_scope(policy, session):
    # Dispatch validates the saved policy before admission. Recheck before
    # replacing a reused session's grant so a stale round cannot restore it.
    await _assert_current_policy(policy)
    if session.owner_user_id != policy.owner or session.project_id != policy.project:
        raise PermissionError('监测会话拥有者或项目不匹配')
    read_only_tools = _read_only_tools(policy)
    if (session.metadata or {}).get('readOnlyTools') == read_only_tools:
        return session

    def update(metadata):
        if metadata.get('monitorScope') != policy.scope:
            raise PermissionError('监测会话范围不匹配')
        return {**metadata, 'readOnlyTools': read_only_tools}

    updated = await Session.mutate_metadata(policy.project, session.id, update)
    if updated is None:
        raise PermissionError('监测会话已归档或正在变更，不能更新授权')
    return updated


async def sync_daily_read_scope(policy):
    """Apply a newly persisted target policy to today's existing session."""
    day = datetime.now(timezone.utc).astimezone(ZoneInfo(policy.timezone)).date().isoformat()
    for item in await rows('SELECT session_id FROM monitor_daily_sessions WHERE owner=? AND project=? AND scope=? AND business_date=?',
                           (policy.owner, policy.project, policy.scope, day)):
        session = await Session.get(policy.project, item['session_id'])
        if session is not None and session.status == 'active':
            await _sync_read_scope(policy, session)


async def ensure_daily(policy, started):
    await _assert_current_policy(policy)
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
        read_only_tools=_read_only_tools(policy),
    )
    session = await _sync_read_scope(policy, session)
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
