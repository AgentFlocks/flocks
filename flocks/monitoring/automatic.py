"""Opt-in, bounded incident status marking with durable intent and readback."""
import asyncio
import json
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5
from pydantic import BaseModel, ConfigDict, StrictBool

from flocks.session.interaction_policy import automatic_mark_scope, require_interactive
from flocks.task.store import TaskStore
from . import disposition as d
from .adapter import XdrAdapter, ContractError, page_items, require_query_contract
from .models import COMPONENT_ID, MonitoringPolicy
from .status_rules import ENTITY_TYPES, RULE_VERSION, select_status
from .store import rows, write, connection, encode

_config_locks = {}
MAX_PER_ROUND = 20


class AutomaticRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


def lock(owner):
    return _config_locks.setdefault(owner, asyncio.Lock())


async def settings(owner):
    records = await rows('SELECT s.* FROM monitor_auto_settings s JOIN monitor_installations i '
                         'ON i.owner=s.owner AND i.scope=s.scope AND i.project=s.project '
                         'WHERE s.owner=? AND s.scope=? AND i.installed=1', (owner, COMPONENT_ID))
    return records[0] if records else {'enabled': False, 'revision': '', 'project': None}


async def configure(owner, enabled):
    await require_interactive()
    from .lifecycle import _owned_installation
    from .adapter import discover
    from flocks.tool.registry import ToolRegistry
    async with lock(owner):
        entry, _, policy = await _owned_installation(owner)
        if enabled:
            from flocks.hub import local
            record = local.get_record('component', COMPONENT_ID)
            if not record or not record.enabled or not entry['ready']:
                raise ValueError('请先安装并启用场景、完成接入检查')
            devices, tool_name, reason = await discover()
            if reason or devices != policy.devices or tool_name != policy.tool:
                raise ValueError(reason or '设备绑定已变化，请重新检查接入')
            tool = ToolRegistry.get(policy.tool)
            if tool is None:
                raise ValueError('XDR 工具不可用')
            require_query_contract(tool.info)
            props = tool.info.get_schema().to_json_schema().get('properties', {})
            if not {'uuids', 'deal_status', 'deal_comment'} <= props.keys():
                raise ValueError('请更新 XDR 工具：缺少事件状态标记字段')
        current = await settings(owner)
        if bool(current['enabled']) == enabled and current.get('project') == policy.project:
            return
        await write('INSERT INTO monitor_auto_settings VALUES(?,?,?,?,?,?) ON CONFLICT(owner,scope) '
                    'DO UPDATE SET project=excluded.project,enabled=excluded.enabled,revision=excluded.revision,updated_at=excluded.updated_at',
                    (owner, COMPONENT_ID, policy.project, int(enabled), str(uuid4()), d.stamp()))
        if enabled:
            # Include previously observed events; fresh server filters are checked before any write.
            await write("INSERT OR IGNORE INTO monitor_auto_queue(owner,scope,project,event_key) "
                        "SELECT DISTINCT a.owner,a.scope,a.project,o.event_key FROM monitor_observations o "
                        "JOIN monitor_attempts a ON a.id=o.attempt_id WHERE a.owner=? AND a.scope=? AND a.project=?",
                        (owner, COMPONENT_ID, policy.project))


async def authorized(policy, revision):
    current = await settings(policy.owner)
    if not current['enabled'] or current['revision'] != revision or current['project'] != policy.project:
        raise ContractError('自动标记已关闭或配置已变化')
    from flocks.hub import local
    record = local.get_record('component', policy.scope)
    entries = await rows('SELECT * FROM monitor_installations WHERE owner=? AND scope=? AND installed=1 AND ready=1',
                         (policy.owner, policy.scope))
    if not record or not record.enabled or not entries:
        raise ContractError('场景已停用或接入未就绪')
    actual = MonitoringPolicy.model_validate_json(entries[0]['policy'])
    scheduler = await TaskStore.get_scheduler(entries[0]['scheduler_id'])
    if actual != policy or not scheduler or scheduler.status.value != 'active':
        raise ContractError('监测已暂停或绑定已变化')


