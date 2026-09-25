"""Resolve the suite's installed agent, never its bundled or stale built-in copy."""
from pathlib import Path
import re

from flocks.agent.agent_factory import load_agent
from flocks.config.config import Config
from flocks.hub import local

AGENT_ID = 'security-monitor'
CONTRACT = 1


def definition():
    record = local.get_record('agent', AGENT_ID)
    if not record or not record.enabled or not record.installPath:
        raise ValueError('监测智能体组件未安装或已停用，请更新安全运营监测套件')
    try:
        root = Path(record.installPath).resolve()
        for name in ('agent.yaml', 'prompt.md'):
            path = root / name
            if not path.is_file() or not path.resolve().is_relative_to(root) or path.stat().st_size > 96000:
                raise ValueError('invalid agent payload')
        agent = load_agent(root, native=False)
        if (not agent or agent.name != AGENT_ID or not agent.prompt or agent.prompt_builder
                or agent.tools or agent.delegatable):
            raise ValueError('invalid monitoring role')
        if (type(agent.options.get('monitorInvestigationContract')) is not int
                or agent.options['monitorInvestigationContract'] != CONTRACT):
            raise ValueError('incompatible agent contract')
        return agent
    except (OSError, ValueError, RuntimeError):
        raise ValueError('监测智能体组件损坏或执行契约不兼容，请更新安全运营监测套件') from None


async def resolve():
    agent = definition()
    config = await Config.get()
    override = (config.agent or {}).get(AGENT_ID)
    if (override and override.disable) or (config.enabled_agents is not None and AGENT_ID not in config.enabled_agents):
        raise ValueError('监测智能体已被配置停用，请启用后再启动监测')
    # Preserve supported user prompt overlays, without inheriting broader tools
    # or executable prompt builders from a conflicting cached agent name.
    if override:
        if override.prompt:
            agent.prompt = override.prompt
        if override.prompt_append:
            agent.prompt += '\n\n' + override.prompt_append
    return agent


async def unavailable_reason():
    try:
        await resolve()
    except ValueError as exc:
        return str(exc)
    return None


def diagnostic_state():
    record = local.get_record('agent', AGENT_ID)
    state = {'installed': bool(record), 'enabled': bool(record and record.enabled),
             'version': record.version if record and re.fullmatch(r'[0-9][0-9A-Za-z.+_-]{0,63}', record.version) else None,
             'required_contract': CONTRACT, 'definition_valid': False}
    try:
        definition()
        state['definition_valid'] = True
    except ValueError:
        pass
    return state
