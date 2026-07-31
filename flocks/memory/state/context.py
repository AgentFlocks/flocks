"""Mission State context loading for active Goal sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from flocks.memory.state.mission import (
    MISSION_STATE_GUIDANCE,
    mission_path,
    render_resume_snapshot,
    render_shared_updates,
)

MissionSnapshotReason = Literal[
    "session_restore",
    "compaction",
    "mission_activated",
    "subagent_completed",
]


@dataclass(frozen=True)
class MissionPromptContext:
    """Stable Guidance and dynamic Snapshot for one active Mission."""

    path: str
    guidance: str
    snapshot: str | None = None


class MissionContextProvider:
    """Load Mission Prompt Context when filesystem State is active."""

    @classmethod
    async def load(
        cls,
        *,
        workspace_dir: str | Path,
        session_id: str,
        include_snapshot: bool = True,
        snapshot_reason: MissionSnapshotReason = "session_restore",
    ) -> MissionPromptContext | None:
        from flocks.session.goal import GoalManager

        goal_state = await GoalManager.get(session_id)
        if getattr(goal_state, "status", None) != "active":
            return None

        path = mission_path(workspace_dir, session_id)
        if not path.is_file():
            return None

        snapshot = None
        if include_snapshot:
            if snapshot_reason == "subagent_completed":
                snapshot = render_shared_updates(workspace_dir, session_id)
            else:
                snapshot = render_resume_snapshot(workspace_dir, session_id)

        return MissionPromptContext(
            path=str(path),
            guidance=MISSION_STATE_GUIDANCE,
            snapshot=snapshot,
        )
