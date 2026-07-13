from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import threading

import pytest

from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolInfo,
    ToolRefreshError,
    ToolRegistry,
)


def _tool(name: str, source: str) -> Tool:
    return Tool(
        info=ToolInfo(
            name=name,
            description=name,
            category=ToolCategory.CUSTOM,
            source=source,
        ),
        handler=lambda _ctx, **_kwargs: None,
    )


@contextmanager
def _isolated_registry() -> Iterator[None]:
    state = {
        "initialized": ToolRegistry._initialized,
        "tools": ToolRegistry._tools,
        "defaults": ToolRegistry._enabled_defaults,
        "plugin_names": ToolRegistry._plugin_tool_names,
        "dynamic_modules": ToolRegistry._dynamic_modules,
        "dynamic_tools": ToolRegistry._dynamic_tools_by_module,
    }
    try:
        ToolRegistry._initialized = True
        ToolRegistry._tools = {}
        ToolRegistry._enabled_defaults = {}
        ToolRegistry._plugin_tool_names = []
        ToolRegistry._dynamic_modules = {}
        ToolRegistry._dynamic_tools_by_module = {}
        yield
    finally:
        ToolRegistry._initialized = state["initialized"]
        ToolRegistry._tools = state["tools"]
        ToolRegistry._enabled_defaults = state["defaults"]
        ToolRegistry._plugin_tool_names = state["plugin_names"]
        ToolRegistry._dynamic_modules = state["dynamic_modules"]
        ToolRegistry._dynamic_tools_by_module = state["dynamic_tools"]


def test_plugin_refresh_restores_last_known_good_on_load_error(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _failed_load(cls, errors):
            replacement = _tool("partial_replacement", "plugin_py")
            cls._tools[replacement.info.name] = replacement
            cls._plugin_tool_names = [replacement.info.name]
            errors.append("broken plugin")

        monkeypatch.setattr(ToolRegistry, "_load_plugin_tools", classmethod(_failed_load))

        with pytest.raises(ToolRefreshError, match="broken plugin"):
            ToolRegistry.refresh_plugin_tools()

        assert ToolRegistry._tools == {previous.info.name: previous}
        assert ToolRegistry._plugin_tool_names == [previous.info.name]


def test_dynamic_refresh_restores_last_known_good_on_load_error(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_dynamic", "custom")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._dynamic_modules = {"demo": "/old/demo.py"}
        ToolRegistry._dynamic_tools_by_module = {"demo": [previous.info.name]}

        def _failed_load(cls, errors):
            cls._tools.pop(previous.info.name)
            replacement = _tool("partial_dynamic", "custom")
            cls._tools[replacement.info.name] = replacement
            cls._dynamic_modules = {"demo": "/new/demo.py"}
            cls._dynamic_tools_by_module = {"demo": [replacement.info.name]}
            errors.append("broken dynamic module")

        monkeypatch.setattr(ToolRegistry, "_register_dynamic_tools", classmethod(_failed_load))

        with pytest.raises(ToolRefreshError, match="broken dynamic module"):
            ToolRegistry.refresh_dynamic_tools()

        assert ToolRegistry._tools == {previous.info.name: previous}
        assert ToolRegistry._dynamic_modules == {"demo": "/old/demo.py"}
        assert ToolRegistry._dynamic_tools_by_module == {"demo": [previous.info.name]}


def test_refresh_transactions_are_serialized_across_stages(
    monkeypatch: pytest.MonkeyPatch,
):
    plugin_load_entered = threading.Event()
    allow_plugin_failure = threading.Event()
    dynamic_load_entered = threading.Event()
    allow_dynamic_finish = threading.Event()
    plugin_failures: list[Exception] = []
    dynamic_failures: list[Exception] = []

    with _isolated_registry():
        base = _tool("base", "builtin")
        ToolRegistry._tools[base.info.name] = base

        def _failed_plugin_load(cls, errors):
            plugin_load_entered.set()
            assert allow_plugin_failure.wait(timeout=2)
            errors.append("broken plugin")

        def _successful_dynamic_load(cls, _errors):
            dynamic_load_entered.set()
            dynamic = _tool("new_dynamic", "custom")
            cls._tools[dynamic.info.name] = dynamic
            cls._dynamic_modules = {"demo": "/new/demo.py"}
            cls._dynamic_tools_by_module = {"demo": [dynamic.info.name]}
            assert allow_dynamic_finish.wait(timeout=2)

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_failed_plugin_load),
        )
        monkeypatch.setattr(
            ToolRegistry,
            "_register_dynamic_tools",
            classmethod(_successful_dynamic_load),
        )
        monkeypatch.setattr(ToolRegistry, "_bump_revision", lambda _reason: None)

        def _refresh_plugin():
            try:
                ToolRegistry.refresh_plugin_tools()
            except Exception as exc:
                plugin_failures.append(exc)

        def _refresh_dynamic():
            try:
                ToolRegistry.refresh_dynamic_tools()
            except Exception as exc:
                dynamic_failures.append(exc)

        plugin_thread = threading.Thread(target=_refresh_plugin)
        dynamic_thread = threading.Thread(target=_refresh_dynamic)
        plugin_thread.start()
        assert plugin_load_entered.wait(timeout=1)
        dynamic_thread.start()

        # If transactions are not serialized, make the dynamic refresh commit
        # before the plugin rollback to deterministically reproduce lost state.
        dynamic_entered_while_plugin_blocked = dynamic_load_entered.wait(timeout=0.2)
        if dynamic_entered_while_plugin_blocked:
            allow_dynamic_finish.set()
            dynamic_thread.join(timeout=1)
            allow_plugin_failure.set()
        else:
            allow_plugin_failure.set()
            allow_dynamic_finish.set()

        plugin_thread.join(timeout=1)
        dynamic_thread.join(timeout=1)

        assert not plugin_thread.is_alive()
        assert not dynamic_thread.is_alive()
        assert len(plugin_failures) == 1
        assert isinstance(plugin_failures[0], ToolRefreshError)
        assert dynamic_failures == []
        assert set(ToolRegistry._tools) == {"base", "new_dynamic"}


