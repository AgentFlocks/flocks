"""Business grouping is an attribute of a device, never its room or credentials."""

import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import ValidationError

from flocks.storage.storage import Storage
from flocks.tool.device import intake, store
from flocks.tool.device.models import (
    DEFAULT_GROUP_ID,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
)


@pytest_asyncio.fixture
async def devices(tmp_path, monkeypatch):
    Storage._initialized = False
    Storage._init_pid = None
    Storage._db_path = None
    await Storage.init(tmp_path / "native-devices.db")
    await store.ensure_default_group()
    sync = AsyncMock()
    monkeypatch.setattr(intake, "sync_service_tool_state", sync)
    monkeypatch.setattr(intake, "_forget_auto_instance_ignore", AsyncMock())
    monkeypatch.setattr(intake, "storage_key_to_service_id", lambda key: "example")
    monkeypatch.setattr(store, "storage_key_to_service_id", lambda key: "example")
    monkeypatch.setattr(intake, "persist_fields", lambda device_id, storage_key, fields, **kwargs: dict(fields))
    yield sync
    await Storage.shutdown()
    Storage._initialized = False
    Storage._init_pid = None
    Storage._db_path = None


async def make_device(name="Device", group=None):
    return await intake.create_device(DeviceIntegrationCreate(
        name=name, storage_key="example_v1", group=group,
        enabled=False, verify_ssl=True,
        fields={"base_url": "https://device.invalid", "api_key": "test-secret"},
    ))


@pytest.mark.asyncio
async def test_group_is_native_record_attribute_and_not_room(devices):
    device = await make_device(group="  日常巡检  ")
    assert device.group == "日常巡检"
    assert device.group_id == DEFAULT_GROUP_ID
    row = await store.fetch_device(device.id)
    assert row["plugin_group"] == "日常巡检"
    assert "group" not in json.loads(row["fields"])
    assert "plugin_group" not in json.loads(row["fields"])
    assert (await store.list_devices())[0].group == "日常巡检"


@pytest.mark.asyncio
async def test_group_only_update_does_not_rewrite_configuration_or_sync(devices):
    device = await make_device(group="Before")
    before = dict(await store.fetch_device(device.id))
    devices.reset_mock()
    updated = await intake.update_device(device.id, DeviceIntegrationUpdate(group="  After  "))
    after = dict(await store.fetch_device(device.id))
    assert updated.group == "After"
    assert devices.await_count == 0
    unchanged = set(before) - {"plugin_group", "updated_at"}
    assert {key: before[key] for key in unchanged} == {key: after[key] for key in unchanged}
    assert updated.group_id == device.group_id
    assert updated.enabled is False and updated.verify_ssl is True


@pytest.mark.asyncio
async def test_omission_preserves_and_explicit_null_or_empty_clears(devices):
    device = await make_device(group="Ops")
    updated = await intake.update_device(device.id, DeviceIntegrationUpdate(name="Renamed"))
    assert updated.name == "Renamed" and updated.group == "Ops"
    cleared = await intake.update_device(device.id, DeviceIntegrationUpdate(group=None))
    assert cleared.group == ""
    await intake.update_device(device.id, DeviceIntegrationUpdate(group="New"))
    cleared = await intake.update_device(device.id, DeviceIntegrationUpdate(group="   "))
    assert cleared.group == ""


@pytest.mark.asyncio
async def test_group_only_write_preserves_status_and_room_changes(devices):
    device = await make_device(group="Ops")
    room = await store.create_group("Another room", None, 1)
    await intake.update_device(device.id, DeviceIntegrationUpdate(group_id=room.id))
    await store.record_test_result(device.id, success=True, message="checked", latency_ms=12)
    devices.reset_mock()
    updated = await intake.update_device(device.id, DeviceIntegrationUpdate(group="Audit"))
    assert updated.group_id == room.id
    assert updated.status == "ok" and updated.message == "checked" and updated.latency_ms == 12
    assert updated.group == "Audit"
    assert devices.await_count == 0


@pytest.mark.asyncio
async def test_room_filter_preserves_business_group_metadata(devices):
    alpha = await make_device(name="Alpha", group="Ops")
    beta = await make_device(name="Beta", group="Other")
    ungrouped = await make_device(name="Ungrouped")
    room = await store.create_group("Second room", None, 1)
    await intake.update_device(beta.id, DeviceIntegrationUpdate(group_id=room.id))
    assert {device.id for device in await store.list_devices()} == {alpha.id, beta.id, ungrouped.id}
    listed = await store.list_devices(room.id)
    assert [device.id for device in listed] == [beta.id]
    assert listed[0].group == "Other"
    assert listed[0].group_readonly is False


@pytest.mark.asyncio
async def test_native_update_does_not_overwrite_a_newer_group_when_omitted(devices, monkeypatch):
    device = await make_device(group="Before")
    stale = await store.fetch_device(device.id)
    await store.update_device_metadata(device.id, group="Latest")
    original = intake.fetch_device
    first = True

    async def stale_then_current(device_id):
        nonlocal first
        if first:
            first = False
            return stale
        return await original(device_id)

    monkeypatch.setattr(intake, "fetch_device", stale_then_current)
    updated = await intake.update_device(device.id, DeviceIntegrationUpdate(name="New name"))
    assert updated.name == "New name" and updated.group == "Latest"


@pytest.mark.asyncio
async def test_group_independent_of_single_room_feature_flag(devices, monkeypatch):
    monkeypatch.setattr(intake, "MULTI_GROUP_ENABLED", False)
    device = await make_device(group="Ops")
    changed = await intake.update_device(device.id, DeviceIntegrationUpdate(group="Personal work"))
    assert changed.group == "Personal work"
    assert changed.group_id == DEFAULT_GROUP_ID


@pytest.mark.parametrize("value", [12, True, [], {}, "x" * 33, "a\x00b", "a\nb", "a\x7fb"])
def test_invalid_group_rejected_without_coercion(value):
    with pytest.raises(ValidationError):
        DeviceIntegrationUpdate(group=value)


def test_unicode_group_and_field_presence():
    assert DeviceIntegrationUpdate(group="  安全 🧑‍💻  ").group == "安全 🧑‍💻"
    assert "group" not in DeviceIntegrationUpdate(name="Name").model_fields_set
    assert "group" in DeviceIntegrationUpdate(group=None).model_fields_set
