"""Discover local, device-bound read capabilities; never expose credentials."""
from dataclasses import dataclass, asdict
import asyncio
import ipaddress
import json
from pathlib import Path

from flocks.tool.registry import ToolRegistry, ToolContext
from flocks.tool.structured_output import OutputCapture, StructuredOutputError
from flocks.session.interaction_policy import investigation_call_scope
from .adapter import ContractError, response_items
from .store import rows


@dataclass(frozen=True)
class Capability:
    id: str
    device: str
    tool: str
    kind: str
    name: str
    status: str
    skill: str = ''

    def json(self):
        return {**asdict(self), 'entities': list(self.entities)}

    @property
    def entities(self):
        if self.kind == 'xdr_alerts':
            return ('alerts', 'alert_proof')
        return ('host', 'file', 'process', 'ip', 'innerip', 'dns', 'proof') if self.kind == 'xdr' else ('related',)


def for_event(catalog, event):
    """Never advertise another XDR device as a source for this event's UUID."""
    return [cap for cap in catalog if cap.kind not in {'xdr', 'xdr_alerts'} or cap.device == event.get('device')]


def resolve_choice(catalog, event, name):
    """Resolve an ID or a unique exact tool alias, without guessing a device."""
    allowed = for_event(catalog, event)
    exact = [cap for cap in allowed if cap.id == name]
    if len(exact) == 1:
        return exact[0], None
    # Base tool aliases are useful for registered names suffixed with __device.
    # Do not use partial, fuzzy or display-name matching.
    aliases = [cap for cap in allowed if name in {cap.tool, cap.tool.split('__')[0]}]
    if len(aliases) == 1:
        return aliases[0], None
    if aliases or len(exact) > 1:
        return None, 'ambiguous_capability'
    if any(cap.id == name for cap in catalog):
        return None, 'wrong_device'
    return None, 'unknown_capability'


async def tdp_skill():
    from flocks.skill.skill import Skill
    skill = await Skill.get('tdp-use')
    if skill is None or Skill.is_disabled(skill.name):
        raise ContractError('TDP 使用技能未安装或已停用')
    base = Path(skill.location)
    texts = [base.read_text(), (base.parent / 'references/api-reference.md').read_text()]
    if sum(map(len, texts)) > 80000:
        raise ContractError('TDP 技能超过读取预算')
    return '\n'.join(texts)


async def discover(policy, *, include_unbound=False):
    from flocks.tool.device.store import list_devices, get_device_tool_enabled
    await ToolRegistry.init_async()
    capabilities, unavailable = [], []
    for device in await list_devices():
        if not device.enabled:
            continue
        if not include_unbound and device.id not in {*policy.devices, *policy.correlation_devices}:
            continue
        candidates = [t for t in ToolRegistry.list_tools() if t.enabled and t.source == 'device'
                      and t.provider == device.storage_key and not t.requires_confirmation]
        alert_sources = []
        for info in candidates:
            kind, skill = None, ''
            if device.id in policy.devices and info.name == policy.tool_for(device.id):
                kind = 'xdr'
            elif (device.id in policy.devices and device.service_id == 'sangfor_xdr'
                  and info.name.split('__')[0] == 'sangfor_xdr_alerts'):
                kind = 'xdr_alerts'
            elif device.service_id == 'tdp_api' and info.name.split('__')[0] == 'tdp_log_search':
                try:
                    await tdp_skill()
                except (OSError, ContractError):
                    unavailable.append('TDP：缺少可用的使用技能及接口说明，未开放自动查询')
                    continue
                kind, skill = 'tdp', 'tdp-use'
            elif device.service_id == 'onesig_api' and info.name.split('__')[0] == 'onesig_strategy_api_query':
                kind = 'sig'
            if not kind or await get_device_tool_enabled(device.id, info.name) is False:
                continue
            required_fields = ('host', 'auth_code') if kind in {'xdr', 'xdr_alerts'} else ('base_url', 'api_key', 'secret')
            if not all(device.fields_set.get(field) for field in required_fields):
                unavailable.append(f'{device.name}：设备地址或认证配置不完整，未开放自动查询')
                continue
            props = info.get_schema().to_json_schema().get('properties', {})
            # Tools with caller-supplied target/credentials are not suitable for
            # unattended use, even when described as read-only.
            if {'base_url', 'host', 'auth_state_path', 'url', 'headers', 'token'} & props.keys():
                unavailable.append(f'{device.name}：工具允许覆盖地址或认证，尚未开放自动查询')
                continue
            required = {'xdr': {'action', 'uuid', 'entity_type'},
                        'xdr_alerts': {'action', 'uuid', 'uuids', 'start_time', 'end_time',
                                       'time_field', 'page_num', 'page_size'},
                        'tdp': {'action', 'time_from', 'time_to', 'sql', 'size'},
                        'sig': {'action', 'body'}}[kind]
            if not required <= props.keys():
                unavailable.append(f'{device.name}：查询接口字段不符合受控契约')
                continue
            if kind == 'xdr_alerts':
                actions = props['action'].get('enum', [])
                if not isinstance(actions, list) or not all(action in actions for action in ('list', 'get_proof')):
                    unavailable.append(f'{device.name}：告警接口缺少只读列表或举证动作，未开放关联告警查询')
                    continue
                alert_sources.append((info, skill))
            else:
                capabilities.append(Capability(f'cap-{len(capabilities)+1}', device.id, info.name, kind,
                                               device.name[:100], str(device.status), skill))
        if device.id in policy.devices and device.service_id == 'sangfor_xdr':
            if len(alert_sources) == 1:
                info, skill = alert_sources[0]
                capabilities.append(Capability(f'cap-{len(capabilities)+1}', device.id, info.name, 'xdr_alerts',
                                               device.name[:100], str(device.status), skill))
            else:
                reason = '存在多个匹配工具' if alert_sources else '工具缺失、停用或契约不完整'
                unavailable.append(f'{device.name}：关联告警查询{reason}；仍可调查事件及实体，HTTP请求响应可能缺失')
        if 'edr' in device.service_id.lower():
            unavailable.append(f'{device.name}：当前 EDR 工具尚缺少受控设备绑定，未自动调用')
    return capabilities, list(dict.fromkeys(unavailable))[:30]


