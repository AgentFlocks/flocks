"""Tests for ToolRegistry._apply_tool_settings — user-level enable/disable overlay."""

from __future__ import annotations

import json

import pytest

# Import before fixture snapshots so registration side effects cannot be lost
# while the modules remain cached for later built-in registry tests.
from flocks.tool.code.lsp_tool import lsp_tool
from flocks.tool.wecom.wecom_mcp import wecom_mcp
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
    """Replace the registry's tool dict + defaults snapshot with a known set.

    Also shadows ``_plugin_tool_names`` and ``_dynamic_tools_by_module``
    so tests exercising the unregister paths can't leak names into the
    real registry when the test process later runs unrelated tests.
    """
    from flocks.config import api_versioning

    # Fake providers in this unit suite must not resolve through real project
    # descriptors simply because pytest was launched from the repository root.
    monkeypatch.setattr(api_versioning, "discover_api_service_descriptors", lambda **kwargs: [])
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


def _set_api_service(name: str, *, enabled: bool) -> None:
    """Helper to write a minimal api_services entry."""
    from flocks.config.config_writer import ConfigWriter
    ConfigWriter.set_api_service(name, {
        "apiKey": "{secret:test_key}",
        "enabled": enabled,
    })


def test_native_group_declarative_python_registration(isolated_registry, monkeypatch, tmp_path):
    from flocks.plugin import PluginLoader

    extensions = []
    monkeypatch.setattr(PluginLoader, "register_extension_point", extensions.append)
    ToolRegistry._register_plugin_extension_point()
    definition = {
        "name": "declarative_group_tool", "description": "Python definition",
        "group": "  Package  ", "handler": lambda ctx: ToolResult(success=True),
        "parameters": [{"name": "value", "type": "string", "description": "Runtime value"}],
    }
    assert extensions[0].consumer([definition], str(tmp_path / "tool.py")) == []
    info = ToolRegistry._tools[definition["name"]].info
    assert info.group == "Package"
    assert info.source == "plugin_py"
    assert list(info.get_schema().properties) == ["value"]
    assert definition["group"] == "  Package  "


def test_native_group_python_registration_and_overlay(temp_config, isolated_registry):
    from flocks.config.config_writer import ConfigWriter

    @ToolRegistry.register_function(name="group_python_tool", description="test", group="  Package  ")
    async def group_python_tool(ctx):
        return ToolResult(success=True)

    tool = ToolRegistry._tools["group_python_tool"]
    assert tool.info.group == "Package"
    assert "group" not in tool.info.get_schema().properties
    ConfigWriter.set_tool_setting(tool.info.name, {"group": "User"})
    ToolRegistry._apply_tool_settings()
    assert tool.info.group == "Package"
    assert ConfigWriter.get_tool_setting(tool.info.name)["group"] == "User"
    assert tool.info.enabled is True
    ConfigWriter.set_tool_setting(tool.info.name, {"group": ""})
    ToolRegistry._apply_tool_settings()
    assert tool.info.group == "Package"
    assert ConfigWriter.get_tool_setting(tool.info.name)["group"] == ""
    ConfigWriter.delete_tool_setting(tool.info.name, field="group")
    ToolRegistry._apply_tool_settings()
    assert tool.info.group == "Package"
    assert tool.info.enabled is True


@pytest.mark.parametrize("name", ["get_time", "lsp", "task", "list_providers", "add_provider", "add_model", "wecom_mcp"])
def test_group_preflight_is_cold_and_source_based(name, isolated_registry, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "init", lambda: pytest.fail("must not initialize registry"))
    from flocks.config.config_writer import ConfigWriter
    monkeypatch.setattr(ConfigWriter, "_read_raw", lambda: pytest.fail("must not read config"))
    readonly, group = ToolRegistry._unloaded_definition_group(name)
    assert readonly and group
    ToolRegistry.validate_group_settings({name: {"group": group}})
    for clear_or_change in (None, "", "Override"):
        with pytest.raises(ValueError, match="read-only"):
            ToolRegistry.validate_group_settings({name: {"group": clear_or_change, "enabled": False}})
    # The actual registered definition wins over a core name or native=True.
    custom = _stub_tool(name, enabled=True, native=True)
    ToolRegistry.register(custom)
    assert custom.info.group_readonly is False
    ToolRegistry.validate_group_settings({name: {"group": "Custom"}})


