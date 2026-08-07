"""Ports implemented by session and infrastructure adapters."""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from flocks.agent.runtime.contracts import (
    AgentRunState,
    ContinuationDecision,
    ModelTurnSnapshot,
    QueuedInputBatch,
    StepResult,
)
from flocks.agent.runtime.events import RuntimeEvent


MessageT = TypeVar("MessageT")


class StepEngine(Protocol, Generic[MessageT]):
    """Execute one model turn from an immutable snapshot."""

    async def run(self, snapshot: ModelTurnSnapshot[MessageT]) -> StepResult:
        """Run one streamed model turn and return its result."""
        ...


class RuntimeServices(Protocol, Generic[MessageT]):
    """Narrow session-host interface consumed by the agent loop."""

    async def prepare_model_turn(
        self,
        state: AgentRunState[MessageT],
    ) -> ModelTurnSnapshot[MessageT]:
        """Prepare stable model, prompt, tool, and message inputs."""
        ...

    async def drain_queued_inputs(
        self,
        state: AgentRunState[MessageT],
    ) -> QueuedInputBatch[MessageT]:
        """Return inputs that arrived since the last consumed cursor."""
        ...

    async def resolve_continuation(
        self,
        state: AgentRunState[MessageT],
        step_result: StepResult,
    ) -> ContinuationDecision[MessageT]:
        """Resolve goal and turn-finish-hook continuation policy."""
        ...

    async def emit_event(self, event: RuntimeEvent) -> None:
        """Forward a runtime event to host-owned sinks."""
        ...
