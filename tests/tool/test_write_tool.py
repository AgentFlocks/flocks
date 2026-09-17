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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import datetime as dt

from flocks.tool.registry import ToolRegistry, ToolContext
from flocks.tool.path_utils import resolve_tool_path
from flocks.workspace.manager import WorkspaceManager, WorkspaceOutputScope


@pytest.fixture(autouse=True)
def isolate_write_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-home"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)


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
async def test_relative_path_uses_session_workspace_not_global_instance(tmp_path):
    """Filesystem paths must resolve relative inputs from the session workspace."""
    session_workspace = tmp_path / "session-workspace"
    global_workspace = tmp_path / "global-workspace"
    session_workspace.mkdir()
    global_workspace.mkdir()
    ctx = _make_ctx(extra={"workspace_dir": str(session_workspace)})

    with patch("flocks.tool.path_utils.Instance.get_directory", return_value=str(global_workspace)):
        resolution = await resolve_tool_path(ctx, "session.txt")

    assert resolution.resolved_path == str(session_workspace / "session.txt")


@pytest.mark.asyncio
async def test_verified_path_rejects_a_replaced_symlink(tmp_path):
    approved_target = tmp_path / "approved.txt"
    replacement_target = tmp_path / "replacement.txt"
    approved_target.write_text("approved")
    replacement_target.write_text("replacement")
    link = tmp_path / "link.txt"
    link.symlink_to(approved_target)
    ctx = _make_ctx(
        extra={
            "workspace_dir": str(tmp_path),
            "filesystem_path_binding": {
                "target_path": "link.txt",
                "target_canonical": {
                    "realpath": str(approved_target),
                    "parent_realpath": str(tmp_path),
                    "final_target": str(approved_target),
                },
            },
        },
    )
    link.unlink()
    link.symlink_to(replacement_target)

    with pytest.raises(ValueError, match="changed after policy approval"):
        await resolve_tool_path(ctx, "link.txt")


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
async def test_write_expands_tilde_path(tmp_path, monkeypatch):
    """Write should expand ~/ paths before writing."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = Path(tmp_path) / "tilde-write.txt"

    ctx = _make_ctx()
    result = await ToolRegistry.execute(
        "write", ctx, filePath="~/tilde-write.txt", content="home"
    )

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "home"


@pytest.mark.asyncio
async def test_filename_only_redirects_to_default_outputs(tmp_path, monkeypatch):
    """Bare filename should go to workspace default outputs, not source dir."""
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    ctx = _make_ctx()
    with patch("flocks.tool.path_utils.Instance.get_directory", return_value=str(project_dir)):
        result = await ToolRegistry.execute("write", ctx, filePath="hello.txt", content="hello")

    assert result.success, f"write failed: {result.error}"
    expected = tmp_path / "workspace" / "outputs" / dt.date.today().isoformat() / "hello.txt"
    assert expected.exists()
    assert expected.read_text() == "hello"
    assert not (project_dir / "hello.txt").exists()
    assert result.attachments is not None
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    assert attachment["type"] == "file"
    assert attachment["filename"] == "hello.txt"
    assert attachment["mime"] == "text/plain"
    assert attachment["size"] == len("hello")
    assert attachment["origin"] == "agent_output"
    assert attachment["source"] == {
        "root": "workspace-output",
        "path": f"{dt.date.today().isoformat()}/hello.txt",
        "username": None,
    }
    assert str(tmp_path) not in str(attachment)


@pytest.mark.asyncio
async def test_source_root_absolute_basename_redirects_to_default_outputs(tmp_path, monkeypatch):
    """Absolute source-root basename should be treated as filename-only intent."""
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    source_root_target = project_dir / "report.txt"

    ctx = _make_ctx()
    with patch("flocks.tool.path_utils.Instance.get_directory", return_value=str(project_dir)):
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
    with patch("flocks.tool.path_utils.Instance.get_directory", return_value=str(tmp_path / "project")):
        result = await ToolRegistry.execute("write", ctx, filePath="notes/x.txt", content="x")

    assert result.success, f"write failed: {result.error}"
    assert target.exists()
    assert target.read_text() == "x"
    assert result.attachments is None


def test_filepath_parameter_describes_output_routing():
    """filePath parameter description must contain directory routing rules."""
    from flocks.tool.registry import ToolRegistry

    tool = ToolRegistry.get("write")
    filepath_param = next(p for p in tool.info.parameters if p.name == "filePath")
    desc = filepath_param.description

    assert "Workspace outputs directory" in desc
    assert "filename-only path" in desc
    assert "Project source files" in desc
    assert "<env>" not in desc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,auth_username,session_owner,writer_username",
    [
        pytest.param("admin", "admin team", "admin team", "admin_team", id="admin"),
        pytest.param("member", "member one", "member one", "member_one", id="member"),
        pytest.param("member", "writer team", "service", "writer_team", id="auth-not-owner"),
        pytest.param(None, None, "owner team", "owner_team", id="owner-fallback"),
        pytest.param(None, None, None, None, id="no-identity"),
    ],
)
@pytest.mark.parametrize(
    "destination", ["global-flat", "global-dated", "user-flat", "user-dated", "filename-only"],
)
async def test_output_attachment_matches_written_scope(
    tmp_path, role, auth_username, session_owner, writer_username, destination,
):
    from flocks.auth.context import AuthUser

    manager = WorkspaceManager.get_instance()
    workspace = manager.get_workspace_dir()
    scope_username = None if destination.startswith("global") else writer_username
    root = (
        manager.get_user_workspace_dir(scope_username) if scope_username else workspace
    ) / "outputs"
    relative = (
        "report.md" if destination.endswith("flat")
        else f"{dt.date.today().isoformat()}/report.md"
    )
    target = root / relative
    other_roots = {workspace / "outputs", manager.get_user_workspace_dir("other") / "outputs"}
    for username in (writer_username, session_owner):
        if username:
            other_roots.add(manager.get_user_workspace_dir(username) / "outputs")
    duplicates = [other_root / relative for other_root in other_roots if other_root != root]
    for duplicate in duplicates:
        duplicate.parent.mkdir(parents=True, exist_ok=True)
        duplicate.write_bytes(b"different same-name output")

    ctx = _make_ctx(extra={"workspace_dir": str(tmp_path / "project")})
    auth_user = AuthUser(id="writer", username=auth_username, role=role) if auth_username else None
    session = SimpleNamespace(
        id=ctx.session_id, project_id="global", owner_user_id="test-owner",
        owner_username=session_owner, directory="", metadata={},
    )
    content = "Correct output: café\n"
    with (
        patch("flocks.auth.context.get_current_auth_user", return_value=auth_user),
        patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=session)),
    ):
        result = await ToolRegistry.execute(
            "write", ctx,
            filePath="report.md" if destination == "filename-only" else str(target),
            content=content,
        )

    assert result.success, result.error
    assert target.read_bytes() == content.encode("utf-8")
    assert all(duplicate.read_bytes() == b"different same-name output" for duplicate in duplicates)
    assert result.metadata["filepath"] == str(target.resolve())
    assert result.attachments is not None and len(result.attachments) == 1
    attachment = result.attachments[0]
    assert attachment["id"].startswith("prt_")
    assert attachment["sessionID"] == ctx.session_id
    assert attachment["messageID"] == ctx.message_id
    assert attachment["type"] == "file"
    assert attachment["origin"] == "agent_output"
    assert attachment["filename"] == "report.md"
    assert attachment["mime"] == "text/markdown"
    assert attachment["size"] == len(content.encode("utf-8"))
    assert attachment["modifiedAt"] == int(target.stat().st_mtime * 1000)
    assert attachment["source"] == {
        "root": "workspace-output", "path": relative, "username": scope_username,
    }
    assert str(tmp_path) not in str(attachment)


@pytest.mark.parametrize("username", [None, "admin team"])
@pytest.mark.parametrize("user_scoped", [False, True])
def test_resolve_output_scope_missing_path_without_creating_directories(tmp_path, username, user_scoped):
    manager = WorkspaceManager.get_instance()
    workspace = tmp_path / "workspace"
    root = workspace / "users" / "admin_team" / "outputs" if user_scoped else workspace / "outputs"
    relative = "2026-03-14/nested/missing.txt"
    target = root / relative

    with patch.object(manager, "get_default_outputs_dir", side_effect=AssertionError("must not mkdir")):
        scope = manager.resolve_output_scope(target, username=username)

    if user_scoped and username is None:
        assert scope is None
    else:
        assert scope == WorkspaceOutputScope(
            path=target.resolve(), root=root.resolve(), relative_path=relative,
            username="admin_team" if user_scoped else None,
        )
    assert not workspace.exists()
    assert not target.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "", "outputs", "users/admin_team/outputs", "users/other/outputs/report.md",
        "knowledge/report.md", "shared/outputs/report.md",
        "outputs/../outputs/report.md", "outputs/../../outside/report.md",
    ],
)
def test_resolve_output_scope_rejects_non_output_paths(tmp_path, relative):
    manager = WorkspaceManager.get_instance()
    assert manager.resolve_output_scope(tmp_path / "workspace" / relative, username="admin team") is None
    assert not (tmp_path / "workspace").exists()


@pytest.mark.parametrize("filepath", [None, "", "report.md", "outputs/report.md", "/invalid\x00path"])
def test_resolve_output_scope_rejects_invalid_host_paths(filepath):
    assert WorkspaceManager.get_instance().resolve_output_scope(filepath) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["other-user", "session-owner", "host"])
async def test_explicit_non_output_write_does_not_attach_or_retarget(tmp_path, destination):
    from flocks.auth.context import AuthUser

    workspace = tmp_path / "workspace"
    root = (
        tmp_path / "host" if destination == "host"
        else workspace / "users" / ("owner" if destination == "session-owner" else "other") / "outputs"
    )
    target = root / "report.md"
    ctx = _make_ctx(extra={"workspace_dir": str(tmp_path / "project")})
    session = SimpleNamespace(
        id=ctx.session_id, project_id="global", owner_user_id="test-owner",
        owner_username="owner", directory="", metadata={},
    )
    with (
        patch("flocks.auth.context.get_current_auth_user", return_value=AuthUser(id="writer", username="writer", role="admin")),
        patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=session)),
    ):
        assert WorkspaceManager.get_instance().resolve_output_scope(target, username="writer") is None
        result = await ToolRegistry.execute("write", ctx, filePath=str(target), content="explicit bytes")

    assert result.success, result.error
    assert target.read_bytes() == b"explicit bytes"
    assert result.metadata["filepath"] == str(target.resolve())
    assert result.attachments is None
    assert not (workspace / "users" / "writer").exists()
    assert not (workspace / "outputs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link_relative,destination_relative,suffix",
    [
        ("outputs", "workspace/users/other/outputs", "report.md"),
        ("users/writer/outputs", "workspace/users/other/outputs", "report.md"),
        ("users/writer", "workspace/users/other", "outputs/report.md"),
        ("users", "workspace/other-users", "writer/outputs/report.md"),
        ("outputs", "outside", "report.md"),
        ("users/writer/outputs", "outside", "report.md"),
        ("users/writer", "outside", "outputs/report.md"),
        ("users", "outside", "writer/outputs/report.md"),
        ("outputs/escape", "workspace/users/other/outputs", "report.md"),
        ("users/writer/outputs/escape", "outside", "report.md"),
    ],
)
async def test_output_scope_rejects_symlink_escapes(
    tmp_path, link_relative, destination_relative, suffix,
):
    from flocks.auth.context import AuthUser

    manager = WorkspaceManager.get_instance()
    link = tmp_path / "workspace" / link_relative
    destination = tmp_path / destination_relative
    destination.mkdir(parents=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(destination, target_is_directory=True)
    candidate = link / suffix
    target = candidate.resolve(strict=False)
    assert manager.resolve_output_scope(candidate, username="writer") is None
    assert manager.resolve_output_scope(target, username="writer") is None

    ctx = _make_ctx(extra={"workspace_dir": str(tmp_path / "project")})
    with patch("flocks.auth.context.get_current_auth_user", return_value=AuthUser(id="writer", username="writer", role="member")):
        result = await ToolRegistry.execute("write", ctx, filePath=str(candidate), content="escaped bytes")

    assert result.success, result.error
    assert target.read_bytes() == b"escaped bytes"
    assert result.metadata["filepath"] == str(target)
    assert result.attachments is None


@pytest.mark.parametrize("user_scoped", [False, True])
def test_output_scope_ignores_unrelated_broken_root(tmp_path, user_scoped):
    workspace = tmp_path / "workspace"
    global_root = workspace / "outputs"
    user_root = workspace / "users" / "writer" / "outputs"
    root, broken_root = (user_root, global_root) if user_scoped else (global_root, user_root)
    broken_root.parent.mkdir(parents=True)
    broken_root.symlink_to(broken_root, target_is_directory=True)
    target = root / "missing.md"

    assert WorkspaceManager.get_instance().resolve_output_scope(target, username="writer") == WorkspaceOutputScope(
        path=target.resolve(), root=root.resolve(), relative_path="missing.md",
        username="writer" if user_scoped else None,
    )
    assert not root.exists()


@pytest.mark.parametrize("user_scoped", [False, True])
def test_output_scope_preserves_workspace_alias_and_in_scope_links(tmp_path, monkeypatch, user_scoped):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(alias))
    manager = WorkspaceManager.get_instance()
    relative_root = Path("users/writer/outputs") if user_scoped else Path("outputs")
    root = workspace / relative_root
    target = root / "nested" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"scoped bytes")
    (root / "link").symlink_to(target.parent, target_is_directory=True)
    expected = WorkspaceOutputScope(
        path=target.resolve(), root=root.resolve(), relative_path="nested/report.md",
        username="writer" if user_scoped else None,
    )
    assert manager.resolve_output_scope(alias / relative_root / "link/report.md", username="writer") == expected
    assert manager.resolve_output_scope(target.resolve(), username="writer") == expected
    assert target.read_bytes() == b"scoped bytes"
    # An unrelated host alias is not itself an output scope.
    host_link = tmp_path / "host-link"
    host_link.symlink_to(root, target_is_directory=True)
    assert manager.resolve_output_scope(host_link / "nested/report.md", username="writer") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "directory"])
async def test_output_attachment_requires_existing_regular_file(tmp_path, kind):
    from flocks.tool.file.write import _build_output_attachment

    target = tmp_path / "workspace" / "outputs" / "not-a-file"
    if kind == "directory":
        target.mkdir(parents=True)
    with patch("flocks.tool.file.write._resolve_owner_username", new=AsyncMock(return_value=None)):
        assert await _build_output_attachment(_make_ctx(), str(target)) is None
    if kind == "missing":
        assert not (tmp_path / "workspace").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_owner", [None, "service"])
async def test_output_scope_tracks_authenticated_writer_not_session_owner(tmp_path, session_owner):
    from flocks.session.files import (
        build_session_context,
        public_resource_id,
        resolve_session_resource,
        session_outputs_root,
    )
    from flocks.auth.context import AuthUser
    from flocks.session.message import ToolPart, ToolStateCompleted

    ctx = _make_ctx(extra={"workspace_dir": str(tmp_path / "project")})
    session = SimpleNamespace(
        id=ctx.session_id,
        project_id="global",
        owner_user_id="api-token-service",
        owner_username=session_owner,
        directory="",
        metadata={},
    )
    relative = f"{dt.date.today().isoformat()}/report.md"
    wrong_file = session_outputs_root(session) / relative
    wrong_file.parent.mkdir(parents=True, exist_ok=True)
    wrong_file.write_text("different owner's file", encoding="utf-8")

    with (
        patch("flocks.auth.context.get_current_auth_user", return_value=AuthUser(id="admin", username="admin team", role="admin")),
        patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=session)),
    ):
        result = await ToolRegistry.execute("write", ctx, filePath="report.md", content="admin output")

    assert result.success, result.error
    attachment = result.attachments[0]
    assert attachment["source"] == {
        "root": "workspace-output",
        "path": relative,
        "username": "admin_team",
    }
    target = tmp_path / "workspace" / "users" / "admin_team" / "outputs" / relative
    assert target.read_text() == "admin output"
    assert wrong_file.read_text() == "different owner's file"
    part = ToolPart(
        id="prt_write",
        sessionID=session.id,
        messageID=ctx.message_id,
        callID=ctx.call_id,
        tool="write",
        state=ToolStateCompleted(
            input={}, output="ok", title="report.md", metadata=result.metadata,
            time={"start": 1, "end": 2}, attachments=result.attachments,
        ),
    )
    message = SimpleNamespace(
        info=SimpleNamespace(id=ctx.message_id, role="assistant", time={"created": 1}),
        parts=[part],
    )
    resource_id = public_resource_id(ctx.message_id, attachment["id"])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        resource = await resolve_session_resource(session, resource_id)
        assert resource.path == target.resolve()
        assert resource.path.read_text() == "admin output"
        context = await build_session_context(session, include_roots=False)
        assert context["outputs"][0]["resourceID"] == resource_id
        assert context["outputs"][0]["status"] == "ready"

        target.unlink()
        context = await build_session_context(session, include_roots=False)
        assert context["outputs"][0]["status"] == "missing"
        assert (await resolve_session_resource(session, resource_id)).path != wrong_file

        # A pre-binding descriptor is only compatible in the owner's root;
        # conflicting metadata cannot silently map it onto the same-name file.
        attachment["source"].pop("username")
        part.state.attachments = [attachment]
        assert (await build_session_context(session, include_roots=False))["outputs"] == []
        with pytest.raises(FileNotFoundError):
            await resolve_session_resource(session, resource_id)
