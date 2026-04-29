"""
BuiltinFsUsageTracker: bumps, state changes, scope routing, atomic writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flocks.evolution import EvolutionEngine
from flocks.evolution.types import UsageState


@pytest.fixture
def tracker():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "tracker": {"use": "builtin"}})
    return engine.tracker


def test_bump_use_creates_file_and_increments(tracker):
    tracker.bump_use("alpha")
    tracker.bump_use("alpha")
    rows = {r.name: r for r in tracker.report()}
    assert rows["alpha"].use_count == 2
    assert rows["alpha"].last_used_at is not None


def test_bump_view_and_patch_independent_counters(tracker):
    tracker.bump_view("alpha")
    tracker.bump_patch("alpha")
    rows = {r.name: r for r in tracker.report()}
    assert rows["alpha"].view_count == 1
    assert rows["alpha"].patch_count == 1
    assert rows["alpha"].use_count == 0


def test_user_and_project_scope_go_to_separate_files(tracker):
    tracker.bump_use("u-only", scope="user")
    tracker.bump_use("p-only", scope="project")
    user_file = Path(os.environ["FLOCKS_ROOT"]) / "data" / "evolution" / "usage" / "_user.json"
    assert user_file.exists()

    usage_dir = user_file.parent
    project_files = [p for p in usage_dir.iterdir() if p != user_file]
    assert len(project_files) == 1
    assert "u-only" in json.loads(user_file.read_text(encoding="utf-8"))
    assert "p-only" in json.loads(project_files[0].read_text(encoding="utf-8"))


def test_set_state_archived_records_archived_at(tracker):
    tracker.bump_use("alpha")
    tracker.set_state("alpha", UsageState.ARCHIVED)
    row = next(r for r in tracker.report() if r.name == "alpha")
    assert row.state == UsageState.ARCHIVED
    assert row.archived_at is not None


def test_bump_use_reactivates_stale_row(tracker):
    tracker.bump_use("alpha")
    tracker.set_state("alpha", UsageState.STALE)
    tracker.bump_use("alpha")
    row = next(r for r in tracker.report() if r.name == "alpha")
    assert row.state == UsageState.ACTIVE


def test_pinned_flag_persists(tracker):
    tracker.bump_use("alpha")
    tracker.set_pinned("alpha", True)
    row = next(r for r in tracker.report() if r.name == "alpha")
    assert row.pinned is True


def test_forget_removes_row(tracker):
    tracker.bump_use("alpha")
    tracker.forget("alpha")
    assert all(r.name != "alpha" for r in tracker.report())
