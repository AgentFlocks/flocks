"""
PluginLoader integration for the evolution module.

Defines:
  - ``StrategySpec``: the dataclass plugins export to register a strategy.
  - 4 ExtensionPoints (acquirer / author / tracker / curator) that consume
    those specs and forward them to ``EvolutionEngine.register_<layer>()``.

Plugin contract
---------------
A Python plugin module under ``~/.flocks/plugins/evolution/<layer>/`` (or
the same path inside a project's ``.flocks/`` directory) declares one or
more strategies by exporting a list named after the ExtensionPoint:

    # ~/.flocks/plugins/evolution/acquirer/my_acquirer.py
    from flocks.evolution import CapabilityAcquirer, StrategySpec

    class MyAcquirer(CapabilityAcquirer):
        name = "my-acquirer"
        ...

    EVOLUTION_ACQUIRERS = [StrategySpec(name="my-acquirer", factory=MyAcquirer)]

YAML plugins use the same subdirectory layout but reference an installed
class by dotted path:

    # ~/.flocks/plugins/evolution/acquirer/my_acquirer.yaml
    name: my-acquirer
    module: my_pkg.acquirers
    class: MyAcquirer

Both styles end up calling
``EvolutionEngine.register_acquirer(name, factory)``; the engine then
instantiates the chosen factory based on ``evolution.acquirer.use`` in
``flocks.json``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Type

from flocks.evolution.engine import EvolutionEngine
from flocks.evolution.strategies import (
    CapabilityAcquirer,
    Curator,
    SkillAuthor,
    UsageTracker,
)
from flocks.plugin.loader import ExtensionPoint, PluginLoader
from flocks.utils.log import Log

log = Log.create(service="evolution.plugin")


@dataclass
class StrategySpec:
    """Bundle of (name, strategy_class) emitted by a plugin module.

    The class must subclass the appropriate base for its layer; the
    consumer below validates that and routes to the right registry.
    """

    name: str
    factory: Type[Any]


# ---------------------------------------------------------------------------
# YAML factory shared by all 4 layers
# ---------------------------------------------------------------------------


def _yaml_to_spec(raw: dict, yaml_path: Path) -> StrategySpec:
    """Convert a YAML mapping into a StrategySpec by importing the class."""
    name = raw.get("name")
    module_name = raw.get("module")
    class_name = raw.get("class") or raw.get("factory")
    if not (name and module_name and class_name):
        raise ValueError(
            f"YAML plugin {yaml_path} must declare 'name', 'module', and 'class'"
        )

    module = importlib.import_module(module_name)
    factory = getattr(module, class_name, None)
    if factory is None:
        raise ImportError(f"{module_name}.{class_name} not found (from {yaml_path})")

    return StrategySpec(name=name, factory=factory)


# ---------------------------------------------------------------------------
# Per-layer consumer factory
# ---------------------------------------------------------------------------


def _make_consumer(
    base_cls: type,
    register_fn: Callable[[str, Type[Any]], None],
    layer: str,
) -> Callable[[List[Any], str], None]:
    """Build a PluginLoader consumer that validates and dispatches specs."""

    def _consume(items: List[Any], source: str) -> None:
        for item in items:
            if not isinstance(item, StrategySpec):
                log.warn(
                    "plugin.invalid_spec",
                    {"source": source, "layer": layer, "item_type": type(item).__name__},
                )
                continue
            if not (isinstance(item.factory, type) and issubclass(item.factory, base_cls)):
                log.warn(
                    "plugin.wrong_base",
                    {
                        "source": source,
                        "layer": layer,
                        "name": item.name,
                        "factory": getattr(item.factory, "__name__", repr(item.factory)),
                        "expected_base": base_cls.__name__,
                    },
                )
                continue
            register_fn(item.name, item.factory)
            log.info(
                "plugin.registered",
                {"source": source, "layer": layer, "name": item.name},
            )

    return _consume


# ---------------------------------------------------------------------------
# ExtensionPoint registration (idempotent, called from EvolutionEngine.bootstrap)
# ---------------------------------------------------------------------------


_EXTENSION_POINTS_REGISTERED = False


def register_extension_points() -> None:
    """Register the 4 evolution ExtensionPoints with PluginLoader.

    Idempotent: subsequent calls (e.g. when ToolRegistry and AgentRegistry
    both invoke PluginLoader.load_all() during startup) are no-ops, so
    consumers never double-register the same factory.
    """
    global _EXTENSION_POINTS_REGISTERED
    if _EXTENSION_POINTS_REGISTERED:
        return

    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="EVOLUTION_ACQUIRERS",
        subdir="evolution/acquirer",
        item_type=StrategySpec,
        dedup_key=lambda s: s.name,
        consumer=_make_consumer(CapabilityAcquirer, EvolutionEngine.register_acquirer, "acquirer"),
        yaml_item_factory=_yaml_to_spec,
        recursive=True,
        max_depth=2,
    ))
    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="EVOLUTION_AUTHORS",
        subdir="evolution/author",
        item_type=StrategySpec,
        dedup_key=lambda s: s.name,
        consumer=_make_consumer(SkillAuthor, EvolutionEngine.register_author, "author"),
        yaml_item_factory=_yaml_to_spec,
        recursive=True,
        max_depth=2,
    ))
    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="EVOLUTION_TRACKERS",
        subdir="evolution/tracker",
        item_type=StrategySpec,
        dedup_key=lambda s: s.name,
        consumer=_make_consumer(UsageTracker, EvolutionEngine.register_tracker, "tracker"),
        yaml_item_factory=_yaml_to_spec,
        recursive=True,
        max_depth=2,
    ))
    PluginLoader.register_extension_point(ExtensionPoint(
        attr_name="EVOLUTION_CURATORS",
        subdir="evolution/curator",
        item_type=StrategySpec,
        dedup_key=lambda s: s.name,
        consumer=_make_consumer(Curator, EvolutionEngine.register_curator, "curator"),
        yaml_item_factory=_yaml_to_spec,
        recursive=True,
        max_depth=2,
    ))

    _EXTENSION_POINTS_REGISTERED = True
    log.debug("evolution.ext_points.registered", {"count": 4})


def _reset_for_tests() -> None:
    """Test-only: clear the idempotency latch so the next call re-registers."""
    global _EXTENSION_POINTS_REGISTERED
    _EXTENSION_POINTS_REGISTERED = False


__all__ = ["StrategySpec", "register_extension_points"]
