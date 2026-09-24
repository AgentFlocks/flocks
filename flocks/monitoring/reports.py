"""One atomic daily projection; failed exports retry independently of business work."""
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flocks.workspace.manager import WorkspaceManager
from .store import rows, write, connection

_locks: dict[tuple, asyncio.Lock] = {}


async def snapshot(owner, scope, day):
    # A single read transaction supplies the dashboard and report consistently.
    async with connection() as db:
        await db.execute('BEGIN')
        async def select(sql, args=()):
            cur = await db.execute(sql, args)
            return [dict(x) for x in await cur.fetchall()]
        installations = await select('SELECT * FROM monitor_installations WHERE owner=? AND scope=?', (owner, scope))
        runs = await select('SELECT * FROM monitor_attempts WHERE owner=? AND scope=? AND business_date=? ORDER BY sequence', (owner, scope, day))
        observations = await select('SELECT o.*,a.sequence,a.session_id,a.message_id,a.started_at FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id WHERE a.owner=? AND a.scope=? AND a.business_date=? ORDER BY a.sequence', (owner, scope, day))
        dispositions = await select('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? ORDER BY created_at', (owner, scope))
        steps = await select('SELECT s.* FROM monitor_steps s JOIN monitor_attempts a ON a.id=s.attempt_id WHERE a.owner=? AND a.scope=? AND a.business_date=? ORDER BY s.started_at', (owner, scope, day))
        report = await select('SELECT status,version,error FROM monitor_reports WHERE owner=? AND scope=? AND business_date=?', (owner, scope, day))
        daily = await select('SELECT session_id FROM monitor_daily_sessions WHERE owner=? AND scope=? AND business_date=?', (owner, scope, day))
        scheduler = []
        queued = []
        if installations:
            scheduler = await select('SELECT status,trigger FROM task_schedulers WHERE id=?', (installations[0]['scheduler_id'],))
            queued = await select("SELECT e.id,e.status,e.created_at,e.error,s.scheduled_for,s.status AS slot_status FROM task_executions e LEFT JOIN monitor_slots s ON s.execution_id=e.id WHERE e.scheduler_id=? AND e.status IN ('queued','running','cancelled') ORDER BY e.created_at DESC LIMIT 200", (installations[0]['scheduler_id'],))
    events = {}
    for item in observations:
        event = json.loads(item['data'])
        event.update(sessionID=item['session_id'], messageID=item['message_id'], attemptID=item['attempt_id'], observedAt=item['started_at'])
        events[item['event_key']] = event
    for disposition in dispositions:
        event = events.get(disposition['event_key'])
        if event is None:
            continue
        event['dispositionRecord'] = disposition
        # A later monitoring observation belongs to the active 0/10 query and
        # therefore reopens an event. Absence from that query never closes it.
        verified = disposition['status'] == 'verified' and disposition['updated_at'] >= event['observedAt']
        event['closure'] = 'closed' if verified else 'open'
        event['disposition'] = 'XDR 已处置，回查确认' if verified else '待确认处置结果' if disposition['status'] in ('writing', 'pending', 'mismatch') else '待人工确认'
    installation = installations[0] if installations else None
    for run in runs:
        run['result'] = json.loads(run['result'])
        run['steps'] = [step for step in steps if step['attempt_id'] == run['id']]
    trigger = json.loads(scheduler[0]['trigger']) if scheduler else {}
    business_timezone = json.loads(installation['policy'])['timezone'] if installation else 'Asia/Shanghai'
    tz = ZoneInfo(business_timezone)
    today = datetime.now(timezone.utc).astimezone(tz).date().isoformat()
    queued = [entry for entry in queued if datetime.fromisoformat(entry['scheduled_for'] or entry['created_at']).astimezone(tz).date().isoformat() == day]

    return {'businessDate': day, 'installation': {'installed': bool(installation and installation['installed']),
            'ready': bool(installation and installation['ready']), 'reason': installation['reason'] if installation else '请从添加场景安装安全运营监测',
            'status': scheduler[0]['status'] if scheduler else 'not_installed',
            'projectID': installation['project'] if installation else None},
            'timezone': business_timezone,
            'scheduledNextRun': (trigger.get('next_run') or trigger.get('nextRun')) if scheduler and scheduler[0]['status'] == 'active' else None,
            'sessionID': daily[0]['session_id'] if daily else None,
            'nextRun': (trigger.get('next_run') or trigger.get('nextRun')) if day == today and scheduler and scheduler[0]['status'] == 'active' else None,
            'runs': runs, 'events': list(events.values()), 'queued': queued,
            'report': report[0] if report else {'status': 'pending', 'version': 0, 'error': None},
            'metrics': {'definitions': int(bool(installation and installation['installed'])),
                        'started': len({r['execution_id'] for r in runs}), 'attempts': len(runs),
                        'events': len(events), 'risk': sum(e['risk'] == 'risk' for e in events.values()),
                        'openRisk': sum(e['risk'] == 'risk' and e['closure'] != 'closed' for e in events.values()),
                        'closed': sum(e['closure'] == 'closed' for e in events.values()),
                        'unknown': sum(e['risk'] == 'unknown' for e in events.values()),
                        'ignored': sum(e['risk'] == 'ignored' for e in events.values())},
            'disposition': '人工确认后写回，回查确认闭环'}


