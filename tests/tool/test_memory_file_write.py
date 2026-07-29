"""Tests for filesystem-managed Memory writes."""

from pathlib import Path
from unittest.mock import patch

import pytest

from flocks.tool.file.write import (
    _existing_memory_write_error,
    write_tool,
)
from flocks.tool.registry import ToolContext, ToolRegistry


def test_memory_crud_tool_is_not_registered() -> None:
    tools = {tool.name for tool in ToolRegistry.list_tools()}

    assert "memory" not in tools
    assert "memory_search" in tools


@pytest.mark.parametrize(
    "relative_path",
    [
        "MEMORY.md",
        "USER.md",
        "daily/2026-07-29.md",
        "projects/prj_test/MEMORY.md",
    ],
)
def test_existing_memory_files_require_edit(
    tmp_path: Path,
    relative_path: str,
) -> None:
    memory_path = tmp_path / "memory" / relative_path
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("existing\n", encoding="utf-8")

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        error = _existing_memory_write_error(str(memory_path))

    assert error is not None
    assert "use edit for a precise change" in error


def test_non_memory_file_is_not_protected_from_write(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.md"
    file_path.write_text("existing\n", encoding="utf-8")

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        error = _existing_memory_write_error(str(file_path))

    assert error is None


@pytest.mark.asyncio
async def test_write_can_create_missing_memory_file(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory" / "MEMORY.md"

    async def approve(_request) -> None:
        return None

    ctx = ToolContext(
        session_id="ses_test",
        message_id="msg_test",
        permission_callback=approve,
    )
    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        result = await write_tool(
            ctx,
            content="# Global Memory\n",
            filePath=str(memory_path),
        )

    assert result.success is True
    assert memory_path.read_text(encoding="utf-8") == "# Global Memory\n"


@pytest.mark.asyncio
async def test_write_rejects_existing_memory_before_permission(
    tmp_path: Path,
) -> None:
    memory_path = tmp_path / "memory" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text("existing\n", encoding="utf-8")
    permission_requested = False

    async def approve(_request) -> None:
        nonlocal permission_requested
        permission_requested = True

    ctx = ToolContext(
        session_id="ses_test",
        message_id="msg_test",
        permission_callback=approve,
    )
    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        result = await write_tool(
            ctx,
            content="replacement\n",
            filePath=str(memory_path),
        )

    assert result.success is False
    assert "use edit for a precise change" in (result.error or "")
    assert memory_path.read_text(encoding="utf-8") == "existing\n"
    assert permission_requested is False
