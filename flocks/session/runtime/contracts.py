"""Data contracts shared by the agent loop and session runtime.

The contracts in this module intentionally avoid importing session storage,
server, CLI, provider, or tool-registry implementations. Session-specific
adapters may carry their native message objects through the generic message
type while the agent loop remains independent of those implementations.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, Mapping, Optional, TypeVar


MessageT = TypeVar("MessageT")
ProviderMessageT = TypeVar("ProviderMessageT")


def _freeze(value: Any) -> Any:
    """Recursively freeze request mappings and sequences."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()},
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if hasattr(value, "model_copy"):
        return value.model_copy(deep=True)
    return copy.copy(value)


def _thaw(value: Any) -> Any:
    """Return a provider-owned mutable copy of a frozen request value."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if hasattr(value, "model_copy"):
        return value.model_copy(deep=True)
    return copy.copy(value)


def _clone_provider_message(value: ProviderMessageT) -> ProviderMessageT:
    if isinstance(value, (Mapping, list, tuple)):
        return _thaw(_freeze(value))
    if hasattr(value, "model_copy"):
        return value.model_copy(deep=True)
    return copy.copy(value)


@dataclass(frozen=True)
class RuntimeModel:
    """Concrete provider/model selection for one model turn."""

    provider_id: str
    model_id: str


@dataclass(frozen=True)
class ModelRequest(Generic[ProviderMessageT]):
    """Frozen provider request reused by retries of one model attempt."""

    provider_id: str
    model_id: str
    messages: tuple[ProviderMessageT, ...]
    tools: tuple[Mapping[str, Any], ...]
    options: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "messages",
            tuple(_clone_provider_message(message) for message in self.messages),
        )
        object.__setattr__(
            self,
            "tools",
            tuple(_freeze(tool) for tool in self.tools),
        )
        object.__setattr__(self, "options", _freeze(self.options))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def provider_messages(self) -> list[ProviderMessageT]:
        """Return an isolated mutable copy for one provider invocation."""
        return [_clone_provider_message(message) for message in self.messages]

    def provider_tools(self) -> list[dict[str, Any]]:
        """Return an isolated mutable tool-schema payload."""
        return [_thaw(tool) for tool in self.tools]

    def provider_options(self) -> dict[str, Any]:
        """Return isolated provider options for one invocation."""
        return _thaw(self.options)


@dataclass
class AttemptEffects:
    """Observable effects accumulated during one provider attempt."""

    request_sent: bool = False
    received_chunk: bool = False
    observable_output_started: bool = False
    tool_execution_started: bool = False
    tool_execution_completed: bool = False
    externally_visible: bool = False

    @property
    def replay_safe(self) -> bool:
        """Return whether another provider may replay the logical request."""
        return not (self.observable_output_started or self.tool_execution_started)


@dataclass(frozen=True)
class FailoverDecision:
    """Classification used by the session runtime's recovery policy."""

    eligible: bool
    reason: str


@dataclass(frozen=True)
class ToolCall:
    """Tool call emitted by a model response."""

    id: str
    name: str
    arguments: dict[str, Any]


class StepAction(str, Enum):
    """Control-flow action produced by one model/tool step."""

    CONTINUE = "continue"
    STOP = "stop"
    COMPACT = "compact"


@dataclass
class StepFailure:
    """Failure returned by a step when runtime finalization is deferred."""

    message: str
    error_data: dict[str, Any]
    assistant_message_id: Optional[str]
    reason: str
    allow_fallback: bool
    attempt_state: AttemptEffects
    attempts: int = 0


@dataclass
class StepResult:
    """Result of one model turn, including any tool execution."""

    action: StepAction | str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: Optional[str] = None
    usage: Optional[dict[str, int]] = None
    failure: Optional[StepFailure] = None
    effective_model: Optional[RuntimeModel] = None


@dataclass
class AgentRunState(Generic[MessageT]):
    """Agent-loop-owned mutable state for one resumable agent run."""

    session_id: str
    agent_name: str
    active_model: RuntimeModel
    messages: list[MessageT] = field(default_factory=list)
    model_turn_index: int = 0
    trace_step_offset: int = 0
    current_user_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def trace_step(self) -> int:
        """Return the session-cumulative model-turn index."""
        return self.trace_step_offset + self.model_turn_index


@dataclass(frozen=True)
class ModelTurnSnapshot(Generic[MessageT]):
    """Immutable input presented to a step engine for one model turn."""

    session_id: str
    agent_name: str
    active_model: RuntimeModel
    model_turn_index: int
    trace_step: int
    messages: tuple[MessageT, ...]
    last_user: MessageT
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Defensively freeze caller-owned collections."""
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


class TurnPreparationStatus(str, Enum):
    """Session preparation result before the next model turn."""

    READY = "ready"
    CONTINUE = "continue"
    COMPLETE = "complete"
    FATAL = "fatal"


@dataclass(frozen=True)
class ModelTurnPreparation(Generic[MessageT]):
    """Result of session-owned preparation at a model-turn boundary."""

    status: TurnPreparationStatus
    snapshot: Optional[ModelTurnSnapshot[MessageT]] = None
    last_message: Optional[MessageT] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class QueuedInputBatch(Generic[MessageT]):
    """New input made visible to the loop at a model-turn boundary."""

    messages: tuple[MessageT, ...] = ()


@dataclass(frozen=True)
class ModelTurnBoundary(Generic[MessageT]):
    """Committed session view after one model turn finishes."""

    messages: tuple[MessageT, ...]
    last_message: Optional[MessageT] = None
    queued_inputs: QueuedInputBatch[MessageT] = field(
        default_factory=QueuedInputBatch,
    )


@dataclass(frozen=True)
class ContinuationDecision(Generic[MessageT]):
    """Session-owned continuation policy result consumed by the outer loop."""

    messages: tuple[MessageT, ...] = ()
    reason: Optional[str] = None

    @property
    def should_continue(self) -> bool:
        """Return whether the loop should process another model turn."""
        return bool(self.messages)


class AgentRunStatus(str, Enum):
    """Terminal states returned from the agent core to the session runtime."""

    COMPLETED = "completed"
    INPUT_AVAILABLE = "input_available"
    RETRYABLE_FAILURE = "retryable_failure"
    CONTEXT_OVERFLOW = "context_overflow"
    FATAL_FAILURE = "fatal_failure"
    ABORTED = "aborted"


@dataclass(frozen=True)
class AgentRunOutcome(Generic[MessageT]):
    """Structured terminal result for a resumable agent-loop invocation."""

    status: AgentRunStatus
    state: AgentRunState[MessageT]
    last_message: Optional[MessageT] = None
    error: Optional[str] = None
    step_result: Optional[StepResult] = None
