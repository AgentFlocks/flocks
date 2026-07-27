from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult
import flocks.tool.system.session_manage  # noqa: F401 - ensure tool registration
from flocks.tool.system.session_manage import _session_archive_impl, session_manage


def make_ctx() -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.ask = AsyncMock(return_value=None)
    return ctx


def test_session_manage_is_single_registered_session_tool():
    names = {tool.name for tool in ToolRegistry.list_tools()}

    assert "session_manage" in names
    assert "session_list" not in names
    assert "session_get" not in names
    assert "session_create" not in names
    assert "session_update" not in names
    assert "session_delete" not in names
    assert "session_archive" not in names


def test_session_manage_schema_uses_action_dispatch():
    tool = next(tool for tool in ToolRegistry.list_tools() if tool.name == "session_manage")
    schema = tool.get_schema()

    assert schema.properties["action"]["enum"] == [
        "list",
        "get",
        "create",
        "update",
        "delete",
        "archive",
    ]
    assert schema.required == ["action"]
    assert "session_id" in schema.properties


@pytest.mark.asyncio
async def test_session_manage_dispatches_list_action():
    ctx = make_ctx()
    expected = ToolResult(success=True, output={"sessions": []})

    with patch(
        "flocks.tool.system.session_manage._session_list_impl",
        AsyncMock(return_value=expected),
    ) as list_impl:
        result = await session_manage(ctx, action="list", status="active", limit=3)

    assert result is expected
    list_impl.assert_awaited_once()
    _, kwargs = list_impl.await_args
    assert kwargs["status"] == "active"
    assert kwargs["limit"] == 3
    ctx.ask.assert_not_called()


@pytest.mark.asyncio
async def test_session_manage_requires_session_id_for_get():
    result = await session_manage(make_ctx(), action="get")

    assert result.success is False
    assert "session_id" in (result.error or "")


@pytest.mark.asyncio
async def test_session_manage_delete_requests_confirmation():
    ctx = make_ctx()
    expected = ToolResult(success=True, output="deleted")

    with patch(
        "flocks.tool.system.session_manage._session_delete_impl",
        AsyncMock(return_value=expected),
    ) as delete_impl:
        result = await session_manage(ctx, action="delete", session_id="ses_123")

    assert result is expected
    ctx.ask.assert_awaited_once()
    ask_kwargs = ctx.ask.await_args.kwargs
    assert ask_kwargs["permission"] == "session_manage"
    assert ask_kwargs["metadata"] == {
        "action": "permanent_delete",
        "session_id": "ses_123",
        "destructive": True,
    }
    delete_impl.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_manage_rejects_archiving_the_current_running_session():
    ctx = make_ctx()
    ctx.session_id = "ses_current"
    session = SimpleNamespace(
        id="ses_current",
        project_id="project",
        title="Current",
        status="active",
    )

    with (
        patch("flocks.storage.storage.Storage.list_keys", AsyncMock(return_value=["session:project:ses_current"])),
        patch("flocks.storage.storage.Storage.get", AsyncMock(return_value=session)),
        patch("flocks.session.session.Session.archive", AsyncMock()) as archive,
    ):
        result = await _session_archive_impl(ctx, "ses_current", True)

    assert result.success is False
    assert "不能在当前会话" in (result.error or "")
    archive.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_manage_restore_revives_removed_project_first():
    ctx = make_ctx()
    ctx.session_id = "ses_operator"
    session = SimpleNamespace(
        id="ses_archived",
        project_id="project_removed",
        title="Archived",
        status="archived",
        owner_user_id="usr_owner",
    )

    with (
        patch(
            "flocks.storage.storage.Storage.list_keys",
            AsyncMock(return_value=["session:project_removed:ses_archived"]),
        ),
        patch("flocks.storage.storage.Storage.get", AsyncMock(return_value=session)),
        patch("flocks.session.session.Session.restore", AsyncMock(return_value=True)) as restore_session,
    ):
        result = await _session_archive_impl(ctx, session.id, False)

    assert result.success is True
    restore_session.assert_awaited_once_with(
        session.project_id,
        session.id,
        project_owner_id="usr_owner",
    )
