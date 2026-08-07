"""Data contracts shared by the agent core and session host.

The contracts in this module intentionally avoid importing session storage,
server, CLI, provider, or tool-registry implementations. Session-specific
adapters may carry their native message objects through the generic message
type while the agent loop remains independent of those implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, Mapping, Optional, TypeVar


MessageT = TypeVar("MessageT")


@dataclass(frozen=True)
class RuntimeModel:
    """Concrete provider/model selection for one model turn."""

    provider_id: str
    model_id: str


@dataclass
class AttemptEffects:
    """Observable effects accumulated during one provider attempt."""

    received_chunk: bool = False
    observable_output_started: bool = False
    tool_execution_started: bool = False
    durable_side_effect_possible: bool = False

    @property
    def replay_safe(self) -> bool:
        """Return whether another provider may replay the logical request."""
        return not (self.observable_output_started or self.tool_execution_started or self.durable_side_effect_possible)


@dataclass(frozen=True)
class FailoverDecision:
    """Classification used by the session host's recovery policy."""

    eligible: bool
    reason: str


@dataclass(frozen=True)
class ToolCall:
    """Tool call emitted by a model response."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StepFailure:
    """Failure returned by a step when host finalization is deferred."""

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

    action: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: Optional[str] = None
    usage: Optional[dict[str, int]] = None
    failure: Optional[StepFailure] = None


@dataclass
class AgentRunState(Generic[MessageT]):
    """Agent-loop-owned mutable state for one resumable agent run."""

    session_id: str
    agent_name: str
    active_model: RuntimeModel
    messages: list[MessageT] = field(default_factory=list)
    model_turn_index: int = 0
    trace_step_offset: int = 0
    consumed_input_cursor: Optional[str] = None
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


@dataclass(frozen=True)
class QueuedInputBatch(Generic[MessageT]):
    """New input made visible to the loop at a model-turn boundary."""

    messages: tuple[MessageT, ...] = ()
    cursor: Optional[str] = None


@dataclass(frozen=True)
class ContinuationDecision(Generic[MessageT]):
    """Host-owned continuation policy result consumed by the agent loop."""

    messages: tuple[MessageT, ...] = ()
    reason: Optional[str] = None

    @property
    def should_continue(self) -> bool:
        """Return whether the loop should process another model turn."""
        return bool(self.messages)


class AgentRunStatus(str, Enum):
    """Terminal states returned from the agent core to the session host."""

    COMPLETED = "completed"
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
    failure: Optional[StepFailure] = None