class AutomaticAdapter(XdrAdapter):
    allowed_actions = frozenset({'list', 'get_entities', 'get_proof', 'update_status'})

    def __init__(self, policy, session_id, revision):
        super().__init__(policy, session_id)
        self.revision = revision

    async def call(self, device, params, message_id):
        if params.get('action') != 'update_status':
            return await super().call(device, params, message_id)
        # Disabling waits for an admitted write; it cannot race with a later write.
        async with lock(self.policy.owner):
            await authorized(self.policy, self.revision)
            ids, target = params.get('uuids'), params.get('deal_status')
            if not isinstance(ids, list) or len(ids) != 1:
                raise PermissionError('Automatic status writes require exactly one incident')
            with automatic_mark_scope(self.policy.tool, device, ids[0], target, self.session_id):
                return await super().call(device, params, message_id)


async def read_event(adapter, event, *, eligible=False):
    params = {'action': 'list', 'uuids': [event['id']], 'start_time': 0,
              'end_time': int(d.datetime.now(d.timezone.utc).timestamp()), 'time_field': 'endTime',
              'page_num': 1, 'page_size': 5, 'white_status': ['未加白', '部分加白'] if eligible else [],
              'deal_statuses': [0, 10] if eligible else []}
    value = await d.operation(adapter, event, params, '重新核对事件及自动标记范围' if eligible else '回查事件当前状态')
    items, total = page_items(value)
    if eligible and not items and total in (None, 0):
        return None
    if len(items) != 1 or items[0]['uuId'] != event['id'] or total not in (None, 1):
        raise ContractError('未返回唯一目标事件，不能自动标记')
    return items[0]


async def finish(*args, **kwargs):
    # One export at round end; previous business days use the existing retry worker.
    return await d.finish(*args, **kwargs, defer_exports=True)


async def process_event(policy, event_key, session_id, revision, adapter_factory=AutomaticAdapter):
    async with d._locks.setdefault((policy.owner, event_key), asyncio.Lock()):
        await authorized(policy, revision)
        _, event = await d.target(policy.owner, event_key)
        adapter = adapter_factory(policy, session_id, revision)
        unresolved = await rows("SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? "
                                "AND status IN ('writing','pending','mismatch') AND (mode!='automatic' OR project=?) ORDER BY created_at DESC LIMIT 1",
                                (policy.owner, policy.scope, event_key, policy.project))
        if unresolved:
            item = unresolved[0]
            if item['mode'] != 'automatic':
                return {'state': 'waiting', 'reason': '存在人工处置结果待确认，自动标记等待回查'}
            if item['status'] == 'writing' and (d.datetime.now(d.timezone.utc) - d.datetime.fromisoformat(item['updated_at'])).total_seconds() < 120:
                return {'state': 'waiting', 'reason': '写回仍在保护窗口，稍后仅回查'}
            try:
                current = await d.read_status(adapter, event)
                state = 'verified' if d.matches_status(item['target_status'], current) else 'mismatch'
                result = await finish(policy.owner, item['id'], state, current)
            except ContractError:
                result = await finish(policy.owner, item['id'], 'pending', error='自动回查未完成，下轮继续回查')
            return {'state': result['status'], 'target': item['target_status'], 'reason': '恢复已有标记，仅回查，不重复写入'}
        raw = await read_event(adapter, event, eligible=True)
        if raw is None:
            await write('DELETE FROM monitor_auto_queue WHERE owner=? AND scope=? AND project=? AND event_key=?',
                        (policy.owner, policy.scope, policy.project, event_key))
            return {'state': 'skipped', 'reason': '事件已离开待处置、处置中及未加白范围'}
        current = raw.get('dealStatus')
        if type(current) is not int or current not in {0, 10}:
            raise ContractError('事件当前状态不符合自动标记条件')
        responses, failures = {}, []
        for kind in ENTITY_TYPES:
            try:
                responses[kind] = await d.operation(adapter, event, {'action': 'get_entities', 'uuid': event['id'], 'entity_type': kind},
                                                    f'核对自动标记证据：{kind}')
            except ContractError:
                failures.append(kind)
        decision = select_status(raw, responses, failures=failures)
        from .runtime import emit_message
        await emit_message(session_id, f'自动研判：{d.status_label(decision.target)}。{decision.reason}')
        # Recheck mutable event fields after evidence reads. A concurrent label or
        # new occurrence invalidates this decision; next round will re-evaluate.
        fresh = await read_event(adapter, event, eligible=True)
        fingerprint = lambda item: {key: item.get(key) for key in ('dealStatus', 'endTime', 'gptResult', 'whiteStatus', 'threatDefineName')}
        if fresh is None or fingerprint(fresh) != fingerprint(raw):
            return {'state': 'skipped', 'reason': '研判期间事件状态或内容变化，下轮重新分析'}
        identity = encode([policy.owner, policy.project, event_key, RULE_VERSION, decision.target, raw.get('endTime')])
        request_id = str(uuid5(NAMESPACE_URL, identity))
        existing = await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND id=?',
                              (policy.owner, policy.scope, request_id))
        if existing:
            # Completed requests never repeat writes, including after restarts.
            item = existing[0]
            status = 'verified' if d.matches_status(item['target_status'], current) else 'mismatch'
            await finish(policy.owner, request_id, status, current)
            return {'state': status, 'target': item['target_status'], 'reason': '同一事件版本已执行标记，仅核对状态'}
        comment = f'Flocks 自动状态研判：{decision.reason}（{RULE_VERSION}）'
        async with connection() as db:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute('INSERT INTO monitor_dispositions '
                             '(id,owner,scope,event_key,comment,status,session_id,created_at,updated_at,mode,target_status,project,decision) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (request_id, policy.owner, policy.scope, event_key, comment, 'writing', session_id,
                              d.stamp(), d.stamp(), 'automatic', decision.target, policy.project, encode(decision.json())))
        attempted = False
        try:
            if not d.matches_status(decision.target, current):
                attempted = True
                await d.operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']],
                                  'deal_status': decision.target, 'deal_comment': comment},
                                  f'自动标记 XDR：{d.status_label(decision.target)}')
            observed = await d.read_status(adapter, event)
            status = 'verified' if d.matches_status(decision.target, observed) else 'mismatch'
            await finish(policy.owner, request_id, status, observed)
        except (Exception, asyncio.CancelledError) as exc:
            status = 'pending' if attempted else 'failed'
            await finish(policy.owner, request_id, status, error='自动标记结果待回查' if attempted else '自动标记未执行')
            if isinstance(exc, asyncio.CancelledError):
                raise
        return {'state': status, 'target': decision.target, 'reason': decision.reason}


