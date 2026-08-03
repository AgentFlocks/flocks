"""
Sandbox-aware file tool tests.
"""

import os
import tempfile

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


def _sandbox_ctx(workspace_dir: str, workspace_access: str = "none") -> ToolContext:
    return ToolContext(
        session_id="sandbox-file-tools",
        message_id="sandbox-file-tools-msg",
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
