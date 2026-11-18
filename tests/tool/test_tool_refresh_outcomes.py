from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from flocks.config.config import Config, ConfigInfo
from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import (
    Tool,
    ToolCategory,
    ToolFileWatcher,
    ToolInfo,
    ToolRefreshError,
    ToolRegistry,
    ToolResult,
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
        "plugin_errors": ToolRegistry._plugin_load_errors,
        "revision": ToolRegistry._revision,
        "dynamic_modules": ToolRegistry._dynamic_modules,
        "dynamic_tools": ToolRegistry._dynamic_tools_by_module,
    }
    try:
        ToolRegistry._initialized = True
        ToolRegistry._tools = {}
        ToolRegistry._enabled_defaults = {}
        ToolRegistry._plugin_tool_names = []
        ToolRegistry._plugin_load_errors = []
        ToolRegistry._revision = 0
        ToolRegistry._dynamic_modules = {}
        ToolRegistry._dynamic_tools_by_module = {}
        yield
    finally:
        ToolRegistry._initialized = state["initialized"]
        ToolRegistry._tools = state["tools"]
        ToolRegistry._enabled_defaults = state["defaults"]
        ToolRegistry._plugin_tool_names = state["plugin_names"]
        ToolRegistry._plugin_load_errors = state["plugin_errors"]
        ToolRegistry._revision = state["revision"]
        ToolRegistry._dynamic_modules = state["dynamic_modules"]
        ToolRegistry._dynamic_tools_by_module = state["dynamic_tools"]


def test_plugin_refresh_restores_last_known_good_on_loader_failure(
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
            errors.append("plugin loader: unavailable")

        monkeypatch.setattr(ToolRegistry, "_load_plugin_tools", classmethod(_failed_load))

        with pytest.raises(ToolRefreshError, match="plugin loader: unavailable"):
            ToolRegistry.refresh_plugin_tools()

        assert ToolRegistry._tools == {previous.info.name: previous}
        assert ToolRegistry._plugin_tool_names == [previous.info.name]


def test_plugin_refresh_accepts_later_unrelated_syntax_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    from flocks.plugin import PluginLoader

    with _isolated_registry():
        project_dir = tmp_path / "project"
        plugin_root = tmp_path / "plugins"
        plugin_dir = plugin_root / "tools" / "device" / "legacy_device"
        project_dir.mkdir()
        plugin_dir.mkdir(parents=True)

        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(PluginLoader, "_plugin_root", plugin_root)
        monkeypatch.setattr(
            ToolRegistry,
            "_finalize_plugin_tools_load",
            classmethod(lambda cls: None),
        )
        monkeypatch.setattr(ToolRegistry, "_bump_revision", lambda _reason: None)
        extension_points_before = PluginLoader._extension_points.copy()
        try:
            ToolRegistry._register_plugin_extension_point()
            startup_errors = ToolRegistry._load_plugin_tools()
            assert startup_errors == []
            ToolRegistry._plugin_load_errors = startup_errors

            (plugin_dir / "broken.handler.py").write_text(
                "def broken(:\n",
                encoding="utf-8",
            )

            (plugin_root / "tools" / "newly_installed.py").write_text(
                "async def handle(ctx):\n"
                "    return None\n\n"
                "TOOLS = [{\n"
                "    'name': 'newly_installed',\n"
                "    'description': 'newly installed tool',\n"
                "    'handler': handle,\n"
                "}]\n",
                encoding="utf-8",
            )

            refreshed = ToolRegistry.refresh_plugin_tools(
                changed_path=plugin_root / "tools" / "newly_installed.py",
            )

            assert "newly_installed" in refreshed
            assert ToolRegistry.get("newly_installed") is not None
            assert any("SyntaxError" in error for error in ToolRegistry._plugin_load_errors)
        finally:
            PluginLoader.clear_extension_points()
            PluginLoader._extension_points.update(extension_points_before)


def test_registry_init_records_plugin_load_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        ToolRegistry._initialized = False
        monkeypatch.setattr(
            ToolRegistry,
            "_register_builtin_tools",
            classmethod(lambda cls: None),
        )
        monkeypatch.setattr(
            ToolRegistry,
            "_register_dynamic_tools",
            classmethod(lambda cls, errors=None: None),
        )
        monkeypatch.setattr(
            ToolRegistry,
            "_register_plugin_extension_point",
            classmethod(lambda cls: None),
        )
        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(lambda cls: ["broken legacy plugin"]),
        )

        ToolRegistry.init()

        assert ToolRegistry._plugin_load_errors == ["broken legacy plugin"]


