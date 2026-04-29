"""
BuiltinIdleCurator: pure-function transitions, manifest gate, throttle.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flocks.evolution import EvolutionEngine, SkillDraft
from flocks.evolution.manifest import AuthorManifest
from flocks.evolution.types import CuratorContext, UsageState


_BODY = """Body content with steps.

## Steps
1. step a
2. step b
"""


@pytest.fixture
def wired():
    engine = EvolutionEngine.get()
    engine.bootstrap({
        "enabled": True,
        "author": {"use": "builtin"},
        "tracker": {"use": "builtin"},
        "curator": {
            "use": "builtin",
            "settings": {
                "min_idle_hours": 0,
                "stale_after_days": 1.0,
                "archive_after_days": 5.0,
            },
        },
    })
    return engine


def _backdate_last_used(name: str, days: float):
    f = Path(os.environ["FLOCKS_ROOT"]) / "data" / "evolution" / "usage" / "_user.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    backdated = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    data[name]["last_used_at"] = backdated
    data[name]["created_at"] = backdated
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_active_to_stale_after_idle_window(wired):
    await wired.author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    wired.tracker.bump_use("alpha")
    _backdate_last_used("alpha", days=2)
    wired.tracker.clear_cache()

    counts = wired.curator.apply_automatic_transitions()
    assert counts.marked_stale == 1
    row = next(r for r in wired.tracker.report() if r.name == "alpha")
    assert row.state == UsageState.STALE


@pytest.mark.asyncio
async def test_stale_to_archived_after_long_idle(wired):
    await wired.author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    wired.tracker.bump_use("alpha")
    _backdate_last_used("alpha", days=10)
    wired.tracker.clear_cache()

    counts = wired.curator.apply_automatic_transitions()
    assert counts.archived == 1
    row = next(r for r in wired.tracker.report() if r.name == "alpha")
    assert row.state == UsageState.ARCHIVED


@pytest.mark.asyncio
async def test_pinned_skill_never_transitions(wired):
    await wired.author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    wired.tracker.bump_use("alpha")
    wired.tracker.set_pinned("alpha", True)
    _backdate_last_used("alpha", days=30)
    wired.tracker.clear_cache()

    counts = wired.curator.apply_automatic_transitions()
    assert counts.archived == 0
    assert counts.marked_stale == 0
    row = next(r for r in wired.tracker.report() if r.name == "alpha")
    assert row.state == UsageState.ACTIVE


@pytest.mark.asyncio
async def test_external_skill_not_in_manifest_is_ignored(wired):
    # Bypass author and write a tracker row directly — curator must skip it.
    wired.tracker.bump_use("external-thing")
    _backdate_last_used("external-thing", days=30)
    wired.tracker.clear_cache()

    counts = wired.curator.apply_automatic_transitions()
    assert counts.archived == 0
    row = next(r for r in wired.tracker.report() if r.name == "external-thing")
    assert row.state == UsageState.ACTIVE


@pytest.mark.asyncio
async def test_throttle_blocks_within_idle_window(wired):
    # First run primes last_run_at
    await wired.curator.run(CuratorContext(triggered_by="cli"))
    wired.curator.min_idle_hours = 24
    assert wired.curator.should_run(CuratorContext(triggered_by="command:new")) is False


@pytest.mark.asyncio
async def test_paused_state_blocks_should_run(wired):
    state = wired.curator.load_state()
    state.paused = True
    wired.curator.save_state(state)
    assert wired.curator.should_run(CuratorContext(triggered_by="command:new")) is False


@pytest.mark.asyncio
async def test_run_cli_trigger_bypasses_throttle(wired):
    await wired.curator.run(CuratorContext(triggered_by="cli"))
    wired.curator.min_idle_hours = 24
    rep = await wired.curator.run(CuratorContext(triggered_by="cli"))
    assert "skipped" not in rep.llm_summary  # cli bypasses throttle