def test_registry_readers_do_not_observe_an_in_progress_refresh(
    monkeypatch: pytest.MonkeyPatch,
):
    load_entered = threading.Event()
    allow_failure = threading.Event()
    reader_finished = threading.Event()
    reader_results: list[Tool | None] = []

    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _failed_plugin_load(cls, errors):
            load_entered.set()
            assert allow_failure.wait(timeout=2)
            errors.append("broken plugin")

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_failed_plugin_load),
        )

        refresh_thread = threading.Thread(
            target=lambda: pytest.raises(
                ToolRefreshError,
                ToolRegistry.refresh_plugin_tools,
            ),
        )

        def _read_plugin() -> None:
            reader_results.append(ToolRegistry.get(previous.info.name))
            reader_finished.set()

        reader_thread = threading.Thread(target=_read_plugin)
        refresh_thread.start()
        assert load_entered.wait(timeout=1)
        reader_thread.start()

        assert not reader_finished.wait(timeout=0.1)
        allow_failure.set()
        refresh_thread.join(timeout=1)
        reader_thread.join(timeout=1)

        assert not refresh_thread.is_alive()
        assert not reader_thread.is_alive()
        assert reader_results == [previous]


def test_tool_extension_consumer_reports_invalid_specs():
    from flocks.plugin import PluginLoader

    extension_points_before = PluginLoader._extension_points.copy()
    try:
        PluginLoader.clear_extension_points()
        ToolRegistry._register_plugin_extension_point()
        extension = PluginLoader._extension_points["TOOLS"]

        errors = extension.consumer(
            [
                {"name": "missing_handler"},
                {"name": "not_callable", "handler": 42},
            ],
            "invalid_tools.py",
        )

        assert errors == [
            "tool definition requires name and handler",
            "tool not_callable: handler must be callable",
        ]
    finally:
        PluginLoader.clear_extension_points()
        PluginLoader._extension_points.update(extension_points_before)


