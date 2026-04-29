"""
Third-party plugin contract: YAML + Python plugins both register strategies.

Validates that a custom acquirer / author / tracker / curator written by
a third party can be discovered via PluginLoader and routed through
EvolutionEngine without modifying core code.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List

import pytest

from flocks.evolution import EvolutionEngine, NoOpTracker, StrategySpec, UsageTracker
from flocks.evolution.types import SkillScope, UsageRow, UsageState
from flocks.plugin.loader import PluginLoader


def test_python_plugin_registers_via_extension_point(tmp_path):
    """Drop a .py plugin into ~/.flocks/plugins/evolution/tracker/ and load it."""
    plugin_root = tmp_path / "plugins" / "evolution" / "tracker"
    plugin_root.mkdir(parents=True)
    plugin_file = plugin_root / "my_tracker.py"
    plugin_file.write_text(
        textwrap.dedent(
            """
            from flocks.evolution import StrategySpec, UsageTracker
            from flocks.evolution.types import SkillScope, UsageRow, UsageState

            class MyTracker(UsageTracker):
                name = "noisy"
                is_noop = False

                def __init__(self):
                    self.calls = []

                def bump_view(self, n, scope="user"): self.calls.append(("view", n))
                def bump_use(self, n, scope="user"): self.calls.append(("use", n))
                def bump_patch(self, n, scope="user"): self.calls.append(("patch", n))
                def set_state(self, n, s, scope="user"): self.calls.append(("state", n, s))
                def set_pinned(self, n, p, scope="user"): self.calls.append(("pin", n, p))
                def report(self): return []
                def forget(self, n, scope="user"): self.calls.append(("forget", n))

            EVOLUTION_TRACKERS = [StrategySpec(name="noisy", factory=MyTracker)]
            """
        ).strip(),
        encoding="utf-8",
    )

    # Register extension points + bootstrap
    EvolutionEngine.get().bootstrap({"enabled": True})
    PluginLoader._plugin_root = tmp_path / "plugins"
    PluginLoader.load_all()

    # The factory is now registered; rebuild the engine selecting it.
    EvolutionEngine.reset()
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "tracker": {"use": "noisy"}})

    assert engine.tracker.name == "noisy"
    engine.tracker.bump_use("foo")
    assert ("use", "foo") in engine.tracker.calls


def test_yaml_plugin_resolves_class_by_module_path(tmp_path, monkeypatch):
    """A YAML plugin file references an existing class by dotted module path."""
    # Use a sibling python module so the YAML plugin has something to import.
    src_dir = tmp_path / "_pkg"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "stub.py").write_text(
        textwrap.dedent(
            """
            from flocks.evolution import UsageTracker
            class StubTracker(UsageTracker):
                name = "yaml_stub"
                is_noop = False
                def bump_view(self,*a,**kw): pass
                def bump_use(self,*a,**kw): pass
                def bump_patch(self,*a,**kw): pass
                def set_state(self,*a,**kw): pass
                def set_pinned(self,*a,**kw): pass
                def report(self): return []
                def forget(self,*a,**kw): pass
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    plugin_root = tmp_path / "plugins" / "evolution" / "tracker"
    plugin_root.mkdir(parents=True)
    yaml_file = plugin_root / "stub.yaml"
    yaml_file.write_text(
        "name: yaml_stub\nmodule: _pkg.stub\nclass: StubTracker\n",
        encoding="utf-8",
    )

    EvolutionEngine.get().bootstrap({"enabled": True})
    PluginLoader._plugin_root = tmp_path / "plugins"
    PluginLoader.load_all()

    EvolutionEngine.reset()
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "tracker": {"use": "yaml_stub"}})
    assert engine.tracker.name == "yaml_stub"


def test_strategy_with_wrong_base_class_is_rejected(tmp_path):
    """A spec whose factory doesn't subclass the right base must be skipped."""
    plugin_root = tmp_path / "plugins" / "evolution" / "tracker"
    plugin_root.mkdir(parents=True)
    plugin_file = plugin_root / "bad.py"
    plugin_file.write_text(
        textwrap.dedent(
            """
            from flocks.evolution import StrategySpec
            class NotATracker:  # doesn't subclass UsageTracker
                pass
            EVOLUTION_TRACKERS = [StrategySpec(name="bad", factory=NotATracker)]
            """
        ).strip(),
        encoding="utf-8",
    )

    EvolutionEngine.get().bootstrap({"enabled": True})
    PluginLoader._plugin_root = tmp_path / "plugins"
    PluginLoader.load_all()

    assert "bad" not in EvolutionEngine.list_strategies()["tracker"]["registered"]
