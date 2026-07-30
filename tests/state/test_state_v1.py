"""Pure filesystem Mission State behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.memory.state.context import MissionContextProvider
from flocks.memory.state.mission import (
    MISSION_STATE_GUIDANCE,
    mission_dir,
    mission_path,
    render_hot_context,
    render_state_snapshot,
)
from flocks.session.goal import GoalManager


def test_state_path_is_deterministic_per_session(tmp_path: Path) -> None:
    workspace = tmp_path / "project"

    assert mission_dir(workspace, "ses-test") == (
        workspace / ".flocks" / "missions" / "ses-test"
    )
    assert mission_path(workspace, "ses-test") == (
        workspace / ".flocks" / "missions" / "ses-test" / "mission.md"
    )


def test_invalid_session_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid session id"):
        mission_dir(tmp_path, "../escape")


def test_missing_mission_file_disables_state(tmp_path: Path) -> None:
    assert render_hot_context(tmp_path, "ses-empty") == ""


def test_filesystem_state_is_rendered_without_parsing(tmp_path: Path) -> None:
    state_dir = mission_dir(tmp_path, "ses-shared")
    artifacts_dir = state_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (state_dir / "mission.md").write_text(
        "# Mission\n\n## Goal\nShip the feature.\n",
        encoding="utf-8",
    )
    (state_dir / "progress.md").write_text(
        "# Progress\n\n- Worker A inspected the API.\n",
        encoding="utf-8",
    )
    (state_dir / "findings.md").write_text(
        "# Findings\n\n- Candidate authorization issue.\n",
        encoding="utf-8",
    )
    (artifacts_dir / "INDEX.md").write_text(
        "# Artifacts\n\n- response.txt\n",
        encoding="utf-8",
    )

    snapshot = render_state_snapshot(tmp_path, "ses-shared")
    context = render_hot_context(tmp_path, "ses-shared")

    assert "Ship the feature." in snapshot
    assert "Worker A inspected the API." in snapshot
    assert "Candidate authorization issue." in snapshot
    assert "response.txt" in snapshot
    assert MISSION_STATE_GUIDANCE not in snapshot
    assert MISSION_STATE_GUIDANCE in context
    assert "`mission.md` is owned by the main Agent" in context
    assert "must not read or edit `mission.md`" in context
    assert "Before finishing its delegated task" in context
    assert "Prefer precise edits or" in context


@pytest.mark.asyncio
async def test_context_provider_loads_only_for_active_mission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "ses-provider"
    path = mission_path(tmp_path, session_id)
    path.parent.mkdir(parents=True)
    path.write_text("# Mission\n\n## Goal\nShip it.\n", encoding="utf-8")
    get_goal = AsyncMock(return_value=None)
    monkeypatch.setattr(GoalManager, "get", get_goal)

    assert (
        await MissionContextProvider.load(
            workspace_dir=tmp_path,
            session_id=session_id,
        )
        is None
    )

    get_goal.return_value = SimpleNamespace(status="active")
    context = await MissionContextProvider.load(
        workspace_dir=tmp_path,
        session_id=session_id,
    )

    assert context is not None
    assert context.path == str(path)
    assert context.guidance == MISSION_STATE_GUIDANCE
    assert "Mission State Snapshot" in context.snapshot
    assert MISSION_STATE_GUIDANCE not in context.snapshot
