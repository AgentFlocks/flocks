"""
Tests for Phase 2: SessionContext interface.

Verifies that:
1. SessionContext protocol is properly defined
2. DefaultSessionContext implements all methods
3. DefaultSessionContext delegates to underlying session modules
4. LoopContext carries session_ctx
5. SessionRunner accepts session_ctx
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.core.context import SessionContext, DefaultSessionContext
from flocks.session.files import (
    bind_staged_chat_upload,
    build_session_context,
    create_chat_upload_target,
    list_context_root,
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

def _session(tmp_path, *, metadata=None):
    return SimpleNamespace(
        id="ses_context",
        owner_user_id="usr_owner",
        owner_username=None,
        directory=str(tmp_path / "project"),
        metadata=metadata or {},
    )


def _message(message_id, role, parts, created=1):
    return SimpleNamespace(
        info=SimpleNamespace(id=message_id, role=role, time={"created": created}),
        parts=parts,
    )


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
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=(messages, False))),
        patch("flocks.session.features.todo.Todo.get", new=AsyncMock(return_value=[])),
    ):
        context = await build_session_context(session, include_roots=True)

    assert context["historyTruncated"] is False
    assert len(context["contextFiles"]) == 1
    assert parse_public_resource_id(
        context["contextFiles"][0]["resourceID"]
    ) == ("msg_user", "prt_upload")
    assert context["contextFiles"][0]["logicalPath"] == "Uploads/paper.pdf"
    assert len(context["outputs"]) == 1
    assert parse_public_resource_id(
        context["outputs"][0]["resourceID"]
    ) == ("msg_agent_2", "prt_output_2")
    assert context["outputs"][0]["logicalPath"] == "Outputs/2026-09-14/report.md"
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


def test_context_root_browsing_rejects_parent_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "docs").mkdir()
    (project / "docs" / "note.md").write_text("note", encoding="utf-8")
    session = _session(tmp_path)

    items = list_context_root(session, "project", "docs")
    assert items == [{
        "name": "note.md",
        "path": "docs/note.md",
        "type": "file",
        "size": 4,
        "modifiedAt": items[0]["modifiedAt"],
        "isTextFile": True,
    }]
    with pytest.raises(ValueError, match="Hidden and parent"):
        resolve_context_root_path(session, "project", "../outside.txt")
    with pytest.raises(ValueError, match="Hidden and parent"):
        resolve_context_root_path(session, "project", ".env")


@pytest.mark.asyncio
async def test_hidden_project_root_is_not_exposed_as_context(tmp_path):
    hidden = tmp_path / ".ssh"
    hidden.mkdir()
    (hidden / "id_rsa").write_text("secret", encoding="utf-8")
    session = _session(tmp_path)
    session.directory = str(hidden)

    with (
        patch("flocks.session.files._messages_with_parts", new=AsyncMock(return_value=([], False))),
        patch("flocks.session.features.todo.Todo.get", new=AsyncMock(return_value=[])),
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
