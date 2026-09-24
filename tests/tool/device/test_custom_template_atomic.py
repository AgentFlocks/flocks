"""Regression: invalid tool names in a custom device template must not leave a half-written plugin dir."""

from __future__ import annotations

import pytest

from flocks.tool.device import plugin_index
from flocks.tool.device.models import CustomDeviceTemplateCreate, CustomDeviceToolCreate


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".flocks").mkdir()
    return tmp_path


def _body(tool_names: list[str]) -> CustomDeviceTemplateCreate:
    return CustomDeviceTemplateCreate(
        plugin_id="atomic-dev",
        name="Atomic",
        service_id="atomic_dev",
        tools=[CustomDeviceToolCreate(name=n, description="d", handler={"type": "http", "url": "https://x"}) for n in tool_names],
    )


def test_invalid_tool_name_writes_nothing(isolated_home):
    target = plugin_index.hub_local.install_dir("device", "atomic-dev", "global")
    with pytest.raises(ValueError, match="must be a valid identifier"):
        plugin_index.create_custom_device_template(_body(["ok_tool", "bad name"]))
    assert not target.exists(), "provider yaml was written before tool validation"


def test_duplicate_tool_name_writes_nothing(isolated_home):
    target = plugin_index.hub_local.install_dir("device", "atomic-dev", "global")
    with pytest.raises(ValueError, match="duplicate tool name"):
        plugin_index.create_custom_device_template(_body(["ping", "ping"]))
    assert not target.exists()
