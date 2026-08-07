"""Ports implemented by session and infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Optional, Protocol, TypeVar

from flocks.agent.runtime.contracts import (
    AgentRunState,
    ContinuationDecision,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
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
    ) -> ModelTurnPreparation[MessageT]:
        """Prepare or defer the next stable model-turn input."""
        ...

    async def complete_model_turn(
        self,
        state: AgentRunState[MessageT],
        step_result: StepResult,
    ) -> ModelTurnBoundary[MessageT]:
        """Return the committed post-step view and queued inputs."""
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


class PromptPort(Protocol):
    """Build provider-ready system prompt sections."""

    async def build_system_prompts(self, **kwargs: Any) -> list[str]:
        """Build prompts from one stable model-turn configuration."""
        ...


class ToolPort(Protocol):
    """Expose the tool registry without coupling the core to its storage."""

    def revision(self) -> int:
        """Return a cache revision for the visible tool set."""
        ...

    def list_tools(self) -> list[Any]:
        """Return registered tool metadata entries."""
        ...

    def get(self, name: str) -> Optional[Any]:
        """Resolve one executable tool by name."""
        ...


class ModelPort(Protocol):
    """Resolve and configure provider/model adapters."""

    def get_provider(self, provider_id: str) -> Optional[Any]:
        """Return one configured provider adapter."""
        ...

    async def apply_config(self, provider_id: str) -> None:
        """Apply persisted provider configuration before execution."""
        ...

    def resolve_model(self, provider_id: str, model_id: str) -> Optional[Any]:
        """Return model capability metadata."""
        ...

    def resolve_model_info(
        self,
        provider_id: str,
        model_id: str,
    ) -> tuple[int, int, Optional[int]]:
        """Return context, output, and input token limits."""
        ...


class HookPort(Protocol):
    """Run existing Flocks hook stages at explicit runtime boundaries."""

    async def run_session_start(self, data: dict[str, Any]) -> Any:
        """Run SessionStart hooks."""
        ...

    async def has_stage_handlers(
        self,
        stage: Any,
        metadata: dict[str, Any],
    ) -> bool:
        """Return whether a hook stage has eligible handlers."""
        ...

    async def run_llm_before(self, data: dict[str, Any]) -> Any:
        """Run LLMBefore hooks."""
        ...

    async def run_llm_after(
        self,
        metadata: dict[str, Any],
        result: dict[str, Any],
    ) -> Any:
        """Run LLMAfter hooks."""
        ...


class RuntimeEventSink(Protocol):
    """Receive observable runtime events; not an event-sourcing store."""

    async def emit(self, event: RuntimeEvent) -> None:
        """Forward an event to UI, tracing, or audit subscribers."""
        ...


@dataclass(frozen=True)
class ExternalRuntimePorts:
    """External interfaces captured once for a stable model attempt."""

    prompts: PromptPort
    tools: ToolPort
    models: ModelPort
    hooks: HookPort
