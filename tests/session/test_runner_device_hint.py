from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.session.runner import SessionRunner
from flocks.tool.registry import ToolCategory, ToolInfo


@pytest.mark.asyncio
async def test_device_asset_hint_stays_short_and_strategy_only() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(return_value=[
            SimpleNamespace(name="上海-TDP", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="北京-NGSOC", storage_key="ngsoc_v4", enabled=True),
            SimpleNamespace(name="深圳-TDP-1", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-2", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-3", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-4", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-5", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-6", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="深圳-TDP-7", storage_key="tdp_v3_3_10", enabled=True),
            SimpleNamespace(name="已禁用-SIP", storage_key="sip_v9", enabled=False),
        ]),
    )
    monkeypatch.setattr(
        "flocks.session.runner.ToolRegistry.list_tools",
        lambda: [
            ToolInfo(
                name="tdp_event_list",
                description="List TDP events",
                category=ToolCategory.CUSTOM,
                enabled=True,
                source="device",
                provider="tdp_v3_3_10",
                vendor="threatbook",
            ),
            ToolInfo(
                name="ngsoc_event_list",
                description="List NGSOC events",
                category=ToolCategory.CUSTOM,
                enabled=True,
                source="device",
                provider="ngsoc_v4",
                vendor="qianxin",
            ),
        ],
    )

    runner = SessionRunner.__new__(SessionRunner)
    hint = await SessionRunner._build_device_asset_hint(runner)
    monkeypatch.undo()

    assert hint is not None
    assert "上海-TDP" in hint
    assert "北京-NGSOC" in hint
    assert "深圳-TDP-7" in hint
    assert "已禁用-SIP" not in hint
    assert "已省略" not in hint
    assert "threatbook" in hint
    assert "qianxin" in hint
    assert "`device_manage(action='list')`" in hint
    assert "`tool_search`" in hint
    assert "`device_id`" in hint
    assert "机房:" not in hint
    assert "可用工具:" not in hint