def render(data):
    metrics = data['metrics']
    lines = [f"# 安全运营监测 · {data['businessDate']}", '', f"业务时区：{data['timezone']}。定时监测只读；人工确认后写回 XDR 已处置状态，回查确认后闭环。状态闭环不代表执行了主机隔离或修复。", '',
             f"启动轮次：{metrics['started']}；尝试：{metrics['attempts']}；去重事件：{metrics['events']}；风险：{metrics['risk']}；待判定：{metrics['unknown']}。", '']
    lines += [f"未闭环风险：{metrics['openRisk']}；回查确认闭环：{metrics['closed']}。", '']
    if data['events']:
        lines += ['## 去重事件', '']
        for event in data['events']:
            # Treat remote strings as plain text; they must not inject Markdown.
            name = str(event['name']).replace('\n', ' ')
            for char in ('\\', '[', ']', '*', '_', '`', '<', '>'):
                name = name.replace(char, '\\' + char)
            closure = '已闭环（XDR 回查确认）' if event['closure'] == 'closed' else '未闭环'
            lines += [f"- {name} · {event['risk']} · {closure}；[关联对话](/sessions?session={event['sessionID']}&focusMessage={event['messageID']})"]
            if event.get('dispositionRecord'):
                item = event['dispositionRecord']
                lines += [f"  - 处置记录：{item['id']}；状态：{item['status']}；回查值：{item['observed_status']}；更新时间：{item['updated_at']}。"]
        lines += ['']
    for run in data['runs']:
        lines += [f"## 轮次 {run['execution_id']} · 尝试 {run['id']}", '',
                  f"状态：{run['status']}；计划：{run['scheduled_for'] or '手动'}；开始：{run['started_at']}；结束：{run['finished_at'] or '执行中'}。", '',
                  f"[查看本轮对话](/sessions?session={run['session_id']}&focusMessage={run['message_id']})", '']
        if run['error']:
            lines += [f"异常：{run['error']}", '']
        for step in run['steps']:
            lines += [f"- {step['tool']}：{step['status']}。{step['error'] or step['output'] or ''}"]
        lines += ['', '本轮为只读监测；人工处置结果见去重事件中的回查状态。', '']
    return '\n'.join(lines)


async def export_report(owner, scope, day):
    lock = _locks.setdefault((owner, scope, day), asyncio.Lock())
    async with lock:
        try:
            data = await snapshot(owner, scope, day)
            content = render(data)
            digest = hashlib.sha256(f'{owner}:{scope}'.encode()).hexdigest()[:16]
            directory = WorkspaceManager.get_instance().get_workspace_dir().expanduser() / 'outputs' / day
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f'host-security-monitor-{digest}.md'
            fd, temp = tempfile.mkstemp(prefix='.monitor-', dir=directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            await write("UPDATE monitor_reports SET status='updated',version=version+CASE WHEN content IS ? THEN 0 ELSE 1 END,content=?,error=NULL WHERE owner=? AND scope=? AND business_date=?", (content, content, owner, scope, day))
        except Exception:
            await write("UPDATE monitor_reports SET status='failed',error=? WHERE owner=? AND scope=? AND business_date=?", ('日报导出失败，等待独立重试', owner, scope, day))


async def retry_exports():
    for item in await rows("SELECT owner,scope,business_date FROM monitor_reports WHERE status IN ('pending','failed')"):
        await export_report(item['owner'], item['scope'], item['business_date'])
