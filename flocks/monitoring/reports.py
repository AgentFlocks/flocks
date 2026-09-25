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
from . import sampling
from .models import MonitoringPolicy

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
        observations = await select('SELECT o.*,a.sequence,a.session_id,a.message_id,a.started_at,a.project FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id WHERE a.owner=? AND a.scope=? AND a.business_date=? ORDER BY a.sequence', (owner, scope, day))
        dispositions = await select('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? ORDER BY created_at', (owner, scope))
        mail_notices = await select('SELECT id,state,created_at,updated_at FROM monitor_mail_notices WHERE owner=? AND project=(SELECT project FROM monitor_installations WHERE owner=? AND scope=?)', (owner, owner, scope))
        mail_replies = await select('SELECT state,received_at FROM monitor_mail_replies WHERE owner=? AND project=(SELECT project FROM monitor_installations WHERE owner=? AND scope=?)', (owner, owner, scope))
        mail_settings = await select('SELECT enabled FROM monitor_mail_settings WHERE owner=? AND project=(SELECT project FROM monitor_installations WHERE owner=? AND scope=?)', (owner, owner, scope))
        auto_settings = await select('SELECT * FROM monitor_auto_settings WHERE owner=? AND scope=?', (owner, scope))
        auto_queue = await select('SELECT * FROM monitor_auto_queue WHERE owner=? AND scope=?', (owner, scope))
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
        event.update(sessionID=item['session_id'], messageID=item['message_id'], attemptID=item['attempt_id'], observedAt=item['started_at'], projectID=item['project'])
        events[item['event_key']] = event
    for disposition in dispositions:
        event = events.get(disposition['event_key'])
        if event is None or disposition['mode'] in ('automatic', 'mail') and disposition['project'] != event['projectID']:
            continue
        event['dispositionRecord'] = disposition
        # Re-sampling an unchanged terminal event must not undo a verified test.
        decision = json.loads(disposition['decision'] or '{}')
        same_sample = (event.get('development_sample') is True and decision.get('development_sample') is True
                       and event.get('dealStatus') == disposition['target_status']
                       and event.get('endTime') is not None and event['endTime'] == decision.get('observed_end_time')
                       and event.get('host') == decision.get('observed_host'))
        verified = disposition['status'] == 'verified' and (disposition['updated_at'] >= event['observedAt'] or same_sample)
        event['closure'] = {40: 'closed', 60: 'ignored', 70: 'contained'}.get(disposition['target_status'], 'open') if verified else 'open'
        from .disposition import status_label
        event['disposition'] = f"{status_label(disposition['target_status'])}，回查确认" if verified else '待确认处置结果' if disposition['status'] in ('writing', 'pending', 'mismatch') else '待跟进'
    for event in events.values():
        if event['closure'] == 'ignored':
            event['originalRisk'] = event['risk']
            event['risk'] = 'ignored'
    installation = installations[0] if installations else None
    automatic = False  # Legacy evidence-only writes are retired.
    queue = [entry for entry in auto_queue if installation and entry['project'] == installation['project']]
    for entry in queue:
        if entry['event_key'] in events:
            events[entry['event_key']]['automaticReason'] = entry['reason']

    for run in runs:
        run['result'] = json.loads(run['result'])
        run['steps'] = [step for step in steps if step['attempt_id'] == run['id']]
    trigger = json.loads(scheduler[0]['trigger']) if scheduler else {}
    business_timezone = json.loads(installation['policy'])['timezone'] if installation else 'Asia/Shanghai'
    tz = ZoneInfo(business_timezone)
    today = datetime.now(timezone.utc).astimezone(tz).date().isoformat()
    queued = [entry for entry in queued if datetime.fromisoformat(entry['scheduled_for'] or entry['created_at']).astimezone(tz).date().isoformat() == day]

    policy = MonitoringPolicy.model_validate_json(installation['policy']) if installation else None
    development = bool(policy and sampling.enabled(policy))
    return {'businessDate': day, 'developmentSample': development,
            'investigationEngine': policy.investigation_engine if policy else 'rules',
            'roundTimeoutSeconds': policy.timeout_seconds if policy else None,
            'installation': {'installed': bool(installation and installation['installed']),
            'ready': bool(installation and installation['ready']), 'reason': installation['reason'] if installation else '请从添加场景安装安全运营监测',
            'status': scheduler[0]['status'] if scheduler else 'not_installed',
            'projectID': installation['project'] if installation else None},
            'timezone': business_timezone,
            'scheduledNextRun': (trigger.get('next_run') or trigger.get('nextRun')) if scheduler and scheduler[0]['status'] == 'active' else None,
            'sessionID': daily[0]['session_id'] if daily else None,
            'nextRun': (trigger.get('next_run') or trigger.get('nextRun')) if day == today and scheduler and scheduler[0]['status'] == 'active' else None,
            'automatic': {'enabled': automatic, 'rule': 'xdr-evidence-v1', 'queued': len(queue)},
            'runs': runs, 'events': list(events.values()), 'queued': queued,
            'report': report[0] if report else {'status': 'pending', 'version': 0, 'error': None},
            'metrics': {'definitions': int(bool(installation and installation['installed'])),
                        'started': len({r['execution_id'] for r in runs}), 'attempts': len(runs),
                        'events': len(events), 'risk': sum(e['risk'] == 'risk' for e in events.values()),
                        'openRisk': sum(e['risk'] == 'risk' and e['closure'] != 'closed' for e in events.values()),
                        'closed': sum(e['closure'] == 'closed' for e in events.values()),
                        'contained': sum(e['closure'] == 'contained' for e in events.values()),
                        'unknown': sum(e['risk'] == 'unknown' for e in events.values()),
                        'ignored': sum(e['risk'] == 'ignored' for e in events.values())},
            'mail': {'enabled': bool(mail_settings and mail_settings[0]['enabled']),
                     'sentToday': sum(n['state']=='sent' and datetime.fromisoformat(n['updated_at']).astimezone(tz).date().isoformat()==day for n in mail_notices),
                     'receivedToday': sum(datetime.fromisoformat(r['received_at']).astimezone(tz).date().isoformat()==day for r in mail_replies),
                     'pending': sum(r['state'] in ('pending','interpreted') for r in mail_replies),
                     'needsReview': sum(r['state']=='needs_review' for r in mail_replies),
                     'sendUnknown': sum(n['state']=='send_unknown' for n in mail_notices)},
            'disposition': '责任人邮件反馈后标记并回查'}


