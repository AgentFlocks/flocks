"""Tests for the built-in device_manage tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.device.intake import DeviceNotFoundError
from flocks.tool.device.manage_tool import device_manage
from flocks.tool.device.models import DeviceTestResult
from flocks.tool.registry import ToolContext, ToolRegistry


def make_ctx() -> ToolContext:
    return ToolContext(
        session_id="session-device-test",
        message_id="message-device-test",
        agent="rex",
    )


def test_device_manage_is_registered():
    tools = {tool.name for tool in ToolRegistry.list_tools()}
    assert "device_manage" in tools


@pytest.mark.asyncio
async def test_device_manage_list_returns_device_inventory():
    with patch(
        "flocks.tool.device.prompt.build_device_manage_list_section",
        AsyncMock(return_value="### 已接入设备\n- device_id: `dev-1`"),
    ):
        result = await device_manage(make_ctx(), action="list")

    assert result.success is True
    assert "dev-1" in result.output


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_writes_status_via_existing_test_path():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=True,
                message="HTTP 200，延迟 12ms",
                latency_ms=12,
            )
        ),
    ) as mocked_test:
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

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
async def test_device_manage_keeps_device_id_when_executed_through_registry():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=True,
                message="HTTP 200，延迟 12ms",
                latency_ms=12,
            )
        ),
    ) as mocked_test:
        result = await ToolRegistry.execute(
            "device_manage",
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

    mocked_test.assert_awaited_once_with("dev-1")
    assert result.success is True
    assert result.metadata["device_id"] == "dev-1"


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_returns_successful_tool_result_for_failed_probe():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=False,
                message="无法连接到 https://device.local",
                latency_ms=10000,
            )
        ),
    ):
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

    assert result.success is True
    assert result.output["connected"] is False
    assert result.output["status"] == "error"


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_reports_missing_device_as_tool_error():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(side_effect=DeviceNotFoundError("missing")),
    ):
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="missing-id",
        )

    assert result.success is False
    assert "未找到" in (result.error or "")