def test_create_cannot_shadow_a_cold_core_definition(tmp_path, isolated_registry, monkeypatch):
    from flocks.tool import tool_loader

    monkeypatch.setattr(tool_loader, "_TOOLS_SUBDIR", tmp_path / "tools")
    monkeypatch.setattr(tool_loader, "_yaml_tool_search_roots", lambda: [tmp_path / "tools"])
    monkeypatch.setattr(ToolRegistry, "init", lambda: pytest.fail("must not initialize registry"))
    raw = {"name": "get_time", "group": "User", "handler": {"type": "http", "url": "https://example.invalid"}}
    with pytest.raises(ValueError, match="Cannot shadow system"):
        tool_loader.create_yaml_tool(raw)
    assert not (tmp_path / "tools").exists()


def test_enabled_preflight_does_not_discover_any_definitions(isolated_registry, monkeypatch):
    monkeypatch.setattr(ToolRegistry, "_unloaded_definition_group", lambda name: pytest.fail("no discovery"))
    ToolRegistry.validate_group_settings({"read": {"enabled": False}, "unknown": {"enabled": True}})


def test_preimported_core_keeps_search_flags_and_readonly(isolated_registry, monkeypatch):
    import importlib

    lsp = Tool(ToolInfo(name="lsp", description="LSP", native=False, group="代码分析"), lsp_tool)
    wecom = Tool(ToolInfo(name="wecom_mcp", description="WeCom", category=ToolCategory.CUSTOM, group="企业协作"), wecom_mcp)
    ToolRegistry.register(lsp)
    ToolRegistry.register(wecom)
    # Simulate modules already imported before the initialization pass; no new
    # registration delta exists, so provenance must be checked on existing tools.
    monkeypatch.setattr(importlib, "import_module", lambda name: None)
    ToolRegistry._register_builtin_tools()
    assert lsp.info.native is False and lsp.info.group_readonly is True
    assert wecom.info.category == ToolCategory.CUSTOM
    assert wecom.info.group_readonly is True
    assert ToolRegistry._tools["get_time"].info.group == "系统管理"
    assert ToolRegistry._tools["get_time"].info.group_readonly is True


