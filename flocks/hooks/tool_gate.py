"""
The single pre-execution checkpoint for tool calls.

Every tool call passes exactly one gate before it runs.  The gate fires the
``tool.execute.before`` stage, normalizes whatever the handlers returned into a
:class:`ToolDecision`, and hands back a verdict the caller applies.

Why this exists
---------------
The check used to happen in two places with two different shapes: the streaming
path read ``output["decision"]`` as the string ``"block"``, while the registry
read the same key as a mapping and validated it into a ``ToolDecision``.  A
handler written against one shape was silently ignored by the other -- a policy
returning ``{"action": "deny"}`` did not match ``"block"``, so the streaming
check let it through.  Routing both paths through this module means a handler
implements one contract and it holds wherever the call originates.

Firing once
-----------
For a streamed tool call the registry is the only place the gate fires, on the
canonical ``tool_execution`` payload (secret-bearing keys redacted).  The
session stream layer runs the separate ``tool.input.rewrite`` stage on the raw
model input before it publishes the running state, and learns about a
``validated_input_patch`` the registry applied through
``ToolContext.input_patch_callback``, so the surface still shows the input the
tool actually receives.  A caller that has already run the gate for a call can
stash the verdict with :func:`carry_decision`; the registry then reuses it
instead of asking the handlers a second time.  Handlers therefore see one call
per tool call, and a handler with side effects -- an audit record, a counter, a
confirmation prompt -- is not duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from flocks.hooks.contracts import HookDecisionAction, ToolDecision
from flocks.utils.log import Log

log = Log.create(service="hooks.tool_gate")

_CARRY_KEY = "tool_gate_decision"


@dataclass(frozen=True)
class ToolGateVerdict:
    """What the gate decided for one tool call."""

    decision: ToolDecision
    tool_input: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None
    # Set when the stage itself failed.  The call is denied, but the reason is
    # an infrastructure failure rather than a policy choice, and callers report
    # it differently.
    stage_failed: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision.action is HookDecisionAction.ALLOW

    @property
    def denied(self) -> bool:
        return self.decision.action is HookDecisionAction.DENY

    @property
    def needs_confirmation(self) -> bool:
        return self.decision.action is HookDecisionAction.CONFIRM

    @property
    def reason(self) -> str:
        return self.decision.reason or ""


_DECISION_KEYS = {"action", "reason", "labels", "validated_input_patch"}


def normalize_decision(raw_decision: Any) -> ToolDecision:
    """Turn whatever a handler put in ``output["decision"]`` into a contract.

    Handlers predating the contract wrote a bare string; ``"block"`` and
    ``"deny"`` both meant stop.  Those still resolve correctly instead of
    quietly reading as "allow".
    """

    if isinstance(raw_decision, ToolDecision):
        return raw_decision

    if isinstance(raw_decision, Mapping):
        filtered = {key: value for key, value in raw_decision.items() if key in _DECISION_KEYS}
        return ToolDecision.model_validate(filtered)

    if isinstance(raw_decision, str):
        token = raw_decision.strip().lower()
        if token in {"block", "deny", "denied", "reject"}:
            return ToolDecision(action=HookDecisionAction.DENY)
        if token in {"confirm", "ask"}:
            return ToolDecision(action=HookDecisionAction.CONFIRM)
        return ToolDecision()

    return ToolDecision()


def decision_from_hook_output(output: Any) -> ToolDecision:
    """Read a decision out of a hook context's ``output`` mapping."""

    if not isinstance(output, Mapping):
        return ToolDecision()

    decision = normalize_decision(output.get("decision"))
    if decision.action is not HookDecisionAction.ALLOW:
        return decision

    # ``skip`` predates the decision contract and still means "do not run".
    if bool(output.get("skip", False)):
        reason = str(output.get("reason") or output.get("error") or "").strip() or None
        return ToolDecision(action=HookDecisionAction.DENY, reason=reason)

    return decision


def rewritten_tool_input(hook_ctx: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a handler's rewritten tool input, or keep the original."""

    hook_input = getattr(hook_ctx, "input", None)
    if isinstance(hook_input, Mapping):
        tool_section = hook_input.get("tool")
        if isinstance(tool_section, Mapping):
            updated = tool_section.get("input")
            if isinstance(updated, dict):
                return updated
    return fallback


# ---------------------------------------------------------------------------
# Carrying a verdict between layers
# ---------------------------------------------------------------------------

def carry_decision(extra: Optional[Dict[str, Any]], verdict: ToolGateVerdict) -> None:
    """Attach a verdict to a tool context so the next layer reuses it."""

    if extra is None:
        return
    extra[_CARRY_KEY] = verdict


def take_carried_decision(
    extra: Optional[Mapping[str, Any]],
    *,
    call_id: Optional[str] = None,
) -> Optional[ToolGateVerdict]:
    """Return a verdict already made for this call, if there is one.

    The call id must match: a context reused across calls must not let one
    call's verdict stand in for another's.
    """

    if not isinstance(extra, Mapping):
        return None
    verdict = extra.get(_CARRY_KEY)
    if not isinstance(verdict, ToolGateVerdict):
        return None
    if call_id is not None and verdict.call_id is not None and verdict.call_id != call_id:
        log.warn(
            "hooks.tool_gate.carried_call_mismatch",
            {"expected": call_id, "carried": verdict.call_id},
        )
        return None
    return verdict


async def run_tool_gate(
    payload: Dict[str, Any],
    *,
    call_id: Optional[str] = None,
    tool_input: Optional[Dict[str, Any]] = None,
) -> ToolGateVerdict:
    """Fire the pre-execution stage and normalize the result.

    A stage that raises denies the call.  Letting the tool run because the
    check itself broke would make every policy handler advisory, so failure
    here is closed rather than open.
    """

    from flocks.hooks.execution import execution_stop_error
    from flocks.hooks.pipeline import HookPipeline

    original_input = dict(tool_input or {})

    try:
        hook_ctx = await HookPipeline.run_tool_before(payload)
    except Exception as exc:
        log.error("hooks.tool_gate.stage_failed", {"error": str(exc), "call_id": call_id})
        return ToolGateVerdict(
            decision=ToolDecision(
                action=HookDecisionAction.DENY,
                reason="Tool execution blocked because tool-before hook failed",
            ),
            tool_input=original_input,
            call_id=call_id,
            stage_failed=True,
        )

    decision = decision_from_hook_output(getattr(hook_ctx, "output", None))

    # A handler may stop execution through the generic lifecycle control rather
    # than by returning a decision; both mean the same thing here.
    legacy_stop = execution_stop_error(hook_ctx)
    if legacy_stop is not None:
        decision = ToolDecision(action=HookDecisionAction.DENY, reason=str(legacy_stop))

    return ToolGateVerdict(
        decision=decision,
        tool_input=rewritten_tool_input(hook_ctx, original_input),
        call_id=call_id,
    )


__all__ = [
    "ToolGateVerdict",
    "carry_decision",
    "decision_from_hook_output",
    "normalize_decision",
    "rewritten_tool_input",
    "run_tool_gate",
    "take_carried_decision",
]
