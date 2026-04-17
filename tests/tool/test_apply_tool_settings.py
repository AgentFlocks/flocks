"""Tests for ToolRegistry._apply_tool_settings — user-level enable/disable overlay."""

from __future__ import annotations

import json

import pytest

from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolRegistry,
    ToolResult,
)


def _stub_tool(name: str, *, enabled: bool, native: bool = True) -> Tool:
    async def handler(ctx: ToolContext, value: str = "ok") -> ToolResult:
        return ToolResult(success=True, output=value)

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"stub tool {name}",
            category=ToolCategory.CUSTOM,
            enabled=enabled,
            native=native,
        ),
        handler=handler,
    )


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Isolated FLOCKS_CONFIG_DIR with an empty flocks.json."""
    from flocks.config.config import Config

    config_dir = tmp_path / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    Config._global_config = None
    Config._cached_config = None
    (config_dir / "flocks.json").write_text(json.dumps({}))
    return config_dir


@pytest.fixture
def isolated_registry(monkeypatch):
    """Replace the registry's tool dict + defaults snapshot with a known set."""
    saved_tools = dict(ToolRegistry._tools)
    saved_defaults = dict(ToolRegistry._enabled_defaults)
    saved_plugin_names = list(ToolRegistry._plugin_tool_names)
    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_enabled_defaults", {})
    monkeypatch.setattr(ToolRegistry, "_plugin_tool_names", [])
    yield
    ToolRegistry._tools = saved_tools
    ToolRegistry._enabled_defaults = saved_defaults
    ToolRegistry._plugin_tool_names = saved_plugin_names


def _set_api_service(name: str, *, enabled: bool) -> None:
    """Helper to write a minimal api_services entry."""
    from flocks.config.config_writer import ConfigWriter
    ConfigWriter.set_api_service(name, {
        "apiKey": "{secret:test_key}",
        "enabled": enabled,
    })


def test_apply_tool_settings_enables_disabled_tool(temp_config, isolated_registry):
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_tool("plugin_thing", enabled=False)
    ToolRegistry._tools[tool.info.name] = tool

    ConfigWriter.set_tool_setting("plugin_thing", {"enabled": True})
    ToolRegistry._apply_tool_settings()

    assert tool.info.enabled is True


def test_apply_tool_settings_disables_enabled_tool(temp_config, isolated_registry):
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry._tools[tool.info.name] = tool

    ConfigWriter.set_tool_setting("plugin_thing", {"enabled": False})
    ToolRegistry._apply_tool_settings()

    assert tool.info.enabled is False


def test_apply_tool_settings_skips_unknown_tool(temp_config, isolated_registry, caplog):
    """Stale entries for tools that no longer exist must not crash."""
    from flocks.config.config_writer import ConfigWriter

    ConfigWriter.set_tool_setting("ghost_tool", {"enabled": False})
    ToolRegistry._apply_tool_settings()

    assert "ghost_tool" not in ToolRegistry._tools


def test_apply_tool_settings_no_op_when_no_settings(temp_config, isolated_registry):
    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry._tools[tool.info.name] = tool

    ToolRegistry._apply_tool_settings()

    assert tool.info.enabled is True


def test_apply_tool_settings_works_for_user_level_tools(temp_config, isolated_registry):
    """Overlay should apply uniformly — including to non-native (user-level) plugin tools."""
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_tool("user_thing", enabled=True, native=False)
    ToolRegistry._tools[tool.info.name] = tool

    ConfigWriter.set_tool_setting("user_thing", {"enabled": False})
    ToolRegistry._apply_tool_settings()

    assert tool.info.enabled is False


def test_apply_tool_settings_ignores_non_enabled_keys(temp_config, isolated_registry):
    """Overlay entries without an `enabled` key must not change tool state."""
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry._tools[tool.info.name] = tool

    ConfigWriter.set_tool_setting("plugin_thing", {"note": "future field"})
    ToolRegistry._apply_tool_settings()

    assert tool.info.enabled is True


