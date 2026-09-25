"""Human confirmation and shared status audit/readback primitives.

Scheduled sessions retain their read-only policy. These operations use separate
owner-bound sessions and a per-call guard before and after tool hooks.
"""
import asyncio
import json
from functools import wraps
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from flocks.session.session import Session
from flocks.session.interaction_policy import require_interactive
from .adapter import XdrAdapter, ContractError, page_items
from .models import COMPONENT_ID, MonitoringPolicy
from .store import connection, rows, write
from . import diagnostics as diag

_locks: dict[tuple, asyncio.Lock] = {}
# Read contract for incidents/list; 30 is 已防护 and is never a writable target
# or closure evidence. incidents/status_list uses a different 1–6 enum.
LIST_RESPONSE_STATUSES = {0, 10, 30, 40, 50, 60, 70}


def traced(stage):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(owner, request, *args, **kwargs):
            request_id = str(request.request_id) if isinstance(request, DispositionRequest) else request
            async with diag.trace_scope(owner, COMPONENT_ID, request_id) as trace:
                with diag.span(stage):
                    result = await fn(owner, request, *args, **kwargs)
                trace.outcome = 'completed' if result['status'] == 'verified' else 'partial' if result['status'] in ('pending', 'mismatch', 'writing') else 'failed'
                return result
        return wrapped
    return decorate


class DispositionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    event_key: str = Field(min_length=1, max_length=1000)
    comment: str = Field(min_length=1, max_length=2048)
    confirmed: StrictBool

    @field_validator('confirmed')
    @classmethod
    def explicitly_confirmed(cls, value):
        if value is not True:
            raise ValueError('需要用户确认处置')
        return value

    @field_validator('comment')
    @classmethod
    def nonempty_comment(cls, value):
        if not value.strip():
            raise ValueError('请填写处置说明')
        return value.strip()


class DispositionAdapter(XdrAdapter):
    allowed_actions = frozenset({'list', 'update_status'})

    async def call(self, device, params, message_id):
        await require_interactive(self.session_id)
        return await super().call(device, params, message_id)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def matches_status(target_status, observed):
    # Native list maps DB contained(6) back to TMG protected(30).
    return type(observed) is int and (observed == target_status or target_status == 70 and observed == 30)


def status_label(value):
    return {0: '待处置', 10: '处置中', 30: '已防护', 40: '处置完成', 50: '已挂起', 60: '忽略（接受风险）', 70: '已遏制'}.get(value, '未知状态')


async def record(owner, request_id):
    result = await rows('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND id=?',
                        (owner, COMPONENT_ID, request_id))
    if not result:
        raise FileNotFoundError('未找到处置记录')
    return result[0]


async def target(owner, event_key):
    from flocks.hub import local
    entry = local.get_record('component', COMPONENT_ID)
    installations = await rows('SELECT * FROM monitor_installations WHERE owner=? AND scope=? AND installed=1',
                               (owner, COMPONENT_ID))
    if not installations:
        raise FileNotFoundError('当前用户未安装安全运营监测')
    if not entry or not entry.enabled:
        raise ValueError('场景已停用，请先启用场景')
    policy = MonitoringPolicy.model_validate_json(installations[0]['policy'])
    found = await rows('SELECT o.data FROM monitor_observations o JOIN monitor_attempts a ON a.id=o.attempt_id '
                       'WHERE a.owner=? AND a.scope=? AND a.project=? AND o.event_key=? ORDER BY a.sequence DESC LIMIT 1',
                       (owner, COMPONENT_ID, policy.project, event_key))
    if not found:
        raise FileNotFoundError('未找到当前用户的监测事件')
    event = json.loads(found[0]['data'])
    if event['device'] not in policy.devices:
        raise ValueError('事件所属设备已不在当前监测范围，请检查接入配置')
    from flocks.project.project import Project
    if Project.registry_state(policy.project, owner_id=owner) != 'active':
        raise ValueError('监测项目不可用')
    return policy, event


async def operation(adapter, event, params, text):
    from .runtime import emit_message
    message = await emit_message(adapter.session_id, text)
    return await asyncio.wait_for(adapter.call(event['device'], params, message.id), timeout=30)


async def read_status(adapter, event):
    # Remove both monitoring filters and the incremental time window. A closed
    # or whitelisted event must remain observable by its exact immutable UUID.
    value = await operation(adapter, event, {
        'action': 'list', 'uuids': [event['id']], 'start_time': 0,
        'end_time': int(datetime.now(timezone.utc).timestamp()), 'time_field': 'endTime',
        'page_num': 1, 'page_size': 5, 'white_status': [], 'deal_statuses': [],
    }, '按事件 ID 回查 XDR 当前处置状态')
    items, total = page_items(value)
    if len(items) != 1 or items[0]['uuId'] != event['id'] or total not in (None, 1):
        raise ContractError('未返回唯一目标事件，不能确认闭环')
    status = items[0].get('dealStatus')
    if type(status) is not int or status not in LIST_RESPONSE_STATUSES:
        raise ContractError('XDR 处置状态缺失或无法识别，不能确认闭环')
    return status


