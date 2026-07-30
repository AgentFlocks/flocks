"""
Sandbox-aware file tool tests.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


def _sandbox_ctx(
    workspace_dir: str,
    workspace_access: str = "none",
    *,
    agent: str = "rex",
) -> ToolContext:
    return ToolContext(
        session_id="sandbox-file-tools",
        message_id="sandbox-file-tools-msg",
        agent=agent,
        extra={
            "sandbox": {
                "workspace_dir": workspace_dir,
                "workspace_access": workspace_access,
            }
        },
    )


@pytest.mark.asyncio
async def test_read_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath="/tmp/definitely-outside-sandbox.txt",
        )
        assert not result.success
        assert "Path escapes sandbox workspace" in (result.error or "")


@pytest.mark.asyncio
async def test_read_tool_reads_inside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        target = os.path.join(sandbox_dir, "notes.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("hello\nsandbox\n")

        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath=target,
        )
        assert result.success
        assert "sandbox" in (result.output or "")


@pytest.mark.asyncio
async def test_file_tools_allow_only_host_memory_root_in_sandbox(
    tmp_path: Path,
) -> None:
    sandbox_dir = tmp_path / "sandbox"
    data_dir = tmp_path / "data"
    sandbox_dir.mkdir()
    memory_file = data_dir / "memory" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("# Global Memory\n\nold fact\n", encoding="utf-8")
    ctx = _sandbox_ctx(
        str(sandbox_dir),
        workspace_access="rw",
        agent="self-improve",
    )

    with patch("flocks.config.Config.get_data_path", return_value=data_dir):
        read_result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath=str(memory_file),
        )
        glob_result = await ToolRegistry.execute(
            "glob",
            ctx=ctx,
            pattern="**/*.md",
            path=str(data_dir / "memory"),
        )
        grep_result = await ToolRegistry.execute(
            "grep",
            ctx=ctx,
            pattern="old fact",
            path=str(data_dir / "memory"),
        )
        edit_result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath=str(memory_file),
            oldString="old fact",
            newString="new fact",
        )

    assert read_result.success
    assert glob_result.success
    assert grep_result.success
    assert edit_result.success
    assert memory_file.read_text(encoding="utf-8") == (
        "# Global Memory\n\nnew fact\n"
    )


@pytest.mark.asyncio
async def test_sandbox_self_improve_can_manage_only_marked_host_skills(
    tmp_path: Path,
) -> None:
    sandbox_dir = tmp_path / "sandbox"
    home_dir = tmp_path / "home"
    sandbox_dir.mkdir()
    skill_root = home_dir / ".flocks" / "plugins" / "skills"
    managed_path = skill_root / "managed-skill" / "SKILL.md"
    unmanaged_path = skill_root / "manual-skill" / "SKILL.md"
    managed_content = (
        "---\n"
        "name: managed-skill\n"
        "description: Use this managed test Skill.\n"
        "metadata:\n"
        "  managed_by: flocks\n"
        "---\n\n"
        "Initial workflow.\n"
    )
    unmanaged_content = (
        "---\n"
        "name: manual-skill\n"
        "description: Use this manually maintained test Skill.\n"
        "---\n\n"
        "Manual workflow.\n"
    )
    unmanaged_path.parent.mkdir(parents=True)
    unmanaged_path.write_text(unmanaged_content, encoding="utf-8")
    ctx = _sandbox_ctx(
        str(sandbox_dir),
        workspace_access="rw",
        agent="self-improve",
    )

    with (
        patch("pathlib.Path.home", return_value=home_dir),
        patch(
            "flocks.memory.evolution.skill_guard.Skill.all",
            new=AsyncMock(return_value=[]),
        ),
    ):
        create_result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath=str(managed_path),
            content=managed_content,
        )
        read_result = await ToolRegistry.execute(
            "read",
            ctx=ctx,
            filePath=str(managed_path),
        )
        overwrite_result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath=str(managed_path),
            content=managed_content.replace("Initial", "Overwritten"),
        )
        edit_result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath=str(managed_path),
            oldString="Initial workflow.",
            newString="Improved workflow.",
        )
        unmanaged_result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath=str(unmanaged_path),
            oldString="Manual workflow.",
            newString="Changed workflow.",
        )

    assert create_result.success
    assert read_result.success
    assert "Initial workflow." in (read_result.output or "")
    assert not overwrite_result.success
    assert "use edit" in (overwrite_result.error or "")
    assert edit_result.success
    assert "Improved workflow." in managed_path.read_text(encoding="utf-8")
    assert not unmanaged_result.success
    assert "existing managed Skills" in (unmanaged_result.error or "")
    assert unmanaged_path.read_text(encoding="utf-8") == unmanaged_content


@pytest.mark.asyncio
async def test_sandbox_agent_cannot_write_or_edit_daily_memory(
    tmp_path: Path,
) -> None:
    sandbox_dir = tmp_path / "sandbox"
    data_dir = tmp_path / "data"
    sandbox_dir.mkdir()
    daily_file = data_dir / "memory" / "daily" / "2026-07-29.md"
    daily_file.parent.mkdir(parents=True)
    daily_file.write_text("lifecycle entry\n", encoding="utf-8")
    ctx = _sandbox_ctx(str(sandbox_dir), workspace_access="rw")

    with patch("flocks.config.Config.get_data_path", return_value=data_dir):
        write_result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath=str(daily_file),
            content="replacement\n",
        )
        edit_result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath=str(daily_file),
            oldString="lifecycle entry",
            newString="replacement",
        )

    assert not write_result.success
    assert not edit_result.success
    assert "Session lifecycle" in (write_result.error or "")
    assert "Session lifecycle" in (edit_result.error or "")
    assert daily_file.read_text(encoding="utf-8") == "lifecycle entry\n"


@pytest.mark.asyncio
async def test_write_tool_blocked_in_ro_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir, workspace_access="ro")
        result = await ToolRegistry.execute(
            "write",
            ctx=ctx,
            filePath=os.path.join(sandbox_dir, "a.txt"),
            content="x",
        )
        assert not result.success
        assert "read-only workspace mode" in (result.error or "")


@pytest.mark.asyncio
async def test_edit_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        outside_file = os.path.join(tempfile.gettempdir(), "sandbox-edit-outside.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("hello")
        try:
            ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw")
            result = await ToolRegistry.execute(
                "edit",
                ctx=ctx,
                filePath=outside_file,
                oldString="hello",
                newString="world",
            )
            assert not result.success
            assert "Path escapes sandbox workspace" in (result.error or "")
        finally:
            try:
                os.remove(outside_file)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_edit_tool_supports_batch_edits_inside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        target = os.path.join(sandbox_dir, "batch.txt")
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write("alpha\r\nbeta\r\ngamma\r\n")

        ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw")
        result = await ToolRegistry.execute(
            "edit",
            ctx=ctx,
            filePath=target,
            edits=[
                {"oldString": "alpha\n", "newString": "ALPHA\n"},
                {"oldString": "gamma\n", "newString": "GAMMA\n"},
            ],
        )

        assert result.success
        with open(target, "r", encoding="utf-8", newline="") as f:
            assert f.read() == "ALPHA\r\nbeta\r\nGAMMA\r\n"


@pytest.mark.asyncio
async def test_glob_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "glob",
            ctx=ctx,
            pattern="*.txt",
            path="/tmp",
        )
        assert not result.success
        assert "Path escapes sandbox workspace" in (result.error or "")


@pytest.mark.asyncio
async def test_grep_tool_searches_inside_sandbox_with_relative_path() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        nested = os.path.join(sandbox_dir, "nested")
        os.makedirs(nested, exist_ok=True)
        target = os.path.join(nested, "notes.txt")
        with open(target, "w", encoding="utf-8") as f:
            f.write("sandbox needle\n")

        ctx = _sandbox_ctx(sandbox_dir)
        result = await ToolRegistry.execute(
            "grep",
            ctx=ctx,
            pattern="needle",
            path="nested",
        )

        assert result.success
        assert "notes.txt" in (result.output or "")


@pytest.mark.asyncio
async def test_apply_patch_tool_rejects_path_outside_sandbox() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw")
        result = await ToolRegistry.execute(
            "apply_patch",
            ctx=ctx,
            patchText=(
                "*** Begin Patch\n"
                "*** Add File: /tmp/outside.txt\n"
                "+hello\n"
                "*** End Patch\n"
            ),
        )
        assert not result.success
        assert "Invalid patch path" in (result.error or "")


@pytest.mark.asyncio
async def test_apply_patch_tool_writes_inside_sandbox_with_relative_path() -> None:
    with tempfile.TemporaryDirectory() as sandbox_dir:
        ctx = _sandbox_ctx(sandbox_dir, workspace_access="rw")
        result = await ToolRegistry.execute(
            "apply_patch",
            ctx=ctx,
            patchText=(
                "*** Begin Patch\n"
                "*** Add File: docs/note.txt\n"
                "sandbox patch\n"
                "*** End Patch\n"
            ),
        )

        assert result.success
        with open(os.path.join(sandbox_dir, "docs", "note.txt"), "r", encoding="utf-8") as f:
            assert f.read() == "sandbox patch\n"