# ---------------------------------------------------------------------------
# Service-gate interaction: overlay can never re-open a service-disabled tool
# ---------------------------------------------------------------------------

def _stub_api_tool(name: str, *, enabled: bool, provider: str) -> Tool:
    async def handler(ctx: ToolContext, value: str = "ok") -> ToolResult:
        return ToolResult(success=True, output=value)

    return Tool(
        info=ToolInfo(
            name=name,
            description=f"stub api tool {name}",
            category=ToolCategory.CUSTOM,
            enabled=enabled,
            provider=provider,
        ),
        handler=handler,
    )


def test_overlay_cannot_enable_when_service_disabled(temp_config, isolated_registry):
    """The most dangerous regression: overlay enabled=True must NOT leak past _sync."""
    from flocks.config.config_writer import ConfigWriter

    _set_api_service("onesec_api", enabled=False)
    tool = _stub_api_tool("onesec_dns", enabled=True, provider="onesec_api")
    ToolRegistry._tools[tool.info.name] = tool
    ToolRegistry._snapshot_enabled_defaults()

    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is False

    ConfigWriter.set_tool_setting("onesec_dns", {"enabled": True})
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is False, (
        "overlay must not be able to open a tool whose API service is disabled"
    )


def test_overlay_can_disable_even_when_service_enabled(temp_config, isolated_registry):
    """The disable side of the gate has no constraint."""
    from flocks.config.config_writer import ConfigWriter

    _set_api_service("onesec_api", enabled=True)
    tool = _stub_api_tool("onesec_dns", enabled=True, provider="onesec_api")
    ToolRegistry._tools[tool.info.name] = tool
    ToolRegistry._snapshot_enabled_defaults()

    ConfigWriter.set_tool_setting("onesec_dns", {"enabled": False})
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is False


def test_overlay_re_enable_when_service_enabled(temp_config, isolated_registry):
    """Overlay enabled=True is honoured once the API service is enabled."""
    from flocks.config.config_writer import ConfigWriter

    _set_api_service("onesec_api", enabled=True)
    tool = _stub_api_tool("onesec_threat", enabled=False, provider="onesec_api")
    ToolRegistry._tools[tool.info.name] = tool
    ToolRegistry._snapshot_enabled_defaults()

    ConfigWriter.set_tool_setting("onesec_threat", {"enabled": True})
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is True


# ---------------------------------------------------------------------------
# Snapshot semantics
# ---------------------------------------------------------------------------

def test_snapshot_captures_yaml_default_before_sync(temp_config, isolated_registry):
    """_enabled_defaults reflects the registration default, not post-sync state."""
    _set_api_service("onesec_api", enabled=False)
    tool = _stub_api_tool("onesec_threat", enabled=True, provider="onesec_api")
    ToolRegistry._tools[tool.info.name] = tool
    ToolRegistry._snapshot_enabled_defaults()

    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is False
    # The snapshot must still report the YAML default, not the synced value.
    assert ToolRegistry.get_default_enabled("onesec_threat") is True


def test_get_default_enabled_returns_none_for_unknown(temp_config, isolated_registry):
    assert ToolRegistry.get_default_enabled("never_seen") is None


# ---------------------------------------------------------------------------
# Snapshot lifecycle — ``_enabled_defaults`` must stay in lock-step with
# the current YAML/registration source of truth, NOT with the first value
# the registry ever observed.  These cover the two real production paths
# that used to leak a stale default:
#
#   1. A YAML edit + ``POST /api/tools/{name}/reload`` — calls
#      :meth:`ToolRegistry.register` directly on the same name.
#   2. A file-watcher / manual ``refresh_plugin_tools`` cycle — goes
#      through :meth:`_unregister_plugin_tools` + :meth:`_load_plugin_tools`
#      again.
# ---------------------------------------------------------------------------

