"""Pure filesystem Mission State behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.memory.state.context import MissionContextProvider
from flocks.memory.state.mission import (
    MISSION_STATE_GUIDANCE,
    SUBAGENT_STATE_GUIDANCE,
    mission_dir,
    mission_path,
    render_hot_context,
    render_shared_updates,
    render_state_snapshot,
    render_subagent_handoff,
)
from flocks.session.goal import GoalManager
from flocks.session.message import ToolPart, ToolStateCompleted
from flocks.session.session_loop import LoopContext, SessionLoop


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
    assert "delegating work, or claiming the Goal is complete" in context
    assert "Prefer precise edits or" in context

    shared_updates = render_shared_updates(tmp_path, "ses-shared")
    assert "Ship the feature." not in shared_updates
    assert "Worker A inspected the API." in shared_updates
    assert "Candidate authorization issue." in shared_updates
    assert "response.txt" in shared_updates

    handoff = render_subagent_handoff(tmp_path, "ses-shared")
    assert SUBAGENT_STATE_GUIDANCE.splitlines()[0] in handoff
    assert "must not read or edit `mission.md`" in handoff
    assert "Completion Report" in handoff
    assert "Ship the feature." not in handoff


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

    guidance_only = await MissionContextProvider.load(
        workspace_dir=tmp_path,
        session_id=session_id,
        include_snapshot=False,
    )
    assert guidance_only is not None
    assert guidance_only.guidance == MISSION_STATE_GUIDANCE
    assert guidance_only.snapshot is None

    shared_context = await MissionContextProvider.load(
        workspace_dir=tmp_path,
        session_id=session_id,
        snapshot_reason="subagent_completed",
    )
    assert shared_context is not None
    assert "Delegated State Updates" in shared_context.snapshot
    assert "Ship it." not in shared_context.snapshot


def test_finished_delegation_requests_shared_state_refresh() -> None:
    part = ToolPart(
        sessionID="ses-parent",
        messageID="msg-parent",
        callID="call-delegate",
        tool="delegate_task",
        state=ToolStateCompleted(
            input={"prompt": "Inspect the API"},
            output="done",
            title="Inspect the API",
            metadata={},
            time={"start": 1, "end": 2},
        ),
    )
    ctx = LoopContext(
        session=SimpleNamespace(id="ses-parent", directory="/tmp/project"),
        provider_id="anthropic",
        model_id="claude",
        agent_name="rex",
    )

    assert SessionLoop._has_finished_delegation([part]) is True

    SessionLoop._request_mission_snapshot(ctx, "subagent_completed")
    assert ctx.mission_snapshot_pending is True
    assert ctx.mission_snapshot_reason == "session_restore"

    SessionLoop._consume_mission_snapshot(ctx)
    SessionLoop._request_mission_snapshot(ctx, "subagent_completed")
    assert ctx.mission_snapshot_pending is True
    assert ctx.mission_snapshot_reason == "subagent_completed"


@pytest.mark.asyncio
async def test_newly_created_mission_gets_one_activation_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = LoopContext(
        session=SimpleNamespace(id="ses-parent", directory="/tmp/project"),
        provider_id="anthropic",
        model_id="claude",
        agent_name="rex",
        mission_snapshot_pending=False,
    )
    guidance_only = SimpleNamespace(
        path="/tmp/project/.flocks/missions/ses-parent/mission.md",
        guidance="guidance",
        snapshot=None,
    )
    activated = SimpleNamespace(
        path=guidance_only.path,
        guidance="guidance",
        snapshot="snapshot",
    )
    load = AsyncMock(side_effect=[guidance_only, activated])
    monkeypatch.setattr(MissionContextProvider, "load", load)

    await SessionLoop._refresh_mission_context(ctx)

    assert ctx.mission_context is activated
    assert ctx.mission_snapshot_pending is True
    assert ctx.mission_snapshot_reason == "mission_activated"
    assert load.await_args_list[0].kwargs["include_snapshot"] is False
    assert load.await_args_list[1].kwargs["include_snapshot"] is True
