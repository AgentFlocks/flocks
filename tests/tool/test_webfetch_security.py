import pytest

from flocks.tool.registry import ToolContext
from flocks.tool.web.webfetch import webfetch_tool


@pytest.mark.asyncio
async def test_webfetch_rejects_loopback_before_permission_request():
    async def _permission_callback(_request):
        raise AssertionError("permission should not be requested for blocked URLs")

    ctx = ToolContext(
        session_id="test",
        message_id="test",
        permission_callback=_permission_callback,
    )

    result = await webfetch_tool(ctx, url="http://localhost:8000/internal")

    assert result.success is False
    assert "not allowed" in result.error


@pytest.mark.asyncio
async def test_webfetch_rejects_legacy_ipv4_loopback_before_permission_request():
    async def _permission_callback(_request):
        raise AssertionError("permission should not be requested for blocked URLs")

    ctx = ToolContext(
        session_id="test",
        message_id="test",
        permission_callback=_permission_callback,
    )

    result = await webfetch_tool(ctx, url="http://127.1:8000/internal")

    assert result.success is False
    assert "restricted network" in result.error
