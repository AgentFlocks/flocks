"""Tests for neutral OSS SSH script transport and output formatting."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.security.ssh_run_script import (
    _extract_sections,
    _truncate_output,
    execute_ssh_script_content,
)


def test_extract_sections_preserves_header_and_named_sections() -> None:
    assert _extract_sections("header\n### HOST ###\ninfo\n") == {
        "HEADER": "header",
        "HOST": "info",
    }


def test_truncate_output_marks_truncated_content() -> None:
    output, truncated = _truncate_output("abcdef", max_bytes=4)
    assert truncated is True
    assert output.startswith("abcd")


@pytest.mark.asyncio
async def test_script_content_is_forwarded_without_oss_safety_interpretation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock(return_value=(0, "### CHECK ###\nok", ""))
    monkeypatch.setattr(
        "flocks.tool.security.ssh_run_script.execute_ssh_command", execute
    )
    content = "#!/bin/sh\nprintf 'collected data'\n"

    result = await execute_ssh_script_content(
        ToolContext(session_id="s-1", message_id="m-1"),
        host="host-a",
        script_content=content,
        script_label="triage.sh",
        username="analyst",
    )

    assert result.success is True
    assert execute.await_args.kwargs["command"] == content
    assert result.metadata["sections_collected"] == ["CHECK"]
