"""
Agent toolset resolution helpers.

This module owns the static layer of the tool-loading model:
- normalize tools declared in `agent.yaml`
- expand legacy `permission` config into concrete tool names
- answer whether an agent statically declares a tool
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set, Tuple

from flocks.permission import from_config as permission_from_config
from flocks.utils.log import Log

log = Log.create(service="agent.toolset")


def get_all_enabled_tool_names() -> List[str]:
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    return [
        tool.name
        for tool in ToolRegistry.list_tools()
        if getattr(tool, "enabled", True) and tool.name not in {"invalid", "_noop"}
    ]


def get_all_enabled_builtin_tool_names() -> List[str]:
    """Return enabled built-in tool names, excluding plugins and dynamic tools."""
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    builtin_tool_names: List[str] = []
    for tool in ToolRegistry.list_tools():
        if tool.name in {"invalid", "_noop"} or not getattr(tool, "enabled", True):
            continue
        if not getattr(tool, "native", False):
            continue
        source = getattr(tool, "source", None)
        if source not in {None, "builtin"}:
            continue
        builtin_tool_names.append(tool.name)
    return builtin_tool_names


def normalize_declared_tool_names(
    tool_names: Iterable[str],
    available_tool_names: Optional[Iterable[str]] = None,
) -> List[str]:
    available = set(available_tool_names or get_all_enabled_tool_names())
    resolved: List[str] = []
    seen: Set[str] = set()

    for tool_name in tool_names:
        raw_name = str(tool_name).strip()
        if not raw_name:
            continue
        if raw_name.startswith("__mcp_"):
            suffix = raw_name[len("__mcp_"):]
            matches = sorted(name for name in available if name.endswith(f"_{suffix}"))
        else:
            matches = [raw_name] if raw_name in available else []

        if not matches:
            # Built-in agent definitions (librarian, prometheus, …) declare optional
            # tools such as ``lsp_*`` / ``ast_grep_search`` that ship in separate
            # binaries; they are gracefully skipped when not installed.  Treat
            # this as informational only to avoid flooding operational logs.
            log.debug("agent.toolset.tool_missing", {"tool": raw_name})
            continue

        for match in matches:
            if match in seen:
                continue
            seen.add(match)
            resolved.append(match)

    return resolved


def expand_legacy_permission_to_tool_names(
    permission_config: dict[str, Any],
    available_tool_names: Optional[Iterable[str]] = None,
) -> Tuple[List[str], Any]:
    from flocks.permission.next import PermissionNext

    available = list(available_tool_names or get_all_enabled_tool_names())
    permission_rules = permission_from_config(permission_config)
    resolved = [
        tool_name
        for tool_name in available
        if PermissionNext.evaluate(tool_name, "*", permission_rules) == "allow"
    ]
    return resolved, permission_rules


def resolve_agent_initial_tools(
    raw_tools: Optional[List[str]],
    legacy_permission_config: Any,
    agent_name: Optional[str] = None,
    available_tool_names: Optional[Iterable[str]] = None,
) -> Tuple[List[str], Any]:
    available = list(available_tool_names or get_all_enabled_tool_names())
    if raw_tools is not None:
        if agent_name == "rex" and not raw_tools:
            return get_all_enabled_builtin_tool_names(), []
        tools = normalize_declared_tool_names(raw_tools, available)
        permission_rules = []
        if isinstance(legacy_permission_config, dict):
            permission_rules = permission_from_config(legacy_permission_config)
        return tools, permission_rules
    if isinstance(legacy_permission_config, dict):
        return expand_legacy_permission_to_tool_names(legacy_permission_config, available)
    # Stricter default: agents without an explicit tools list only receive
    # always-load tools at session/schema time instead of inheriting all tools.
    return [], []


def agent_declares_tool(agent: Any, tool_name: str) -> bool:
    declared_tools = getattr(agent, "tools", None)
    if not isinstance(declared_tools, (list, tuple, set)):
        return False
    return tool_name in set(declared_tools)
