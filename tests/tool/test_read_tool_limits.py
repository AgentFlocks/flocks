"""Tests for read tool pagination and truncation limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.tool.file import read as read_tool_module
from flocks.tool.registry import ToolContext, ToolRegistry


@pytest.fixture
def tool_context() -> ToolContext:
    return ToolContext(
        session_id="test-read-limits-session",
        message_id="test-read-limits-message",
        agent="test",
    )


def test_read_tool_limit_constants():
    assert read_tool_module.DEFAULT_READ_LIMIT == 2000
    assert read_tool_module.MAX_LINE_LENGTH == 2000
    assert read_tool_module.MAX_BYTES == 20 * 1024


@pytest.mark.asyncio
async def test_default_read_limit_is_2000_lines(tool_context, tmp_path):
    file_path = tmp_path / "many-lines.txt"
    file_path.write_text(
        "\n".join(f"line {i}" for i in range(1, 2002)),
        encoding="utf-8",
    )

    result = await ToolRegistry.execute("read", ctx=tool_context, filePath=str(file_path))

    assert result.success is True
    assert "02000| line 2000" in result.output
    assert "02001| line 2001" not in result.output
    assert "To continue reading, call read with offset=2000" in result.output


@pytest.mark.asyncio
async def test_long_lines_are_truncated_at_2000_characters(tool_context, tmp_path):
    file_path = tmp_path / "long-line.txt"
    file_path.write_text("a" * 2001, encoding="utf-8")

    result = await ToolRegistry.execute("read", ctx=tool_context, filePath=str(file_path))

    assert result.success is True
    assert ("a" * 2000) + "..." in result.output
    assert ("a" * 2001) not in result.output


@pytest.mark.asyncio
async def test_byte_limit_truncation_prompts_offset_continue(tool_context, tmp_path):
    file_path = tmp_path / "wide-lines.txt"
    file_path.write_text(
        "\n".join("x" * 100 for _ in range(300)),
        encoding="utf-8",
    )

    result = await ToolRegistry.execute("read", ctx=tool_context, filePath=str(file_path))

    assert result.success is True
    assert result.truncated is True
    assert f"Output truncated at {read_tool_module.MAX_BYTES} bytes" in result.output
    assert "To continue reading, call read with offset=" in result.output
