"""
Idempotency contract for EvolutionEngine.bootstrap().

The ToolRegistry, AgentRegistry, and ChannelRegistry all call
``PluginLoader.load_all()`` during startup. Each pass triggers
``EvolutionEngine.bootstrap()`` (via server.app and acp.py wiring), so
multiple invocations must produce exactly the same wiring as the first.
"""

from __future__ import annotations

import pytest

from flocks.evolution import EvolutionEngine, NoOpAuthor
from flocks.plugin.loader import PluginLoader


def test_default_bootstrap_wires_noops_when_disabled():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": False})

    status = engine.status()
    assert status["initialized"] is True
    assert status["noop"] == {"acquirer": True, "author": True, "tracker": True, "curator": True}
    assert isinstance(engine.author, NoOpAuthor)


def test_bootstrap_is_idempotent_across_repeated_calls():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "author": {"use": "builtin"}})
    first_author = engine.author

    engine.bootstrap({"enabled": True, "author": {"use": "builtin"}})
    engine.bootstrap({"enabled": True, "author": {"use": "different"}})  # would change wiring on a non-idempotent call

    assert engine.author is first_author, "bootstrap must not re-wire after first call"


def test_extension_points_register_only_once():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True})
    keys_after_first = set(PluginLoader._extension_points.keys())
    engine.bootstrap({"enabled": True})
    keys_after_second = set(PluginLoader._extension_points.keys())

    assert keys_after_first == keys_after_second
    assert keys_after_first == {
        "EVOLUTION_ACQUIRERS",
        "EVOLUTION_AUTHORS",
        "EVOLUTION_TRACKERS",
        "EVOLUTION_CURATORS",
    }


def test_unknown_strategy_falls_back_to_noop_without_raising():
    engine = EvolutionEngine.get()
    engine.bootstrap({
        "enabled": True,
        "acquirer": {"use": "this-strategy-does-not-exist"},
    })
    assert engine.acquirer.is_noop is True


@pytest.mark.parametrize("layer", ["acquirer", "author", "tracker", "curator"])
def test_layer_disabled_yields_noop(layer):
    engine = EvolutionEngine.get()
    engine.bootstrap({
        "enabled": True,
        layer: {"enabled": False, "use": "builtin"},
    })
    assert getattr(engine, layer).is_noop is True
