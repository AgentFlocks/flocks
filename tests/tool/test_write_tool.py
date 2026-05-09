"""
Tests for Write tool

Verifies file writing behavior:
- Absolute paths are written directly (no path modification)
- Relative paths fall back to Instance directory
- Sandbox read-only mode blocks writes
- Non-string content is coerced
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch
import datetime as dt

from flocks.tool.registry import ToolRegistry, ToolContext
from flocks.workspace.manager import WorkspaceManager


def _make_ctx(**extra_kwargs) -> ToolContext:
    """Create a minimal ToolContext that auto-approves all permissions."""

    async def _auto_approve(req):
        pass

    return ToolContext(
        session_id="test-session",
        message_id="msg-1",
        agent="test",
        call_id="call-1",
        permission_callback=_auto_approve,
        **extra_kwargs,
    )


@pytest.mark.asyncio
async def test_absolute_path_written_directly(tmp_path):
    """Absolute filePath must be written to the exact location given."""
    target = tmp_path / "exact_location.txt"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content="absolute"
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "absolute"


@pytest.mark.asyncio
async def test_write_to_workspace_outputs_no_modification(tmp_path):
    """Write to workspace outputs path — tool must not alter the path."""
    outputs_dir = tmp_path / "outputs" / "2026-03-14"
    outputs_dir.mkdir(parents=True)
    target = outputs_dir / "hello.py"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content='print("hi")'
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == 'print("hi")'


@pytest.mark.asyncio
async def test_sandbox_readonly_blocks_write(tmp_path):
    """Write must fail when sandbox.workspace_access == 'ro'."""
    sandbox = {
        "workspace_dir": str(tmp_path),
        "workspace_access": "ro",
    }
    ctx = _make_ctx(extra={"sandbox": sandbox})

    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(tmp_path / "blocked.txt"), content="x"
    )

    assert not result.success
    assert "read-only" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_dict_content_serialized_to_json(tmp_path):
    """Dict content should be serialized as pretty-printed JSON."""
    target = tmp_path / "data.json"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content={"key": "value", "num": 42}
    )

    assert result.success, f"write failed: {result.error}"
    import json

    data = json.loads(target.read_text())
    assert data == {"key": "value", "num": 42}


@pytest.mark.asyncio
async def test_creates_parent_directory(tmp_path):
    """Write should auto-create parent directories if they don't exist."""
    target = tmp_path / "deep" / "nested" / "file.txt"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath=str(target), content="nested"
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "nested"


@pytest.mark.asyncio
async def test_filename_only_redirects_to_default_outputs(tmp_path, monkeypatch):
    """Bare filename should go to workspace default outputs, not source dir."""
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    ctx = _make_ctx()
    with patch("flocks.tool.file.write.Instance.get_directory", return_value=str(project_dir)):
        result = await ToolRegistry.execute("write", ctx, filePath="hello.txt", content="hello")

    assert result.success, f"write failed: {result.error}"
    expected = tmp_path / "workspace" / "outputs" / dt.date.today().isoformat() / "hello.txt"
    assert expected.exists()
    assert expected.read_text() == "hello"
    assert not (project_dir / "hello.txt").exists()


@pytest.mark.asyncio
async def test_source_root_absolute_basename_redirects_to_default_outputs(tmp_path, monkeypatch):
    """Absolute source-root basename should be treated as filename-only intent."""
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    source_root_target = project_dir / "report.txt"

    ctx = _make_ctx()
    with patch("flocks.tool.file.write.Instance.get_directory", return_value=str(project_dir)):
        result = await ToolRegistry.execute(
            "write", ctx, filePath=str(source_root_target), content="report"
        )

    assert result.success, f"write failed: {result.error}"
    expected = tmp_path / "workspace" / "outputs" / dt.date.today().isoformat() / "report.txt"
    assert expected.exists()
    assert expected.read_text() == "report"
    assert not source_root_target.exists()


@pytest.mark.asyncio
async def test_relative_with_subdir_keeps_project_path(tmp_path):
    """Relative paths with explicit directory should keep existing semantics."""
    target = tmp_path / "project" / "notes" / "x.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    ctx = _make_ctx()
    with patch("flocks.tool.file.write.Instance.get_directory", return_value=str(tmp_path / "project")):
        result = await ToolRegistry.execute("write", ctx, filePath="notes/x.txt", content="x")

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "x"


def test_filepath_parameter_references_env():
    """filePath parameter description must contain directory routing rules."""
    from flocks.tool.registry import ToolRegistry

    tool = ToolRegistry.get("write")
    filepath_param = next(p for p in tool.info.parameters if p.name == "filePath")
    desc = filepath_param.description

    assert "Workspace outputs directory" in desc
    assert "<env>" in desc
    assert "Source code directory" in desc
