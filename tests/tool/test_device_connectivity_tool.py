"""Tests for the built-in device_connectivity_test tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.tool.device.connectivity_tool import device_connectivity_test
from flocks.tool.device.intake import DeviceNotFoundError
from flocks.tool.device.models import DeviceTestResult
from flocks.tool.registry import ToolContext, ToolRegistry


def make_ctx() -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.session_id = "session-device-test"
    return ctx


def test_device_connectivity_test_is_registered():
    tools = {tool.name for tool in ToolRegistry.list_tools()}
    assert "device_connectivity_test" in tools


@pytest.mark.asyncio
async def test_device_connectivity_test_writes_status_via_existing_test_path():
    with patch(
        "flocks.tool.device.connectivity_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=True,
                message="HTTP 200，延迟 12ms",
                latency_ms=12,
            )
        ),
    ) as mocked_test:
        result = await device_connectivity_test(make_ctx(), device_id="dev-1")

    mocked_test.assert_awaited_once_with("dev-1")
    assert result.success is True
    assert result.output == {
        "device_id": "dev-1",
        "connected": True,
        "status": "ok",
        "message": "HTTP 200，延迟 12ms",
        "latency_ms": 12,
    }
    assert result.metadata["card_status_updated"] is True


@pytest.mark.asyncio
async def test_device_connectivity_test_returns_successful_tool_result_for_failed_probe():
    with patch(
        "flocks.tool.device.connectivity_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=False,
                message="无法连接到 https://device.local",
                latency_ms=10000,
            )
        ),
    ):
        result = await device_connectivity_test(make_ctx(), device_id="dev-1")

    assert result.success is True
    assert result.output["connected"] is False
    assert result.output["status"] == "error"


@pytest.mark.asyncio
async def test_device_connectivity_test_reports_missing_device_as_tool_error():
    with patch(
        "flocks.tool.device.connectivity_tool.test_device",
        AsyncMock(side_effect=DeviceNotFoundError("missing")),
    ):
        result = await device_connectivity_test(make_ctx(), device_id="missing-id")

    assert result.success is False
    assert "未找到" in (result.error or "")
