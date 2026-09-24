"""Regression tests for ``flocks.tool.device.startup._sync_all``.

The startup sync must NOT touch pure-API integrations
(``integration_type: api``).  Doing so was the root cause of a
production incident where every restart silently flipped
``api_services[<tdp_api>].enabled`` to ``False``: those services have
no rows in ``device_integrations``, so the device-centric sync judged
them as "0 enabled devices" and disabled them.

These tests pin the behavioural contract:
  * ``_device_type_storage_keys()`` returns only device-type keys,
    regardless of how many api-type plugins are also installed.
  * ``_sync_all()`` only calls ``sync_service_tool_state`` for
    storage_keys whose plugin declares ``integration_type: device``.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
import yaml

from flocks.tool.device import startup


@pytest.mark.asyncio
async def test_heal_stale_service_ids_reads_named_postgres_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "devices.db"
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path / "data"))
    from flocks.tool.device.store import ensure_default_group, insert_device

    await startup.Storage.init(db_path)
    await ensure_default_group()
    await insert_device(
        device_id="dev-1", group_id="default-room", name="retained device",
        storage_key="onesec_api_v2_8_2", service_id="stale_service_id",
        enabled=True, verify_ssl=False, db_fields={},
    )

    monkeypatch.setattr(
        "flocks.tool.device.store.storage_key_to_service_id",
        lambda storage_key: "onesec_api" if storage_key == "onesec_api_v2_8_2" else storage_key,
    )

    await startup._heal_stale_service_ids()

    async with startup.Storage.transaction() as db:
        row = await (await db.execute(
            "SELECT service_id FROM device_integrations WHERE id = ?", ("dev-1",)
        )).fetchone()
    assert row["service_id"] == "onesec_api"
    assert not db_path.exists()


@pytest.fixture
def isolated_plugins(monkeypatch, tmp_path):
    """Point plugin discovery at an empty tmp HOME so production plugins
    on disk don't bleed into the test."""
    from flocks.config import api_versioning as versioning

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    versioning._reset_descriptor_cache()
    yield home
    versioning._reset_descriptor_cache()


def _drop_plugin(
    home: Path,
    *,
    integration_type: str,
    plugin_id: str,
    service_id: str,
    version: str = "1.0.0",
) -> str:
    """Write a minimal ``_provider.yaml`` for either api/ or device/ and
    return the resulting storage_key."""
    subdir = "device" if integration_type == "device" else "api"
    plugin_dir = home / ".flocks" / "plugins" / "tools" / subdir / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "_provider.yaml").write_text(
        yaml.safe_dump(
            {
                "name": plugin_id,
                "service_id": service_id,
                "version": version,
                "integration_type": integration_type,
                "credential_fields": [{"key": "base_url", "label": "Base URL"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    from flocks.config.api_versioning import derive_storage_key

    return derive_storage_key(service_id, version)


# ---------------------------------------------------------------------------
# _device_type_storage_keys
# ---------------------------------------------------------------------------

class TestDeviceTypeStorageKeys:
    def test_empty_when_no_plugins(self, isolated_plugins):
        assert startup._device_type_storage_keys() == set()

    def test_includes_device_excludes_api(self, isolated_plugins):
        device_sk = _drop_plugin(
            isolated_plugins,
            integration_type="device",
            plugin_id="sangfor_sip_v92",
            service_id="sangfor_sip",
            version="9.2",
        )
        api_sk = _drop_plugin(
            isolated_plugins,
            integration_type="api",
            plugin_id="tdp_api_v3_3_10",
            service_id="tdp_api",
            version="3.3.10",
        )
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        keys = startup._device_type_storage_keys()
        assert device_sk in keys
        assert api_sk not in keys, (
            "pure-API plugins must be excluded so the device sync loop "
            "never touches their api_services entry"
        )

    def test_handles_unreadable_yaml(self, isolated_plugins, monkeypatch):
        """A broken YAML must not crash the whole sweep."""
        ok_sk = _drop_plugin(
            isolated_plugins,
            integration_type="device",
            plugin_id="ok_device",
            service_id="ok_device",
            version="1.0.0",
        )
        broken_dir = (
            isolated_plugins
            / ".flocks" / "plugins" / "tools" / "device" / "broken_plugin"
        )
        broken_dir.mkdir(parents=True)
        (broken_dir / "_provider.yaml").write_text(":\nthis is not yaml\n: : :", encoding="utf-8")
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        keys = startup._device_type_storage_keys()
        assert ok_sk in keys

    def test_treats_unknown_type_as_non_device(self, isolated_plugins):
        """Anything other than the literal ``"device"`` is excluded."""
        _drop_plugin(
            isolated_plugins,
            integration_type="proxy",  # not a recognised type
            plugin_id="weird_plugin",
            service_id="weird_plugin",
            version="1.0.0",
        )
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        assert startup._device_type_storage_keys() == set()


# ---------------------------------------------------------------------------
# _sync_all integration check via stubbing
# ---------------------------------------------------------------------------

class TestSyncAllScope:
    @pytest.mark.asyncio
    async def test_skips_pure_api_services_with_no_db_rows(
        self, isolated_plugins, monkeypatch
    ):
        """End-to-end style: ``_sync_all`` must NOT invoke
        ``sync_service_tool_state`` for storage_keys whose plugin is
        pure-API.

        A real empty PostgreSQL table drives the database branch. The config
        and final tool synchronization are stubbed to isolate their effects.
        """
        device_sk = _drop_plugin(
            isolated_plugins,
            integration_type="device",
            plugin_id="sangfor_sip_v92",
            service_id="sangfor_sip",
            version="9.2",
        )
        api_sk = _drop_plugin(
            isolated_plugins,
            integration_type="api",
            plugin_id="tdp_api_v3_3_10",
            service_id="tdp_api",
            version="3.3.10",
        )
        from flocks.config.api_versioning import (
            discover_api_service_descriptors,
        )

        discover_api_service_descriptors(refresh=True)

        # Both keys exist in the api_services config, but only the device
        # one should make it into the sync loop.
        monkeypatch.setattr(
            "flocks.config.config_writer.ConfigWriter.list_api_services_raw",
            staticmethod(lambda: {device_sk: {"enabled": True}, api_sk: {"enabled": True}}),
        )

        # No DB rows exist (the API service never had a device row).
        await startup.Storage.init(isolated_plugins / "devices.db")

        called_with: List[str] = []

        async def _fake_sync(sid, deleted_storage_keys=None):
            called_with.append(sid)

        monkeypatch.setattr(
            "flocks.tool.device.startup.sync_service_tool_state",
            _fake_sync,
        )

        await startup._sync_all()

        # sangfor_sip (device-type) must be synced; tdp_api must NOT be.
        assert "sangfor_sip" in called_with
        assert "tdp_api" not in called_with, (
            "pure-API service tdp_api was synced by the device subsystem — "
            "this would silently disable its tools on every restart"
        )