@pytest.mark.asyncio
async def test_refresh_route_reports_partial_and_total_stage_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import tool as tool_routes

    monkeypatch.setattr(tool_routes.ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(tool_routes.ToolRegistry, "all_tool_ids", lambda: ["working"])
    monkeypatch.setattr(
        tool_routes.ToolRegistry,
        "refresh_dynamic_tools",
        lambda: (_ for _ in ()).throw(ToolRefreshError("dynamic", ["bad dynamic"])),
    )
    monkeypatch.setattr(tool_routes.ToolRegistry, "refresh_plugin_tools", lambda: ["working"])

    partial = await tool_routes.refresh_tools(_admin=object())

    assert partial.status == "partial"
    assert partial.stages == {"dynamic": "error", "plugin": "success"}
    assert partial.errors == ["dynamic: bad dynamic"]

    monkeypatch.setattr(
        tool_routes.ToolRegistry,
        "refresh_plugin_tools",
        lambda: (_ for _ in ()).throw(ToolRefreshError("plugin", ["bad plugin"])),
    )

    failed = await tool_routes.refresh_tools(_admin=object())

    assert failed.status == "error"
    assert failed.stages == {"dynamic": "error", "plugin": "error"}
    assert failed.errors == ["dynamic: bad dynamic", "plugin: bad plugin"]


@pytest.mark.asyncio
async def test_delete_tool_cleans_hub_record_when_registry_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.hub import local as hub_local
    from flocks.server.routes import tool as tool_routes
    from flocks.tool import tool_loader

    removed_records: list[tuple[str, str]] = []
    monkeypatch.setattr(tool_routes.ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(tool_loader, "find_yaml_tool", lambda _name: object())
    monkeypatch.setattr(tool_loader, "delete_yaml_tool", lambda _name: True)
    monkeypatch.setattr(
        tool_routes.ToolRegistry,
        "refresh_plugin_tools",
        lambda: (_ for _ in ()).throw(ToolRefreshError("plugin", ["broken"])),
    )
    monkeypatch.setattr(
        hub_local,
        "remove_installed_record",
        lambda kind, name: removed_records.append((kind, name)),
    )

    response = await tool_routes.delete_tool("demo", _admin=object())

    assert response["status"] == "partial"
    assert response["errors"] == ["registry refresh: plugin: broken"]
    assert removed_records == [("tool", "demo")]


@pytest.mark.asyncio
async def test_delete_tool_reports_hub_cleanup_failure_as_partial(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.hub import local as hub_local
    from flocks.server.routes import tool as tool_routes
    from flocks.tool import tool_loader

    monkeypatch.setattr(tool_routes.ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(tool_loader, "find_yaml_tool", lambda _name: object())
    monkeypatch.setattr(tool_loader, "delete_yaml_tool", lambda _name: True)
    monkeypatch.setattr(
        tool_routes.ToolRegistry,
        "refresh_plugin_tools",
        lambda: [],
    )
    monkeypatch.setattr(
        hub_local,
        "remove_installed_record",
        lambda _kind, _name: (_ for _ in ()).throw(RuntimeError("index locked")),
    )

    response = await tool_routes.delete_tool("demo", _admin=object())

    assert response["status"] == "partial"
    assert response["errors"] == ["Hub record cleanup: index locked"]


@pytest.mark.asyncio
async def test_delete_tool_reports_all_post_delete_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.hub import local as hub_local
    from flocks.server.routes import tool as tool_routes
    from flocks.tool import tool_loader

    monkeypatch.setattr(tool_routes.ToolRegistry, "init", lambda: None)
    monkeypatch.setattr(tool_loader, "find_yaml_tool", lambda _name: object())
    monkeypatch.setattr(tool_loader, "delete_yaml_tool", lambda _name: True)
    monkeypatch.setattr(
        tool_routes.ToolRegistry,
        "refresh_plugin_tools",
        lambda: (_ for _ in ()).throw(ToolRefreshError("plugin", ["broken"])),
    )
    monkeypatch.setattr(
        hub_local,
        "remove_installed_record",
        lambda _kind, _name: (_ for _ in ()).throw(RuntimeError("index locked")),
    )

    response = await tool_routes.delete_tool("demo", _admin=object())

    assert response["status"] == "partial"
    assert response["errors"] == [
        "registry refresh: plugin: broken",
        "Hub record cleanup: index locked",
    ]