async def assert_active(policy):
    from flocks.auth.context import get_current_auth_user
    from flocks.hub import local
    from .models import MonitoringPolicy
    user = get_current_auth_user()
    record = local.get_record('component', policy.scope)
    saved = await rows('SELECT i.policy FROM monitor_installations i JOIN task_schedulers s ON s.id=i.scheduler_id '
                       "WHERE i.owner=? AND i.project=? AND i.scope=? AND i.installed=1 AND i.ready=1 AND s.status='active'",
                       (policy.owner, policy.project, policy.scope))
    if (not user or user.id != policy.owner or not record or not record.enabled or not saved
            or MonitoringPolicy.model_validate_json(saved[0]['policy']) != policy):
        raise ContractError('监测已暂停或项目授权已变化')


async def tdp_timestamp():
    # The TDP skill requires a dynamically calculated Python timestamp through
    # uv. The command is constant: no model/query text reaches a shell.
    process = await asyncio.create_subprocess_exec(
        'bash', '-c', 'uv run --no-project python -c "from datetime import datetime, timezone; print(int(datetime.now(timezone.utc).timestamp()))"',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 15)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode or not output.strip().isdigit():
        raise ContractError('TDP 动态时间计算失败')
    return int(output)


MAX_RELATED_ALERTS = 5


def related_alert_ids(event):
    """Only IDs persisted from the original XDR event, never model targets."""
    values = event.get('alertIds')
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if isinstance(value, str)
                              and value and value not in {'.', '..'} and len(value) <= 200
                              and not any(char in value for char in '/\\?#\r\n')))


def _bound_alert_response(value, params, *, incomplete=False):
    """Do not accept another event's alert as evidence, even from a valid tool."""
    data = value.get('data')
    if params['action'] == 'get_proof':
        if not isinstance(data, dict) or data.get('uuId') != params['uuid']:
            raise ContractError('告警举证编号与原事件关联告警不一致，未采用该证据')
        return value
    items = response_items(data)
    wanted = set(params['uuids'])
    if any(not isinstance(item, dict) or not isinstance(item.get('uuId'), str)
           or item['uuId'] not in wanted for item in items):
        raise ContractError('告警列表包含原事件之外的告警，未采用该证据')
    identifiers = [item['uuId'] for item in items]
    total = data.get('total')
    if total is not None and (type(total) is not int or total < 0):
        raise ContractError('关联告警列表 total 无效，未采用该证据')
    incomplete = (incomplete or set(identifiers) != wanted or len(identifiers) != len(set(identifiers))
                  or (total is not None and total != len(items)))
    return {**value, '_monitor_partial': incomplete}


def _bound_incident_proof(value, identifier):
    """The documented incident proof response is data[].uuId, not an ID-less blob."""
    items = value.get('data')
    if (not isinstance(items, list) or not items
            or any(not isinstance(item, dict) or item.get('uuId') != identifier for item in items)):
        raise ContractError('事件举证未明确匹配原事件编号，未采用其时间线或关联告警编号')
    return value


