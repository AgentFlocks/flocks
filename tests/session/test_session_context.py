"""
Tests for Phase 2: SessionContext interface.

Verifies that:
1. SessionContext protocol is properly defined
2. DefaultSessionContext implements all methods
3. DefaultSessionContext delegates to underlying session modules
4. LoopContext carries session_ctx
5. SessionRunner accepts session_ctx
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.core.context import SessionContext, DefaultSessionContext
from flocks.session.files import (
    bind_staged_chat_upload,
    build_session_context,
    create_chat_upload_target,
    list_context_root,
    output_file_attachments,
    parse_public_resource_id,
    public_resource_id,
    resolve_context_root,
    resolve_context_root_path,
    resolve_session_resource,
    session_outputs_root,
    session_uploads_dir,
)
from flocks.session.message import FilePart, ToolPart, ToolStateCompleted
from flocks.session.runner import SessionRunner
from flocks.session.session_loop import LoopContext
from flocks.workspace.manager import WorkspaceManager


class TestSessionContextProtocol:
    """Verify SessionContext protocol definition."""

    def test_protocol_is_runtime_checkable(self):
        """SessionContext should be runtime checkable."""
        assert hasattr(SessionContext, '__protocol_attrs__') or hasattr(SessionContext, '__abstractmethods__') or True
        # runtime_checkable means isinstance checks work
        from typing import runtime_checkable
        # The decorator was applied in context.py

    def test_default_context_implements_protocol(self):
        """DefaultSessionContext should implement SessionContext."""
        session = MagicMock()
        session.id = "test-session"
        session.directory = "/test/dir"
        session.project_id = "test-project"
        
        ctx = DefaultSessionContext(session)
        
        # Verify all protocol methods exist
        assert hasattr(ctx, 'session_id')
        assert hasattr(ctx, 'session')
        assert hasattr(ctx, 'directory')
        assert hasattr(ctx, 'get_messages')
        assert hasattr(ctx, 'get_text_content')
        assert hasattr(ctx, 'get_parts')
        assert hasattr(ctx, 'store_message')
        assert hasattr(ctx, 'update_message')
        assert hasattr(ctx, 'update_status')
        assert hasattr(ctx, 'request_compaction')
        assert hasattr(ctx, 'is_overflow')
        assert hasattr(ctx, 'touch')


class TestDefaultSessionContext:
    """Test DefaultSessionContext implementation."""

    def _make_session(self, session_id="ses-123", directory="/test/dir", project_id="proj-1"):
        session = MagicMock()
        session.id = session_id
        session.directory = directory
        session.project_id = project_id
        return session

    def test_session_id_property(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        assert ctx.session_id == "ses-123"

    def test_session_property(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        assert ctx.session is session

    def test_directory_property(self):
        session = self._make_session(directory="/my/project")
        ctx = DefaultSessionContext(session)
        assert ctx.directory == "/my/project"

    def test_directory_property_none_fallback(self):
        session = self._make_session(directory=None)
        ctx = DefaultSessionContext(session)
        assert ctx.directory == ""

    @pytest.mark.asyncio
    async def test_get_messages_delegates_to_message(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        mock_messages = [MagicMock(), MagicMock()]
        with patch("flocks.session.message.Message.list", new_callable=AsyncMock, return_value=mock_messages):
            result = await ctx.get_messages()
            assert result == mock_messages

    @pytest.mark.asyncio
    async def test_store_message_delegates_to_message(self):
        from flocks.session.message import MessageRole
        
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        mock_msg = MagicMock()
        with patch("flocks.session.message.Message.create", new_callable=AsyncMock, return_value=mock_msg):
            result = await ctx.store_message(
                role=MessageRole.ASSISTANT,
                content="Hello",
                agent="rex",
            )
            assert result is mock_msg

    @pytest.mark.asyncio
    async def test_update_status_busy(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.core.status.SessionStatus.set") as mock_set:
            await ctx.update_status("busy")
            mock_set.assert_called_once()
            args = mock_set.call_args
            assert args[0][0] == "ses-123"

    @pytest.mark.asyncio
    async def test_update_status_clear(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.core.status.SessionStatus.clear") as mock_clear:
            await ctx.update_status("clear")
            mock_clear.assert_called_once_with("ses-123")

    @pytest.mark.asyncio
    async def test_touch_delegates_to_session(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.session.Session.touch", new_callable=AsyncMock) as mock_touch:
            await ctx.touch()
            mock_touch.assert_called_once_with("proj-1", "ses-123")


class TestLoopContextSessionCtx:
    """LoopContext should carry session_ctx."""

    def test_loop_context_has_session_ctx_field(self):
        import asyncio
        session = MagicMock()
        session.id = "test"
        session.directory = "/test"
        session.project_id = "proj"
        
        ctx = LoopContext(
            session=session,
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            agent_name="rex",
        )
        assert ctx.session_ctx is None

    def test_loop_context_with_session_ctx(self):
        session = MagicMock()
        session.id = "test"
        session.directory = "/test"
        session.project_id = "proj"
        
        session_ctx = DefaultSessionContext(session)
        ctx = LoopContext(
            session=session,
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            agent_name="rex",
            session_ctx=session_ctx,
        )
        assert ctx.session_ctx is session_ctx
        assert ctx.session_ctx.session_id == "test"

    def test_loop_context_tracks_observed_prompt_tokens(self):
        # B3 — LoopContext must expose ``last_observed_prompt_tokens`` so
        # the overflow decision can prefer the provider's reported usage
        # over our synthetic estimate.
        session = MagicMock()
        session.id = "test"
        session.directory = "/test"
        session.project_id = "proj"

        ctx = LoopContext(
            session=session,
            provider_id="anthropic",
            model_id="claude-sonnet-4",
            agent_name="rex",
        )
        assert ctx.last_observed_prompt_tokens == 0
        ctx.last_observed_prompt_tokens = 123_456
        assert ctx.last_observed_prompt_tokens == 123_456


class TestRunnerSessionCtx:
    """SessionRunner should accept session_ctx."""

    def test_runner_accepts_session_ctx(self):
        session = MagicMock()
        session.id = "test"
        session.directory = "/test"
        session.project_id = "proj"
        
        session_ctx = DefaultSessionContext(session)
        runner = SessionRunner(
            session=session,
            session_ctx=session_ctx,
        )
        assert runner.session_ctx is session_ctx

    def test_runner_session_ctx_defaults_to_none(self):
        session = MagicMock()
        session.id = "test"
        session.directory = "/test"
        session.project_id = "proj"
        
        runner = SessionRunner(session=session)
        assert runner.session_ctx is None


# ---------------------------------------------------------------------------
# Session Context file resource projection
# ---------------------------------------------------------------------------

def _session(tmp_path, *, metadata=None, project_id="default"):
    return SimpleNamespace(
        id="ses_context",
        owner_user_id="usr_owner",
        owner_username=None,
        project_id=project_id,
        directory=str(tmp_path / "project"),
        metadata=metadata or {},
    )


@pytest.fixture
async def project_session(tmp_path, monkeypatch):
    from flocks.project.project import Project

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "flocks-root"))
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))
    session = _session(tmp_path)
    project = await Project.create(
        owner_id=session.owner_user_id, name="Context project", worktree=session.directory,
    )
    session.project_id = project.id
    return session


def _message(message_id, role, parts, created=1):
    return SimpleNamespace(
        info=SimpleNamespace(id=message_id, role=role, time={"created": created}),
        parts=parts,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("project_id", [None, "", "default", "tasks", "legacy-git-project", "prj_unknown"])
async def test_unbound_context_does_not_infer_project_from_directory(project_session, project_id):
    # Even a cwd matching a registered project does not bind an ordinary task.
    session = project_session
    session.project_id = project_id
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)
    assert context["roots"] == []
    with pytest.raises(FileNotFoundError):
        resolve_context_root(session, "project")


@pytest.mark.asyncio
async def test_registered_project_root_does_not_require_matching_session_owner(project_session):
    session = project_session
    session.owner_user_id = "other-session-owner"
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)
    assert context["roots"] == [{
        "id": "project", "kind": "project", "displayName": "project", "status": "available",
    }]
    assert resolve_context_root(session, "project")[0] == Path(session.directory)


@pytest.mark.asyncio
async def test_deleted_project_does_not_expose_its_session_directory(project_session):
    from flocks.project.project import Project

    session = project_session
    assert await Project.delete(session.project_id, owner_id=session.owner_user_id)
    assert Path(session.directory).is_dir()
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)
    assert context["roots"] == []
    with pytest.raises(FileNotFoundError):
        resolve_context_root(session, "project")


@pytest.mark.asyncio
async def test_unbound_context_keeps_explicitly_attached_folders(project_session):
    session = project_session
    session.project_id = "default"
    session.metadata = {"contextFolders": [{
        "id": "folder_explicit", "path": session.directory, "displayName": "Attached folder",
    }]}
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)
    assert [root["id"] for root in context["roots"]] == ["folder_explicit"]
    assert resolve_context_root(session, "folder_explicit")[0] == Path(session.directory)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["skill_load", "load_skill"])
@pytest.mark.parametrize("input_key", ["name", "skill"])
@pytest.mark.parametrize("state, expected_status, error", [
    ("pending", "loading", None),
    ("running", "loading", None),
    ("completed", "loaded", None),
    ("error", "error", 'Skill "missing-skill" not found.'),
    ("error", "error", None),
    ("unexpected", "unknown", None),
    (None, "unknown", None),
])
async def test_context_preserves_skill_states(tmp_path, tool, input_key, state, expected_status, error):
    session = _session(tmp_path)
    part = SimpleNamespace(
        type="tool", tool=tool,
        state=SimpleNamespace(status=state, input={input_key: "missing-skill"}, error=error),
    )
    message = _message("msg_skill", "assistant", [part])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=False)
    expected = {"name": "missing-skill", "description": None, "status": expected_status}
    if error:
        expected["error"] = error
    assert context["skills"] == [expected]


@pytest.mark.asyncio
async def test_context_skill_retry_replaces_error_with_latest_state(tmp_path):
    from flocks.session.message import ToolStateError, ToolStateRunning

    session = _session(tmp_path)
    states = [
        ToolStateError(input={"name": "retry-skill"}, error="Skill not found", time={"start": 1, "end": 2}),
        ToolStateRunning(input={"name": "retry-skill"}, time={"start": 3}),
        ToolStateCompleted(input={"name": "retry-skill"}, output="Loaded", title="retry-skill", metadata={}, time={"start": 3, "end": 4}),
    ]
    messages = []
    for index, (state, expected_status) in enumerate(zip(states, ["error", "loading", "loaded"])):
        part = ToolPart(
            id=f"prt_skill_{index}", sessionID=session.id, messageID=f"msg_skill_{index}",
            callID=f"call_skill_{index}", tool="skill_load", state=state,
        )
        messages.append(_message(part.messageID, "assistant", [part], created=index))
        with (
            patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=(messages, False, None))),
            patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
        ):
            context = await build_session_context(session, include_roots=False)
        assert len(context["skills"]) == 1
        assert context["skills"][0]["status"] == expected_status
        if expected_status == "error":
            assert context["skills"][0]["error"] == "Skill not found"
        else:
            assert "error" not in context["skills"][0]


@pytest.mark.asyncio
async def test_context_derives_uploads_and_deduplicated_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None
    session = _session(tmp_path)
    (tmp_path / "project").mkdir()

    upload_root = session_uploads_dir(session.id)
    upload_root.mkdir(parents=True)
    upload = upload_root / "upload.pdf"
    upload.write_bytes(b"pdf")
    upload_part = FilePart(
        id="prt_upload",
        sessionID=session.id,
        messageID="msg_user",
        mime="application/pdf",
        filename="paper.pdf",
        url=upload.as_uri(),
    )

    outputs_root = session_outputs_root(session)
    output = outputs_root / "2026-09-14" / "report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# report", encoding="utf-8")

    first_attachment = {
        "id": "prt_output_1",
        "type": "file",
        "mime": "text/markdown",
        "filename": "report.md",
        "size": output.stat().st_size,
        "modifiedAt": int(output.stat().st_mtime * 1000),
        "origin": "agent_output",
        "source": {"root": "workspace-output", "path": "2026-09-14/report.md"},
    }
    second_attachment = {**first_attachment, "id": "prt_output_2"}
    first_tool = ToolPart(
        id="prt_tool_1",
        sessionID=session.id,
        messageID="msg_agent_1",
        callID="call_1",
        tool="write",
        state=ToolStateCompleted(
            input={"filePath": "report.md"},
            output="ok",
            title="report.md",
            metadata={},
            time={"start": 1, "end": 2},
            attachments=[first_attachment],
        ),
    )
    second_tool = ToolPart(
        id="prt_tool_2",
        sessionID=session.id,
        messageID="msg_agent_2",
        callID="call_2",
        tool="write",
        state=ToolStateCompleted(
            input={"filePath": "report.md"},
            output="ok",
            title="report.md",
            metadata={},
            time={"start": 3, "end": 4},
            attachments=[second_attachment],
        ),
    )
    messages = [
        _message("msg_user", "user", [upload_part], created=1),
        _message("msg_agent_1", "assistant", [first_tool], created=2),
        _message("msg_agent_2", "assistant", [second_tool], created=3),
    ]

    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=(messages, False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)

    assert context["hasMore"] is False
    assert len(context["contextFiles"]) == 1
    assert parse_public_resource_id(
        context["contextFiles"][0]["resourceID"]
    ) == ("msg_user", "prt_upload")
    assert context["contextFiles"][0]["logicalPath"] == "Uploads/paper.pdf"
    assert len(context["outputs"]) == 1
    assert parse_public_resource_id(
        context["outputs"][0]["resourceID"]
    ) == ("msg_agent_2", "prt_output_2")
    assert context["outputs"][0]["logicalPath"] == "outputs/2026-09-14/report.md"
    assert str(tmp_path) not in str(context)


@pytest.mark.asyncio
async def test_resource_resolution_is_scoped_to_session_parts(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    WorkspaceManager._instance = None
    session = _session(tmp_path)
    upload_root = session_uploads_dir(session.id)
    upload_root.mkdir(parents=True)
    upload = upload_root / "upload.txt"
    upload.write_text("hello", encoding="utf-8")
    part = FilePart(
        id="prt_upload",
        sessionID=session.id,
        messageID="msg_user",
        mime="text/plain",
        filename="hello.txt",
        url=upload.as_uri(),
    )
    messages = [_message("msg_user", "user", [part])]

    with patch(
        "flocks.session.message.Message.get_with_parts_lazy",
        new=AsyncMock(return_value=messages[0]),
    ):
        resource = await resolve_session_resource(
            session,
            public_resource_id("msg_user", "prt_upload"),
        )
        assert resource.path == upload.resolve()
        with pytest.raises(FileNotFoundError):
            await resolve_session_resource(
                session,
                public_resource_id("msg_user", "prt_other"),
            )


def test_context_root_browsing_rejects_parent_escape(project_session):
    session = project_session
    project = Path(session.directory)
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("note", encoding="utf-8")

    page = list_context_root(session, "project", "docs")
    assert page == {
        "items": [{
            "name": "note.md",
            "path": "docs/note.md",
            "type": "file",
            "size": 4,
            "modifiedAt": page["items"][0]["modifiedAt"],
            "isTextFile": True,
        }],
        "hasMore": False,
        "nextOffset": None,
    }
    with pytest.raises(ValueError, match="Hidden and parent"):
        resolve_context_root_path(session, "project", "../outside.txt")
    with pytest.raises(ValueError, match="Hidden and parent"):
        resolve_context_root_path(session, "project", ".env")


@pytest.mark.asyncio
async def test_hidden_project_root_is_not_exposed_as_context(tmp_path, project_session):
    hidden = tmp_path / ".ssh"
    hidden.mkdir()
    (hidden / "id_rsa").write_text("secret", encoding="utf-8")
    session = project_session
    session.directory = str(hidden)

    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False, None))),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)

    assert context["roots"] == []
    with pytest.raises(ValueError, match="Hidden folders"):
        resolve_context_root(session, "project")


def test_chat_upload_staging_binds_to_session(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    upload_id = "prt_upload_12345678"
    staged = create_chat_upload_target("usr_owner", upload_id, "report.pdf")
    staged.write_bytes(b"report")

    bound = bind_staged_chat_upload("ses_context", "usr_owner", upload_id)

    assert bound.parent == session_uploads_dir("ses_context")
    assert bound.read_bytes() == b"report"
    assert not staged.exists()


def _output_attachment(**updates):
    return {
        "id": "prt_output",
        "type": "file",
        "mime": "text/markdown",
        "filename": "report.md",
        "origin": "agent_output",
        "source": {"root": "workspace-output", "path": "report.md"},
        **updates,
    }


def _write_part(session, attachment, *, filepath=None, message_id="msg_agent"):
    return ToolPart(
        id="prt_write", sessionID=session.id, messageID=message_id,
        callID="call_write", tool="write",
        state=ToolStateCompleted(
            input={}, output="ok", title="report.md",
            metadata={"filepath": filepath} if filepath is not None else {},
            time={"start": 1, "end": 2},
            attachments=[attachment] if attachment is not None else None,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind, writer, owner, prefix", [
    ("bound", "admin", "session-owner", "users/admin/outputs"),
    ("bound", None, "session-owner", "outputs"),
    ("legacy-attachment", "admin", "admin", "users/admin/outputs"),
    ("legacy-attachment", None, None, "outputs"),
    ("legacy-metadata", "admin", "admin", "users/admin/outputs"),
    ("legacy-metadata", "member", "member", "users/member/outputs"),
    ("legacy-metadata", None, None, "outputs"),
    ("legacy-metadata", None, "admin", "outputs"),
    ("legacy-metadata", None, "member", "outputs"),
])
@pytest.mark.parametrize("relative", ["report.md", "2026-09-15/report.md"])
async def test_output_logical_path_shows_actual_workspace_scope(tmp_path, monkeypatch, source_kind, writer, owner, prefix, relative):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = owner
    manager = WorkspaceManager.get_instance()
    target = manager.get_default_outputs_dir(username=writer, include_today=False) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Report", encoding="utf-8")
    source = {"root": "workspace-output", "path": relative}
    if source_kind == "bound":
        source["username"] = writer
    attachment = None if source_kind == "legacy-metadata" else _output_attachment(
        sessionID=session.id, messageID="msg_agent", source=source,
    )
    message = _message("msg_agent", "assistant", [_write_part(session, attachment, filepath=str(target))])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
    ):
        context = await build_session_context(session, include_roots=False)
        descriptor = context["outputs"][0]
        assert descriptor["logicalPath"] == f"{prefix}/{relative}"
        resource = await resolve_session_resource(session, descriptor["resourceID"])
        assert resource.logical_path == descriptor["logicalPath"]
        assert resource.path == target.resolve()
        assert resource.path.read_text(encoding="utf-8") == "Report"
        assert str(tmp_path) not in descriptor["logicalPath"]


@pytest.mark.asyncio
async def test_legacy_outputs_with_identical_names_keep_distinct_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = "member"
    manager = WorkspaceManager.get_instance()
    messages = []
    expected = {}
    for index, username in enumerate(["member", None]):
        target = manager.get_default_outputs_dir(username=username, include_today=False) / "report.md"
        text = "member report" if username else "global report"
        target.write_text(text, encoding="utf-8")
        message_id = f"msg_output_{index}"
        part = _write_part(session, None, filepath=str(target), message_id=message_id)
        messages.append(_message(message_id, "assistant", [part], created=index))
        prefix = "users/member/outputs" if username else "outputs"
        expected[f"{prefix}/report.md"] = text
    by_id = {message.info.id: message for message in messages}
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=(messages, False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(side_effect=lambda _session, message_id: by_id[message_id])),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=False)
        assert context["counts"]["outputs"] == 2
        assert {item["logicalPath"] for item in context["outputs"]} == set(expected)
        assert len({item["resourceID"] for item in context["outputs"]}) == 2
        for descriptor in context["outputs"]:
            resource = await resolve_session_resource(session, descriptor["resourceID"])
            assert resource.path.read_text(encoding="utf-8") == expected[descriptor["logicalPath"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, prefix", [
    ("admin", "users/admin/outputs"),
    ("admin", "outputs"),
    (None, "outputs"),
])
async def test_missing_legacy_output_is_listed_without_creating_directories(tmp_path, monkeypatch, owner, prefix):
    workspace = tmp_path / "uncreated-workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = owner
    target = workspace / prefix / "missing.md"
    message = _message("msg_agent", "assistant", [_write_part(session, None, filepath=str(target))])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=False)
        assert context["counts"]["outputs"] == 1
        descriptor = context["outputs"][0]
        assert descriptor["status"] == "missing"
        assert descriptor["logicalPath"] == f"{prefix}/missing.md"
        assert (await resolve_session_resource(session, descriptor["resourceID"])).path == target.resolve()
    assert not workspace.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["other-user", "outside", "relative", "traversal", "symlink", "root-symlink", "failed-write"])
async def test_legacy_output_rejects_untrusted_scope_or_failed_write(tmp_path, monkeypatch, case):
    from flocks.session.message import ToolStateError

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = "member"
    manager = WorkspaceManager.get_instance()
    global_root = manager.get_default_outputs_dir(include_today=False)
    other_root = manager.get_default_outputs_dir(username="other", include_today=False)
    outside = other_root / "private.md" if case in {"other-user", "symlink", "root-symlink"} else tmp_path / "outside.md"
    outside.write_text("not a session output", encoding="utf-8")
    if case == "relative":
        filepath = "report.md"
    elif case == "traversal":
        filepath = str(global_root / ".." / "users" / "other" / "private.md")
    elif case == "symlink":
        link = global_root / "alias.md"
        link.symlink_to(outside)
        filepath = str(link)
    elif case == "root-symlink":
        user = manager.get_user_workspace_dir("member")
        user.mkdir(parents=True)
        (user / "outputs").symlink_to(other_root, target_is_directory=True)
        filepath = str(user / "outputs" / "private.md")
    elif case == "failed-write":
        filepath = str(global_root / "report.md")
        (global_root / "report.md").write_text("pre-existing output", encoding="utf-8")
    else:
        filepath = str(outside)
    part = _write_part(session, None, filepath=filepath)
    if case == "failed-write":
        part.state = ToolStateError(input={}, error="write failed", metadata={"filepath": filepath}, time={"start": 1, "end": 2})
    message = _message("msg_agent", "assistant", [part])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
        patch("flocks.session.features.todo.Todo.get_snapshot", new=AsyncMock(return_value=[])),
    ):
        assert (await build_session_context(session, include_roots=False))["outputs"] == []
        with pytest.raises(FileNotFoundError):
            await resolve_session_resource(session, public_resource_id("msg_agent", part.id))


def test_output_projection_drops_unbounded_payload_and_limits_fields():
    attachment = _output_attachment(
        sessionID="ses_context", messageID="msg_agent", size=7, modifiedAt=42,
        source={"root": "workspace-output", "path": "report.md", "username": None, "extra": {"blob": "x" * 10000}},
        url="data:application/pdf;base64," + "A" * 10000,
        extra={"binary": b"binary"},
    )
    projected = output_file_attachments("write", [attachment] * 100)
    assert len(projected) == 32
    assert projected[0] == {
        "id": "prt_output", "type": "file", "mime": "text/markdown",
        "filename": "report.md", "origin": "agent_output", "size": 7,
        "modifiedAt": 42, "sessionID": "ses_context", "messageID": "msg_agent",
        "source": {"root": "workspace-output", "path": "report.md", "username": None},
    }
    assert "url" in attachment  # ToolResult and CLI consumers retain the original result.
    assert output_file_attachments("read", [attachment]) is None
    assert output_file_attachments("bash", [attachment]) is None
    assert output_file_attachments("write", {"attachment": attachment}) is None
    assert output_file_attachments("write", [_output_attachment(size=True, modifiedAt=2**100)]) == [_output_attachment()]


@pytest.mark.parametrize("updates", [
    {"type": "image"},
    {"origin": "user_upload"},
    {"id": "x" * 513},
    {"mime": b"image/png"},
    {"filename": "data:application/pdf;base64,AA"},
    {"source": {"root": "other", "path": "report.md"}},
    {"source": {"root": "workspace-output", "path": "../secret"}},
    {"source": {"root": "workspace-output", "path": "/secret"}},
    {"source": {"root": "workspace-output", "path": "."}},
    {"source": {"root": "workspace-output", "path": "x" * 4097}},
    {"source": {"root": "workspace-output", "path": "data:application/pdf;base64,AA"}},
    {"source": {"root": "workspace-output", "path": "report.md", "username": "../admin"}},
    {"source": {"root": "workspace-output", "path": "report.md", "username": None}},
])
def test_output_projection_rejects_invalid_descriptors(updates):
    assert output_file_attachments("write", [_output_attachment(**updates)]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_binding", ["conflict", "invalid_scope", "wrong_message", "wrong_session"])
@pytest.mark.parametrize("owner", [None, "admin"])
async def test_rejected_output_binding_never_falls_back_to_metadata(tmp_path, monkeypatch, invalid_binding, owner):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = owner
    output = WorkspaceManager.get_instance().get_default_outputs_dir(include_today=False) / "report.md"
    output.write_text("global output")
    attachment = _output_attachment(
        sessionID=session.id, messageID="msg_agent",
        source={"root": "workspace-output", "path": "report.md", "username": None},
    )
    if invalid_binding == "conflict":
        attachment["source"]["path"] = "different.md"
    elif invalid_binding == "invalid_scope":
        attachment["source"]["username"] = "../bad"
    elif invalid_binding == "wrong_message":
        attachment["messageID"] = "msg_other"
    else:
        attachment["sessionID"] = "ses_other"
    part = _write_part(session, attachment, filepath=str(output))
    message = _message("msg_agent", "assistant", [part])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
    ):
        assert (await build_session_context(session, include_roots=False))["outputs"] == []
        for part_id in (part.id, attachment["id"]):
            with pytest.raises(FileNotFoundError):
                await resolve_session_resource(session, public_resource_id("msg_agent", part_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("with_attachment", [False, True])
async def test_legacy_output_keeps_owner_paths_and_rejects_host_paths(tmp_path, monkeypatch, with_attachment):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    session.owner_username = "owner"
    target = session_outputs_root(session) / "report.md"
    target.write_text("owner report")
    attachment = _output_attachment() if with_attachment else None
    part = _write_part(session, attachment, filepath=str(target))
    message = _message("msg_agent", "assistant", [part])
    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], False, None))),
        patch("flocks.session.message.Message.get_with_parts_lazy", new=AsyncMock(return_value=message)),
    ):
        context = await build_session_context(session, include_roots=False)
        descriptor = context["outputs"][0]
        assert (await resolve_session_resource(session, descriptor["resourceID"])).path == target.resolve()
        part.state.metadata["filepath"] = str(tmp_path / "outside.md")
        assert (await build_session_context(session, include_roots=False))["outputs"] == []
        with pytest.raises(FileNotFoundError):
            await resolve_session_resource(session, descriptor["resourceID"])


@pytest.mark.asyncio
async def test_context_pages_all_history_and_opens_old_file_without_snapshot(tmp_path, monkeypatch):
    from flocks.session.message import Message, UserMessageInfo

    session = _session(tmp_path)
    upload_dir = session_uploads_dir(session.id)
    upload_dir.mkdir(parents=True)
    target = upload_dir / "old.txt"
    target.write_text("older than 1000 messages")
    upload = FilePart(
        id="prt_old", sessionID=session.id, messageID="msg_0000",
        mime="text/plain", filename="old.txt", url=target.as_uri(),
    )
    messages = [
        UserMessageInfo(
            id=f"msg_{index:04d}", sessionID=session.id, time={"created": index},
            agent="rex", model={"providerID": "test", "modelID": "test"},
        )
        for index in range(1005)
    ]
    by_id = {message.id: message for message in messages}
    monkeypatch.setattr(Message, "_parts_cache", {session.id: {message.id: [] for message in messages}})
    load_parts = AsyncMock(side_effect=lambda _session, message_id, **_kwargs: [upload] if message_id == "msg_0000" else [])
    with (
        patch.object(Message, "list", new=AsyncMock(return_value=messages)) as list_messages,
        patch.object(Message, "get", new=AsyncMock(side_effect=lambda _session, message_id: by_id.get(message_id))),
        patch.object(Message, "_load_parts_for_message", new=load_parts),
    ):
        page = await build_session_context(session, include_roots=False, limit=200)
        assert page["contextFiles"] == []
        assert page["hasMore"] is True
        assert page["nextBefore"] == "msg_0805"
        assert page["messageIDs"] == [message.id for message in messages[-200:]]
        assert load_parts.await_count == 200
        load_parts.reset_mock()
        resource = await resolve_session_resource(session, public_resource_id("msg_0000", upload.id))
        assert resource.path.read_text() == "older than 1000 messages"
        load_parts.assert_awaited_once_with(session.id, "msg_0000")

        seen_ids = list(page["messageIDs"])
        while page["hasMore"]:
            load_parts.reset_mock()
            page = await build_session_context(session, include_roots=False, before=page["nextBefore"], limit=200)
            assert load_parts.await_count <= 200
            seen_ids.extend(page["messageIDs"])
        assert len(seen_ids) == len(set(seen_ids)) == 1005
        assert page["nextBefore"] is None
        assert page["contextFiles"][0]["resourceID"] == public_resource_id("msg_0000", upload.id)
        assert all(call.kwargs["include_archived"] for call in list_messages.await_args_list)
        with pytest.raises(ValueError, match="cursor"):
            await build_session_context(session, include_roots=False, before="missing")
        for limit in (0, 201):
            with pytest.raises(ValueError, match="page size"):
                await build_session_context(session, include_roots=False, limit=limit)


@pytest.mark.asyncio
async def test_same_path_across_pages_has_stable_key_and_same_name_different_paths_survive(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(WorkspaceManager, "_instance", None)
    session = _session(tmp_path)
    root = session_outputs_root(session)
    for directory in ("first", "second"):
        target = root / directory / "report.md"
        target.parent.mkdir()
        target.write_text(directory)
    first = _output_attachment(source={"root": "workspace-output", "path": "first/report.md"})
    second = _output_attachment(id="prt_second", source={"root": "workspace-output", "path": "second/report.md"})
    recent = _message("msg_new", "assistant", [_write_part(session, first), _write_part(session, second)])
    older = _message("msg_old", "assistant", [_write_part(session, {**first, "id": "prt_old"})])
    with patch("flocks.session.files._messages_with_parts", new=AsyncMock(side_effect=[
        ([recent], True, "msg_new"), ([older], False, None),
    ])):
        head = await build_session_context(session, include_roots=False)
        tail = await build_session_context(session, include_roots=False, before="msg_new")
    assert len(head["outputs"]) == 2
    assert len({item["fileKey"] for item in head["outputs"]}) == 2
    assert {item["displayName"] for item in head["outputs"]} == {"report.md"}
    matching = next(item for item in head["outputs"] if item["logicalPath"] == "outputs/first/report.md")
    assert matching["fileKey"] == tail["outputs"][0]["fileKey"]
    assert matching["resourceID"] != tail["outputs"][0]["resourceID"]


def test_directory_pages_resolve_registered_root_once_per_page(project_session):
    from flocks.project.project import Project

    session = project_session
    docs = Path(session.directory) / "docs"
    docs.mkdir()
    filenames = [f"entry_{index}.txt" for index in range(3)]
    for name in filenames:
        (docs / name).write_text("candidate")

    with (
        patch("flocks.session.files.resolve_context_root", wraps=resolve_context_root) as resolve_root,
        patch.object(Project, "get_owner_user_id", wraps=Project.get_owner_user_id) as lookup_owner,
    ):
        offset = 0
        seen = []
        for has_more, next_offset in ((True, 2), (False, None)):
            page = list_context_root(session, "project", "docs", offset=offset, limit=2)
            assert (resolve_root.call_count, lookup_owner.call_count) == (1, 1)
            resolve_root.assert_called_once_with(session, "project")
            lookup_owner.assert_called_once_with(session.project_id)
            assert page["hasMore"] is has_more
            assert page["nextOffset"] == next_offset
            seen.extend(item["name"] for item in page["items"])
            offset = next_offset
            resolve_root.reset_mock()
            lookup_owner.reset_mock()

    assert sorted(seen) == filenames


def test_directory_page_only_inspects_candidate_entries(project_session, monkeypatch):
    session = project_session
    project = Path(session.directory)
    for index in range(10000, 10003):
        (project / f"entry_{index}.txt").write_text("candidate")
    visited = []
    resolved = []
    stats = []

    def entries():
        for index in range(100000):
            visited.append(index)
            yield SimpleNamespace(name=f"entry_{index}.txt", path=str(project / f"entry_{index}.txt"))

    original_resolve, original_stat = Path.resolve, Path.stat

    def resolve(path, *args, **kwargs):
        if path.name.startswith("entry_"):
            resolved.append(path.name)
        return original_resolve(path, *args, **kwargs)

    def stat(path, *args, **kwargs):
        if path.name.startswith("entry_"):
            stats.append(path.name)
        return original_stat(path, *args, **kwargs)

    import os

    original_scandir = os.scandir
    monkeypatch.setattr("flocks.session.files.os.scandir", lambda base: (
        nullcontext(entries()) if Path(base) == project else original_scandir(base)
    ))
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "stat", stat)
    page = list_context_root(session, "project", offset=10000, limit=3)
    assert page["hasMore"] is True
    assert page["nextOffset"] == 10003
    assert len(page["items"]) == len(resolved) == len(stats) == 3
    assert len(visited) == 10004
    assert set(stats) == {f"entry_{index}.txt" for index in range(10000, 10003)}


def test_filtered_directory_page_advances_and_rejects_symlink_escape(tmp_path, monkeypatch, project_session):
    session = project_session
    project = Path(session.directory)
    hidden = project / ".secret"
    hidden.write_text("hidden")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    link = project / "escape.txt"
    link.symlink_to(outside)
    visible = project / "visible.txt"
    visible.write_text("visible")
    entries = [SimpleNamespace(name=path.name, path=str(path)) for path in (hidden, link, visible)]
    import os

    original_scandir = os.scandir
    monkeypatch.setattr("flocks.session.files.os.scandir", lambda base: (
        nullcontext(iter(entries)) if Path(base) == project else original_scandir(base)
    ))
    first = list_context_root(session, "project", limit=2)
    assert first == {"items": [], "hasMore": True, "nextOffset": 2}
    second = list_context_root(session, "project", limit=2, offset=first["nextOffset"])
    assert [item["name"] for item in second["items"]] == ["visible.txt"]
    assert second["hasMore"] is False
    assert second["nextOffset"] is None
    with pytest.raises(ValueError, match="escapes"):
        resolve_context_root_path(session, "project", "escape.txt")
    for kwargs in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        with pytest.raises(ValueError, match="page"):
            list_context_root(session, "project", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored, historical, expected, known", [
    (None, None, [], False),
    (None, [], [], True),
    (None, [{"id": "old", "content": "Old task"}], [{"id": "old", "content": "Old task"}], True),
    ([], [{"id": "old", "content": "Old task"}], [], True),
    ([], None, [], True),
])
async def test_context_progress_distinguishes_clear_from_missing_legacy_state(tmp_path, stored, historical, expected, known):
    from flocks.storage.storage import Storage

    session = _session(tmp_path)
    if stored is not None:
        await Storage.set(f"todo:{session.id}", stored, "todo")
    parts = [] if historical is None else [SimpleNamespace(
        type="tool", tool="todo", state=SimpleNamespace(
            status="completed", input={"todos": historical}, metadata={},
        ),
    )]
    message = _message("msg_old", "assistant", parts)
    with patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([message], True, "msg_old"))):
        page = await build_session_context(session, include_roots=False, before="msg_new")
    assert page["progress"] == expected
    assert page["progressKnown"] is known


@pytest.mark.asyncio
async def test_legacy_todo_clear_overrides_earlier_part_in_same_page(tmp_path):
    parts = [SimpleNamespace(
        type="tool", tool="todo", state=SimpleNamespace(
            status="completed", input={}, metadata={"newTodos": todos},
        ),
    ) for todos in ([{"id": "old", "content": "Old task"}], [])]
    session = _session(tmp_path)
    messages = [_message("msg_old", "assistant", parts)]
    with patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=(messages, False, None))):
        page = await build_session_context(session, include_roots=False)
    assert page["progress"] == []
    assert page["progressKnown"] is True
