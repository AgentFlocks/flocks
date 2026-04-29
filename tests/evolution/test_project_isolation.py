"""
Project isolation for L3 tracker: per-project sidecar files.

Ensures swapping the active Instance (different project_id) routes
project-scope writes to a different file so two projects in the same
~/.flocks/ never corrupt each other's usage telemetry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flocks.evolution import EvolutionEngine


@pytest.fixture
def tracker():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "tracker": {"use": "builtin"}})
    return engine.tracker


def _usage_files() -> list:
    return sorted(p.name for p in (Path(os.environ["FLOCKS_ROOT"]) / "data" / "evolution" / "usage").iterdir())


def _make_project(pid: str):
    from flocks.project.project import ProjectInfo, ProjectTime
    return ProjectInfo(id=pid, worktree=os.environ["FLOCKS_ROOT"], time=ProjectTime(created=0, updated=0))


def test_project_id_change_routes_to_new_file(tracker, monkeypatch):
    from flocks.project.instance import InstanceContext, _current_instance

    # First project
    ctx1 = InstanceContext(
        directory=os.environ["FLOCKS_ROOT"],
        worktree=os.environ["FLOCKS_ROOT"],
        project=_make_project("proj-aaa"),
    )
    tok1 = _current_instance.set(ctx1)
    try:
        tracker.bump_use("alpha", scope="project")
    finally:
        _current_instance.reset(tok1)

    # Second project — must land in a different file
    ctx2 = InstanceContext(
        directory=os.environ["FLOCKS_ROOT"],
        worktree=os.environ["FLOCKS_ROOT"],
        project=_make_project("proj-bbb"),
    )
    tok2 = _current_instance.set(ctx2)
    try:
        tracker.clear_cache()
        tracker.bump_use("beta", scope="project")
    finally:
        _current_instance.reset(tok2)

    files = _usage_files()
    project_files = [f for f in files if f != "_user.json"]
    assert len(project_files) == 2, f"expected two project sidecars, got {project_files}"

    # Verify each file holds only its project's skill
    contents = {
        f: set(json.loads((Path(os.environ["FLOCKS_ROOT"]) / "data" / "evolution" / "usage" / f).read_text(encoding="utf-8")).keys())
        for f in project_files
    }
    all_keys = set().union(*contents.values())
    assert all_keys == {"alpha", "beta"}
    # No file holds both
    assert all(len(keys) == 1 for keys in contents.values())


def test_no_project_falls_back_to_default(tracker):
    tracker.bump_use("x", scope="project")
    files = _usage_files()
    assert "_default.json" in files
