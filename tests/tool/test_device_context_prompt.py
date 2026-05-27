from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool.device.prompt import build_device_context_section
from flocks.tool.registry import ParameterType, ToolCategory, ToolInfo, ToolParameter


@pytest.mark.asyncio
async def test_device_context_deduplicates_tool_sets_and_references_them_from_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.tool.device.prompt.list_groups",
        AsyncMock(return_value=[SimpleNamespace(id="room-1", name="上海机房")]),
    )
    monkeypatch.setattr(
        "flocks.tool.device.prompt.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(
                id="dev-1",
                group_id="room-1",
                name="TDP-A",
                storage_key="tdp_v3_3_10",
                enabled=True,
            ),
            SimpleNamespace(
                id="dev-2",
                group_id="room-1",
                name="TDP-B",
                storage_key="tdp_v3_3_10",
                enabled=True,
            ),
        ]),
    )
    monkeypatch.setattr(
        "flocks.tool.registry.ToolRegistry.list_tools",
        lambda: [
            ToolInfo(
                name="tdp_event_list",
                description="List TDP events.",
                category=ToolCategory.CUSTOM,
                parameters=[
                    ToolParameter(
                        name="action",
                        type=ParameterType.STRING,
                        required=False,
                        enum=["list"],
                    )
                ],
                enabled=True,
                source="device",
                provider="tdp_v3_3_10",
                vendor="threatbook",
            ),
            ToolInfo(
                name="tdp_alert_list",
                description="List TDP alerts.",
                category=ToolCategory.CUSTOM,
                parameters=[],
                enabled=True,
                source="device",
                provider="tdp_v3_3_10",
                vendor="threatbook",
            ),
        ],
    )

    content = await build_device_context_section()

    assert content is not None
    assert content.count("`tool_set_id=tdp_v3_3_10`") == 1
    assert content.count("tool_set_id: `tdp_v3_3_10`") == 2
    assert "`tdp_event_list`" in content
    assert "`tdp_alert_list`" in content
    assert "**TDP-A**" in content
    assert "**TDP-B**" in content
