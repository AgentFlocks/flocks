from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import flocks.plugin.loader as plugin_loader_module
import flocks.tool.registry as tool_registry_module
import flocks.tool.tool_loader as tool_loader_module
from flocks.plugin.loader import ExtensionPoint, PluginLoader
from flocks.tool.registry import ToolFileWatcher, ToolRegistry


def test_yaml_to_tool_logs_loaded_at_debug(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(tool_loader_module, "log", logger)
    monkeypatch.setattr(tool_loader_module, "_load_provider_config", lambda _path: None)
    monkeypatch.setattr(
        tool_loader_module,
        "_merge_provider_defaults",
        lambda raw, _provider_cfg: raw,
    )
    monkeypatch.setattr(
        tool_loader_module,
        "_build_handler",
        lambda _handler, _path: object(),
    )

    tool = tool_loader_module.yaml_to_tool(
        {
            "name": "demo_tool",
            "description": "Demo tool",
            "handler": {"type": "http", "method": "GET", "url": "https://example.com"},
        },
        Path("/tmp/demo_tool.yaml"),
    )

    assert tool.info.name == "demo_tool"
    logger.debug.assert_called_once()
    event, payload = logger.debug.call_args.args
    assert event == "tool.yaml.loaded"
    assert payload["name"] == "demo_tool"
    logger.info.assert_not_called()


def test_plugin_loader_high_volume_logs_use_debug(tmp_path: Path, monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(plugin_loader_module, "log", logger)

    user_root = tmp_path / "user_plugins"
    project_dir = tmp_path / "project"
    user_tools_dir = user_root / "tools"
    project_tools_dir = project_dir / ".flocks" / "plugins" / "tools"
    user_tools_dir.mkdir(parents=True)
    project_tools_dir.mkdir(parents=True)
    (user_tools_dir / "user.yaml").write_text("name: user-tool\n", encoding="utf-8")
    (project_tools_dir / "project.yaml").write_text("name: project-tool\n", encoding="utf-8")

    monkeypatch.setattr(PluginLoader, "_plugin_root", user_root)
    PluginLoader.clear_extension_points()
    consumed: list[dict[str, str]] = []
    PluginLoader.register_extension_point(
        ExtensionPoint(
            attr_name="TOOLS",
            subdir="tools",
            consumer=lambda items, _source: consumed.extend(items),
            yaml_item_factory=lambda raw, path: {
                "name": str(raw["name"]),
                "path": str(path),
            },
        )
    )

    try:
        PluginLoader.load_all(project_dir=project_dir)
    finally:
        PluginLoader.clear_extension_points()

    assert consumed == [
        {"name": "user-tool", "path": str(user_tools_dir / "user.yaml")},
        {"name": "project-tool", "path": str(project_tools_dir / "project.yaml")},
    ]

    debug_events = [call.args[0] for call in logger.debug.call_args_list]
    assert "plugin.scan" in debug_events
    assert "plugin.project.scan" in debug_events
    assert debug_events.count("plugin.yaml_dispatched") == 2

    info_events = [call.args[0] for call in logger.info.call_args_list]
    assert "plugin.scan" not in info_events
    assert "plugin.project.scan" not in info_events
    assert "plugin.yaml_dispatched" not in info_events


def test_tool_registry_high_volume_logs_use_debug(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(tool_registry_module, "log", logger)
    monkeypatch.setattr("flocks.agent.registry.Agent.invalidate_cache", lambda: None)
    monkeypatch.setattr(ToolRegistry, "_revision", 41)

    ToolRegistry._bump_revision("plugin_refresh")

    event, payload = logger.debug.call_args.args
    assert event == "tool.registry.revision.bumped"
    assert payload == {"revision": 42, "reason": "plugin_refresh"}
    logger.info.assert_not_called()


def test_tool_registry_api_service_sync_logs_at_debug(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(tool_registry_module, "log", logger)
    tool = SimpleNamespace(info=SimpleNamespace(provider="svc", enabled=True))
    monkeypatch.setattr(ToolRegistry, "_tools", {"demo": tool})
    monkeypatch.setattr(
        "flocks.config.config_writer.ConfigWriter.list_api_services_raw",
        lambda: {"svc": {"enabled": False}},
    )

    ToolRegistry._sync_api_service_states()

    assert tool.info.enabled is False
    event, payload = logger.debug.call_args.args
    assert event == "tool_registry.api_service_sync"
    assert payload == {"disabled_tools": 1, "disabled_providers": ["svc"]}
    logger.info.assert_not_called()


def test_tool_file_watcher_refresh_logs_at_debug(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(tool_registry_module, "log", logger)
    monkeypatch.setattr(ToolRegistry, "refresh_plugin_tools", lambda: [])

    watcher = ToolFileWatcher()
    watcher._run_refresh()

    event, payload = logger.debug.call_args.args
    assert event == "tool.watcher.reloaded"
    assert payload == {"reason": "plugin tool file changed on disk"}
    logger.info.assert_not_called()
