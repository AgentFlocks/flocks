"""
Backward compatibility: existing flocks installs see no behaviour change.

Verifies that with ``evolution.enabled=false`` (the default for new
installs and the implicit value for upgrades) nothing in the existing
self-enhance / skill / tool / hook surface changes.
"""

from __future__ import annotations

import pytest

from flocks.evolution import EvolutionEngine


def test_no_evolution_section_keeps_module_disabled():
    """A config without the evolution section bootstraps to NoOps everywhere."""
    engine = EvolutionEngine.get()
    engine.bootstrap({})  # no evolution key at all
    status = engine.status()
    assert status["initialized"] is True
    assert all(status["noop"].values())


def test_disabled_evolution_does_not_alter_acquirer_passthrough():
    """delegate_task interception only triggers when acquirer is non-NoOp + non-passthrough."""
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": False})

    # Both predicates the interceptor checks must be false → falls through to native.
    assert engine.acquirer.is_noop is True


def test_skill_manage_tool_returns_friendly_error_when_author_disabled():
    """skill_manage must not crash when evolution is disabled."""
    from flocks.evolution import EvolutionEngine
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": False})

    # With author=NoOpAuthor, calling create() raises RuntimeError per protocol.
    import asyncio
    from flocks.evolution import SkillDraft

    with pytest.raises(RuntimeError, match="NoOp"):
        asyncio.run(engine.author.create(
            SkillDraft(name="x", description="d", content="body")
        ))


def test_tracker_bump_use_silently_dropped_when_noop():
    """Crucial: skill_tool_impl injection point must be safe when tracker is NoOp."""
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": False})

    # Should NOT raise even with weird inputs.
    engine.tracker.bump_use("any-skill")
    engine.tracker.bump_use("")
    engine.tracker.bump_view("x")
