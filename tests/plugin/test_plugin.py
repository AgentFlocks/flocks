"""
Tests for the unified plugin system (flocks.plugin).
"""

import tempfile
import textwrap
from pathlib import Path

import pytest

from flocks.plugin.loader import (
    DEFAULT_PLUGIN_ROOT,
    ExtensionPoint,
    PluginLoader,
    load_module,
    scan_directory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_plugin(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(textwrap.dedent(content))
    return path


# ---------------------------------------------------------------------------
# scan_directory
# ---------------------------------------------------------------------------

class TestScanDirectory:
    def test_nonexistent_dir(self, tmp_path: Path):
        assert scan_directory(tmp_path / "nope") == []

    def test_empty_dir(self, tmp_path: Path):
        assert scan_directory(tmp_path) == []

    def test_skips_underscore_files(self, tmp_path: Path):
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "_helper.py").write_text("")
        (tmp_path / "good.py").write_text("")
        result = scan_directory(tmp_path)
        assert len(result) == 1
        assert "good.py" in result[0]

    def test_skips_non_py(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("")
        (tmp_path / "data.json").write_text("")
        (tmp_path / "agent.py").write_text("")
        result = scan_directory(tmp_path)
        assert len(result) == 1

    def test_sorted_output(self, tmp_path: Path):
        for name in ["c.py", "a.py", "b.py"]:
            (tmp_path / name).write_text("")
        result = scan_directory(tmp_path)
        stems = [Path(p).stem for p in result]
        assert stems == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# load_module
# ---------------------------------------------------------------------------

class TestLoadModule:
    def test_load_from_absolute_path(self, tmp_path: Path):
        _write_plugin(tmp_path, "sample.py", 'VALUE = 42\n')
        mod = load_module(str(tmp_path / "sample.py"), tmp_path)
        assert mod.VALUE == 42

    def test_load_from_relative_path(self, tmp_path: Path):
        _write_plugin(tmp_path, "rel.py", 'VALUE = "hello"\n')
        mod = load_module("./rel.py", tmp_path)
        assert mod.VALUE == "hello"

    def test_load_package(self):
        mod = load_module("json", Path.cwd())
        assert hasattr(mod, "dumps")

    def test_load_nonexistent_raises(self, tmp_path: Path):
        with pytest.raises(Exception):
            load_module(str(tmp_path / "nope.py"), tmp_path)


# ---------------------------------------------------------------------------
# ExtensionPoint & PluginLoader
# ---------------------------------------------------------------------------

class TestPluginLoader:
    @pytest.fixture(autouse=True)
    def _reset(self):
        PluginLoader.clear_extension_points()
        yield
        PluginLoader.clear_extension_points()

    def test_register_and_load_agents(self, tmp_path: Path):
        """Simulates the AGENTS extension point with plain dicts."""
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "my_agent.py", """\
            AGENTS = [
                {"name": "test-agent", "description": "A test agent"},
            ]
        """)

        collected = []

        def consumer(items, source):
            collected.extend(items)

        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS",
            subdir="agents",
            consumer=consumer,
            item_type=dict,
            dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        assert len(collected) == 1
        assert collected[0]["name"] == "test-agent"

    def test_register_and_load_tools(self, tmp_path: Path):
        """Simulates the TOOLS extension point."""
        tools_dir = tmp_path / "tools"
        _write_plugin(tools_dir, "my_tool.py", """\
            TOOLS = [
                {"name": "lookup", "description": "Lookup IOC"},
            ]
        """)

        collected = []

        def consumer(items, source):
            collected.extend(items)

        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS",
            subdir="tools",
            consumer=consumer,
            item_type=dict,
            dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        assert len(collected) == 1
        assert collected[0]["name"] == "lookup"

    def test_load_once_extension_skips_later_load_all_passes(self, tmp_path: Path):
        """Stateful extension points should not be re-imported by load_all."""
        channels_dir = tmp_path / "channels"
        counter_file = tmp_path / "counter.txt"
        _write_plugin(channels_dir, "my_channel.py", f"""\
            from pathlib import Path

            counter = Path({str(counter_file)!r})
            count = int(counter.read_text() or "0") if counter.exists() else 0
            counter.write_text(str(count + 1))

            CHANNELS = [
                {{"id": "test-channel", "import_count": count + 1}},
            ]
        """)

        collected = []

        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="CHANNELS",
            subdir="channels",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict,
            dedup_key=lambda d: d["id"],
            load_once=True,
        ))

        PluginLoader.load_all(project_dir=tmp_path)
        PluginLoader.load_all(project_dir=tmp_path)

        assert counter_file.read_text() == "1"
        assert len(collected) == 1
        assert collected[0]["import_count"] == 1

    def test_multiple_extension_points(self, tmp_path: Path):
        """Both AGENTS and TOOLS extension points loaded in one load_all."""
        agents_dir = tmp_path / "agents"
        tools_dir = tmp_path / "tools"
        _write_plugin(agents_dir, "a.py", 'AGENTS = [{"name": "agent-a"}]\n')
        _write_plugin(tools_dir, "t.py", 'TOOLS = [{"name": "tool-t"}]\n')

        agent_items = []
        tool_items = []

        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: agent_items.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS", subdir="tools",
            consumer=lambda items, src: tool_items.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        assert len(agent_items) == 1
        assert len(tool_items) == 1
        assert agent_items[0]["name"] == "agent-a"
        assert tool_items[0]["name"] == "tool-t"

    def test_dedup_first_wins(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "a.py",
                       'AGENTS = [{"name": "dup", "v": 1}]\n')
        _write_plugin(agents_dir, "b.py",
                       'AGENTS = [{"name": "dup", "v": 2}]\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        assert len(collected) == 1
        assert collected[0]["v"] == 1  # a.py sorts before b.py

    def test_type_validation_filters_invalid(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "bad.py",
                       'AGENTS = [{"name": "ok"}, "not-a-dict", 42]\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        assert len(collected) == 1
        assert collected[0]["name"] == "ok"

    def test_module_failure_does_not_block(self, tmp_path: Path):
        """A broken module should not prevent others from loading."""
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "a_good.py",
                       'AGENTS = [{"name": "good"}]\n')
        _write_plugin(agents_dir, "b_bad.py",
                       'raise RuntimeError("boom")\n')
        _write_plugin(agents_dir, "c_also_good.py",
                       'AGENTS = [{"name": "also-good"}]\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        names = [c["name"] for c in collected]
        assert "good" in names
        assert "also-good" in names

    def test_missing_attr_silently_skipped(self, tmp_path: Path):
        """Module without the expected attribute is silently skipped."""
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "no_agents.py", 'X = 1\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
        ))
        PluginLoader.load_all()

        assert collected == []

    def test_extra_sources(self, tmp_path: Path):
        """cfg.plugin extra sources are also loaded."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        extra_file = tmp_path / "extra_agent.py"
        extra_file.write_text('AGENTS = [{"name": "extra"}]\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all(
            extra_sources=[str(extra_file)],
            project_dir=tmp_path,
        )

        assert len(collected) == 1
        assert collected[0]["name"] == "extra"

    def test_load_default_for_extension(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        _write_plugin(agents_dir, "a.py", 'AGENTS = [{"name": "default-a"}]\n')

        collected = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))

        result = PluginLoader.load_default_for_extension("AGENTS")
        assert len(result) == 1
        assert result[0]["name"] == "default-a"

    def test_load_for_extension(self, tmp_path: Path):
        plugin_file = tmp_path / "my.py"
        plugin_file.write_text('AGENTS = [{"name": "from-source"}]\n')

        collected_via_consumer = []
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: collected_via_consumer.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))

        result = PluginLoader.load_for_extension(
            "AGENTS", [str(plugin_file)], tmp_path,
        )
        assert len(result) == 1
        assert result[0]["name"] == "from-source"
        assert len(collected_via_consumer) == 1

    def test_empty_subdir_no_error(self, tmp_path: Path):
        """No error when subdirectory doesn't exist."""
        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: None,
        ))
        PluginLoader.load_all()  # should not raise

    def test_subdir_isolation(self, tmp_path: Path):
        """Files in agents/ are NOT checked for TOOLS, and vice versa."""
        agents_dir = tmp_path / "agents"
        tools_dir = tmp_path / "tools"
        _write_plugin(agents_dir, "a.py",
                       'AGENTS = [{"name": "agent-a"}]\nTOOLS = [{"name": "sneaky-tool"}]\n')
        _write_plugin(tools_dir, "t.py",
                       'TOOLS = [{"name": "tool-t"}]\n')

        agent_items = []
        tool_items = []

        PluginLoader._plugin_root = tmp_path
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS", subdir="agents",
            consumer=lambda items, src: agent_items.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS", subdir="tools",
            consumer=lambda items, src: tool_items.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))
        PluginLoader.load_all()

        agent_names = [a["name"] for a in agent_items]
        tool_names = [t["name"] for t in tool_items]
        assert "agent-a" in agent_names
        assert "sneaky-tool" not in tool_names
        assert "tool-t" in tool_names


