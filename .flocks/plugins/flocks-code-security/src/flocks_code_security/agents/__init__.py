"""Load and register the plugin's declarative Agent definitions."""

from __future__ import annotations

from pathlib import Path

from flocks.agent.agent import AgentInfo
from flocks.agent.agent_factory import load_agent
from flocks.agent.registry import Agent

from flocks_code_security.projection import AGENT_TOOLS


AGENTS_ROOT = Path(__file__).parent
REGISTERED_CODE_SECURITY_AGENTS: dict[str, AgentInfo] = {}


def register_agents() -> None:
    definitions: list[AgentInfo] = []
    for name in AGENT_TOOLS:
        agent = load_agent(AGENTS_ROOT / name, native=False)
        if agent is None:
            raise RuntimeError(f"Failed to load code-security Agent: {name}")
        definitions.append(agent)

    for agent in definitions:
        existing = Agent._custom_agents.get(agent.name)
        registered = REGISTERED_CODE_SECURITY_AGENTS.get(agent.name)
        if existing is not None and existing is not registered:
            raise RuntimeError(
                f"Refusing to overwrite existing agent registration: {agent.name}"
            )

    for agent in definitions:
        if Agent._custom_agents.get(agent.name) is not None:
            continue
        Agent.register(agent.name, agent)
        REGISTERED_CODE_SECURITY_AGENTS[agent.name] = agent
    Agent.invalidate_cache()
