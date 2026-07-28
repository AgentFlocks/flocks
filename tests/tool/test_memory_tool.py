"""Tests for the Hermes-style curated memory tool."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.tool.system.memory import memory_tool


def test_memory_tool_replaces_legacy_read_write_tools() -> None:
    tools = {tool.name: tool for tool in ToolRegistry.list_tools()}

    assert "memory" in tools
    assert "memory_get" not in tools
    assert "memory_write" not in tools
    schema = tools["memory"].get_schema().to_json_schema()
    assert schema["required"] == ["target", "action"]
    assert schema["properties"]["target"]["enum"] == ["user", "memory"]
    assert schema["properties"]["action"]["enum"] == ["add", "replace", "remove"]


@pytest.mark.asyncio
async def test_memory_tool_delegates_curated_operation() -> None:
    manager = SimpleNamespace(
        update_curated_memory=AsyncMock(
            return_value={
                "target": "user",
                "action": "add",
                "path": "USER.md",
                "changed": True,
                "content": "# User Profile\n\nPrefers concise answers.\n",
            }
        )
    )
    memory = SimpleNamespace(get_manager=lambda: manager)
    with patch(
        "flocks.tool.system.memory._get_session_memory",
        AsyncMock(return_value=(memory, None)),
    ):
        result = await memory_tool(
            ToolContext(session_id="ses_test", message_id="msg_test"),
            target="user",
            action="add",
            content="Prefers concise answers.",
        )

    assert result.success is True
    manager.update_curated_memory.assert_awaited_once_with(
        target="user",
        action="add",
        content="Prefers concise answers.",
        old_text=None,
    )
    assert result.output["path"] == "USER.md"


@pytest.mark.asyncio
async def test_memory_tool_returns_validation_error() -> None:
    manager = SimpleNamespace(
        update_curated_memory=AsyncMock(
            side_effect=ValueError("old_text is required for action=remove")
        )
    )
    memory = SimpleNamespace(get_manager=lambda: manager)
    with patch(
        "flocks.tool.system.memory._get_session_memory",
        AsyncMock(return_value=(memory, None)),
    ):
        result = await memory_tool(
            ToolContext(session_id="ses_test", message_id="msg_test"),
            target="memory",
            action="remove",
        )

    assert result.success is False
    assert result.error == "old_text is required for action=remove"
