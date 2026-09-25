"""Scheduled investigation and durable mail follow-up in native daily sessions."""
import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flocks.auth.context import set_current_auth_user, reset_current_auth_user
from flocks.auth.service import AuthService
from flocks.session.message import Message, MessageRole, TextPart, ToolPart, ToolStateRunning, ToolStateCompleted, ToolStateError
from flocks.session.session_loop import LoopResult
from flocks.session.interaction_policy import unattended_scope, monitoring_read_scope
from flocks.task.background import get_background_manager
from flocks.task.models import TaskStatus
from flocks.task.store import TaskStore
from flocks.utils.id import Identifier
from .models import MonitoringPolicy
from .sessions import ensure_daily
from .store import rows, write, connection, encode
from .adapter import XdrAdapter, ContractError, page_items, response_items
from . import diagnostics as diag
from .summaries import Summary, page_summary, hosts_summary, analysis_summary, event_label, round_summary
from . import sampling

_running: dict[str, asyncio.Task] = {}


def now():
    return datetime.now(timezone.utc)


async def publish(kind, payload):
    from flocks.server.routes.event import publish_event
    try:
        await publish_event(kind, payload)
    except Exception:
        pass  # All facts precede notification; snapshot polling repairs loss.


async def emit_message(session_id, text, *, message_id=None, finished=True, role=MessageRole.ASSISTANT, parent_id=None, agent='rex'):
    message = await Message.create(session_id=session_id, role=role,
                                   content=text, id=message_id or Identifier.ascending('message'), agent=agent, parentID=parent_id or '')
    if finished and role == MessageRole.ASSISTANT:
        message = await Message.update(session_id, message.id, finish='stop', time={**message.time, 'completed': int(now().timestamp() * 1000)})
    await publish('message.updated', {'sessionID': session_id, 'info': message.model_dump(mode='json', by_alias=True)})
    for part in await Message.parts(message.id, session_id):
        await publish('message.part.updated', {'sessionID': session_id, 'part': part.model_dump(mode='json', by_alias=True)})
    return message


class Recorder:
    def __init__(self, session_id, attempt_id, on_start=None, parent_id=None, agent='rex'):
        self.session_id, self.attempt_id = session_id, attempt_id
        self.on_start = on_start
        self.parent_id = parent_id
        self.agent = agent

    async def call(self, name, params, operation, *, failure_context=""):
        from .disposition import operation_recorder
        stage = {'查询 XDR 事件': 'query.events', '查询关联主机': 'query.entities', '关联分析': 'correlate', '自动研判与状态标记': 'automatic.mark', '发送告警通知': 'mail.send', '解读回信并跟进状态': 'mail.interpret'}.get(name, 'step.other')
        token = operation_recorder.set(self)
        try:
            with diag.span(stage, page=params.get('page_num'), page_size=params.get('page_size')):
                return await self._call(name, params, operation, failure_context=failure_context)
        finally:
            operation_recorder.reset(token)

    async def _call(self, name, params, operation, *, failure_context=""):
        message = await emit_message(self.session_id, name, finished=False, parent_id=self.parent_id, agent=self.agent)
        step_id = Identifier.ascending('part')
        start = now()
        ms = int(start.timestamp() * 1000)
        part = ToolPart(id=step_id, sessionID=self.session_id, messageID=message.id,
                        callID=step_id, tool=name,
                        state=ToolStateRunning(input=params, title=name, time={'start': ms}))
        await write('INSERT INTO monitor_steps(id,attempt_id,message_id,part_id,tool,input,status,started_at) VALUES(?,?,?,?,?,?,?,?)',
                    (step_id, self.attempt_id, message.id, part.id, name, encode(params), 'running', start.isoformat()))
        await Message.add_part(self.session_id, message.id, part)
        await publish('message.part.updated', {'sessionID': self.session_id, 'part': part.model_dump(mode='json', by_alias=True)})
        try:
            if self.on_start:
                callback, self.on_start = self.on_start, None
                await callback()
            outcome = await operation(message.id)
            result, display = outcome[:2]
            summary = outcome[2] if len(outcome) > 2 else Summary(f'{name}已完成。')
            state = ToolStateCompleted(input=params, output=display, title=name, metadata={},
                                       time={'start': ms, 'end': int(now().timestamp() * 1000)})
            await write("UPDATE monitor_steps SET status='completed',finished_at=?,output=? WHERE id=?", (now().isoformat(), encode(display), step_id))
        except BaseException as exc:
            error = str(exc) if isinstance(exc, ContractError) else ('执行已中断' if isinstance(exc, asyncio.CancelledError) else '步骤执行失败')
            state = ToolStateError(input=params, error=error, time={'start': ms, 'end': int(now().timestamp() * 1000)})
            await write("UPDATE monitor_steps SET status='failed',finished_at=?,error=? WHERE id=?", (now().isoformat(), error, step_id))
            explanation = f'{failure_context}{name}未完成：{error}。'
            if name == '查询 XDR 事件':
                explanation += '本设备本轮事件批次未提交，上次查询进度保持不变；不能把失败视为 0 条事件。'
            elif name == '查询关联主机':
                explanation += '已查询的事件保留，本次关联数据不完整。'
            await self.finish_part(message.id, part.id, state, Summary(explanation))
            raise
        await self.finish_part(message.id, part.id, state, summary)
        return result

    async def finish_part(self, message, part_id, state, summary):
        part = await Message.update_part(self.session_id, message, part_id, state=state.model_dump())
        await publish('message.part.updated', {'sessionID': self.session_id, 'part': part.model_dump(mode='json', by_alias=True)})
        text = TextPart(sessionID=self.session_id, messageID=message, text=summary.text,
                        metadata={'monitoringSummary': True, 'toolPartID': part_id, 'details': summary.details})
        await Message.add_part(self.session_id, message, text)
        await publish('message.part.updated', {'sessionID': self.session_id, 'part': text.model_dump(mode='json', by_alias=True)})
        info = await Message.get(self.session_id, message)
        if info:
            info = await Message.update(self.session_id, message, finish='error' if state.status == 'error' else 'stop', time={**info.time, 'completed': int(now().timestamp() * 1000)})
            await publish('message.updated', {'sessionID': self.session_id, 'info': info.model_dump(mode='json', by_alias=True)})


