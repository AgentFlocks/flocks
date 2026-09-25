"""Discover local, device-bound read capabilities; never expose credentials."""
from dataclasses import dataclass, asdict
import asyncio
import ipaddress
import json
from pathlib import Path

from flocks.tool.registry import ToolRegistry, ToolContext
from flocks.tool.structured_output import OutputCapture, StructuredOutputError
from flocks.session.interaction_policy import investigation_call_scope
from .adapter import ContractError
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
        return asdict(self)


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
        for info in candidates:
            kind, skill = None, ''
            if device.id in policy.devices and info.name == policy.tool:
                kind = 'xdr'
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
            required_fields = ('host', 'auth_code') if kind == 'xdr' else ('base_url', 'api_key', 'secret')
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
                        'tdp': {'action', 'time_from', 'time_to', 'sql', 'size'},
                        'sig': {'action', 'body'}}[kind]
            if not required <= props.keys():
                unavailable.append(f'{device.name}：查询接口字段不符合受控契约')
                continue
            capabilities.append(Capability(f'cap-{len(capabilities)+1}', device.id, info.name, kind,
                                           device.name[:100], str(device.status), skill))
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


async def query(policy, session_id, message_id, capability, event, kind):
    await assert_active(policy)
    current, _ = await discover(policy)
    if not any((c.device, c.tool, c.kind) == (capability.device, capability.tool, capability.kind) for c in current):
        raise ContractError('数据源或工具已停用，未执行查询')
    if capability.kind == 'xdr':
        if capability.device != event['device'] or kind not in {'host', 'file', 'process', 'ip', 'innerip', 'dns', 'proof'}:
            raise ContractError('只能查询原事件的受控实体或举证信息')
        params = {'action': 'get_proof' if kind == 'proof' else 'get_entities', 'uuid': event['id']}
        if kind != 'proof':
            params['entity_type'] = kind
    else:
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
    capture = OutputCapture(capability.tool)
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
        return value, params
    except (StructuredOutputError, ValueError, TimeoutError):
        raise ContractError('关联查询结果不完整、无法解析或超时；不能视为没有风险') from None
    finally:
        del context._output_capture


# Only these known security facts enter model context. No command lines,
# arbitrary HTTP payloads, credentials, authentication headers or raw mail.
FACT_KEYS = frozenset({'data', 'item', 'list', 'items', 'records', 'result', 'results', 'total', 'count',
    'id', 'uuId', 'uuid', 'name', 'hostId', 'hostIp', 'ip', 'srcIp', 'destIp', 'src_ip', 'dest_ip',
    'net.src_ip', 'net.dest_ip', 'threat.name', 'threat.level', 'threat.result', 'time', 'timestamp',
    'startTime', 'endTime', 'fileName', 'fileHash', 'md5', 'sha256', 'processName', 'domain',
    'threatLevel', 'incidentSeverity', 'dealStatus', 'gptResult', 'threatDefineName',
    'edrDealStatusInfo', 'ndrDealStatusInfo', 'status', 'isPermanent', 'expireTime'})


def facts(value):
    budget, omitted = [100], [False]
    def keep(item, depth=0):
        if depth > 8 or budget[0] <= 0:
            omitted[0] = True
            return None
        budget[0] -= 1
        if isinstance(item, dict):
            if depth and any(key not in FACT_KEYS for key in item):
                omitted[0] = True
            total = item.get('total')
            listed = [val for key, val in item.items() if key in {'item', 'list', 'items', 'records'} and isinstance(val, list)]
            if type(total) is int and listed and any(total > len(val) for val in listed):
                omitted[0] = True
            return {key: keep(val, depth+1) for key, val in item.items() if key in FACT_KEYS}
        if isinstance(item, list):
            if len(item) > 20:
                omitted[0] = True
            return [keep(val, depth+1) for val in item[:20]]
        if isinstance(item, str):
            if len(item) > 160:
                omitted[0] = True
            return item[:160]
        return item if item is None or type(item) in (bool, int, float) else None
    safe = keep(value)
    if not safe or len(json.dumps(safe)) > 4000:
        return {}, True
    return safe, omitted[0]
