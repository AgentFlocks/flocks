"""Tests for curated memory_write targets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.system.memory import memory_write_tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_path"),
    [("user", "USER.md"), ("memory", "MEMORY.md")],
)
async def test_memory_write_resolves_curated_target(
    target: str,
    expected_path: str,
) -> None:
    memory = SimpleNamespace(write=AsyncMock(return_value=expected_path))
    with patch(
        "flocks.tool.system.memory._get_session_memory",
        AsyncMock(return_value=(memory, None)),
    ):
        result = await memory_write_tool(
            ToolContext(session_id="ses_test", message_id="msg_test"),
            content="Remember this",
            target=target,
        )

    assert result.success is True
    memory.write.assert_awaited_once_with(
        content="Remember this",
        path=expected_path,
        append=True,
    )
    assert result.output["target"] == target


@pytest.mark.asyncio
async def test_memory_write_rejects_target_with_custom_path() -> None:
    result = await memory_write_tool(
        ToolContext(session_id="ses_test", message_id="msg_test"),
        content="Remember this",
        path="daily/2026-07-28.md",
        target="user",
    )

    assert result.success is False
    assert result.error == "Specify either path or target, not both"
