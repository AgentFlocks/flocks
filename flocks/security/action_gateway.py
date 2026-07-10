"""Domain-neutral B3 gateway for control-plane actions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

from flocks.hooks.pipeline import HookPipeline, ToolDecision, normalize_tool_decision
from flocks.security.canonical import canonicalize_json


@dataclass(frozen=True)
class SecurityAction:
    action: str
    resource: Mapping[str, Any]
    canonical_input: Any
    execution_domain: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ActionDecisionError(RuntimeError):
    """Base class for action decisions that prevent immediate execution."""

    def __init__(self, decision: ToolDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason or f"action_{decision.action}")


class ActionDeniedError(ActionDecisionError):
    """Raised when policy denies an action before its side effect."""


class ActionPendingError(ActionDecisionError):
    """Raised when an action requires confirmation or approval."""


def _subject_payload(subject: Any) -> Dict[str, Any]:
    if subject is None:
        from flocks.identity.subject import get_current_subject

        subject = get_current_subject()
    if subject is None:
        return {}
    if isinstance(subject, dict):
        return deepcopy(subject)
    if hasattr(subject, "model_dump"):
        payload = subject.model_dump()
        return deepcopy(payload) if isinstance(payload, dict) else {}
    return {}


def _canonical_payload(action: SecurityAction) -> Dict[str, Any]:
    resource = deepcopy(dict(action.resource))
    execution_domain = str(action.execution_domain or "").strip() or "unknown"
    result = canonicalize_json(
        {
            "action": str(action.action),
            "input": deepcopy(action.canonical_input),
            "resource": resource,
            "execution_domain": execution_domain,
        }
    )
    return {
        "status": result.status,
        "parser_version": result.parser_version,
        "reason": result.reason,
        "generic": result.value,
        "resource": resource,
        "execution_domain": execution_domain,
        "hash": result.hash,
    }


async def run_before_action(
    action: SecurityAction,
    *,
    subject: Any = None,
) -> ToolDecision:
    """Run ACTION_BEFORE hooks and return their monotonic decision."""
    subject_data = _subject_payload(subject)
    metadata = deepcopy(dict(action.metadata))
    canonical = _canonical_payload(action)
    payload = {
        "phase": "before_action",
        "entry": str(metadata.get("entry") or subject_data.get("entry") or "unknown"),
        "sessionID": str(metadata.get("sessionID") or metadata.get("session_id") or ""),
        "actor": deepcopy(metadata.get("actor")),
        "subject": subject_data,
        "action": str(action.action),
        "resource": deepcopy(dict(action.resource)),
        "canonical": canonical,
        "canonical_hash": canonical.get("hash"),
        "execution_domain": canonical["execution_domain"],
        "metadata": metadata,
    }
    ctx = await HookPipeline.run_action_before(payload)
    output = ctx.output if ctx else None
    policy_engine_present = bool(
        isinstance(output, dict) and output.get("policy_engine_present")
    )
    return normalize_tool_decision(
        output,
        decision_expected=policy_engine_present,
    )


def enforce_action_decision(decision: ToolDecision) -> None:
    """Prevent denied or unresolved actions from reaching their side effect."""
    if decision.action == "deny":
        raise ActionDeniedError(decision)
    if decision.action == "ask":
        raise ActionPendingError(decision)
    if decision.action == "constrain":
        constrained = ToolDecision(
            action="deny",
            reason=decision.reason or "action_constraint_not_supported",
            updated_input=decision.updated_input,
            mode=decision.mode,
            grant_ref=decision.grant_ref,
            matched_rule=decision.matched_rule,
            policy_version=decision.policy_version,
        )
        raise ActionDeniedError(constrained)
