"""Opaque sandbox tool-policy metadata transport.

Flocks deliberately does not interpret ``sandbox.tools`` as an authorization
decision.  The optional Pro policy gate receives the raw global and agent
configuration and is the sole owner of validation, merging, and enforcement.
"""

from typing import Any, Dict, Mapping


def build_tool_policy_metadata(
    config_data: Mapping[str, Any] | None,
    agent_id: str | None,
) -> Dict[str, Any]:
    """Return raw sandbox tool-policy configuration for an extension hook.

    This copies containers only.  It intentionally does not normalize patterns,
    merge scopes, or decide whether any tool is allowed.
    """
    config = config_data if isinstance(config_data, Mapping) else {}
    global_sandbox = config.get("sandbox")
    global_tools = (
        global_sandbox.get("tools", {})
        if isinstance(global_sandbox, Mapping)
        else {}
    )
    agents = config.get("agent")
    agent_config = (
        agents.get(agent_id)
        if isinstance(agents, Mapping) and agent_id
        else None
    )
    agent_sandbox = (
        agent_config.get("sandbox")
        if isinstance(agent_config, Mapping)
        and isinstance(agent_config.get("sandbox"), Mapping)
        else {}
    )
    agent_tools = (
        agent_sandbox.get("tools", {})
        if isinstance(agent_sandbox, Mapping)
        else {}
    )
    return {
        "source": "sandbox.tool_policy",
        "global": dict(global_tools) if isinstance(global_tools, Mapping) else global_tools,
        "agent": dict(agent_tools) if isinstance(agent_tools, Mapping) else agent_tools,
    }
