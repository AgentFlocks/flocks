"""``PluginLoader.load_extension`` must report source-level load errors to its caller.

Regression for the 2026-09 full test pass: broken YAML / Python plugin files were only
logged, so ``ToolRegistry.refresh_plugin_tools`` (and ``POST /api/tools/refresh``) kept
reporting success with ``errors=[]`` and its partial/fatal branches were dead code.
"""

from __future__ import annotations

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