def test_register_refreshes_enabled_default(temp_config, isolated_registry):
    """Re-registering the same name (e.g. reload after YAML edit) must
    overwrite the factory-default snapshot.  Previously the snapshot was
    only written once under :meth:`_snapshot_enabled_defaults` which used
    ``setdefault``, so a flipped ``enabled:`` in the YAML would never be
    picked up until the process restarted.
    """
    tool_v1 = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry.register(tool_v1)
    assert ToolRegistry.get_default_enabled("plugin_thing") is True

    tool_v2 = _stub_tool("plugin_thing", enabled=False)
    ToolRegistry.register(tool_v2)
    assert ToolRegistry.get_default_enabled("plugin_thing") is False, (
        "register() must treat the newly-constructed tool as the current "
        "factory default, not fall back to the first value ever seen"
    )


def test_register_snapshot_is_immune_to_overlay_mutation(temp_config, isolated_registry):
    """Applying a user setting that flips ``info.enabled`` must NOT change
    the snapshot — it was captured at register time before any overlay
    could run.
    """
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry.register(tool)
    assert ToolRegistry.get_default_enabled("plugin_thing") is True

    ConfigWriter.set_tool_setting("plugin_thing", {"enabled": False})
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is False
    assert ToolRegistry.get_default_enabled("plugin_thing") is True, (
        "overlay must never leak into the factory-default snapshot"
    )


def test_unregister_plugin_tools_drops_enabled_default(temp_config, isolated_registry):
    """Regression for review P1: the refresh cycle calls
    ``_unregister_plugin_tools`` before reloading.  If that step doesn't
    pop ``_enabled_defaults`` the next ``register()`` still overwrites
    the entry correctly, but any intermediate read (e.g. between
    unregister and the new register, or for a tool that was deleted and
    never re-registered) would hand back a stale factory value.
    """
    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry.register(tool)
    ToolRegistry._plugin_tool_names = ["plugin_thing"]
    assert ToolRegistry.get_default_enabled("plugin_thing") is True

    ToolRegistry._unregister_plugin_tools()
    assert "plugin_thing" not in ToolRegistry._tools
    assert ToolRegistry.get_default_enabled("plugin_thing") is None, (
        "_unregister_plugin_tools must pop the snapshot entry so a stale "
        "default cannot survive into the next refresh cycle"
    )


def test_refresh_cycle_picks_up_new_yaml_default(temp_config, isolated_registry):
    """End-to-end check of the full refresh path: unregister + reload
    (simulated by a fresh ``register`` of the same name with a different
    factory default) must update the snapshot.  This is the exact
    scenario the review flagged as High.
    """
    from flocks.config.config_writer import ConfigWriter

    # v1 — shipped with enabled: true; user disables via overlay.
    v1 = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry.register(v1)
    ToolRegistry._plugin_tool_names = ["plugin_thing"]
    ConfigWriter.set_tool_setting("plugin_thing", {"enabled": False})
    ToolRegistry._apply_tool_settings()
    assert v1.info.enabled is False

    # Upgrade: YAML now ships with enabled: false by default.
    ToolRegistry._unregister_plugin_tools()
    v2 = _stub_tool("plugin_thing", enabled=False)
    ToolRegistry.register(v2)
    ToolRegistry._plugin_tool_names = ["plugin_thing"]

    assert ToolRegistry.get_default_enabled("plugin_thing") is False, (
        "after refresh the snapshot must reflect the new YAML factory "
        "default, not the one observed before the upgrade"
    )


def test_snapshot_defaults_safety_net_uses_assignment(temp_config, isolated_registry):
    """``_snapshot_enabled_defaults`` is a backstop for code paths that
    insert into ``_tools`` without going through :meth:`register`.  It
    must use direct assignment (not ``setdefault``) so a stale entry
    from a previous cycle is corrected rather than kept.
    """
    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry._tools[tool.info.name] = tool
    ToolRegistry._enabled_defaults["plugin_thing"] = False  # stale entry

    ToolRegistry._snapshot_enabled_defaults()

    assert ToolRegistry.get_default_enabled("plugin_thing") is True