def render(data):
    metrics = data['metrics']
    lines = [f"# 安全运营监测执行时间线 · {data['businessDate']}", '', f"业务时区：{data['timezone']}。每十分钟触发检查；同一监测串行执行，忙碌时只保留一次待执行任务。逐条邮件通知责任人，下一轮解读回信、标记并回查。状态标记不代表组件执行了主机隔离或修复。", '',
             f"启动轮次：{metrics['started']}；尝试：{metrics['attempts']}；去重事件：{metrics['events']}；风险：{metrics['risk']}；待判定：{metrics['unknown']}。", '']
    lines += [f"未闭环风险：{metrics['openRisk']}；处置完成：{metrics['closed']}；已遏制：{metrics['contained']}；已忽略：{metrics['ignored']}。", '']
    if any(r['result'].get('development_sample') for r in data['runs']):
        lines += [sampling.DESCRIPTION, '联调中的忽略标记仅验证回写链路，不代表事件被判定为误报或威胁已消除。', '']
    if data['events']:
        lines += ['## 去重事件', '']
        for event in data['events']:
            # Treat remote strings as plain text; they must not inject Markdown.
            name = str(event['name']).replace('\n', ' ')
            for char in ('\\', '[', ']', '*', '_', '`', '<', '>'):
                name = name.replace(char, '\\' + char)
            closure = {'closed': '已闭环（XDR 回查确认）', 'contained': '已遏制（XDR 回查确认，仍需跟进）', 'ignored': '已忽略（XDR 回查确认）'}.get(event['closure'], '未闭环')
            lines += [f"- {name} · {event['risk']} · {closure}；[关联对话](/sessions?session={event['sessionID']}&focusMessage={event['messageID']})"]
            if event.get('dispositionRecord'):
                item = event['dispositionRecord']
                lines += [f"  - 处置记录：{item['id']}；来源：{item['mode']}；目标：{item['target_status']}；状态：{item['status']}；回查值：{item['observed_status']}；更新时间：{item['updated_at']}。"]
        lines += ['']
    for run in data['runs']:
        lines += [f"## 轮次 {run['execution_id']} · 尝试 {run['id']}", '',
                  f"状态：{run['status']}；计划：{run['scheduled_for'] or '手动'}；开始：{run['started_at']}；结束：{run['finished_at'] or '执行中'}。", '',
                  f"[查看本轮对话](/sessions?session={run['session_id']}&focusMessage={run['message_id']})", '']
        if run['error']:
            lines += [f"异常：{run['error']}", '']
        for step in run['steps']:
            lines += [f"- {step['tool']}：{step['status']}。{step['error'] or step['output'] or ''}"]
        lines += ['', '状态标记和回查结果见上方步骤及去重事件；处置中、已遏制不计为处置完成。', '']
    return '\n'.join(lines)