async def finish(owner, request_id, status, observed=None, error=None, *, defer_exports=False):
    await write('UPDATE monitor_dispositions SET status=?,observed_status=?,error=?,updated_at=? '
                'WHERE owner=? AND scope=? AND id=?',
                (status, observed, error, stamp(), owner, COMPONENT_ID, request_id))
    result = await record(owner, request_id)
    from .runtime import emit_message
    if result['session_id']:
        try:
            await emit_message(result['session_id'], {
                'verified': f"XDR 回查确认：{status_label(result['target_status'])}。",
                'pending': '处置结果待确认，请回查状态；不要重复写回。',
                'failed': '处置未执行。',
                'mismatch': 'XDR 回查状态与目标不匹配，保持待确认。',
            }[status] + (f' {error}' if error else ''))
        except Exception:
            pass  # Durable audit and report remain available if message persistence fails.
    from .reports import export_report
    days = await rows('SELECT DISTINCT a.business_date FROM monitor_attempts a JOIN monitor_observations o ON o.attempt_id=a.id '
                      'WHERE a.owner=? AND a.scope=? AND o.event_key=?', (owner, COMPONENT_ID, result['event_key']))
    for day in days:
        await write("INSERT INTO monitor_reports(owner,scope,business_date,status) VALUES(?,?,?,'pending') "
                    "ON CONFLICT(owner,scope,business_date) DO UPDATE SET status='pending'", (owner, COMPONENT_ID, day['business_date']))
        if not defer_exports:
            await export_report(owner, COMPONENT_ID, day['business_date'])
    return result


@traced('disposition.confirm')
async def confirm(owner, request: DispositionRequest, adapter_factory=DispositionAdapter):
    request_id = str(request.request_id)
    async with _locks.setdefault((owner, request.event_key), asyncio.Lock()):
        policy, event = await target(owner, request.event_key)
        async with connection() as db:
            await db.execute('BEGIN IMMEDIATE')
            cur = await db.execute('SELECT * FROM monitor_dispositions WHERE owner=? AND scope=? AND id=?',
                                   (owner, COMPONENT_ID, request_id))
            existing = await cur.fetchone()
            if existing:
                if existing['event_key'] != request.event_key or existing['comment'] != request.comment:
                    raise ValueError('请求标识已用于不同处置内容')
                return dict(existing)  # Replaying a request never repeats a write.
            cur = await db.execute("SELECT id FROM monitor_dispositions WHERE owner=? AND scope=? AND event_key=? AND status IN ('writing','pending')",
                                   (owner, COMPONENT_ID, request.event_key))
            if await cur.fetchone():
                raise ValueError('已有处置结果待确认，请先回查状态，勿重复提交')
            await db.execute('INSERT INTO monitor_dispositions(id,owner,scope,event_key,comment,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                             (request_id, owner, COMPONENT_ID, request.event_key, request.comment, 'writing', stamp(), stamp()))
        attempted = False
        try:
            session = await Session.create(project_id=policy.project, directory=policy.directory,
                                           title='安全运营监测 · 人工处置确认', owner_user_id=owner,
                                           metadata={'monitorDisposition': request_id})
            await write('UPDATE monitor_dispositions SET session_id=? WHERE owner=? AND scope=? AND id=?',
                        (session.id, owner, COMPONENT_ID, request_id))
            from .runtime import emit_message
            from flocks.session.message import MessageRole
            await emit_message(session.id, f'确认将事件 {event["id"]} 写回为已处置（40）。处置说明：{request.comment}', role=MessageRole.USER)
            adapter = adapter_factory(policy, session.id)
            current = await read_status(adapter, event)
            if current == 40:
                return await finish(owner, request_id, 'verified', current)
            if current not in (0, 10):
                return await finish(owner, request_id, 'failed', current, 'XDR 状态已变化，仅允许确认待处置或处置中的事件')
            attempted = True  # Persisted writing intent precedes the only network mutation.
            await operation(adapter, event, {'action': 'update_status', 'uuids': [event['id']],
                                             'deal_status': 40, 'deal_comment': request.comment}, '写回 XDR：已处置（40）')
            current = await read_status(adapter, event)
            return await finish(owner, request_id, 'verified' if current == 40 else 'mismatch', current)
        except asyncio.CancelledError:
            await finish(owner, request_id, 'pending' if attempted else 'failed', error='操作中断，请回查状态')
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, ContractError) else '操作未完成，请检查接入与权限后回查状态'
            return await finish(owner, request_id, 'pending' if attempted else 'failed', error=error)


@traced('disposition.recheck')
async def recheck(owner, request_id, adapter_factory=DispositionAdapter):
    item = await record(owner, request_id)
    async with _locks.setdefault((owner, item['event_key']), asyncio.Lock()):
        policy, event = await target(owner, item['event_key'])
        item = await record(owner, request_id)
        if item['mode'] in ('automatic', 'mail') and item['project'] != policy.project:
            raise ValueError('自动标记记录不属于当前监测项目')
        # Another process may still be sending the original request. Recovery
        # after a crash is read-only and becomes available after its call budget.
        if item['status'] == 'writing' and (datetime.now(timezone.utc) - datetime.fromisoformat(item['updated_at'])).total_seconds() < 120:
            raise ValueError('处置正在执行，请稍后回查')
        if not item['session_id']:
            return await finish(owner, request_id, 'failed', error='处置尚未执行，请重新确认')
        try:
            factory = XdrAdapter if item['mode'] == 'automatic' and adapter_factory is DispositionAdapter else adapter_factory
            current = await read_status(factory(policy, item['session_id']), event)
        except Exception as exc:
            error = str(exc) if isinstance(exc, ContractError) else '回查失败，请检查接入与权限后重试'
            return await finish(owner, request_id, 'pending', error=error)
        return await finish(owner, request_id, 'verified' if matches_status(item['target_status'], current) else 'mismatch', current)
