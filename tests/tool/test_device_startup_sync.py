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

        We stub ``sync_service_tool_state`` to record invocations and
        also stub ``Storage.connect`` + ``ConfigWriter.list_api_services_raw``
        so we don't need a real DB or config file to drive the test.
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

        # No DB rows exist (simulating: user deleted the last device of the
        # device-type service, and the api-type service never had any rows).
        class _FakeCursor:
            def __init__(self, rows):
                self._rows = rows

            async def fetchall(self):
                return self._rows

        class _FakeDB:
            async def execute(self, *_args, **_kwargs):
                return _FakeCursor([])

        class _CtxMgr:
            async def __aenter__(self):
                return _FakeDB()

            async def __aexit__(self, *_args):
                return False

        monkeypatch.setattr(
            "flocks.tool.device.startup.Storage.connect",
            staticmethod(lambda *_a, **_kw: _CtxMgr()),
        )
        monkeypatch.setattr(
            "flocks.tool.device.startup.Storage.get_db_path",
            staticmethod(lambda: ":memory:"),
        )

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
