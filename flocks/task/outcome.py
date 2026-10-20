"""Whether a task run that did not finish may simply run again.

A run that failed, timed out or was interrupted may have done part of its work
already: run a command, written a file, sent a message.  Starting it again from
the beginning repeats whatever it did -- the external system answered, the
receipt was lost, and the second run does it twice.  So a run is retried
automatically only when nothing it started can have changed anything outside
it; otherwise it waits for a person, who checks what took effect and then
resumes it in the same session (its persisted checkpoint) or runs it afresh.

What a tool may change is its side-effect class:

* ``none`` -- reads only; running it again changes nothing;
* ``idempotent`` -- changes something, but doing it again has the effect of
  doing it once, by its nature: setting a value, writing the same content to
  the same place.  Not a call that relies on an idempotency key: a retried run
  starts in a new session, its calls are new calls with new keys
  (``ToolContext.idempotency_key``), and the external system does it again;
* ``queryable`` -- changes something whose result can be looked up, so a
  person (or a check) can see whether it happened -- the class for a call
  whose protection against repeats is a key;
* ``unknown`` -- anything else.  The default for every tool that does not say.

A tool says so with ``ToolInfo.side_effect``; the read-only built-ins are
listed here.  Only ``none`` and ``idempotent`` calls let a run retry on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_IDEMPOTENT = "idempotent"
SIDE_EFFECT_QUERYABLE = "queryable"
SIDE_EFFECT_UNKNOWN = "unknown"
SIDE_EFFECT_CLASSES = (SIDE_EFFECT_NONE, SIDE_EFFECT_IDEMPOTENT, SIDE_EFFECT_QUERYABLE, SIDE_EFFECT_UNKNOWN)

#: The outcome of a run that did not finish.
SAFE_TO_RETRY = "safe_to_retry"
NEEDS_VERIFICATION = "needs_verification"

#: Why such a run is held (RetryConfig.blocked_reason).
BLOCKED_UNVERIFIED_EFFECTS = "unverified_effects"

#: Built-in tools that change nothing outside the run.
READ_ONLY_TOOLS = frozenset(
    {
        "get_time",
        "glob",
        "grep",
        "invalid",
        "list_providers",
        "lsp",
        "memory_search",
        "plan_exit",
        "question",
        "rag_retrieve",
        "read",
        "skill_load",
        "sleep",
        "todo",
        "tool_search",
        "webfetch",
        "websearch",
    }
)

#: Tool states of a call that started: from here on it may have had its effect.
_STARTED_STATES = frozenset({"running", "completed", "error"})


def tool_side_effect(name: str) -> str:
    """The side-effect class of tool *name* (see the module docstring)."""
    try:
        from flocks.tool.registry import ToolRegistry

        tool = ToolRegistry.get(name)
    except Exception:
        tool = None
    declared = getattr(getattr(tool, "info", None), "side_effect", None)
    if declared in SIDE_EFFECT_CLASSES:
        return declared
    return SIDE_EFFECT_NONE if name in READ_ONLY_TOOLS else SIDE_EFFECT_UNKNOWN


@dataclass(frozen=True)
class RunOutcome:
    kind: str
    #: Calls whose effects are not confirmed, as "tool:callID".
    effects: Tuple[str, ...] = ()

    @property
    def safe_to_retry(self) -> bool:
        return self.kind == SAFE_TO_RETRY


def outcome_of_calls(calls: Iterable[Tuple[str, str, str]]) -> RunOutcome:
    """The outcome of a run that made these ``(tool, call id, state)`` calls."""
    classes: dict[str, str] = {}
    effects = []
    for tool, call_id, state in calls:
        if state not in _STARTED_STATES:
            continue  # asked for, never started: it did nothing
        if tool not in classes:
            classes[tool] = tool_side_effect(tool)
        if classes[tool] not in (SIDE_EFFECT_NONE, SIDE_EFFECT_IDEMPOTENT):
            effects.append(f"{tool}:{call_id}")
    return RunOutcome(NEEDS_VERIFICATION if effects else SAFE_TO_RETRY, tuple(effects))


async def session_calls(session_id: str) -> list[Tuple[str, str, str]]:
    """Every tool call a session made, as ``(tool, call id, state)``."""
    from flocks.session.message import Message

    calls: list[Tuple[str, str, str]] = []
    for message in await Message.list_with_parts(session_id, include_archived=True):
        for part in message.parts:
            if getattr(part, "type", None) != "tool":
                continue
            calls.append((part.tool, part.callID, getattr(part.state, "status", "") or ""))
    return calls


async def run_outcome(execution) -> RunOutcome:
    """Whether a task run that did not finish may simply run again.

    An agent run is judged by the calls its session made.  A workflow run is
    judged by whether it got as far as running the workflow: its nodes run
    code and requests this module cannot see into.  A run that cannot be
    judged (its session unreadable) is not safe to repeat.
    """
    from flocks.task.models import ExecutionMode

    if execution.execution_mode == ExecutionMode.WORKFLOW:
        from flocks.task.executor import TaskExecutor

        started = TaskExecutor.workflow_started(execution)
        if started is None:
            # Not run here (recovered from another node): one that started
            # may have got as far as the workflow.
            started = execution.started_at is not None
        if started:
            return RunOutcome(NEEDS_VERIFICATION, (f"workflow:{execution.workflow_id}",))
        return RunOutcome(SAFE_TO_RETRY)
    session_id: Optional[str] = execution.session_id
    if not session_id:
        return RunOutcome(SAFE_TO_RETRY)
    try:
        calls = await session_calls(session_id)
    except Exception:
        return RunOutcome(NEEDS_VERIFICATION, (f"session:{session_id}",))
    return outcome_of_calls(calls)


def hold_for_verification(execution, outcome: RunOutcome) -> None:
    """Keep the run from being retried automatically, and say why (RetryConfig)."""
    execution.retry.retry_after = None
    execution.retry.blocked_reason = BLOCKED_UNVERIFIED_EFFECTS
    execution.retry.unverified_effects = list(outcome.effects)


__all__ = [
    "BLOCKED_UNVERIFIED_EFFECTS",
    "NEEDS_VERIFICATION",
    "READ_ONLY_TOOLS",
    "RunOutcome",
    "SAFE_TO_RETRY",
    "SIDE_EFFECT_CLASSES",
    "SIDE_EFFECT_IDEMPOTENT",
    "SIDE_EFFECT_NONE",
    "SIDE_EFFECT_QUERYABLE",
    "SIDE_EFFECT_UNKNOWN",
    "hold_for_verification",
    "outcome_of_calls",
    "run_outcome",
    "session_calls",
    "tool_side_effect",
]
