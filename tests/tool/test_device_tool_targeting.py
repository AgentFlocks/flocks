from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import Tool, ToolCategory, ToolContext, ToolInfo, ToolRegistry, ToolResult


def _device_tool(storage_key: str) -> Tool:
    async def _handler(_ctx: ToolContext, **_kwargs) -> ToolResult:
        return ToolResult(success=True, output="ok")

    return Tool(
        info=ToolInfo(
            name="tdp_event_list",
            description="List device events",
            category=ToolCategory.CUSTOM,
            enabled=True,
            source="device",
            provider=storage_key,
        ),
        handler=_handler,
    )


@pytest.mark.asyncio
async def test_device_tool_requires_device_id_when_multiple_enabled_devices_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.get", lambda _name: _device_tool("tdp_v3_3_10"))
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(id="dev-1", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(id="dev-2", storage_key="tdp_v3_3_10", enabled=True),
        ]),
    )

    result = await ToolRegistry.execute("tdp_event_list")

    assert result.success is False
    assert "必须显式传入 `device_id`" in (result.error or "")


@pytest.mark.asyncio
async def test_device_tool_rejects_device_id_from_other_tool_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.get", lambda _name: _device_tool("tdp_v3_3_10"))
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(id="dev-other", storage_key="skyeye_v1", enabled=True),
        ]),
    )

    result = await ToolRegistry.execute("tdp_event_list", device_id="dev-other")

    assert result.success is False
    assert "不属于当前工具对应的设备类型" in (result.error or "")


@pytest.mark.asyncio
async def test_device_tool_auto_resolves_single_enabled_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activated_ids = []

    monkeypatch.setattr("flocks.tool.registry.ToolRegistry.get", lambda _name: _device_tool("tdp_v3_3_10"))
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(id="dev-1", storage_key="tdp_v3_3_10", enabled=True),
        ]),
    )

    @asynccontextmanager
    async def _activate(device_id: str):
        activated_ids.append(device_id)
        yield True

    monkeypatch.setattr("flocks.tool.credential_context.activate_device_credentials", _activate)

    result = await ToolRegistry.execute("tdp_event_list")

    assert result.success is True
    assert activated_ids == ["dev-1"]
