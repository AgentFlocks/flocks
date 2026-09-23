"""Sangfor XDR read-only contract; no device requests happen during discovery."""
import json
from flocks.tool.registry import ToolContext, ToolRegistry

ALLOWED_ACTIONS = frozenset({'list', 'get_entities', 'get_proof'})


class ContractError(RuntimeError):
    pass


async def discover():
    from flocks.tool.device.store import list_devices, get_device_tool_enabled
    await ToolRegistry.init_async()
    candidates = []
    enabled_devices = 0
    configured_devices = 0
    for device in await list_devices():
        if not device.enabled or device.service_id != 'sangfor_xdr':
            continue
        enabled_devices += 1
        if not all(device.fields_set.get(key) for key in ('host', 'auth_code')):
            continue
        configured_devices += 1
        for info in ToolRegistry.list_tools():
            if (info.name.startswith('sangfor_xdr_incidents') and info.enabled
                    and info.source == 'device' and info.provider == device.storage_key
                    and not info.requires_confirmation
                    and await get_device_tool_enabled(device.id, info.name) is not False):
                schema = info.get_schema().to_json_schema()
                properties = schema.get('properties', {})
                if {'action', 'start_time', 'end_time', 'page_num', 'page_size', 'uuid', 'entity_type'} <= properties.keys():
                    candidates.append((device.id, info.name))
    if len(candidates) != 1:
        reason = ('未找到已启用的 XDR 接入，请先在设备接入中添加并启用 XDR' if not enabled_devices else
                  'XDR 接入缺少地址或认证信息，请在设备接入中补全' if not configured_devices else
                  '存在多个可用 XDR 接入，当前仅支持唯一监测目标' if len(candidates) > 1 else
                  'XDR 事件查询工具不可用，请检查工具启用状态、确认要求及只读查询能力')
        return [], None, reason
    device, tool = candidates[0]
    return [device], tool, None


class XdrAdapter:
    def __init__(self, policy, session_id):
        self.policy, self.session_id = policy, session_id

    async def call(self, device, params, message_id):
        if params.get('action') not in ALLOWED_ACTIONS or device not in self.policy.devices:
            raise PermissionError('Monitoring permits only bound read-only XDR actions')
        tool = ToolRegistry.get(self.policy.tool)
        if tool is None or not tool.info.enabled or tool.info.requires_confirmation:
            raise ContractError('只读查询能力不可用或需要确认')
        result = await ToolRegistry.execute(
            self.policy.tool,
            ctx=ToolContext(session_id=self.session_id, message_id=message_id, agent='rex'),
            device_id=device, **params,
        )
        if not result.success:
            # Device errors may include credentials or raw HTTP bodies. Persist a
            # bounded code; the protected device subsystem retains diagnostics.
            raise ContractError('设备查询失败或权限不足；请检查接入状态')
        value = result.output
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                raise ContractError('XDR 返回非 JSON 数据') from None
        if not isinstance(value, dict):
            raise ContractError('XDR 返回结构不符合契约')
        if value.get('success') is False or value.get('code') not in (None, 0, '0', 200, '200'):
            raise ContractError('XDR 业务查询失败')
        return value


def page_items(value):
    data = value.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('list'), list):
        raise ContractError('缺少 data.list；不能将未知响应视为空事件')
    items = data['list']
    if any(not isinstance(x, dict) or not isinstance(x.get('uuId'), str) or not x['uuId'] for x in items):
        raise ContractError('事件缺少稳定 uuId')
    total = data.get('total')
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total < 0):
        raise ContractError('分页 total 无效')
    return items, total


def normalize(device, raw):
    level = raw.get('riskLevel')
    # Low/info is not evidence of a benign event. No auto-ignore policy exists.
    risk = 'risk' if type(level) is int and 0 <= level <= 2 else 'unknown'
    return {
        'key': f'{device}:incident:{raw["uuId"]}', 'id': raw['uuId'], 'device': device,
        'name': str(raw.get('name') or '未命名事件')[:300],
        'risk': risk, 'riskLevel': level if type(level) is int else None,
        'host': str(raw.get('hostIp') or '')[:200],
        'alertIds': [str(x)[:200] for x in raw.get('alertIds', [])] if isinstance(raw.get('alertIds'), list) else [],
        'closure': 'open', 'disposition': '未启用',
        'reason': 'XDR 风险等级 0–2' if risk == 'risk' else '缺少明确忽略依据，保持待判定',
    }