def render_summary(data):
    m, mail = data['metrics'], data.get('mail', {})
    lines = [f"# 当日告警总结 · {data['businessDate']}", '',
             f"当天执行 {m['started']} 轮，查询到 {m['events']} 条去重事件。风险 {m['risk']} 条，待判定 {m['unknown']} 条。",
             f"处置完成 {m['closed']} 条，已遏制 {m['contained']} 条，已忽略 {m['ignored']} 条，仍未闭环风险 {m['openRisk']} 条。", '',
             '## 邮件与处置进展', '',
             f"当天确认发送 {mail.get('sentToday', 0)} 封，收到 {mail.get('receivedToday', 0)} 封回复。",
             f"截至当前，待处理回复 {mail.get('pending', 0)} 封，待确认 {mail.get('needsReview', 0)} 封，发件结果未知 {mail.get('sendUnknown', 0)} 封。", '',
             '## 需要继续跟进', '']
    for event in data['events']:
        if event['closure'] not in ('closed', 'ignored'):
            name = str(event['name']).replace('\n', ' ')
            for char in ('\\', '[', ']', '*', '_', '`', '<', '>'):
                name = name.replace(char, '\\' + char)
            lines.append(f"- {name}：{event['disposition']}。")
    if not data['events']:
        lines.append('当天没有已提交的事件；查询失败不能视为没有告警。')
    failed = sum(r['status'] not in ('completed', 'running') for r in data['runs'])
    lines += ['', '## 数据完整性', '', f"未完整完成的轮次：{failed}。统计以已提交事实及回查结果为准，发信和收到回信不等于处置完成。",
              '当天持续更新；跨日收到的反馈归入实际收到当天，原事件保留关联。邮件存量统计反映生成报告时的进度。']
    if any(r['result'].get('development_sample') for r in data['runs']):
        lines += ['', sampling.DESCRIPTION, '含联调抽样轮次，不能视为全天全量告警统计。测试忽略不代表消除威胁或确认误报。']
    return '\n'.join(lines)


async def export_report(owner, scope, day):
    lock = _locks.setdefault((owner, scope, day), asyncio.Lock())
    async with lock:
        try:
            data = await snapshot(owner, scope, day)
            content = render(data)
            summary_content = render_summary(data)
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
            summary_target = directory / f'host-security-monitor-{digest}-summary.md'
            fd, temp = tempfile.mkstemp(prefix='.monitor-summary-', dir=directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(summary_content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, summary_target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            await write("UPDATE monitor_reports SET status='updated',version=version+CASE WHEN content IS ? AND summary_content IS ? THEN 0 ELSE 1 END,content=?,summary_content=?,error=NULL WHERE owner=? AND scope=? AND business_date=?", (content, summary_content, content, summary_content, owner, scope, day))
        except Exception:
            await write("UPDATE monitor_reports SET status='failed',error=? WHERE owner=? AND scope=? AND business_date=?", ('日报导出失败，等待独立重试', owner, scope, day))


async def retry_exports():
    for item in await rows("SELECT owner,scope,business_date FROM monitor_reports WHERE status IN ('pending','failed')"):
        await export_report(item['owner'], item['scope'], item['business_date'])
