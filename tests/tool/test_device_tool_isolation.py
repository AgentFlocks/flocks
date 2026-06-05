"""Tests for per-device tool enable/disable isolation (DB-backed).

Regression suite for the bug where two device instances sharing the same
``storage_key`` (same product version, different names) would have their
tool on/off state coupled — toggling a tool "for Device A" also affected
Device B.

Fix: store per-device tool overrides in the ``device_tool_settings`` SQLite
table (ON DELETE CASCADE cleans up automatically on device removal).  The
override is checked at ToolRegistry.execute() time, AFTER the shared global
tool_settings have been applied.  The in-memory ToolInfo.enabled remains a
global/shared concept; per-device gates live exclusively in the execution path.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest

from flocks.tool.registry import Tool, ToolCategory, ToolContext, ToolInfo, ToolRegistry, ToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_env(tmp_path: Path, monkeypatch):
    """Isolated SQLite DB for each test.

    Importing ``flocks.tool.device.models`` ensures all DDLs (including
    device_tool_settings) are registered before Storage.init() runs.
    """
    from flocks.config.config import Config
    from flocks.storage.storage import Storage
    import flocks.tool.device.models  # noqa: F401 — registers DDLs

    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))

    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False

    await Storage.init()
    yield data_dir


@pytest.fixture
def isolated_registry(monkeypatch):
    saved_tools = dict(ToolRegistry._tools)
    saved_defaults = dict(ToolRegistry._enabled_defaults)
    saved_plugin_names = list(ToolRegistry._plugin_tool_names)
    saved_dynamic = dict(ToolRegistry._dynamic_tools_by_module)
    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_enabled_defaults", {})
    monkeypatch.setattr(ToolRegistry, "_plugin_tool_names", [])
    monkeypatch.setattr(ToolRegistry, "_dynamic_tools_by_module", {})
    yield
    ToolRegistry._tools = saved_tools
    ToolRegistry._enabled_defaults = saved_defaults
    ToolRegistry._plugin_tool_names = saved_plugin_names
    ToolRegistry._dynamic_tools_by_module = saved_dynamic


def _device_tool(name: str, storage_key: str, *, enabled: bool = True) -> Tool:
    async def _handler(_ctx: ToolContext, **_kwargs) -> ToolResult:
        return ToolResult(success=True, output="ok")

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"stub device tool {name}",
            category=ToolCategory.CUSTOM,
            enabled=enabled,
            source="device",
            provider=storage_key,
        ),
        handler=_handler,
    )


async def _insert_stub_device(device_id: str, storage_key: str) -> None:
    """Insert a minimal device row so FK constraints on device_tool_settings pass."""
    from flocks.storage.storage import Storage

    now = 1_700_000_000_000
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("""
            INSERT OR IGNORE INTO device_groups (id, name, sort_order, created_at, updated_at)
            VALUES ('default-room', '默认机房', 0, ?, ?)
        """, (now, now))
        await db.execute("""
            INSERT OR IGNORE INTO device_integrations
                (id, group_id, name, storage_key, service_id, enabled,
                 verify_ssl, fields, status, created_at, updated_at)
            VALUES (?, 'default-room', ?, ?, ?, 1, 0, '{}', 'unknown', ?, ?)
        """, (device_id, f"dev-{device_id[:4]}", storage_key, storage_key, now, now))
        await db.commit()


# ---------------------------------------------------------------------------
# store.py: device_tool_settings CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStoreDeviceToolSettings:
    async def test_get_returns_none_when_absent(self, db_env):
        from flocks.tool.device.store import get_device_tool_enabled
        result = await get_device_tool_enabled("dev-a", "sangfor_af_login")
        assert result is None

    async def test_set_and_get_roundtrip(self, db_env):
        from flocks.tool.device.store import get_device_tool_enabled, set_device_tool_enabled
        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)
        result = await get_device_tool_enabled(dev_id, "sangfor_af_login")
        assert result is False

    async def test_set_does_not_affect_other_device(self, db_env):
        from flocks.tool.device.store import get_device_tool_enabled, set_device_tool_enabled
        dev_a = str(uuid.uuid4())
        dev_b = str(uuid.uuid4())
        await _insert_stub_device(dev_a, "sangfor_af_v8_0_106")
        await _insert_stub_device(dev_b, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_a, "sangfor_af_login", False)
        result_b = await get_device_tool_enabled(dev_b, "sangfor_af_login")
        assert result_b is None

    async def test_set_does_not_affect_other_tool(self, db_env):
        from flocks.tool.device.store import get_device_tool_enabled, set_device_tool_enabled
        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)
        other = await get_device_tool_enabled(dev_id, "sangfor_af_query")
        assert other is None

    async def test_delete_returns_true_when_entry_existed(self, db_env):
        from flocks.tool.device.store import (
            delete_device_tool_setting,
            get_device_tool_enabled,
            set_device_tool_enabled,
        )
        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)
        removed = await delete_device_tool_setting(dev_id, "sangfor_af_login")
        assert removed is True
        assert await get_device_tool_enabled(dev_id, "sangfor_af_login") is None

    async def test_delete_returns_false_when_absent(self, db_env):
        from flocks.tool.device.store import delete_device_tool_setting
        removed = await delete_device_tool_setting("non-existent", "some_tool")
        assert removed is False

    async def test_list_returns_all_settings_for_device(self, db_env):
        from flocks.tool.device.store import list_device_tool_settings, set_device_tool_enabled
        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "tool_x", False)
        await set_device_tool_enabled(dev_id, "tool_y", False)
        settings = await list_device_tool_settings(dev_id)
        assert set(settings.keys()) == {"tool_x", "tool_y"}
        assert settings["tool_x"] is False
        assert settings["tool_y"] is False

    async def test_delete_does_not_affect_other_device(self, db_env):
        from flocks.tool.device.store import (
            delete_device_tool_setting,
            get_device_tool_enabled,
            set_device_tool_enabled,
        )
        dev_a = str(uuid.uuid4())
        dev_b = str(uuid.uuid4())
        await _insert_stub_device(dev_a, "sangfor_af_v8_0_106")
        await _insert_stub_device(dev_b, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_a, "tool_x", False)
        await set_device_tool_enabled(dev_b, "tool_x", False)
        await delete_device_tool_setting(dev_a, "tool_x")
        assert await get_device_tool_enabled(dev_b, "tool_x") is False

    async def test_cascade_delete_on_device_removal(self, db_env):
        """Removing the parent device row must cascade to device_tool_settings."""
        from flocks.storage.storage import Storage
        from flocks.tool.device.store import get_device_tool_enabled, set_device_tool_enabled

        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)

        async with Storage.connect(Storage.get_db_path()) as db:
            await db.execute(
                "DELETE FROM device_integrations WHERE id = ?", (dev_id,)
            )
            await db.commit()

        result = await get_device_tool_enabled(dev_id, "sangfor_af_login")
        assert result is None

    async def test_global_tool_settings_unaffected(self, db_env):
        """device_tool_settings must not touch flocks.json tool_settings."""
        from flocks.config.config_writer import ConfigWriter
        from flocks.tool.device.store import set_device_tool_enabled

        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)

        global_setting = ConfigWriter.get_tool_setting("sangfor_af_login")
        assert global_setting is None

    async def test_set_bumps_device_revision(self, db_env):
        """Cache-invalidation contract: setting a per-device override must bump
        the device_revision so the session runner rebuilds the DeviceAssetContext
        section in the system prompt.
        """
        from flocks.tool.device.store import device_revision, set_device_tool_enabled

        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")
        before = device_revision()
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)
        assert device_revision() > before

    async def test_delete_bumps_device_revision_only_on_real_removal(self, db_env):
        """Deleting a non-existent override must NOT bump the revision."""
        from flocks.tool.device.store import (
            delete_device_tool_setting,
            device_revision,
            set_device_tool_enabled,
        )

        dev_id = str(uuid.uuid4())
        await _insert_stub_device(dev_id, "sangfor_af_v8_0_106")

        # No-op delete: revision unchanged.
        rev_a = device_revision()
        await delete_device_tool_setting(dev_id, "missing_tool")
        assert device_revision() == rev_a

        # Real delete after a set: revision bumps.
        await set_device_tool_enabled(dev_id, "sangfor_af_login", False)
        rev_b = device_revision()
        removed = await delete_device_tool_setting(dev_id, "sangfor_af_login")
        assert removed is True
        assert device_revision() > rev_b

    async def test_list_all_returns_grouped_by_device(self, db_env):
        """Batch helper used by the system-prompt builder to avoid N+1."""
        from flocks.tool.device.store import (
            list_all_device_tool_settings,
            set_device_tool_enabled,
        )

        dev_a = str(uuid.uuid4())
        dev_b = str(uuid.uuid4())
        await _insert_stub_device(dev_a, "sangfor_af_v8_0_106")
        await _insert_stub_device(dev_b, "sangfor_af_v8_0_106")
        await set_device_tool_enabled(dev_a, "tool_x", False)
        await set_device_tool_enabled(dev_a, "tool_y", True)
        await set_device_tool_enabled(dev_b, "tool_x", False)

        all_settings = await list_all_device_tool_settings()
        assert all_settings[dev_a] == {"tool_x": False, "tool_y": True}
        assert all_settings[dev_b] == {"tool_x": False}

    async def test_list_all_returns_empty_dict_when_no_overrides(self, db_env):
        from flocks.tool.device.store import list_all_device_tool_settings

        result = await list_all_device_tool_settings()
        assert result == {}


# ---------------------------------------------------------------------------
# ToolRegistry.execute: per-device gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeviceToolIsolationExecution:
    async def _run_tool(
        self,
        monkeypatch,
        db_env,
        *,
        storage_key: str,
        device_id: str,
        tool_name: str = "sangfor_af_login",
        enabled_in_registry: bool = True,
        per_device_enabled: Optional[bool] = None,
    ) -> ToolResult:
        """Helper: stub the registry + device store, then call execute()."""
        tool = _device_tool(tool_name, storage_key, enabled=enabled_in_registry)
        monkeypatch.setattr(
            "flocks.tool.registry.ToolRegistry.get", lambda _name: tool
        )

        monkeypatch.setattr(
            "flocks.tool.device.store.list_devices",
            AsyncMock(return_value=[
                SimpleNamespace(id=device_id, storage_key=storage_key, enabled=True),
            ]),
        )

        @asynccontextmanager
        async def _activate(did: str):
            yield True

        monkeypatch.setattr(
            "flocks.tool.credential_context.activate_device_credentials", _activate
        )

        # Apply per-device DB setting.
        from flocks.tool.device.store import (
            delete_device_tool_setting,
            set_device_tool_enabled,
        )
        await _insert_stub_device(device_id, storage_key)
        if per_device_enabled is False:
            await set_device_tool_enabled(device_id, tool_name, False)
        elif per_device_enabled is True:
            await delete_device_tool_setting(device_id, tool_name)

        return await ToolRegistry.execute(tool_name, device_id=device_id)

    async def test_tool_executes_when_no_per_device_override(
        self, monkeypatch, db_env
    ):
        result = await self._run_tool(
            monkeypatch, db_env,
            storage_key="sangfor_af_v8_0_106",
            device_id=str(uuid.uuid4()),
            per_device_enabled=None,
        )
        assert result.success is True

    async def test_tool_blocked_by_per_device_disable(
        self, monkeypatch, db_env
    ):
        result = await self._run_tool(
            monkeypatch, db_env,
            storage_key="sangfor_af_v8_0_106",
            device_id=str(uuid.uuid4()),
            per_device_enabled=False,
        )
        assert result.success is False
        assert "已禁用" in (result.error or "")

    async def test_per_device_disable_does_not_affect_other_device(
        self, monkeypatch, db_env
    ):
        """Core regression: disabling tool for dev-a must NOT affect dev-b."""
        from flocks.tool.device.store import set_device_tool_enabled

        dev_a = str(uuid.uuid4())
        dev_b = str(uuid.uuid4())
        storage_key = "sangfor_af_v8_0_106"

        await _insert_stub_device(dev_a, storage_key)
        await set_device_tool_enabled(dev_a, "sangfor_af_login", False)

        result = await self._run_tool(
            monkeypatch, db_env,
            storage_key=storage_key,
            device_id=dev_b,
            per_device_enabled=None,
        )
        assert result.success is True, (
            "Disabling a tool for dev-a must not affect dev-b even if they "
            "share the same storage_key (same plugin version)."
        )

    async def test_global_disable_still_blocks_all_devices(
        self, monkeypatch, db_env, isolated_registry
    ):
        """Global tool_settings (enabled=False in registry) must still block ALL devices."""
        tool = _device_tool("sangfor_af_login", "sangfor_af_v8_0_106", enabled=False)
        monkeypatch.setattr(
            "flocks.tool.registry.ToolRegistry.get", lambda _name: tool
        )

        result = await ToolRegistry.execute("sangfor_af_login", device_id=str(uuid.uuid4()))
        assert result.success is False
        assert "disabled" in (result.error or "").lower()