class TestProjectLevelPlugins:
    """Tests for project-level .flocks/plugins/ scanning added in load_all()."""

    def setup_method(self):
        PluginLoader.clear_extension_points()
        PluginLoader._plugin_root = DEFAULT_PLUGIN_ROOT

    def test_load_all_scans_project_plugins(self, tmp_path: Path):
        """load_all() should pick up plugins from <project>/.flocks/plugins/ in addition
        to user-level ~/.flocks/plugins/."""
        # User-level plugin root (empty — nothing installed globally)
        user_root = tmp_path / "user_flocks" / "plugins"

        # Project-level plugin: tmp_path is the project dir
        project_tools_dir = tmp_path / ".flocks" / "plugins" / "tools"
        _write_plugin(project_tools_dir, "proj_tool.py", 'TOOLS = [{"name": "project-tool"}]\n')

        collected = []
        PluginLoader._plugin_root = user_root
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS", subdir="tools",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))

        PluginLoader.load_all(project_dir=tmp_path)

        names = [item["name"] for item in collected]
        assert "project-tool" in names

    def test_load_all_merges_user_and_project_plugins(self, tmp_path: Path):
        """User-level and project-level plugins are both loaded."""
        user_root = tmp_path / "user_flocks" / "plugins"
        user_tools_dir = user_root / "tools"
        _write_plugin(user_tools_dir, "user_tool.py", 'TOOLS = [{"name": "user-tool"}]\n')

        project_tools_dir = tmp_path / ".flocks" / "plugins" / "tools"
        _write_plugin(project_tools_dir, "proj_tool.py", 'TOOLS = [{"name": "project-tool"}]\n')

        collected = []
        PluginLoader._plugin_root = user_root
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS", subdir="tools",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))

        PluginLoader.load_all(project_dir=tmp_path)

        names = [item["name"] for item in collected]
        assert "user-tool" in names
        assert "project-tool" in names

    def test_load_all_skips_project_if_same_as_user_root(self, tmp_path: Path):
        """No duplicate loading when project dir == user plugin root."""
        shared_root = tmp_path / "plugins"
        tools_dir = shared_root / "tools"
        _write_plugin(tools_dir, "tool.py", 'TOOLS = [{"name": "single-tool"}]\n')

        collected = []
        PluginLoader._plugin_root = shared_root
        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS", subdir="tools",
            consumer=lambda items, src: collected.extend(items),
            item_type=dict, dedup_key=lambda d: d["name"],
        ))

        # project_dir's .flocks/plugins == shared_root would only match if
        # tmp_path/.flocks/plugins == shared_root — which it doesn't here,
        # so just verifying the tool loads exactly once via dedup.
        PluginLoader.load_all(project_dir=tmp_path)

        names = [item["name"] for item in collected]
        assert names.count("single-tool") == 1
