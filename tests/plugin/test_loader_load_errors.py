"""``PluginLoader.load_extension`` must report source-level load errors to its caller.

Regression for the 2026-09 full test pass: broken YAML / Python plugin files were only
logged, so ``ToolRegistry.refresh_plugin_tools`` (and ``POST /api/tools/refresh``) kept
reporting success with ``errors=[]`` and its partial/fatal branches were dead code.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from flocks.plugin.loader import ExtensionPoint, PluginLoader


def _write(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(PluginLoader, "_extension_points", {})
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user")
    monkeypatch.setattr(PluginLoader, "_last_load_errors", [])
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", False)


def _register_tools(collected: list, *, factory=None, load_once: bool = False) -> None:
    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="TOOLS",
        subdir="tools",
        consumer=lambda items, _source: collected.extend(items),
        item_type=dict,
        dedup_key=lambda item: item["name"],
        yaml_item_factory=factory,
        load_once=load_once,
    ))


def test_clean_sources_report_no_errors(tmp_path: Path) -> None:
    collected: list = []
    _register_tools(collected, factory=lambda raw, _path: raw)
    _write(tmp_path / "user" / "tools", "good.py", 'TOOLS = [{"name": "py-tool"}]\n')
    _write(tmp_path / "user" / "tools", "good.yaml", "name: yaml-tool\n")

    errors = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")

    assert errors == []
    assert sorted(item["name"] for item in collected) == ["py-tool", "yaml-tool"]


def test_yaml_parse_failure_is_reported_with_absolute_path(tmp_path: Path) -> None:
    collected: list = []
    _register_tools(collected, factory=lambda raw, _path: raw)
    bad = _write(tmp_path / "user" / "tools", "bad.yaml", "name: x\nhandler: [\n")
    _write(tmp_path / "user" / "tools", "good.yaml", "name: yaml-tool\n")

    errors = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")

    assert len(errors) == 1
    source_text, separator, detail = errors[0].partition(": ")
    assert separator == ": "
    assert Path(source_text).is_absolute()
    assert Path(source_text) == bad.resolve()
    assert detail
    assert [item["name"] for item in collected] == ["yaml-tool"], "valid siblings still load"


def test_yaml_non_mapping_and_factory_failures_are_reported(tmp_path: Path) -> None:
    collected: list = []

    def _factory(raw, _path):
        if raw.get("name") == "explode":
            raise ValueError("factory rejected tool")
        return raw

    _register_tools(collected, factory=_factory)
    listy = _write(tmp_path / "user" / "tools", "list.yaml", "- 1\n- 2\n")
    boom = _write(tmp_path / "user" / "tools", "boom.yaml", "name: explode\n")

    errors = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")

    assert len(errors) == 2
    by_source = {Path(e.partition(": ")[0]): e.partition(": ")[2] for e in errors}
    assert by_source[listy.resolve()] == "Expected a YAML mapping"
    assert "factory rejected tool" in by_source[boom.resolve()]
    assert collected == []


def test_python_import_failure_reports_absolute_path_and_type(tmp_path: Path) -> None:
    collected: list = []
    _register_tools(collected)
    broken = _write(tmp_path / "user" / "tools", "broken.py", "TOOLS = [\n")

    errors = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")

    assert len(errors) == 1
    assert errors[0].startswith(f"{broken.resolve()}: SyntaxError: ")


def test_project_level_relative_extra_source_is_reported_absolute(tmp_path: Path) -> None:
    collected: list = []
    _register_tools(collected)
    project = tmp_path / "proj"
    broken = _write(project, "extra_tools.py", "TOOLS = [\n")

    errors = PluginLoader.load_extension(
        "TOOLS",
        extra_sources=["./extra_tools.py"],
        project_dir=project,
    )

    assert len(errors) == 1
    assert errors[0].startswith(f"{broken.resolve()}: ")


def test_unknown_extension_point_is_reported_as_fatal_prefix() -> None:
    assert PluginLoader.load_extension("NOPE") == ["extension point not found: NOPE"]


def test_load_once_skip_does_not_replay_previous_errors(tmp_path: Path) -> None:
    collected: list = []
    _register_tools(collected, load_once=True)
    _write(tmp_path / "user" / "tools", "broken.py", "TOOLS = [\n")

    first = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")
    second = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj")

    assert len(first) == 1
    assert second == []


def test_entry_point_failures_are_reported_as_non_fatal_entries(tmp_path: Path, monkeypatch) -> None:
    collected: list = []
    _register_tools(collected)

    class _BrokenEntryPoint:
        name = "broken-plugin"

        def load(self):
            raise RuntimeError("cannot import plugin")

    class _EntryPoints:
        def select(self, *, group):
            return [_BrokenEntryPoint()] if group == "flocks.plugins" else []

    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", lambda: _EntryPoints())

    errors = PluginLoader.load_extension("TOOLS", project_dir=tmp_path / "proj", load_entry_points=True)

    assert errors == ["entry point broken-plugin: RuntimeError: cannot import plugin"]


def test_registry_refresh_reports_broken_yaml_instead_of_success(tmp_path: Path, monkeypatch) -> None:
    """End to end through ToolRegistry: a broken YAML tool file must surface as a refresh error."""
    from flocks.tool import registry as registry_mod

    registry_cls = registry_mod.ToolRegistry
    # Restoring loader points alone would leave this fresh registry initialized
    # after its TOOLS point had been removed. Restore both sides of the pair.
    for name, value in {
        "_initialized": False,
        "_tools": {},
        "_enabled_defaults": {},
        "_plugin_tool_names": [],
        "_plugin_load_errors": [],
        "_revision": 0,
        "_dynamic_modules": {},
        "_dynamic_tools_by_module": {},
        "_config_state_token": None,
        "_failure_state": {},
    }.items():
        monkeypatch.setattr(registry_cls, name, value)
    monkeypatch.setattr(registry_cls, "_register_builtin_tools", classmethod(lambda cls: None))
    monkeypatch.setattr(registry_cls, "_register_dynamic_tools", classmethod(lambda cls: None))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    registry_cls.init()
    bad = _write(tmp_path / "user" / "tools", "bad.yaml", "name: x\nhandler: [\n")

    with pytest.raises(registry_mod.ToolRefreshError) as excinfo:
        registry_cls.refresh_plugin_tools()
    assert any(str(bad.resolve()) in error for error in excinfo.value.errors)


@pytest.mark.parametrize("extension", ["py", "yaml"])
def test_consumer_failure_does_not_skip_siblings_or_security_entrypoints(
    tmp_path: Path, monkeypatch, extension: str,
) -> None:
    accepted: list[str] = []
    security_loaded: list[bool] = []

    def consume(items, _source):
        if items[0]["name"] == "broken":
            raise ValueError("consumer rejected plugin")
        accepted.extend(item["name"] for item in items)

    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="TOOLS", subdir="tools", consumer=consume,
        yaml_item_factory=lambda raw, _path: raw,
    ))
    for filename, name in (("a_broken", "broken"), ("b_good", "good")):
        content = f'TOOLS = [{{"name": "{name}"}}]' if extension == "py" else f"name: {name}"
        _write(tmp_path / "user" / "tools", f"{filename}.{extension}", content)

    class EntryPoint:
        name = "security-plugin"

        def load(self):
            return lambda: security_loaded.append(True)

    class EntryPoints:
        def select(self, *, group):
            return [EntryPoint()] if group == "flocks.plugins.critical" else []

    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", EntryPoints)

    result = PluginLoader.load_all(project_dir=tmp_path / "project")

    assert accepted == ["good"]
    assert security_loaded == [True]
    assert not result.has_critical_entrypoint_failure
    assert any("consumer rejected plugin" in error for error in PluginLoader._last_load_errors)


@pytest.mark.parametrize("scoped", [False, True])
def test_aborted_load_keeps_execution_blocked_until_successful_retry(
    tmp_path: Path, monkeypatch, scoped: bool,
) -> None:
    """With a fail-closed plugin installed, an interrupted pass cannot prove its
    guards are in place; only a completed retry clears the signal."""
    _register_tools([])
    monkeypatch.setattr(PluginLoader, "critical_entrypoints_declared", classmethod(lambda cls: True))
    original = PluginLoader._load_extension_point

    def unavailable(*_args, **_kwargs):
        raise OSError("plugin directory unavailable")

    def load():
        if scoped:
            return PluginLoader.load_extension("TOOLS", project_dir=tmp_path, load_entry_points=True)
        return PluginLoader.load_all(project_dir=tmp_path)

    monkeypatch.setattr(PluginLoader, "_load_extension_point", unavailable)
    with pytest.raises(OSError, match="plugin directory unavailable"):
        load()
    assert PluginLoader.has_runtime_critical_entrypoint_failure()

    monkeypatch.setattr(PluginLoader, "_load_extension_point", original)
    # No installed plugins, but group lookup still follows the metadata contract.
    class EmptyEntryPoints:
        def select(self, *, group):
            return []

    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", EmptyEntryPoints)
    load()
    assert not PluginLoader.has_runtime_critical_entrypoint_failure()


class _NoEntryPoints:
    def select(self, *, group):
        return []


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("previously_failed", [False, True])
def test_aborted_load_without_critical_plugins_keeps_the_last_verdict(
    tmp_path: Path, monkeypatch, scoped: bool, previously_failed: bool,
) -> None:
    """Without a fail-closed plugin there are no required guards to miss. An
    interrupted pass neither raises the signal (503 for every request) nor
    clears a failure found by the last completed pass."""
    _register_tools([])

    def unavailable(*_args, **_kwargs):
        raise OSError("plugin directory unavailable")

    monkeypatch.setattr(PluginLoader, "_load_extension_point", unavailable)
    monkeypatch.setattr(PluginLoader, "critical_entrypoints_declared", classmethod(lambda cls: False))
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", previously_failed)

    with pytest.raises(OSError, match="plugin directory unavailable"):
        if scoped:
            PluginLoader.load_extension("TOOLS", project_dir=tmp_path, load_entry_points=True)
        else:
            PluginLoader.load_all(project_dir=tmp_path)
    assert PluginLoader.has_runtime_critical_entrypoint_failure() is previously_failed


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs directory permissions enforced for the current user",
)
def test_unreadable_tools_directory_does_not_stop_an_oss_server(tmp_path: Path, monkeypatch) -> None:
    """Tool warm-up scans plugins/tools/ together with entrypoints. Without a
    fail-closed plugin, an unreadable sub-directory is a reported load error,
    not a runtime failure that answers every request with 503."""
    from flocks.tool import registry as registry_mod

    registry_cls = registry_mod.ToolRegistry
    for name, value in {
        "_initialized": False,
        "_tools": {},
        "_enabled_defaults": {},
        "_plugin_tool_names": [],
        "_plugin_load_errors": [],
        "_revision": 0,
        "_dynamic_modules": {},
        "_dynamic_tools_by_module": {},
        "_config_state_token": None,
        "_failure_state": {},
    }.items():
        monkeypatch.setattr(registry_cls, name, value)
    monkeypatch.setattr(registry_cls, "_register_builtin_tools", classmethod(lambda cls: None))
    monkeypatch.setattr(registry_cls, "_register_dynamic_tools", classmethod(lambda cls: None))
    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", _NoEntryPoints)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    locked = tmp_path / "user" / "tools" / "locked"
    locked.mkdir(parents=True)
    locked.chmod(0)
    try:
        registry_cls.init()
    finally:
        locked.chmod(0o755)

    assert registry_cls._initialized
    assert any(error.startswith("plugin loader:") for error in registry_cls._plugin_load_errors)
    assert not PluginLoader.has_runtime_critical_entrypoint_failure()


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("previously_failed", [False, True])
def test_running_reload_does_not_toggle_the_failure_signal(
    tmp_path: Path, monkeypatch, scoped: bool, previously_failed: bool,
) -> None:
    """Tool warm-up and Hub installs reload plugins in worker threads while the
    event loop keeps serving under the previously loaded guards. Requests read
    the result of the last finished pass until the reload completes; a failed
    state is kept for the whole reload and cleared only when it succeeds."""
    import threading

    inside, release = threading.Event(), threading.Event()

    def consume(_items, _source):
        inside.set()
        assert release.wait(5)

    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="TOOLS", subdir="tools", consumer=consume,
    ))
    _write(tmp_path / "user" / "tools", "slow.py", 'TOOLS = [{"name": "slow"}]\n')
    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", _NoEntryPoints)
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", previously_failed)

    def load():
        if scoped:
            PluginLoader.load_extension("TOOLS", project_dir=tmp_path, load_entry_points=True)
        else:
            PluginLoader.load_all(project_dir=tmp_path)

    worker = threading.Thread(target=load)
    worker.start()
    try:
        assert inside.wait(5)
        assert PluginLoader.has_runtime_critical_entrypoint_failure() is previously_failed
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not PluginLoader.has_runtime_critical_entrypoint_failure()


def test_critical_entrypoints_declared_reads_only_the_critical_group(monkeypatch) -> None:
    class Declared:
        def select(self, *, group):
            return ["ingress"] if group == "flocks.plugins.critical" else []

    def unreadable():
        raise OSError("metadata unavailable")

    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", Declared)
    assert PluginLoader.critical_entrypoints_declared()
    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", _NoEntryPoints)
    assert not PluginLoader.critical_entrypoints_declared()
    # Metadata that cannot be read could hide a fail-closed plugin.
    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", unreadable)
    assert PluginLoader.critical_entrypoints_declared()


@pytest.mark.parametrize("scoped", [False, True])
def test_explicit_critical_consumer_failure_is_not_downgraded(
    tmp_path: Path, monkeypatch, scoped: bool,
) -> None:
    """An explicit critical failure fails closed even when no installed
    package declared a critical entrypoint."""
    from flocks.plugin.loader import CriticalPluginEntrypointFailure

    def reject(_items, _source):
        raise CriticalPluginEntrypointFailure("security registration failed")

    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="TOOLS", subdir="tools", consumer=reject,
    ))
    _write(tmp_path / "user" / "tools", "critical.py", 'TOOLS = [{"name": "guard"}]')
    monkeypatch.setattr(PluginLoader, "critical_entrypoints_declared", classmethod(lambda cls: False))

    with pytest.raises(CriticalPluginEntrypointFailure, match="security registration failed"):
        if scoped:
            PluginLoader.load_extension("TOOLS", project_dir=tmp_path, load_entry_points=True)
        else:
            PluginLoader.load_all(project_dir=tmp_path)
    assert PluginLoader.has_runtime_critical_entrypoint_failure()


def test_plugin_registering_extension_does_not_abort_package_loading(tmp_path: Path, monkeypatch) -> None:
    loaded: list[str] = []

    def register_extension(_items, _source):
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="NEW_TOOLS", subdir="new-tools", consumer=lambda *_args: None,
        ))

    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="TOOLS", subdir="tools", consumer=register_extension,
    ))
    _write(tmp_path / "user" / "tools", "extension.py", 'TOOLS = [{"name": "extension"}]')

    class EntryPoint:
        name = "security-plugin"

        def load(self):
            return lambda: loaded.append(self.name)

    class EntryPoints:
        def select(self, *, group):
            return [EntryPoint()] if group == "flocks.plugins.critical" else []

    monkeypatch.setattr("flocks.plugin.loader.importlib.metadata.entry_points", EntryPoints)
    result = PluginLoader.load_all(project_dir=tmp_path)

    assert loaded == ["security-plugin"]
    assert not result.has_critical_entrypoint_failure
