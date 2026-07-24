"""Core helpers for unified session tool-execution payloads."""

from __future__ import annotations

from typing import Any, Mapping

from flocks.session.execution_profile import get_session_execution_profile


async def build_session_tool_execution_payload(
    *,
    session_id: str,
    message_id: str,
    agent: str,
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_context_extra: Mapping[str, Any] | None = None,
    execution_domain: str = "execution_runtime",
) -> dict[str, Any]:
    """Build one canonical payload used by all tool execution entrypoints."""
    extra = dict(tool_context_extra or {})
    if not isinstance(extra.get("session_execution_profile"), dict):
        profile = await get_session_execution_profile(session_id)
        if isinstance(profile, dict):
            extra["session_execution_profile"] = profile
    return {
        "operation": "tool.execute",
        "execution_domain": str(execution_domain or "execution_runtime"),
        "entry": str(
            (
                (extra.get("session_execution_profile") or {}).get("entry")
                if isinstance(extra.get("session_execution_profile"), Mapping)
                else ""
            )
            or "unknown"
        ),
        "tool": {
            "name": tool_name,
            "input": dict(tool_input or {}),
        },
        "session_id": session_id,
        "message_id": message_id,
        "agent": agent,
        "tool_context_extra": extra,
    }
