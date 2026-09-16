"""Tests for central tool output truncation limits."""

from __future__ import annotations

import pytest

from flocks.tool import truncation


def test_default_limits_are_1000_lines_and_100kb():
    assert truncation.MAX_LINES == 1000
    assert truncation.MAX_BYTES == 100 * 1024
    assert truncation.HARD_MAX_TOOL_RESULT_CHARS == 100_000


def test_output_at_default_line_limit_is_not_truncated():
    text = "\n".join(f"line {i}" for i in range(truncation.MAX_LINES))

    result = truncation.truncate_output(text)

    assert result.truncated is False
    assert result.content == text


def test_output_over_default_byte_limit_is_truncated(monkeypatch, tmp_path):
    monkeypatch.setattr(truncation, "_ensure_output_dir", lambda: tmp_path)
    monkeypatch.setattr(truncation, "_maybe_cleanup", lambda _output_dir: None)
    text = "x" * (truncation.MAX_BYTES + 1)

    result = truncation.truncate_output(text)

    assert result.truncated is True
    assert result.output_path is not None
    assert "bytes truncated" in result.content


@pytest.mark.parametrize("direction", ["head", "tail"])
def test_truncation_never_grants_or_invents_tool_access(monkeypatch, tmp_path, direction):
    monkeypatch.setattr(truncation, "_ensure_output_dir", lambda: tmp_path)
    monkeypatch.setattr(truncation, "_maybe_cleanup", lambda _output_dir: None)
    text = "first\nsecond\nthird"

    result = truncation.truncate_output(text, max_lines=1, direction=direction)

    assert result.truncated
    assert "Use Grep" not in result.content
    assert "Read with" not in result.content
    assert "Task tool" not in result.content
    assert "only with tools allowed" in result.content
    assert "does not grant file access" in result.content
    assert "do not assume the omitted content was read" in result.content
    assert (tmp_path / result.output_path).read_text() == text


@pytest.mark.asyncio
async def test_registry_does_not_infer_permissions_from_agent_name(monkeypatch, tmp_path):
    from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolResult

    monkeypatch.setattr(truncation, "_ensure_output_dir", lambda: tmp_path)
    monkeypatch.setattr(truncation, "_maybe_cleanup", lambda _output_dir: None)

    async def handler(ctx):
        return ToolResult(success=True, output="x" * (truncation.MAX_BYTES + 1))

    tool = Tool(ToolInfo(name="bounded-test", description="unit test"), handler)
    result = await tool.execute(ToolContext("test", "message", agent="strict-task-agent"))
    assert result.success and result.truncated
    assert "Task tool" not in result.output
    assert "Use Grep" not in result.output
