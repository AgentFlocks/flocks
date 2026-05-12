from types import SimpleNamespace

from flocks.agent.toolset import (
    agent_declares_tool,
    get_all_enabled_builtin_tool_names,
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


def test_get_all_enabled_builtin_tool_names_excludes_plugins_and_disabled(monkeypatch) -> None:
    tools = [
        SimpleNamespace(name="read", enabled=True, native=True, source=None),
        SimpleNamespace(name="bash", enabled=True, native=True, source="builtin"),
        SimpleNamespace(name="project_tool", enabled=True, native=True, source="plugin_yaml"),
        SimpleNamespace(name="user_tool", enabled=True, native=False, source="plugin_py"),
        SimpleNamespace(name="mcp_lookup", enabled=True, native=False, source="mcp"),
        SimpleNamespace(name="disabled_tool", enabled=False, native=True, source=None),
        SimpleNamespace(name="invalid", enabled=True, native=True, source=None),
    ]

    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.init", lambda: None)
    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.list_tools", lambda: tools)

    assert get_all_enabled_builtin_tool_names() == ["read", "bash"]


def test_resolve_agent_initial_tools_expands_empty_rex_tools_to_builtin_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        "flocks.agent.toolset.get_all_enabled_builtin_tool_names",
        lambda: ["read", "bash", "tool_search"],
    )

    tools, permission = resolve_agent_initial_tools(
        raw_tools=[],
        legacy_permission_config=None,
        agent_name="rex",
    )

    assert tools == ["read", "bash", "tool_search"]
    assert permission == []


def test_resolve_agent_initial_tools_keeps_empty_non_rex_tools_empty() -> None:
    tools, permission = resolve_agent_initial_tools(
        raw_tools=[],
        legacy_permission_config=None,
        agent_name="plan",
        available_tool_names=["read", "bash"],
    )

    assert tools == []
    assert permission == []
