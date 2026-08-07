"""Session adapters consumed by the host-neutral agent runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from flocks.agent.runtime.contracts import (
    AgentRunState,
    ContinuationDecision,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
    StepResult,
)
from flocks.agent.runtime.events import RuntimeEvent


class SessionStepCancelled(Exception):
    """Internal signal raised when a hosted model turn is cancelled."""


class SessionLoopStepEngine:
    """Bind the session runner and cancellation state to one AgentLoop."""

    def __init__(self, context: Any, callbacks: Any, policy: Any):
        self._context = context
        self._callbacks = callbacks
        self._policy = policy

    async def run(self, snapshot: ModelTurnSnapshot[Any]) -> StepResult:
        """Execute a model turn while exposing its task to session abort."""
        task = asyncio.create_task(
            self._policy._process_model_step(
                self._context,
                self._callbacks,
                snapshot,
            )
        )
        self._context._current_step_task = task
        started_at = asyncio.get_running_loop().time()
        try:
            return await task
        except asyncio.CancelledError as exc:
            raise SessionStepCancelled from exc
        finally:
            self._context._current_step_task = None
            duration_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
            self._policy._log_step_complete(self._context, duration_ms)


class SessionRuntimeServices:
    """Adapt session persistence and policy to narrow runtime ports."""

    def __init__(self, context: Any, callbacks: Any, policy: Any):
        self._context = context
        self._callbacks = callbacks
        self._policy = policy

    async def prepare_model_turn(
        self,
        state: AgentRunState[Any],
    ) -> ModelTurnPreparation[Any]:
        """Prepare persisted session state for a stable model turn."""
        return await self._policy._prepare_model_turn(
            self._context,
            self._callbacks,
            state,
        )

    async def complete_model_turn(
        self,
        state: AgentRunState[Any],
        step_result: StepResult,
    ) -> ModelTurnBoundary[Any]:
        """Commit and expose the state written by a completed model turn."""
        return await self._policy._complete_model_turn(
            self._context,
            self._callbacks,
            state,
            step_result,
        )

    async def resolve_continuation(
        self,
        state: AgentRunState[Any],
        step_result: StepResult,
    ) -> ContinuationDecision[Any]:
        """Resolve queued goal and TurnFinish continuation policy."""
        return await self._policy._resolve_continuation(
            self._context,
            self._callbacks,
            state,
            step_result,
        )

    async def emit_event(self, event: RuntimeEvent) -> None:
        """Forward a host-neutral runtime event to session subscribers."""
        await self._policy._publish_runtime_event(
            self._callbacks,
            event.type,
            dict(event.payload),
        )
