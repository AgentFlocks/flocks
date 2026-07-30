"""Mission State context loading for active Goal sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flocks.memory.state.mission import (
    MISSION_STATE_GUIDANCE,
    mission_path,
    render_state_snapshot,
)


@dataclass(frozen=True)
class MissionPromptContext:
    """Stable Guidance and dynamic Snapshot for one active Mission."""

    path: str
    guidance: str
    snapshot: str


class MissionContextProvider:
    """Load Mission Prompt Context when filesystem State is active."""

    @classmethod
    async def load(
        cls,
        *,
        workspace_dir: str | Path,
        session_id: str,
    ) -> MissionPromptContext | None:
        from flocks.session.goal import GoalManager

        goal_state = await GoalManager.get(session_id)
        if getattr(goal_state, "status", None) != "active":
            return None

        path = mission_path(workspace_dir, session_id)
        if not path.is_file():
            return None

        snapshot = render_state_snapshot(workspace_dir, session_id)
        if not snapshot:
            return None

        return MissionPromptContext(
            path=str(path),
            guidance=MISSION_STATE_GUIDANCE,
            snapshot=snapshot,
        )