async def query_device(adapter, device, start, end, recorder, *, selection=None):
    if sampling.enabled(adapter.policy):
        if selection is not None:
            raise ContractError('联调抽样不能叠加其他本地筛选规则')
        return await sampling.query_sample(adapter, device, start, end, recorder)
    from .pagination import IncidentPages
    batch = IncidentPages(device, selection)
    for page in range(1, adapter.policy.max_pages + 1):
        params = {'action': 'list', 'start_time': start, 'end_time': end, 'page_num': page, 'page_size': 100,
                  'time_field': 'endTime', 'deal_statuses': [0, 10], 'white_status': ['未加白', '部分加白']}
        async def query(message_id):
            value = await adapter.call(device, params, message_id)
            items, total = page_items(value)
            selected, complete = batch.add(items, total, params['page_size'])
            if not complete and page == adapter.policy.max_pages:
                raise ContractError('达到分页预算，查询未完成')
            display = {'device': device, 'page': page, 'received': len(items), 'total': total}
            summary_args = {}
            if selection is not None:
                display['selection'] = {'description': selection.description, 'matched': len(selected),
                                        'excluded': len(items) - len(selected), 'cumulative': batch.counts}
                summary_args = {'selection': selection.description, 'received': len(items), 'counts': batch.counts}
            return complete, display, page_summary(page, selected, len(batch.events), complete, **summary_args)
        complete = await recorder.call('查询 XDR 事件', {'device': device, **params}, query)
        if complete:
            return list(batch.events.values())
    raise ContractError('达到分页预算，查询未完成')


