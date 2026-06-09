from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry, ToolResult
import flocks.tool.system.session_manage  # noqa: F401 - ensure tool registration
from flocks.tool.system.session_manage import session_manage


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
    assert ask_kwargs["metadata"] == {"action": "delete", "session_id": "ses_123"}
    delete_impl.assert_awaited_once()
