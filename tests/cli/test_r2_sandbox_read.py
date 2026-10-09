"""Keep both read-only sandbox mounts readable without weakening path checks."""
from pathlib import Path

import pytest

from flocks.config.config import Config, ConfigInfo
from flocks.session.runtime_controls import RuntimeControls, runtime_controls
from flocks.tool.file.read import read_tool
from flocks.tool.registry import ToolContext


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    project = tmp_path / "project"
    workspace = tmp_path / "workspace"
    project.mkdir()
    workspace.mkdir()
    (project / "shared.txt").write_text("PROJECT_SENTINEL", encoding="utf-8")
    (workspace / "shared.txt").write_text("WORKSPACE_SENTINEL", encoding="utf-8")
    (project / "source.txt").write_text("PROJECT_SOURCE", encoding="utf-8")
    (workspace / "output.txt").write_text("WORKSPACE_OUTPUT", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    ctx = ToolContext("sandbox-read", "message", extra={"sandbox": {
        "workspace_dir": str(workspace),
        "agent_workspace_dir": str(project),
        "workspace_access": "ro",
        "container_name": "not-started",
        "container_workdir": "/workspace",
    }})
    token = runtime_controls.set(RuntimeControls(sandbox_required=True))
    try:
        yield ctx, project, workspace, outside
    finally:
        runtime_controls.reset(token)


@pytest.mark.parametrize("kind", ["relative", "absolute", "container"])
async def test_existing_workspace_file_stays_readable(sandbox, kind):
    ctx, project, workspace, _ = sandbox
    path = {"relative": "output.txt", "absolute": str(workspace / "output.txt"),
            "container": "/workspace/output.txt"}[kind]
    result = await read_tool(ctx, path)
    assert result.success, result.error
    assert "WORKSPACE_OUTPUT" in result.output
    assert ctx.extra["sandbox"]["workspace_dir"] == str(workspace)
    assert ctx.extra["sandbox"]["agent_workspace_dir"] == str(project)


@pytest.mark.parametrize("kind,expected", [
    ("relative", "WORKSPACE_SENTINEL"),
    ("workspace_absolute", "WORKSPACE_SENTINEL"),
    ("workspace_container", "WORKSPACE_SENTINEL"),
    ("project_absolute", "PROJECT_SENTINEL"),
    ("project_container", "PROJECT_SENTINEL"),
])
async def test_same_name_uses_workspace_unless_project_is_explicit(sandbox, kind, expected):
    ctx, project, workspace, _ = sandbox
    paths = {"relative": "shared.txt", "workspace_absolute": str(workspace / "shared.txt"),
             "workspace_container": "/workspace/shared.txt", "project_absolute": str(project / "shared.txt"),
             "project_container": "/agent/shared.txt"}
    result = await read_tool(ctx, paths[kind])
    assert result.success, result.error
    assert expected in result.output
    other = "PROJECT_SENTINEL" if expected == "WORKSPACE_SENTINEL" else "WORKSPACE_SENTINEL"
    assert other not in result.output
    assert ctx.extra["sandbox"]["workspace_dir"] == str(workspace)


@pytest.mark.parametrize("kind", ["relative", "absolute", "container"])
async def test_project_only_file_remains_readable(sandbox, kind):
    ctx, project, _, _ = sandbox
    path = {"relative": "source.txt", "absolute": str(project / "source.txt"),
            "container": "/agent/source.txt"}[kind]
    result = await read_tool(ctx, path)
    assert result.success, result.error
    assert "PROJECT_SOURCE" in result.output


async def test_configured_container_workdir_maps_to_workspace(sandbox):
    ctx, _, _, _ = sandbox
    ctx.extra["sandbox"]["container_workdir"] = "/scratch/work"
    result = await read_tool(ctx, "/scratch/work/output.txt")
    assert result.success, result.error
    assert "WORKSPACE_OUTPUT" in result.output


@pytest.mark.parametrize("kind", ["absolute", "relative", "project_container", "workspace_container"])
async def test_existing_outside_file_is_rejected(sandbox, kind):
    ctx, _, _, outside = sandbox
    path = {"absolute": str(outside), "relative": "../outside.txt",
            "project_container": "/agent/../outside.txt",
            "workspace_container": "/workspace/../outside.txt"}[kind]
    result = await read_tool(ctx, path)
    assert not result.success
    assert "escapes" in result.error
    assert "OUTSIDE_SENTINEL" not in (result.output or "")


@pytest.mark.parametrize("root_name", ["project", "workspace"])
@pytest.mark.parametrize("kind", ["absolute", "container"])
async def test_links_to_existing_outside_files_are_rejected(sandbox, root_name, kind):
    ctx, project, workspace, outside = sandbox
    root = project if root_name == "project" else workspace
    link = root / "link.txt"
    link.symlink_to(outside)
    mount = "/agent" if root_name == "project" else "/workspace"
    path = str(link) if kind == "absolute" else f"{mount}/link.txt"
    result = await read_tool(ctx, path)
    assert not result.success
    assert "escapes" in result.error
    assert "OUTSIDE_SENTINEL" not in (result.output or "")


async def test_workspace_link_rejection_does_not_fall_back_to_project(sandbox):
    ctx, project, workspace, outside = sandbox
    (workspace / "fallback.txt").symlink_to(outside.with_name("missing.txt"))
    (project / "fallback.txt").write_text("MUST_NOT_FALL_BACK", encoding="utf-8")
    result = await read_tool(ctx, "fallback.txt")
    assert not result.success
    assert "Symlink" in result.error
    assert "MUST_NOT_FALL_BACK" not in (result.output or "")


async def test_non_headless_read_keeps_workspace_semantics(sandbox):
    ctx, _, _, _ = sandbox
    token = runtime_controls.set(None)
    try:
        result = await read_tool(ctx, "shared.txt")
    finally:
        runtime_controls.reset(token)
    assert result.success, result.error
    assert "WORKSPACE_SENTINEL" in result.output
    assert "PROJECT_SENTINEL" not in result.output