@diag.traced('run')
async def run(execution, policy, adapter_factory=XdrAdapter):
    started = now()  # actual semaphore admission, not scheduled/queued time
    from .mailflow import settings, process_replies, notify_batch, cutoff
    reply_high = await cutoff(policy.owner, policy.project, before=started.isoformat())
    with diag.span('session.prepare', devices=len(policy.devices), max_pages=policy.max_pages, timeout_seconds=policy.timeout_seconds):
        session, day = await ensure_daily(policy, started)
    execution.session_id = session.id
    execution.started_at = started
    await TaskStore.update_execution(execution)
    attempt_id = Identifier.ascending('task')
    message_id = Identifier.ascending('message')
    await write('INSERT INTO monitor_attempts(id,execution_id,owner,project,scope,business_date,session_id,message_id,scheduled_for,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (attempt_id, execution.id, policy.owner, policy.project, policy.scope, day, session.id, message_id,
                 execution.execution_input_snapshot.get('scheduledFor'), started.isoformat(), 'running'))
    automatic_enabled = bool((await settings(policy.owner))['enabled'])
    agent_name = 'security-monitor' if policy.investigation_engine == 'agent-v1' else 'rex'
    mode_label = '邮件协同跟进' if automatic_enabled else '只读任务'
    trigger_message = await emit_message(session.id, f'系统自动监测 · {started.astimezone(ZoneInfo(policy.timezone)).strftime("%H:%M:%S")} · {mode_label}', role=MessageRole.USER)
    query_description = (sampling.DESCRIPTION + '查询最近 24 小时且未加白或部分加白的事件。' if sampling.enabled(policy)
                         else '查询待处置、处置中且未加白或部分加白的 XDR 事件，并关联分析。')
    await emit_message(session.id, '本轮安全运营监测开始。' + query_description + ('先处理此前收到的回信，再查询新事件并逐条邮件通知责任人。' if automatic_enabled else '邮件跟进未启用，本轮只查询分析。'), message_id=message_id, parent_id=trigger_message.id, agent=agent_name)
    sequence = (await rows('SELECT sequence FROM monitor_attempts WHERE id=?', (attempt_id,)))[0]['sequence']
    from flocks.session.core.status import SessionStatus, SessionStatusBusy, SessionStatusIdle
    SessionStatus.set(session.id, SessionStatusBusy())
    await publish('session.status', {'sessionID': session.id, 'status': {'type': 'busy'}})
    async def confirm_start():
        await write('UPDATE monitor_attempts SET navigation_confirmed=1 WHERE id=?', (attempt_id,))
        await publish('monitor.execution.started', {'sessionID': session.id, 'executionID': execution.id,
                      'attemptID': attempt_id, 'sequence': sequence, 'businessDate': day, 'startedAt': started.isoformat()})
    recorder = Recorder(session.id, attempt_id, confirm_start, trigger_message.id, agent=agent_name)
    adapter = adapter_factory(policy, session.id)
    errors, observed = [], []
    status, summary = 'failed', ''
    try:
        if not policy.devices:
            raise ContractError('未绑定可用数据源')
        feedback = await process_replies(policy, session.id, reply_high, recorder)
        if policy.investigation_engine == 'agent-v1':
            from . import investigation
            budget = investigation.Budget()
            backlog = await investigation.pending(policy)
            if sampling.enabled(policy) and backlog:
                # A resumed case takes this round's single development sample;
                # do not select a second root while the first is unfinished.
                observed = backlog[:1]
                await write('INSERT OR IGNORE INTO monitor_observations VALUES(?,?,?)',
                            (attempt_id, observed[0]['key'], encode(observed[0])))
                await emit_message(session.id, '本轮优先续查此前未完成的事件：' + event_label(observed[0])
                                   + '。继续使用已保存的证据，不额外抽取新样本。', parent_id=trigger_message.id, agent=agent_name)
        for device in policy.devices:
            if sampling.enabled(policy) and observed:
                break  # One new sample per round, not one per device.
            cursor = await rows('SELECT through_time FROM monitor_cursors WHERE owner=? AND scope=? AND device=?', (policy.owner, policy.scope, device))
            end = int(started.timestamp())
            start = (cursor[0]['through_time'] - 600) if cursor and not sampling.enabled(policy) else end - 86400
            diag.event('query.window', device=diag.opaque(device), window_seconds=max(0, end - start), cursor_present=bool(cursor), development_sample=sampling.enabled(policy))
            try:
                with diag.span('query.device', device=diag.opaque(device)):
                    events = await query_device(adapter, device, start, end, recorder)
                # A sample is not a complete scan: leave the normal cursor intact.
                async with connection() as db:
                    for event in events:
                        if policy.investigation_engine == 'agent-v1':
                            event['investigationWindow'] = {'start': max(start, end-86400), 'end': end}
                            await investigation.enqueue(db, policy, event, started.isoformat())
                        await db.execute('INSERT INTO monitor_observations VALUES(?,?,?)', (attempt_id, event['key'], encode(event)))
                    if not sampling.enabled(policy):
                        await db.execute('INSERT INTO monitor_cursors VALUES(?,?,?,?) ON CONFLICT(owner,scope,device) DO UPDATE SET through_time=MAX(through_time,excluded.through_time)',
                                         (policy.owner, policy.scope, device, end))
                observed.extend(events)
                if policy.investigation_engine == 'agent-v1':
                    continue  # The model chooses which evidence to query next.
                for event in events:
                    params = {'action': 'get_entities', 'uuid': event['id'], 'entity_type': 'host'}
                    async def entities(message_id):
                        value = await adapter.call(device, params, message_id)
                        data = value.get('data')
                        if not isinstance(data, (dict, list)):
                            raise ContractError('关联实体响应缺失')
                        # Retain only whitelisted stable host identifiers; no raw
                        # credential-bearing HTTP response is rendered/persisted.
                        items = data if isinstance(data, list) else response_items(data)
                        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                            raise ContractError('关联实体列表或记录结构无效')
                        hosts = [{k: x[k] for k in ('id', 'hostId', 'hostIp', 'ip', 'name') if k in x and type(x[k]) in (str, int)}
                                 for x in items if isinstance(x, dict)]
                        return hosts, {'event': event['id'], 'hosts': hosts}, hosts_summary(event, hosts)
                    try:
                        event['entities'] = await recorder.call('查询关联主机', {'device': device, **params}, entities, failure_context=f'针对事件 {event_label(event)}，')
                    except ContractError:
                        event['enrichment'] = '关联主机查询失败'
                        errors.append('关联数据不完整')
                    await write('UPDATE monitor_observations SET data=? WHERE attempt_id=? AND event_key=?', (encode(event), attempt_id, event['key']))
            except ContractError as exc:
                errors.append(str(exc))
        if policy.investigation_engine == 'agent-v1':
            if not sampling.enabled(policy):
                current_events = {event['key']: event for event in observed}
                resumed = []
                for event in backlog:
                    resumed.append(current_events.get(event['key'], event))
                    if event['key'] not in current_events:
                        await write('INSERT OR IGNORE INTO monitor_observations VALUES(?,?,?)',
                                    (attempt_id, event['key'], encode(event)))
                resumed_keys = {event['key'] for event in resumed}
                observed = resumed + [event for event in observed if event['key'] not in resumed_keys]
            for event in observed[:1 if sampling.enabled(policy) else 20]:
                await investigation.investigate(policy, event, recorder, budget)
                if event['investigation']['state'] != 'ready':
                    errors.append('智能体调查尚未完成，证据和待办已保存')
                await write('UPDATE monitor_observations SET data=? WHERE attempt_id=? AND event_key=?',
                            (encode(event), attempt_id, event['key']))
            if len(observed) > 20:
                errors.append('本轮事件超过调查批次上限，其余事件已保存待后续调查')
        async def analyze(_):
            groups = {}
            for event in observed:
                # Scope includes the device; an IP alone never merges sources.
                key = (event['device'], event['host'])
                if event['host']:
                    groups.setdefault(key, []).append(event['key'])
            for event in observed:
                event['related'] = [x for x in groups.get((event['device'], event['host']), []) if x != event['key']]
                await write('UPDATE monitor_observations SET data=? WHERE attempt_id=? AND event_key=?', (encode(event), attempt_id, event['key']))
            result = {'events': len(observed), 'risk': sum(e['risk'] == 'risk' for e in observed),
                      'unknown': sum(e['risk'] == 'unknown' for e in observed), 'ignored': 0,
                      'errors': errors, 'disposition': '待人工确认', 'closure': 'open'}
            return result, result, analysis_summary(result, observed, groups)
        result = await recorder.call('关联分析', {'policy': 'xdr-risk-v1'}, analyze)
        result['development_sample'] = sampling.enabled(policy)
        # Development mail validates transport even for incomplete evidence.
        # Normal monitoring waits for a completed investigation before queuing
        # a new notification. Existing delivery/write intents remain authoritative.
        mail_events = (observed if sampling.enabled(policy) or policy.investigation_engine == 'rules' else
                       [event for event in observed if event.get('investigation', {}).get('state') == 'ready'])
        notification = await notify_batch(policy, session.id, mail_events, recorder)
        result['mail'] = {'feedback': feedback, 'notification': notification, 'enabled': automatic_enabled}
        result['disposition'] = '责任人邮件反馈后标记并回查' if automatic_enabled else '只读监测'
        status = 'partial' if errors and observed or feedback['pending'] or notification['pending'] else 'failed' if errors else 'completed'
        summary = round_summary(status, result, observed, feedback, notification, automatic_enabled, sampling.enabled(policy))
        await write('UPDATE monitor_attempts SET result=? WHERE id=?', (encode(result), attempt_id))
    except BaseException as exc:
        status = 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed'
        timed_out = execution.execution_input_snapshot.get('monitorStopReason') == 'timeout'
        errors.append(f'达到本轮 {policy.timeout_seconds} 秒执行上限，已请求停止并保存进度' if timed_out else
                      str(exc) if isinstance(exc, ContractError) else '运行中断或内部错误')
        summary = ('本轮未完成：' + '；'.join(errors) + f'。已读取 {len(observed)} 条事件，不能将中断视为无风险。'
                   '已保存的通知和回信保留在邮件跟进中；请检查失败步骤或导出诊断日志。再次运行会核对已有记录，发送或写入结果未知时不会直接重复执行。')
        if policy.investigation_engine == 'agent-v1':
            for event in observed:
                await write('UPDATE monitor_observations SET data=? WHERE attempt_id=? AND event_key=?',
                            (encode(event), attempt_id, event['key']))
            summary += '已保存的调查证据和原事件编号会保留，后续轮次优先续查未完成事件；连续三轮未完成的事件保留待人工核对。'
        raise
    finally:
        diag.result(status, events=len(observed), errors=len(errors))
        await write('UPDATE monitor_attempts SET status=?,finished_at=?,error=? WHERE id=?', (status, now().isoformat(), '；'.join(errors) or None, attempt_id))
        await emit_message(session.id, summary or '本轮已中断，未记录为成功。', parent_id=trigger_message.id, agent=agent_name)
        await write("INSERT INTO monitor_reports(owner,scope,business_date,status) VALUES(?,?,?,'pending') ON CONFLICT(owner,scope,business_date) DO UPDATE SET status='pending'", (policy.owner, policy.scope, day))
        SessionStatus.set(session.id, SessionStatusIdle())
        await publish('session.status', {'sessionID': session.id, 'status': {'type': 'idle'}})
        from .reports import export_report
        with diag.span('report.export'):
            await export_report(policy.owner, policy.scope, day)
    return LoopResult(action='error' if status == 'failed' else 'stop', error=summary if status == 'failed' else None,
                      metadata={'summary': summary})


