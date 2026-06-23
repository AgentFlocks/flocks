from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.tool.device.prompt import build_device_context_section
from flocks.tool.registry import ParameterType, ToolCategory, ToolInfo, ToolParameter

def _stub_groups(monkeypatch: pytest.MonkeyPatch, groups):
    monkeypatch.setattr(
        "flocks.tool.device.prompt.list_groups", AsyncMock(return_value=groups)
    )


def _stub_devices(monkeypatch: pytest.MonkeyPatch, devices):
    monkeypatch.setattr(
        "flocks.tool.device.prompt.list_devices", AsyncMock(return_value=devices)
    )


def _stub_per_device(monkeypatch: pytest.MonkeyPatch, mapping):
    monkeypatch.setattr(
        "flocks.tool.device.prompt.list_all_device_tool_settings",
        AsyncMock(return_value=mapping),
    )


def _stub_tools(monkeypatch: pytest.MonkeyPatch, tools):
    monkeypatch.setattr(
        "flocks.tool.registry.ToolRegistry.list_tools", lambda: tools
    )


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
    assert "设备不存在时，请提醒用户前往设备接入页面添加设备。" in content
    assert "工具名和描述:" in content
    assert "工具能力:" not in content
    assert "action 可选:" not in content
    assert "以下是当前机房中已接入的安全设备及其工具集映射。" not in content
    assert content.index("### 设备列表") < content.index("### 工具集")
    assert content.count("`tool_set_id=tdp_v3_3_10`") == 1
    assert content.count("tool_set_id: `tdp_v3_3_10`") == 2
    assert "`tdp_event_list`" in content
    assert "`tdp_alert_list`" in content
    assert "**TDP-A**" in content
    assert "**TDP-B**" in content


@pytest.mark.asyncio
async def test_device_context_shows_per_device_disabled_tools_only_for_their_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-device disabled tools must be annotated only under the device that
    has the override — not under sibling devices sharing the same storage_key.
    """
    _stub_groups(monkeypatch, [SimpleNamespace(id="room-1", name="上海机房")])
    _stub_devices(monkeypatch, [
        SimpleNamespace(
            id="dev-a", group_id="room-1", name="TDP-A",
            storage_key="tdp_v3_3_10", enabled=True,
        ),
        SimpleNamespace(
            id="dev-b", group_id="room-1", name="TDP-B",
            storage_key="tdp_v3_3_10", enabled=True,
        ),
    ])
    # Per-device override: TDP-A has tdp_alert_list disabled; TDP-B has no overrides.
    _stub_per_device(monkeypatch, {
        "dev-a": {"tdp_alert_list": False},
    })
    _stub_tools(monkeypatch, [
        ToolInfo(
            name="tdp_event_list", description="List TDP events.",
            category=ToolCategory.CUSTOM, parameters=[],
            enabled=True, source="device", provider="tdp_v3_3_10",
        ),
        ToolInfo(
            name="tdp_alert_list", description="List TDP alerts.",
            category=ToolCategory.CUSTOM, parameters=[],
            enabled=True, source="device", provider="tdp_v3_3_10",
        ),
    ])

    content = await build_device_context_section()
    assert content is not None

    # Locate the per-device blocks by anchoring on the device name line.
    a_block_start = content.index("**TDP-A**")
    b_block_start = content.index("**TDP-B**")
    a_block = content[a_block_start:b_block_start]
    b_block = content[b_block_start:]

    # TDP-A block must mention the disabled tool with its OWN device name & id.
    assert "已单独禁用" in a_block
    assert "`tdp_alert_list`" in a_block
    assert "TDP-A" in a_block
    assert "dev-a" in a_block

    # TDP-B block must NOT mention any per-device disable.
    assert "已单独禁用" not in b_block

    # Defence-in-depth: TDP-A's notice must not leak the wrong device name.
    assert "TDP-B" not in a_block.split("已单独禁用", 1)[1].split("\n", 1)[0]


@pytest.mark.asyncio
async def test_device_context_omits_notice_when_no_per_device_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No notice line should appear when a device has no per-device overrides."""
    _stub_groups(monkeypatch, [SimpleNamespace(id="room-1", name="上海机房")])
    _stub_devices(monkeypatch, [
        SimpleNamespace(
            id="dev-a", group_id="room-1", name="TDP-A",
            storage_key="tdp_v3_3_10", enabled=True,
        ),
    ])
    _stub_per_device(monkeypatch, {})
    _stub_tools(monkeypatch, [
        ToolInfo(
            name="tdp_event_list", description="List TDP events.",
            category=ToolCategory.CUSTOM, parameters=[],
            enabled=True, source="device", provider="tdp_v3_3_10",
        ),
    ])

    content = await build_device_context_section()
    assert content is not None
    assert "已单独禁用" not in content
