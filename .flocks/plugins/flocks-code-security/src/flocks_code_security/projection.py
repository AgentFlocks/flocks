"""Subset-only callable-tool projection for code-security agents."""

from __future__ import annotations

from typing import Any, Mapping

from flocks_code_security.agents import AGENT_TOOLS
from flocks_code_security.tools import is_registered_audit_tool
from flocks.tool.registry import ToolRegistry


RESOLVER_NAME = "flocks-code-security"


def _is_canonical_question(tool_info: Any) -> bool:
    if getattr(tool_info, "name", None) != "question":
        return False
    from flocks.tool.system.question import question_tool

    current_tool = ToolRegistry.get("question")
    return (
        current_tool is not None
        and current_tool.info is tool_info
        and current_tool.handler is question_tool
    )


def code_security_tool_projection(
    tool_infos: list[Any],
    context: Mapping[str, Any],
) -> list[Any]:
    agent_name = str(context.get("agent") or "")
    allowed = AGENT_TOOLS.get(agent_name)
    if allowed is None:
        return tool_infos
    allowed_names = set(allowed)
    return [
        tool
        for tool in tool_infos
        if getattr(tool, "name", None) in allowed_names
        and (
            is_registered_audit_tool(tool)
            or _is_canonical_question(tool)
        )
    ]


def register_projection() -> None:
    from flocks.session.callable_schema import register_callable_tool_resolver

    register_callable_tool_resolver(RESOLVER_NAME, code_security_tool_projection)