@diag.traced('dispatch')
async def dispatch(execution, scheduler, *, adapter_factory=XdrAdapter):
    policy = MonitoringPolicy.model_validate(execution.execution_input_snapshot['context']['monitoring'])
    from flocks.hub import local
    record = local.get_record('component', policy.scope)
    if not record or not record.enabled:
        raise PermissionError('监测场景未启用')
    if policy.investigation_engine == 'agent-v1':
        from .agent_component import resolve
        await resolve()
    installed = await rows('SELECT * FROM monitor_installations WHERE owner=? AND scope=? AND installed=1 AND ready=1', (policy.owner, policy.scope))
    if (not installed or scheduler.status.value != 'active'
            or installed[0]['scheduler_id'] != scheduler.id
            or MonitoringPolicy.model_validate_json(installed[0]['policy']) != policy):
        raise RuntimeError('监测套件未安装、未就绪或已停用')
    user = await AuthService.get_user_by_id(policy.owner)
    if user is None or user.status != 'active':
        raise PermissionError('监测拥有者不可用')
    token = set_current_auth_user(user.to_auth_user())
    task = asyncio.current_task()
    _running[execution.id] = task
    try:
        with unattended_scope(), monitoring_read_scope(policy.tool, policy.devices):
            manager = get_background_manager()
            async def runner():
                return await run(execution, policy, adapter_factory)
            background = await manager.run_existing_session(
                session_id=execution.session_id or '', description=execution.title,
                agent='security-monitor' if policy.investigation_engine == 'agent-v1' else 'rex',
                allow_user_questions=False, runner=runner)
            try:
                result = await manager.wait_for(background.id, timeout_ms=policy.timeout_seconds * 1000)
            except BaseException:
                manager.cancel(background.id)
                handle = manager._task_handles.get(background.id)
                if handle:
                    await asyncio.gather(handle, return_exceptions=True)
                raise
            if result is None:
                execution.execution_input_snapshot['monitorStopReason'] = 'timeout'
                manager.cancel(background.id)
                handle = manager._task_handles.get(background.id)
                if handle:
                    await asyncio.gather(handle, return_exceptions=True)
            if result is None or result.status != 'completed':
                diag.event('background.result', failure=True, outcome='failed')
                raise RuntimeError(result.error if result else '监测执行超时')
        execution.status = TaskStatus.COMPLETED
    except Exception as exc:
        diag.result('failed')
        diag.event('dispatch.result', failure=True, outcome='failed', error_type=type(exc).__name__)
        execution.status = TaskStatus.FAILED
        execution.error = str(exc)
    finally:
        _running.pop(execution.id, None)
        reset_current_auth_user(token)
    execution.completed_at = now()
    if execution.started_at:
        execution.duration_ms = int((execution.completed_at - execution.started_at).total_seconds() * 1000)
    await TaskStore.update_execution(execution)
    return execution


async def cancel(execution_id):
    task = _running.get(execution_id)
    if task and task is not asyncio.current_task():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