def test_shipped_yaml_group_guards_source_update_and_direct_reload(tmp_path, monkeypatch, isolated_registry):
    import yaml
    from flocks.tool import registry, tool_loader

    installation = tmp_path / "installation"
    monkeypatch.setattr(registry, "__file__", str(installation / "flocks" / "tool" / "registry.py"))
    path = installation / ".flocks/plugins/tools/api/provider/tool.yaml"
    path.parent.mkdir(parents=True)
    raw = {
        "name": "declared_name", "description": "before", "group": "Canonical",
        "handler": {"type": "http", "url": "https://example.invalid"},
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    tool = tool_loader.yaml_to_tool(raw, path)
    assert tool.info.group_readonly is True
    ToolRegistry.register(tool)
    assert tool_loader.find_yaml_tool("declared_name") == path
    before = path.read_bytes()
    for group in (None, "", "Changed"):
        with pytest.raises(ValueError, match="read-only"):
            tool_loader.update_yaml_tool("declared_name", {"group": group, "description": "after"})
        assert path.read_bytes() == before
    assert tool_loader.update_yaml_tool("declared_name", {"group": "Canonical", "description": "after"})
    reloaded = tool_loader.yaml_to_tool(yaml.safe_load(path.read_text()), path)
    assert reloaded.info.group_readonly is True
    assert reloaded.info.group == "Canonical"

    custom_path = tmp_path / "project/.flocks/plugins/tools/custom.yaml"
    custom_path.parent.mkdir(parents=True)
    custom_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    custom = tool_loader.yaml_to_tool(raw, custom_path)
    custom.info.native = True
    ToolRegistry.register(custom)
    assert custom.info.group_readonly is False
    assert tool_loader.update_yaml_tool("declared_name", {"group": None})
    assert yaml.safe_load(custom_path.read_text())["group"] == ""
    assert yaml.safe_load(path.read_text())["group"] == "Canonical"

    symlink = path.parent / "user_symlink.yaml"
    symlink.symlink_to(custom_path)
    assert tool_loader.yaml_to_tool(raw, symlink).info.group_readonly is False


def test_native_group_yaml_default_and_editor_preservation(tmp_path, monkeypatch):
    import yaml
    from flocks.tool import tool_loader

    path = tmp_path / "standalone.yaml"
    raw = {
        "name": "yaml_group_tool", "description": "before", "group": " Package ",
        "unknown": {"keep": True},
        "handler": {"type": "http", "url": "https://example.invalid", "method": "GET"},
    }
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(tool_loader, "_find_yaml_file", lambda name: path)
    tool = tool_loader.yaml_to_tool(raw, path)
    assert tool.info.group == "Package"
    assert tool_loader.update_yaml_tool(raw["name"], {"description": "after"})
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["group"] == " Package "
    assert saved["unknown"] == {"keep": True}
    assert tool_loader.update_yaml_tool(raw["name"], {"group": None})
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["group"] == ""
    assert saved["handler"] == raw["handler"]
    assert tool_loader.yaml_to_tool(saved, path).info.group == ""


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


def test_snapshot_safety_net_does_not_clobber_post_sync_state(temp_config, isolated_registry):
    """``_snapshot_enabled_defaults`` runs at the tail of every
    ``_load_plugin_tools`` cycle — including the ones triggered by
    :meth:`refresh_plugin_tools`.  By the time the *second* cycle
    reaches it, the previous cycle's :meth:`_sync_api_service_states`
    has already flipped ``info.enabled`` on any built-in / previously
    registered tool whose API service is disabled.

    If the safety net overwrote with direct assignment it would capture
    that post-sync ``False`` as the "factory default", breaking
    :meth:`reset_tool_setting` and ``enabled_default`` reporting for
    every built-in API tool whose provider happens to be disabled.  The
    contract is therefore: :meth:`register` is the authoritative writer,
    the safety net must only fill genuine gaps (``setdefault``), and
    must never clobber a correct snapshot.
    """
    tool = _stub_api_tool("onesec_threat", enabled=True, provider="onesec_api")
    ToolRegistry.register(tool)
    assert ToolRegistry.get_default_enabled("onesec_threat") is True

    # Simulate the first init-style sequence: service sync flips live state.
    _set_api_service("onesec_api", enabled=False)
    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is False

    # And now the refresh-style tail call runs again.  It must not turn
    # the snapshot into a mirror of the post-sync (False) state.
    ToolRegistry._snapshot_enabled_defaults()

    assert ToolRegistry.get_default_enabled("onesec_threat") is True, (
        "safety net must never overwrite a correct factory-default "
        "snapshot with the post-sync live state"
    )


def test_snapshot_safety_net_fills_missing_entry(temp_config, isolated_registry):
    """For tools that landed in ``_tools`` without going through
    :meth:`register` (exclusively a test / unorthodox code path) the
    safety net is still expected to insert a best-effort factory
    default so ``enabled_default`` doesn't come back as ``None``.
    """
    tool = _stub_tool("plugin_thing", enabled=True)
    ToolRegistry._tools[tool.info.name] = tool
    assert ToolRegistry.get_default_enabled("plugin_thing") is None

    ToolRegistry._snapshot_enabled_defaults()

    assert ToolRegistry.get_default_enabled("plugin_thing") is True


def test_unregister_dynamic_tools_drops_enabled_default(temp_config, isolated_registry):
    """Dynamic tools go through a different unregister path
    (:meth:`_unregister_dynamic_tools`) than plugin tools.  When a
    dynamic module is deleted — the ``module_name not in modules``
    branch of :meth:`_register_dynamic_tools` — the tool vanishes
    from ``_tools`` for good, so the matching factory-default snapshot
    must be popped too.  Otherwise ``enabled_default`` would keep
    reporting a stale value for a tool that no longer exists.
    """
    tool = _stub_tool("dyn_thing", enabled=True)
    ToolRegistry.register(tool)
    ToolRegistry._dynamic_tools_by_module["dyn_module"] = ["dyn_thing"]
    assert ToolRegistry.get_default_enabled("dyn_thing") is True

    try:
        ToolRegistry._unregister_dynamic_tools("dyn_module")
    finally:
        ToolRegistry._dynamic_tools_by_module.pop("dyn_module", None)

    assert "dyn_thing" not in ToolRegistry._tools
    assert ToolRegistry.get_default_enabled("dyn_thing") is None, (
        "_unregister_dynamic_tools must pop the snapshot entry to keep "
        "the factory-default lifecycle symmetric with plugin tools"
    )


# ---------------------------------------------------------------------------
# Bi-directional sync: re-enabling a service must restore its tools to the
# factory default. Without this, deleting and re-adding a device would leave
# its tools silently disabled because ``_sync_api_service_states`` used to
# only flip ``enabled = False`` and never flip back to True.
# ---------------------------------------------------------------------------

def test_sync_restores_tool_when_service_re_enabled(
    temp_config, isolated_registry
):
    """Regression: tool whose factory default is True should bounce back
    to True after the owning service is re-enabled."""
    tool = _stub_api_tool("onesec_dns", enabled=True, provider="onesec_api")
    ToolRegistry.register(tool)
    assert ToolRegistry.get_default_enabled("onesec_dns") is True

    _set_api_service("onesec_api", enabled=False)
    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is False

    _set_api_service("onesec_api", enabled=True)
    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is True, (
        "service flipping enabled=False→True must restore the tool to its "
        "factory default — otherwise re-adding a device leaves its tools "
        "permanently off"
    )


def test_sync_does_not_resurrect_user_disabled_tool(
    temp_config, isolated_registry
):
    """A user that explicitly turned a tool off via ``tool_settings`` must
    keep that choice across service-state flips.  ``_sync_api_service_states``
    restores the YAML default, then ``_apply_tool_settings`` re-applies
    the user's disable; the net result is still disabled."""
    from flocks.config.config_writer import ConfigWriter

    tool = _stub_api_tool("onesec_dns", enabled=True, provider="onesec_api")
    ToolRegistry.register(tool)
    ConfigWriter.set_tool_setting("onesec_dns", {"enabled": False})

    _set_api_service("onesec_api", enabled=False)
    ToolRegistry._sync_api_service_states()
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is False

    _set_api_service("onesec_api", enabled=True)
    ToolRegistry._sync_api_service_states()
    ToolRegistry._apply_tool_settings()
    assert tool.info.enabled is False, (
        "the user's explicit disable in tool_settings must win over "
        "service-state bounce-back"
    )


def test_sync_does_not_flip_factory_disabled_tool(
    temp_config, isolated_registry
):
    """A tool whose factory default is ``enabled=False`` must NOT be
    flipped on by ``_sync_api_service_states`` when the service is
    enabled — the sync's job is to honour the YAML default, not to
    re-enable everything blindly."""
    tool = _stub_api_tool("onesec_admin", enabled=False, provider="onesec_api")
    ToolRegistry.register(tool)
    assert ToolRegistry.get_default_enabled("onesec_admin") is False

    _set_api_service("onesec_api", enabled=True)
    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is False, (
        "tools whose YAML default is enabled=false must stay off when "
        "the service is enabled — only an explicit user overlay can open them"
    )


def test_sync_leaves_already_enabled_tool_alone(
    temp_config, isolated_registry
):
    """When a tool is already enabled and its service is enabled, the
    sync must be a true no-op for it — no spurious writes or log spam."""
    tool = _stub_api_tool("onesec_dns", enabled=True, provider="onesec_api")
    ToolRegistry.register(tool)

    _set_api_service("onesec_api", enabled=True)
    ToolRegistry._sync_api_service_states()
    assert tool.info.enabled is True
