"""Compatibility for the retired evidence-only automatic marking setting."""
from pydantic import BaseModel, ConfigDict, StrictBool
from flocks.session.interaction_policy import require_interactive
from . import disposition as d
from .adapter import ContractError, page_items
from .models import COMPONENT_ID
from .store import write

_config_locks = {}

class AutomaticRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool

async def settings(owner):
    return {'enabled': False, 'revision': '', 'project': None}

async def configure(owner, enabled):
    await require_interactive()
    from .lifecycle import _owned_installation
    await _owned_installation(owner)
    if enabled:
        raise ValueError('已改为邮件反馈处置，请配置并启用邮件跟进')
    await write('UPDATE monitor_auto_settings SET enabled=0 WHERE owner=? AND scope=?', (owner, COMPONENT_ID))

async def authorized(policy, revision):
    raise ContractError('旧版直接自动标记已停用，请使用责任人邮件反馈闭环')

async def process_event(*args, **kwargs):
    raise ContractError('旧版直接自动标记已停用，请使用责任人邮件反馈闭环')

async def process_batch(*args, **kwargs):
    return {'enabled': False, 'processed': 0, 'verified': 0, 'pending': 0}


async def read_event(adapter, event, *, eligible=False):
    params = {'action': 'list', 'uuids': [event['id']], 'start_time': 0,
              'end_time': int(d.datetime.now(d.timezone.utc).timestamp()), 'time_field': 'endTime',
              'page_num': 1, 'page_size': 5, 'white_status': ['未加白', '部分加白'] if eligible else [],
              'deal_statuses': [0, 10] if eligible else []}
    value = await d.operation(adapter, event, params, '发信前按编号确认原事件' if eligible else '回查事件当前状态')
    items, total = page_items(value)
    if eligible and not items and total in (None, 0):
        return None
    if len(items) != 1 or items[0]['uuId'] != event['id'] or total not in (None, 1):
        raise ContractError('未返回唯一目标事件，不能自动标记')
    return items[0]
