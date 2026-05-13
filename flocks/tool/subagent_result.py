"""Helpers for formatting synchronous subagent tool results."""

from __future__ import annotations

from typing import Any, Dict, Optional

from flocks.session.message import Message
from flocks.tool.registry import ToolResult


def _task_metadata_block(session_id: str) -> str:
    return f"<task_metadata>\nsession_id: {session_id}\n</task_metadata>"


def _extract_message_error(message: Any) -> Optional[str]:
    raw_error = getattr(message, "error", None)
    if isinstance(raw_error, dict):
        detail = raw_error.get("message") or raw_error.get("data", {}).get("message")
        if detail:
            return str(detail)
        name = raw_error.get("name")
        if name:
            return str(name)
    elif raw_error:
        return str(raw_error)
    return None


async def format_sync_subagent_result(
    *,
    description: str,
    session_id: str,
    loop_result: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> ToolResult:
    """Convert a synchronous `SessionLoop.run()` result into a tool result."""
    final_metadata = dict(metadata or {})
    final_metadata.setdefault("sessionId", session_id)

    if getattr(loop_result, "action", None) == "error":
        error_detail = getattr(loop_result, "error", None) or "Sub-agent execution failed"
        return ToolResult(
            success=False,
            error=f"Sub-agent failed: {error_detail}",
            title=description,
            metadata=final_metadata,
        )

    last_message = getattr(loop_result, "last_message", None)
    if not last_message:
        return ToolResult(
            success=True,
            output=(
                "Sub-agent completed without producing a final assistant message.\n\n"
                f"{_task_metadata_block(session_id)}"
            ),
            title=description,
            metadata={**final_metadata, "emptyOutput": True},
        )

    output_text = await Message.get_text_content(last_message)
    if output_text.strip():
        return ToolResult(
            success=True,
            output=f"{output_text}\n\n{_task_metadata_block(session_id)}",
            title=description,
            metadata=final_metadata,
        )

    message_error = _extract_message_error(last_message)
    if message_error:
        return ToolResult(
            success=False,
            error=f"Sub-agent failed: {message_error}",
            title=description,
            metadata=final_metadata,
        )

    finish_reason = getattr(last_message, "finish", None)
    suffix = f" (finish={finish_reason})" if finish_reason else ""
    return ToolResult(
        success=True,
        output=f"Sub-agent completed without text output{suffix}.\n\n{_task_metadata_block(session_id)}",
        title=description,
        metadata={**final_metadata, "emptyOutput": True},
    )
