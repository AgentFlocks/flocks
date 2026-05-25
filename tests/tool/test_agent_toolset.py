from pathlib import Path
from types import SimpleNamespace

import yaml

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


def test_builtin_agent_yaml_tool_names_match_current_registry_surface() -> None:
    available_tool_names = [
        "apply_patch",
        "background_cancel",
        "background_output",
        "bash",
        "channel_message",
        "delegate_task",
        "edit",
        "glob",
        "grep",
        "lsp",
        "memory_search",
        "plan_exit",
        "question",
        "read",
        "run_workflow",
        "run_workflow_node",
        "session_list",
        "skill_load",
        "task",
        "todoread",
        "todowrite",
        "tool_search",
        "webfetch",
        "websearch",
        "write",
    ]
    agent_root = Path(__file__).resolve().parents[2] / "flocks" / "agent" / "agents"

    for agent_name in (
        "explore",
        "hephaestus",
        "librarian",
        "prometheus",
        "multimodal_looker",
        "oracle",
        "plan",
        "rex_junior",
        "self_enhance",
    ):
        agent_yaml = agent_root / agent_name / "agent.yaml"
        if not agent_yaml.exists():
            continue
        raw_tools = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))["tools"]

        assert normalize_declared_tool_names(raw_tools, available_tool_names) == raw_tools


def test_agent_prompt_sources_do_not_reference_retired_tool_names() -> None:
    retired_tool_names = (
        "ast_grep_search",
        "lsp_completion",
        "lsp_diagnostics",
        "lsp_find_references",
        "lsp_goto_definition",
        "lsp_hover",
        "lsp_rename",
    )
    agent_root = Path(__file__).resolve().parents[2] / "flocks" / "agent" / "agents"

    for prompt_path in list(agent_root.glob("*/prompt.md")) + list(agent_root.glob("*/prompt_builder.py")):
        content = prompt_path.read_text(encoding="utf-8")
        for retired_tool_name in retired_tool_names:
            assert retired_tool_name not in content, f"{prompt_path} still references {retired_tool_name}"
