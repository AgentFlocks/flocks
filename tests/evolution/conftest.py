"""
Shared fixtures for evolution module tests.

Each test gets a fresh ``FLOCKS_ROOT`` under the pytest tmp_path so file
state cannot leak between cases. The EvolutionEngine + AuthorManifest
singletons are reset around every test so factory tables and cached
records start empty.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from flocks.evolution import EvolutionEngine
from flocks.evolution.manifest import AuthorManifest
from flocks.evolution.plugin import _reset_for_tests as _reset_plugin_latch
from flocks.plugin.loader import PluginLoader


@pytest.fixture(autouse=True)
def isolated_flocks_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point FLOCKS_ROOT at a per-test tmp dir."""
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    yield tmp_path


@pytest.fixture(autouse=True)
def reset_evolution_singletons() -> Iterator[None]:
    """Drop EvolutionEngine + AuthorManifest singletons + ExtensionPoint latch."""
    EvolutionEngine.reset()
    AuthorManifest.reset()
    _reset_plugin_latch()
    PluginLoader.clear_extension_points()
    EvolutionEngine._acquirer_factories.clear()
    EvolutionEngine._author_factories.clear()
    EvolutionEngine._tracker_factories.clear()
    EvolutionEngine._curator_factories.clear()
    yield
    EvolutionEngine.reset()
    AuthorManifest.reset()
    _reset_plugin_latch()
    PluginLoader.clear_extension_points()
    EvolutionEngine._acquirer_factories.clear()
    EvolutionEngine._author_factories.clear()
    EvolutionEngine._tracker_factories.clear()
    EvolutionEngine._curator_factories.clear()
