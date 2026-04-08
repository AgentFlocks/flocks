from types import SimpleNamespace

from flocks.agent.toolset import (
    agent_declares_tool,
    normalize_declared_tool_names,
    resolve_agent_initial_tools,
)


def test_normalize_declared_tool_names_expands_mcp_alias() -> None:
    resolved = normalize_declared_tool_names(
        ["read", "__mcp_ip_query", "missing_tool"],
        available_tool_names=["read", "threatbook_mcp_ip_query", "websearch"],
    )

    assert resolved == ["read", "threatbook_mcp_ip_query"]


def test_agent_declares_tool_uses_explicit_tools_list() -> None:
    agent = SimpleNamespace(tools=["read", "websearch"])

    assert agent_declares_tool(agent, "read") is True
    assert agent_declares_tool(agent, "bash") is False


def test_agent_declares_tool_defaults_to_deny_when_tools_missing() -> None:
    agent = SimpleNamespace(tools=None)

    assert agent_declares_tool(agent, "bash") is False


def test_resolve_agent_initial_tools_defaults_to_empty_when_unset() -> None:
    tools, permission = resolve_agent_initial_tools(
        raw_tools=None,
        legacy_permission_config=None,
        available_tool_names=["read", "bash"],
    )

    assert tools == []
    assert permission == []
