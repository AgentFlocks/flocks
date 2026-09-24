"""Sangfor XDR read-only contract; no device requests happen during discovery."""
import json
from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.tool.structured_output import OutputCapture, StructuredOutputError, bounded_copy
from . import diagnostics as diag

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
        with diag.tool_call(), diag.span('tool.execute', device=diag.opaque(device),
                                        action=params.get('action') if params.get('action') in ALLOWED_ACTIONS else 'other'):
            return await self._call(device, params, message_id)

    async def _call(self, device, params, message_id):
        if params.get('action') not in ALLOWED_ACTIONS or device not in self.policy.devices:
            diag.event('adapter.failure', failure=True, reason='permission')
            raise PermissionError('Monitoring permits only bound read-only XDR actions')
        tool = ToolRegistry.get(self.policy.tool)
        if tool is None or not tool.info.enabled or tool.info.requires_confirmation:
            diag.event('adapter.failure', failure=True, reason='unavailable')
            raise ContractError('只读查询能力不可用或需要确认')
        ctx = ToolContext(session_id=self.session_id, message_id=message_id, agent='rex')
        capture = OutputCapture(self.policy.tool)
        ctx._output_capture = capture
        try:
            result = await ToolRegistry.execute(
                self.policy.tool,
                ctx=ctx,
                device_id=device, **params,
            )
        finally:
            del ctx._output_capture
        diag.event('adapter.result', success=result.success, truncated=bool(result.truncated),
                   has_error=bool(result.error), **diag.shape(result.output))
        if not result.success:
            diag.event('adapter.failure', failure=True, reason='tool_failed')
            # Device errors may include credentials or raw HTTP bodies. Persist a
            # bounded code; the protected device subsystem retains diagnostics.
            raise ContractError('设备查询失败或权限不足；请检查接入状态')
        try:
            value = capture.take(result)
        except StructuredOutputError as exc:
            diag.event('adapter.failure', failure=True, reason='structured_output', structured_reason=str(exc), truncated=bool(result.truncated))
            raise ContractError('XDR 完整响应不可用或超过处理预算，未提交本页数据') from None
        diag.event('adapter.structured', **diag.shape(value))
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, RecursionError) as exc:
                diag.event('adapter.failure', failure=True, reason='non_json', truncated=bool(result.truncated),
                           error_type=type(exc).__name__, json_position=getattr(exc, 'pos', None),
                           json_line=getattr(exc, 'lineno', None), json_column=getattr(exc, 'colno', None),
                           **diag.shape(value))
                raise ContractError('XDR 返回非 JSON 数据') from None
            try:
                value = bounded_copy(value)
            except StructuredOutputError as exc:
                diag.event('adapter.failure', failure=True, reason='structured_output', structured_reason=str(exc))
                raise ContractError('XDR 响应结构超过处理预算，未提交本页数据') from None
        diag.event('adapter.decoded', **diag.shape(value))
        if not isinstance(value, dict):
            diag.event('adapter.failure', failure=True, reason='not_object')
            raise ContractError('XDR 返回结构不符合契约')
        code = value.get('code')
        code_ok = code is None or (type(code) in (int, str) and code in (0, '0', 200, '200', 'Success'))
        if value.get('success') is False or not code_ok:
            diag.event('adapter.failure', failure=True, reason='business_error')
            raise ContractError('XDR 业务查询失败')
        return value


def response_items(data):
    # Existing XDR handler fixtures use data.item; tool descriptions also
    # advertise data.list. Accept these explicit contracts, never arbitrary
    # nested lists or a missing field as an empty successful response.
    if not isinstance(data, dict):
        raise ContractError('缺少 data.list/data.item；不能将未知响应视为空事件')
    fields = [key for key in ('list', 'item') if key in data]
    if not fields or any(not isinstance(data[key], list) for key in fields):
        raise ContractError('缺少有效 data.list/data.item；不能将未知响应视为空事件')
    if len(fields) == 2 and data['list'] != data['item']:
        raise ContractError('data.list 与 data.item 冲突，查询完整性无法确认')
    return data[fields[0]]


def page_items(value):
    data = value.get('data')
    items = response_items(data)
    if any(not isinstance(x, dict) or not isinstance(x.get('uuId'), str) or not x['uuId'] for x in items):
        raise ContractError('事件缺少稳定 uuId')
    total = data.get('total')
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total < 0):
        raise ContractError('分页 total 无效')
    diag.event('page.validated', items=len(items), total=total)
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