async def process_batch(policy, session_id, observed, recorder, adapter_factory=AutomaticAdapter):
    config = await settings(policy.owner)
    if not config['enabled'] or config['project'] != policy.project:
        return {'enabled': False, 'processed': 0, 'verified': 0, 'pending': 0}
    for event in observed:
        await write('INSERT OR IGNORE INTO monitor_auto_queue(owner,scope,project,event_key) VALUES(?,?,?,?)',
                    (policy.owner, policy.scope, policy.project, event['key']))
    queued = await rows('SELECT event_key FROM monitor_auto_queue WHERE owner=? AND scope=? AND project=? '
                        "ORDER BY EXISTS(SELECT 1 FROM monitor_dispositions d WHERE d.owner=monitor_auto_queue.owner "
                        "AND d.scope=monitor_auto_queue.scope AND d.project=monitor_auto_queue.project AND d.event_key=monitor_auto_queue.event_key "
                        "AND d.mode='automatic' AND d.status IN ('writing','pending','mismatch')) DESC, checked_at,event_key LIMIT ?", (policy.owner, policy.scope, policy.project, MAX_PER_ROUND))
    result = {'enabled': True, 'processed': 0, 'verified': 0, 'pending': 0}
    from .summaries import Summary
    deadline = monotonic() + 120
    for item in queued:
        if monotonic() >= deadline:
            break
        async def perform(_):
            try:
                outcome = await asyncio.wait_for(process_event(policy, item['event_key'], session_id, config['revision'], adapter_factory),
                                                 timeout=max(1, deadline - monotonic()))
            except (ContractError, TimeoutError, ValueError, FileNotFoundError):
                outcome = {'state': 'waiting', 'reason': '接入、范围或证据核对未完成，保留待跟进'}
            await write('UPDATE monitor_auto_queue SET checked_at=?,reason=? WHERE owner=? AND scope=? AND project=? AND event_key=?',
                        (d.stamp(), outcome['reason'], policy.owner, policy.scope, policy.project, item['event_key']))
            state_text = {'verified': '目标状态已确认', 'pending': '回查待确认', 'mismatch': '目标状态尚未生效',
                          'failed': '未执行', 'skipped': '本次未标记', 'waiting': '等待后续核对'}[outcome['state']]
            return outcome, outcome, Summary(f"自动标记：{outcome['reason']}；结果：{state_text}。")
        outcome = await recorder.call('自动研判与状态标记', {'rule': RULE_VERSION}, perform)
        result['processed'] += 1
        result['verified' if outcome['state'] == 'verified' else 'pending'] += 1
    return result
