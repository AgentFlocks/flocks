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
    monkeypatch.setattr(ToolRegistry, "_tools", {})
    monkeypatch.setattr(ToolRegistry, "_enabled_defaults", {})
    yield
    ToolRegistry._tools = saved_tools
    ToolRegistry._enabled_defaults = saved_defaults


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


def test_snapshot_preserves_builtin_default_on_refresh(temp_config, isolated_registry):
    """Regression: refresh_plugin_tools must NOT overwrite the snapshot of
    builtin tools whose info.enabled was already mutated by a previous
    overlay application.
    """
    from flocks.config.config_writer import ConfigWriter

    builtin = _stub_tool("builtin_thing", enabled=True)
    ToolRegistry._tools[builtin.info.name] = builtin
    ToolRegistry._snapshot_enabled_defaults()
    assert ToolRegistry.get_default_enabled("builtin_thing") is True

    ConfigWriter.set_tool_setting("builtin_thing", {"enabled": False})
    ToolRegistry._apply_tool_settings()
    assert builtin.info.enabled is False

    # Simulate what refresh_plugin_tools does: snapshot again.
    # The snapshot must NOT pick up the overlay-mutated value.
    ToolRegistry._snapshot_enabled_defaults()
    assert ToolRegistry.get_default_enabled("builtin_thing") is True, (
        "snapshot must use setdefault so a refresh cycle does not overwrite "
        "the original default with the overlay-mutated value"
    )