def test_plugin_refresh_commits_valid_sources_alongside_new_source_error(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]
        ToolRegistry._plugin_load_errors = ["/plugins/legacy.py: broken legacy plugin"]

        def _load_with_new_error(cls, errors):
            replacement = _tool("partial_replacement", "plugin_py")
            cls._tools[replacement.info.name] = replacement
            cls._plugin_tool_names = [replacement.info.name]
            errors.extend(
                [
                    "/plugins/legacy.py: broken legacy plugin",
                    "/plugins/unrelated.py: new broken plugin",
                    "entry point legacy_package: RuntimeError: broken",
                ]
            )

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_load_with_new_error),
        )

        refreshed = ToolRegistry.refresh_plugin_tools(
            changed_path=Path("/plugins/newly_installed.py"),
        )

        assert refreshed == ["partial_replacement"]
        assert set(ToolRegistry._tools) == {"partial_replacement"}
        assert ToolRegistry._plugin_tool_names == ["partial_replacement"]
        assert ToolRegistry._plugin_load_errors == [
            "/plugins/legacy.py: broken legacy plugin",
            "/plugins/unrelated.py: new broken plugin",
            "entry point legacy_package: RuntimeError: broken",
        ]


def test_plugin_refresh_rejects_error_inside_changed_path(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _load_target_error(cls, errors):
            errors.append("/plugins/new/broken.py: ImportError: missing dependency")

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_load_target_error),
        )

        with pytest.raises(ToolRefreshError, match="missing dependency"):
            ToolRegistry.refresh_plugin_tools(changed_path=Path("/plugins/new"))

        assert ToolRegistry._tools == {previous.info.name: previous}
        assert ToolRegistry._plugin_tool_names == [previous.info.name]


def test_plugin_refresh_restores_snapshot_when_finalize_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        builtin = _tool("builtin", "builtin")
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools = {
            builtin.info.name: builtin,
            previous.info.name: previous,
        }
        ToolRegistry._enabled_defaults = {
            builtin.info.name: True,
            previous.info.name: True,
        }
        ToolRegistry._plugin_tool_names = [previous.info.name]
        ToolRegistry._plugin_load_errors = ["old error"]
        ToolRegistry._revision = 7

        def _load_with_unrelated_error(cls, errors):
            replacement = _tool("replacement", "plugin_py")
            cls._tools[replacement.info.name] = replacement
            cls._enabled_defaults[replacement.info.name] = True
            cls._plugin_tool_names = [replacement.info.name]
            errors.append("/plugins/unrelated.py: SyntaxError: broken")

        def _failed_finalize(cls):
            cls._tools[builtin.info.name].info.enabled = False
            raise RuntimeError("finalize failed")

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_load_with_unrelated_error),
        )
        monkeypatch.setattr(
            ToolRegistry,
            "_finalize_plugin_tools_load",
            classmethod(_failed_finalize),
        )

        with pytest.raises(RuntimeError, match="finalize failed"):
            ToolRegistry.refresh_plugin_tools(
                changed_path=Path("/plugins/target.py"),
            )

        assert ToolRegistry._tools == {
            builtin.info.name: builtin,
            previous.info.name: previous,
        }
        assert builtin.info.enabled is True
        assert ToolRegistry._enabled_defaults == {
            builtin.info.name: True,
            previous.info.name: True,
        }
        assert ToolRegistry._plugin_tool_names == [previous.info.name]
        assert ToolRegistry._plugin_load_errors == ["old error"]
        assert ToolRegistry._revision == 7