async def query(policy, session_id, message_id, capability, event, kind, *, alert_id=''):
    await assert_active(policy)
    current, _ = await discover(policy)
    if not any((c.device, c.tool, c.kind) == (capability.device, capability.tool, capability.kind) for c in current):
        raise ContractError('数据源或工具已停用，未执行查询')
    coverage_incomplete = False
    if capability.kind == 'xdr_alerts':
        identifiers = related_alert_ids(event)
        if capability.device != event['device'] or kind not in {'alerts', 'alert_proof'}:
            raise ContractError('只能查询原设备中与本事件关联的告警')
        if not identifiers:
            raise ContractError('原事件没有可信的关联告警编号，未执行告警查询')
        if kind == 'alert_proof':
            if alert_id not in identifiers:
                raise ContractError('告警编号不属于原事件的关联告警，未执行查询')
            params = {'action': 'get_proof', 'uuid': alert_id}
        else:
            if alert_id:
                raise ContractError('关联告警列表不接受额外告警目标')
            window = event.get('investigationWindow') or {}
            start, end = window.get('start'), window.get('end')
            if type(start) is not int or type(end) is not int or not 0 < end - start <= 86400:
                raise ContractError('原事件关联告警查询时间范围无效')
            # The handler always adds a time filter, including UUID-list reads.
            # Earlier related alerts must not disappear just because this
            # monitoring run's window starts later than the incident itself.
            first = event.get('startTime')
            if type(first) is int and 0 <= first <= end:
                start = min(start, first)
            else:
                coverage_incomplete = True
            params = {'action': 'list', 'uuids': identifiers[:MAX_RELATED_ALERTS],
                      'start_time': start, 'end_time': end, 'time_field': 'lastTime',
                      'page_num': 1, 'page_size': MAX_RELATED_ALERTS}
    elif capability.kind == 'xdr':
        if alert_id:
            raise ContractError('事件实体查询不接受告警目标')
        if capability.device != event['device'] or kind not in {'host', 'file', 'process', 'ip', 'innerip', 'dns', 'proof'}:
            raise ContractError('只能查询原事件的受控实体或举证信息')
        params = {'action': 'get_proof' if kind == 'proof' else 'get_entities', 'uuid': event['id']}
        if kind != 'proof':
            params['entity_type'] = kind
    else:
        if alert_id:
            raise ContractError('跨设备关联查询不接受告警目标')
        # Cross-device lookup is constrained to the root host and time window;
        # the model cannot invent an unrelated target or widen the query.
        try:
            host = str(ipaddress.ip_address(event['host']))
            if '%' in host:
                raise ValueError('Scoped IPv6 is not a cross-device asset identifier')
        except ValueError:
            raise ContractError('原事件缺少有效主机 IP，不能可靠地定位跨设备查询') from None
        if capability.kind == 'tdp':
            await tdp_skill()
            end = min(await tdp_timestamp(), event['investigationWindow']['end'])
            start = event['investigationWindow']['start']
            if not 0 < end - start <= 86400:
                raise ContractError('TDP 关联时间范围无效或超过 24 小时')
            params = {'action': 'search', 'time_from': start, 'time_to': end,
                      'net_data_type': ['attack', 'risk', 'action'], 'size': 50,
                      'sql': f"net.src_ip = '{host}' OR net.dest_ip = '{host}'"}
        else:
            params = {'action': 'asset_list', 'body': {'pageNo': 1, 'pageSize': 50, 'search': host}}
    capture = OutputCapture(capability.tool, private=True)
    context = ToolContext(session_id=session_id, message_id=message_id, agent='security-monitor')
    context._output_capture = capture
    try:
        with investigation_call_scope(capability.tool, capability.device, params, session_id):
            result = await asyncio.wait_for(ToolRegistry.execute(capability.tool, ctx=context,
                                                                device_id=capability.device, **params), 90)
        if not result.success:
            raise ContractError('关联查询失败，请核对设备接入、权限及连接')
        value = capture.take(result)
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict) or value.get('success') is False:
            raise ContractError('关联查询返回无效结构或失败结果')
        if type(value.get('code')) is bool or value.get('code') not in (None, 0, '0', 200, '200', 'Success'):
            raise ContractError('关联查询返回业务错误')
        if capability.kind == 'xdr_alerts':
            value = _bound_alert_response(value, params,
                                          incomplete=coverage_incomplete or len(identifiers) > MAX_RELATED_ALERTS)
        elif capability.kind == 'xdr' and kind == 'proof':
            value = _bound_incident_proof(value, event['id'])
        return value, params
    except (StructuredOutputError, ValueError, TimeoutError):
        raise ContractError('关联查询结果不完整、无法解析或超时；不能视为没有风险') from None
    finally:
        del context._output_capture


def facts(value):
    from .event_details import safe_facts
    partial = bool(isinstance(value, dict) and value.get('_monitor_partial'))
    source = {key: item for key, item in value.items() if key != '_monitor_partial'} if isinstance(value, dict) else value
    safe, omitted = safe_facts(source)
    return safe, partial or omitted
