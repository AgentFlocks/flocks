"""Tests for central tool output truncation limits."""

from __future__ import annotations

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