def test_plugin_refresh_rejects_entry_point_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("entrypoint_old", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _load_with_scan_failure(cls, errors):
            replacement = _tool("hub_target", "plugin_py")
            cls._tools[replacement.info.name] = replacement
            cls._plugin_tool_names = [replacement.info.name]
            errors.append("entry point scan: RuntimeError: metadata unavailable")

        monkeypatch.setattr(
            ToolRegistry,
            "_load_plugin_tools",
            classmethod(_load_with_scan_failure),
        )

        with pytest.raises(ToolRefreshError, match="metadata unavailable"):
            ToolRegistry.refresh_plugin_tools(
                changed_path=Path("/plugins/hub_target.py"),
            )

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
            errors.append("plugin loader: unavailable")

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


@contextmanager
def _plugin_refresh_held_open(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: bool = True,
    on_load=None,
) -> Iterator[threading.Thread]:
    """Run refresh_plugin_tools on a thread and keep it inside the plugin load.

    The registry has already unregistered every plugin tool at that point, as
    a real reload does before it loads them again one by one.
    """
    load_entered = threading.Event()
    allow_finish = threading.Event()
    outcome: list[BaseException | None] = []

    def _held_plugin_load(cls, errors):
        if on_load is not None:
            on_load()
        load_entered.set()
        assert allow_finish.wait(timeout=5)
        if fail:
            errors.append("plugin loader: unavailable")

    monkeypatch.setattr(
        ToolRegistry,
        "_load_plugin_tools",
        classmethod(_held_plugin_load),
    )

    def _refresh() -> None:
        try:
            ToolRegistry.refresh_plugin_tools()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome.append(exc)
        else:
            outcome.append(None)

    refresh_thread = threading.Thread(target=_refresh, daemon=True)
    refresh_thread.start()
    try:
        assert load_entered.wait(timeout=2)
        yield SimpleNamespace(thread=refresh_thread, release=allow_finish.set)
    finally:
        allow_finish.set()
        refresh_thread.join(timeout=5)
    assert not refresh_thread.is_alive()
    if fail:
        assert len(outcome) == 1 and isinstance(outcome[0], ToolRefreshError)
    else:
        assert outcome == [None]


def _on_thread(fn, timeout: float = 1.0):
    """Call *fn* on another thread; return (finished in time, its result)."""
    results: list = []
    worker = threading.Thread(target=lambda: results.append(fn()), daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    return (not worker.is_alive(), results[0] if results else None)


def test_registry_readers_see_the_pre_refresh_state_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
):
    """A read during a reload neither waits for it nor sees it half done.

    Reads run on the event loop (request handlers, tool calls): waiting out a
    reload there stopped every request on the server until the reload ended.
    """
    with _isolated_registry():
        builtin = _tool("builtin_tool", "builtin")
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[builtin.info.name] = builtin
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._enabled_defaults[previous.info.name] = True
        ToolRegistry._plugin_tool_names = [previous.info.name]
        ToolRegistry._revision = 7

        with _plugin_refresh_held_open(monkeypatch):
            # The live registry is mid-reload: the plugin tool is unregistered.
            assert previous.info.name not in ToolRegistry._tools

            finished, got = _on_thread(lambda: ToolRegistry.get(previous.info.name))
            assert finished, "get() waited for the reload"
            assert got is previous

            finished, infos = _on_thread(ToolRegistry.list_tools)
            assert finished
            assert {info.name for info in infos} == {builtin.info.name, previous.info.name}

            finished, ids = _on_thread(ToolRegistry.all_tool_ids)
            assert finished
            assert set(ids) == {builtin.info.name, previous.info.name}

            finished, identity = _on_thread(ToolRegistry.snapshot_identity)
            assert finished
            assert identity[0] == 7
            assert set(identity[1]) == {builtin.info.name, previous.info.name}

            finished, revision = _on_thread(ToolRegistry.revision)
            assert finished and revision == 7

            finished, default = _on_thread(
                lambda: ToolRegistry.get_default_enabled(previous.info.name)
            )
            assert finished and default is True

        # The failed reload restored the previous registry; reads use it again.
        assert ToolRegistry.get(previous.info.name) is previous
        assert ToolRegistry._reload_view is None


def test_registry_reads_after_a_reload_see_its_result(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        replacement = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _register_replacement():
            ToolRegistry.register(replacement)

        with _plugin_refresh_held_open(monkeypatch, fail=False, on_load=_register_replacement):
            finished, during = _on_thread(lambda: ToolRegistry.get(previous.info.name))
            assert finished and during is previous

        assert ToolRegistry.get(previous.info.name) is replacement
        assert ToolRegistry._reload_view is None


def test_reloading_thread_reads_its_own_progress(monkeypatch: pytest.MonkeyPatch):
    """Plugin code run by the reload (entry points) sees what it has loaded so far."""
    seen: dict[str, object] = {}

    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        fresh = _tool("fresh_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        def _load_and_look():
            ToolRegistry.register(fresh)
            seen["fresh"] = ToolRegistry.get(fresh.info.name)
            seen["previous"] = ToolRegistry.get(previous.info.name)
            seen["ids"] = set(ToolRegistry.all_tool_ids())

        with _plugin_refresh_held_open(monkeypatch, fail=False, on_load=_load_and_look):
            pass

    assert seen["fresh"] is fresh
    assert seen["previous"] is None
    assert seen["ids"] == {fresh.info.name}


def test_registry_writers_still_wait_for_a_reload(monkeypatch: pytest.MonkeyPatch):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        late = _tool("late_tool", "builtin")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        with _plugin_refresh_held_open(monkeypatch):
            writer = threading.Thread(target=lambda: ToolRegistry.register(late), daemon=True)
            writer.start()
            writer.join(timeout=0.2)
            assert writer.is_alive(), "register() ran inside another thread's reload"
        writer.join(timeout=2)
        assert not writer.is_alive()
        # Registered after the failed reload rolled back, so it survives it.
        assert ToolRegistry.get(late.info.name) is late


def test_tool_failure_bookkeeping_does_not_wait_for_a_reload(
    monkeypatch: pytest.MonkeyPatch,
):
    """Every tool call resets or counts failures on the event loop afterwards."""
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]
        monkeypatch.setattr(ToolRegistry, "_failure_state", {"previous_plugin": {"key": "k", "count": 1}})

        with _plugin_refresh_held_open(monkeypatch):
            finished, _ = _on_thread(lambda: ToolRegistry._reset_failure_state("previous_plugin"))
            assert finished, "_reset_failure_state waited for the reload"
            assert "previous_plugin" not in ToolRegistry._failure_state

            finished, disabled = _on_thread(
                lambda: ToolRegistry._record_failure(previous, {"q": 1}, "upstream exploded")
            )
            assert finished, "_record_failure waited for the reload"
            assert disabled is False
            assert ToolRegistry._failure_state["previous_plugin"]["count"] == 1


def test_config_sync_is_skipped_during_another_threads_reload(
    monkeypatch: pytest.MonkeyPatch,
):
    synced: list[str] = []
    monkeypatch.setattr(ToolRegistry, "_config_state_token", ("before",))
    monkeypatch.setattr(
        ToolRegistry,
        "_current_config_state_token",
        classmethod(lambda cls: ("after",)),
    )
    monkeypatch.setattr(
        ToolRegistry,
        "_apply_tool_settings",
        classmethod(lambda cls: synced.append("settings")),
    )
    monkeypatch.setattr(ToolRegistry, "_sync_api_service_states", classmethod(lambda cls: None))

    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        with _plugin_refresh_held_open(monkeypatch):
            finished, got = _on_thread(lambda: ToolRegistry.get(previous.info.name))
            assert finished and got is previous
            assert synced == []
            assert ToolRegistry._config_state_token == ("before",)

        # The first read after the reload applies the changed config.
        ToolRegistry.get(previous.info.name)
        assert synced == ["settings"]
        assert ToolRegistry._config_state_token == ("after",)


def _custom_tool(name: str, handler, provider: str | None = None) -> Tool:
    return Tool(
        info=ToolInfo(
            name=name,
            description=name,
            category=ToolCategory.CUSTOM,
            source="plugin_py",
            provider=provider,
        ),
        handler=handler,
    )


def _execute_across_a_held_reload(held, tool_name: str, on_hold=None):
    """Start executing *tool_name* while a reload is held, then let it finish.

    Returns (event-loop ticks while held, whether the call finished while held, result).
    """
    async def scenario():
        ticks = 0
        stop = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not stop.is_set():
                await asyncio.sleep(0.01)
                ticks += 1

        ticking = asyncio.create_task(ticker())
        call = asyncio.create_task(ToolRegistry.execute(tool_name))
        await asyncio.sleep(0.3)
        observed = (ticks, call.done())
        if on_hold is not None:
            on_hold()
        held.release()
        result = await asyncio.wait_for(call, timeout=5)
        stop.set()
        await ticking
        return (*observed, result)

    return asyncio.run(scenario())


def test_execute_waits_out_a_reload_and_obeys_config_written_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reads skip the config sync during a reload; execution must not.

    A call made during a reload waits for it (the event loop keeps running),
    then decides on the registry with the current config applied: a tool
    switched off meanwhile by another worker does not run. That includes a
    tool off by default whose "on" override was removed, and a tool whose
    API service was switched off.
    """
    runs: list[str] = []

    async def handler(_ctx, **_kwargs) -> ToolResult:
        runs.append("ran")
        return ToolResult(success=True, output="ran")

    token = ["before"]
    settings: dict = {"default_off_tool": {"enabled": True}}
    services: dict = {"guarded_api": {"enabled": True}}
    monkeypatch.setattr(ToolRegistry, "_current_config_state_token", classmethod(lambda cls: (token[0],)))
    monkeypatch.setattr(ConfigWriter, "list_tool_settings", lambda: settings)
    monkeypatch.setattr(ConfigWriter, "list_api_services_raw", lambda: services)

    with _isolated_registry():
        plain = _custom_tool("guarded_tool", handler)
        api = _custom_tool("guarded_api_tool", handler, provider="guarded_api")
        default_off = _custom_tool("default_off_tool", handler)
        for tool in (plain, api, default_off):
            ToolRegistry._tools[tool.info.name] = tool
            ToolRegistry._enabled_defaults[tool.info.name] = True
        ToolRegistry._enabled_defaults[default_off.info.name] = False
        ToolRegistry._plugin_tool_names = [plain.info.name, api.info.name, default_off.info.name]
        monkeypatch.setattr(ToolRegistry, "_config_state_token", ("before",))

        cases = [
            (plain.info.name, None, True),
            (plain.info.name, lambda: settings.__setitem__(plain.info.name, {"enabled": False}), False),
            (api.info.name, lambda: services.__setitem__("guarded_api", {"enabled": False}), False),
            (default_off.info.name, lambda: settings.pop(default_off.info.name), False),
        ]
        for name, change, should_run in cases:
            with _plugin_refresh_held_open(monkeypatch) as held:
                on_hold = None
                if change is not None:
                    def on_hold(change=change, name=name):
                        token[0] = f"after: {name}"
                        change()
                ticks, finished_during_reload, result = _execute_across_a_held_reload(held, name, on_hold)
            assert ticks >= 10, f"{name}: the event loop stood still during the reload"
            assert finished_during_reload is False, f"{name}: ran from the pre-reload copy"
            assert result.success is should_run, (name, result.error)
            if not should_run:
                assert result.error == f"Tool is disabled: {name}"

    assert runs == ["ran"]


def test_auto_disable_after_repeated_failures_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    """The third identical failure switches the tool off under the registry
    lock; while a reload holds it, that wait must not stop the event loop,
    and the reload's rollback must not switch the tool back on."""
    error = "synthetic repeated failure"

    async def handler(_ctx, **_kwargs) -> ToolResult:
        return ToolResult(success=False, error=error)

    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    persisted_on: list[threading.Thread] = []
    monkeypatch.setattr(
        ConfigWriter,
        "set_tool_setting",
        lambda _name, _setting: persisted_on.append(threading.current_thread()),
    )

    with _isolated_registry():
        tool = _custom_tool("flaky_tool", handler)
        ToolRegistry._tools[tool.info.name] = tool
        ToolRegistry._plugin_tool_names = [tool.info.name]
        monkeypatch.setattr(ToolRegistry, "_failure_state", {
            tool.info.name: {
                "key": ToolRegistry._failure_key(tool.info.name, {"query": "same"}, error),
                "count": ToolRegistry._failure_disable_threshold - 1,
            },
        })

        with _plugin_refresh_held_open(monkeypatch) as held:
            async def scenario():
                ticks = 0
                stop = asyncio.Event()

                async def ticker():
                    nonlocal ticks
                    while not stop.is_set():
                        await asyncio.sleep(0.01)
                        ticks += 1

                ticking = asyncio.create_task(ticker())
                call = asyncio.create_task(ToolRegistry.execute(tool.info.name, query="same"))
                await asyncio.sleep(0.3)
                observed = (ticks, call.done())
                held.release()
                result = await asyncio.wait_for(call, timeout=5)
                stop.set()
                await ticking
                return observed, result

            (ticks, done_during_reload), result = asyncio.run(scenario())

    assert ticks >= 10, "the event loop stood still while the disable waited for the reload"
    assert done_during_reload is False
    assert result.metadata == {"disabled": True, "disabled_reason": "repeated_error"}
    assert tool.info.enabled is False
    # Written on the event loop's thread, in line with the other config
    # writers there; from a worker thread it could overwrite a config they
    # saved in between (lost update).
    assert persisted_on == [threading.current_thread()]


def test_failure_bookkeeping_waits_for_a_held_registry_lock_without_blocking_the_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    """A reload that starts while a call runs: the failure bookkeeping after it
    waits for the registry lock asynchronously, on the loop's own thread."""
    error = "synthetic repeated failure"
    finish = asyncio.Event()

    async def handler(_ctx, **_kwargs) -> ToolResult:
        await finish.wait()
        return ToolResult(success=False, error=error)

    monkeypatch.setattr(Config, "_cached_config", ConfigInfo())
    persisted_on: list[threading.Thread] = []
    monkeypatch.setattr(
        ConfigWriter,
        "set_tool_setting",
        lambda _name, _setting: persisted_on.append(threading.current_thread()),
    )

    with _isolated_registry():
        tool = _custom_tool("flaky_tool", handler)
        ToolRegistry._tools[tool.info.name] = tool
        monkeypatch.setattr(ToolRegistry, "_failure_state", {
            tool.info.name: {
                "key": ToolRegistry._failure_key(tool.info.name, {"query": "same"}, error),
                "count": ToolRegistry._failure_disable_threshold - 1,
            },
        })
        held = threading.Event()
        release = threading.Event()

        def hold_registry_lock():
            with ToolRegistry._refresh_lock:
                held.set()
                release.wait(timeout=2)

        async def scenario():
            ticks = 0
            stop = asyncio.Event()

            async def ticker():
                nonlocal ticks
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                    ticks += 1

            call = asyncio.create_task(ToolRegistry.execute(tool.info.name, query="same"))
            await asyncio.sleep(0.05)  # the call is inside the tool now
            holder = threading.Thread(target=hold_registry_lock, daemon=True)
            holder.start()
            while not held.is_set():
                await asyncio.sleep(0.01)
            ticking = asyncio.create_task(ticker())
            finish.set()
            await asyncio.sleep(0.3)
            observed = (ticks, call.done())
            release.set()
            result = await asyncio.wait_for(call, timeout=5)
            stop.set()
            await ticking
            holder.join(timeout=2)
            return observed, result

        (ticks, done_while_held), result = asyncio.run(scenario())

    assert ticks >= 10, "the event loop stood still while the bookkeeping waited for the lock"
    assert done_while_held is False
    assert result.metadata == {"disabled": True, "disabled_reason": "repeated_error"}
    assert persisted_on == [threading.current_thread()]


def test_first_execute_during_background_init_does_not_deadlock(monkeypatch: pytest.MonkeyPatch):
    """init() holds _init_lock and takes _refresh_lock per tool; a call that
    took _refresh_lock first and then initialized would wait the other way."""
    async def handler(_ctx, **_kwargs) -> ToolResult:
        return ToolResult(success=True, output="ok")

    tool = _custom_tool("cold_tool", handler)
    init_holding = threading.Event()

    def slow_builtins(cls) -> None:
        init_holding.set()
        threading.Event().wait(0.3)  # the first call arrives meanwhile
        cls.register(tool)

    monkeypatch.setattr(ToolRegistry, "_register_builtin_tools", classmethod(slow_builtins))
    monkeypatch.setattr(ToolRegistry, "_register_dynamic_tools", classmethod(lambda cls, errors=None: None))
    monkeypatch.setattr(ToolRegistry, "_register_plugin_extension_point", classmethod(lambda cls: None))
    monkeypatch.setattr(ToolRegistry, "_load_plugin_tools", classmethod(lambda cls, errors=None: []))
    monkeypatch.setattr(ToolRegistry, "_plugin_sources_fingerprint", classmethod(lambda cls: ()))

    with _isolated_registry():
        ToolRegistry._initialized = False
        init_thread = threading.Thread(target=ToolRegistry.init, daemon=True)
        init_thread.start()
        assert init_holding.wait(timeout=2)
        results: list[ToolResult] = []
        caller = threading.Thread(
            target=lambda: results.append(asyncio.run(ToolRegistry.execute(tool.info.name))),
            daemon=True,
        )
        caller.start()
        caller.join(timeout=5)
        init_thread.join(timeout=5)
        assert not caller.is_alive() and not init_thread.is_alive(), (
            "the first call deadlocked with the background init"
        )

    assert [result.success for result in results] == [True]


def test_registry_write_lock_waits_for_a_reload_without_blocking_the_loop(
    monkeypatch: pytest.MonkeyPatch,
):
    with _isolated_registry():
        previous = _tool("previous_plugin", "plugin_py")
        ToolRegistry._tools[previous.info.name] = previous
        ToolRegistry._plugin_tool_names = [previous.info.name]

        with _plugin_refresh_held_open(monkeypatch) as held:
            async def scenario():
                ticks = 0
                entered = asyncio.Event()

                async def writer():
                    async with ToolRegistry.write_lock():
                        entered.set()
                        assert ToolRegistry._reload_view is None  # the reload is over

                async def ticker():
                    nonlocal ticks
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                        ticks += 1

                write = asyncio.create_task(writer())
                ticking = asyncio.create_task(ticker())
                await asyncio.sleep(0.3)
                observed = (ticks, entered.is_set())
                held.release()
                await asyncio.wait_for(write, timeout=5)
                await ticking
                return observed

            ticks, entered_during_reload = asyncio.run(scenario())

    assert ticks >= 10
    assert entered_during_reload is False


def test_file_watcher_reloads_off_the_event_loop(monkeypatch: pytest.MonkeyPatch):
    """A plugin file change used to run the whole reload on the event loop."""
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()
    ran_on: list[threading.Thread] = []
    try:
        watcher = ToolFileWatcher()
        # How the watcher found the loop before (start() captured it).
        watcher._loop = loop
        monkeypatch.setattr(watcher, "_run_refresh", lambda: ran_on.append(threading.current_thread()))
        timer_thread = threading.Thread(target=watcher._do_refresh)
        timer_thread.start()
        timer_thread.join(timeout=2)
        for _ in range(100):
            if ran_on:
                break
            threading.Event().wait(0.01)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

    assert ran_on == [timer_thread]


def test_plugin_fingerprint_follows_installed_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """A newly installed package may bring a flocks.plugins entry point."""
    site_packages = tmp_path / "lib" / "site-packages"
    site_packages.mkdir(parents=True)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setattr(ToolRegistry, "_plugin_source_roots", classmethod(lambda cls: [str(plugins)]))
    monkeypatch.setattr(sys, "path", [str(site_packages)])

    before = ToolRegistry._plugin_sources_fingerprint()
    assert ToolRegistry._plugin_sources_fingerprint() == before
    (site_packages / "acme_flocks_plugin-1.0.dist-info").mkdir()
    assert ToolRegistry._plugin_sources_fingerprint() != before


def _counting_plugin_load(monkeypatch: pytest.MonkeyPatch, errors_to_report: list[str]) -> list[int]:
    loads: list[int] = []

    def _load(cls, errors):
        loads.append(1)
        errors.extend(errors_to_report)

    monkeypatch.setattr(ToolRegistry, "_load_plugin_tools", classmethod(_load))
    return loads


def test_refresh_if_changed_reloads_only_when_a_plugin_file_changed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    root = tmp_path / "plugins"
    tool_dir = root / "tools" / "device" / "acme"
    tool_dir.mkdir(parents=True)
    (tool_dir / "acme_query.yaml").write_text("name: acme_query\n", encoding="utf-8")
    (tool_dir / "_provider.yaml").write_text("name: acme\n", encoding="utf-8")
    monkeypatch.setattr(ToolRegistry, "_plugin_source_roots", classmethod(lambda cls: [str(root)]))
    monkeypatch.setattr(ToolRegistry, "_plugin_sources_stamp", None)
    loads = _counting_plugin_load(monkeypatch, [])

    with _isolated_registry():
        assert ToolRegistry.refresh_plugin_tools_if_changed() is True  # nothing on record yet
        assert ToolRegistry.refresh_plugin_tools_if_changed() is False
        assert len(loads) == 1

        # Bytecode caches are not sources.
        (tool_dir / "__pycache__").mkdir()
        (tool_dir / "__pycache__" / "acme.cpython-312.pyc").write_bytes(b"cache")
        assert ToolRegistry.refresh_plugin_tools_if_changed() is False

        (tool_dir / "acme_query.yaml").write_text("name: acme_query\ndescription: changed\n", encoding="utf-8")
        assert ToolRegistry.refresh_plugin_tools_if_changed() is True
        assert ToolRegistry.refresh_plugin_tools_if_changed() is False

        (tool_dir / "acme_ops.yaml").write_text("name: acme_ops\n", encoding="utf-8")
        assert ToolRegistry.refresh_plugin_tools_if_changed() is True

        (tool_dir / "acme_ops.yaml").unlink()
        assert ToolRegistry.refresh_plugin_tools_if_changed() is True

        # A forced reload records the files it read, too.
        (tool_dir / "acme_query.yaml").write_text("name: acme_query\n", encoding="utf-8")
        ToolRegistry.refresh_plugin_tools()
        assert ToolRegistry.refresh_plugin_tools_if_changed() is False

    assert len(loads) == 5


def test_refresh_if_changed_retries_a_load_that_reported_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    root = tmp_path / "plugins"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "broken.yaml").write_text("name: [\n", encoding="utf-8")
    monkeypatch.setattr(ToolRegistry, "_plugin_source_roots", classmethod(lambda cls: [str(root)]))
    monkeypatch.setattr(ToolRegistry, "_plugin_sources_stamp", None)

    with _isolated_registry():
        # A failed reload: rolled back, never treated as current.
        loads = _counting_plugin_load(monkeypatch, ["plugin loader: unavailable"])
        with pytest.raises(ToolRefreshError):
            ToolRegistry.refresh_plugin_tools_if_changed()
        with pytest.raises(ToolRefreshError):
            ToolRegistry.refresh_plugin_tools_if_changed()
        assert len(loads) == 2
        assert ToolRegistry._plugin_sources_stamp is None

        # A partial reload (errors outside the changed path) is kept, but not recorded as clean.
        outside = str((root / "tools" / "broken.yaml").resolve())
        loads = _counting_plugin_load(monkeypatch, [f"{outside}: YAMLError: bad"])
        ToolRegistry.refresh_plugin_tools(changed_path=tmp_path / "elsewhere")
        assert len(loads) == 1
        assert ToolRegistry._plugin_sources_stamp is None

        # Nothing changed on disk, yet the next ask reloads (whatever failed may be
        # fixed now); once the same files load cleanly they are current.
        loads = _counting_plugin_load(monkeypatch, [])
        assert ToolRegistry.refresh_plugin_tools_if_changed() is True
        assert ToolRegistry.refresh_plugin_tools_if_changed() is False
        assert len(loads) == 1


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
