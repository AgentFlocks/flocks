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
    assert read_tool_module.MAX_BYTES == 50 * 1024


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
        "\n".join("x" * 100 for _ in range(600)),
        encoding="utf-8",
    )

    result = await ToolRegistry.execute("read", ctx=tool_context, filePath=str(file_path))

    assert result.success is True
    assert result.truncated is True
    assert f"Output truncated at {read_tool_module.MAX_BYTES} bytes" in result.output
    assert "To continue reading, call read with offset=" in result.output


@pytest.mark.asyncio
async def test_column_reads_reconstruct_a_long_unicode_line(tool_context, tmp_path, monkeypatch):
    import json
    text = "甲乙丙" * 30
    path = tmp_path / "long.txt"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(read_tool_module, "get_read_max_bytes", lambda: 11)
    offset, chunks = 0, []
    while offset < len(text):
        result = await read_tool_module.read_tool(tool_context, str(path), columnOffset=offset, columnLimit=20)
        assert result.success, result.error
        payload = json.loads(result.output)
        assert payload["next_column"] > offset
        chunks.append(payload["text"])
        offset = payload["next_column"]
    assert "".join(chunks) == text
    assert not payload["has_more"]
    assert path.read_text() == text


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [{"columnOffset": -1}, {"columnLimit": 0}, {"columnOffset": 100}, {"offset": 100, "columnOffset": 0}])
async def test_column_reads_reject_invalid_ranges(tool_context, tmp_path, parameters):
    path = tmp_path / "short.txt"
    path.write_text("hello", encoding="utf-8")
    result = await read_tool_module.read_tool(tool_context, str(path), **parameters)
    assert not result.success


# A model may fill in every optional parameter: column values that come along
# with a read of several lines must not turn it into an error (INC-DEF23E6D).

@pytest.mark.asyncio
async def test_a_read_of_several_lines_with_column_defaults_reads_the_lines(tool_context, tmp_path):
    path = tmp_path / "README.md"
    path.write_text("".join(f"line {number}\n" for number in range(1, 11)), encoding="utf-8")

    # The very call in the report, through the registry as a model makes it.
    result = await ToolRegistry.execute(
        "read", ctx=tool_context, filePath=str(path), offset=0, limit=350, columnOffset=0, columnLimit=2000,
    )

    assert result.success, result.error
    assert "00001| line 1" in result.output and "00010| line 10" in result.output


@pytest.mark.asyncio
async def test_column_limit_caps_each_line_of_a_read_of_several_lines(tool_context, tmp_path):
    path = tmp_path / "wide.txt"
    path.write_text("abcdefghij\nklmnopqrst\n", encoding="utf-8")

    result = await read_tool_module.read_tool(
        tool_context, str(path), offset=0, limit=5, columnOffset=0, columnLimit=4,
    )

    assert result.success, result.error
    assert "00001| abcd" in result.output and "abcde" not in result.output
    assert "00002| klmn" in result.output
    assert "Long lines were shortened to 4 characters, as columnLimit asked." in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters, message", [
    ({"columnOffset": 3}, "set limit to 1"),
    ({"columnOffset": 0, "columnLimit": 0}, "columnLimit must be positive"),
])
async def test_a_read_of_several_lines_says_how_to_use_column_values(tool_context, tmp_path, parameters, message):
    path = tmp_path / "short.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")

    result = await read_tool_module.read_tool(tool_context, str(path), limit=10, **parameters)

    assert not result.success
    assert message in result.error
