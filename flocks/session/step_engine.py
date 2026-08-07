"""Session adapter for the agent runtime's step-engine port."""

from __future__ import annotations

from flocks.agent.runtime.contracts import ModelTurnSnapshot, StepResult
from flocks.session.message import MessageInfo
from flocks.session.runner import SessionRunner


class SessionStepEngine:
    """Run the existing session runner behind the StepEngine contract."""

    def __init__(self, runner: SessionRunner):
        self._runner = runner

    @property
    def session_start_fired(self) -> bool:
        """Return whether this engine fired the session-start hook."""
        return self._runner._session_start_fired

    @property
    def attempt_effects(self):
        """Return effects recorded by the latest provider attempt."""
        return self._runner._attempt_state

    async def run(
        self,
        snapshot: ModelTurnSnapshot[MessageInfo],
    ) -> StepResult:
        """Delegate one immutable model-turn snapshot to SessionRunner."""
        self._runner._step = snapshot.trace_step
        return await self._runner._process_step(
            list(snapshot.messages),
            snapshot.last_user,
        )
