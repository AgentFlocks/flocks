from __future__ import annotations

import pytest

from flocks.hooks.pipeline import (
    HookBase,
    HookPipeline,
    ToolDecision,
    merge_tool_decisions,
    normalize_tool_decision,
)


def test_approval_cannot_be_weakened_by_later_allow() -> None:
    merged = merge_tool_decisions(
        ToolDecision(
            action="ask",
            mode="approval",
            reason="approval_required",
            matched_rule="managed:approval",
            policy_version="b3-v1",
        ),
        ToolDecision(action="allow"),
    )

    assert merged.action == "ask"
    assert merged.mode == "approval"
    assert merged.reason == "approval_required"
    assert merged.matched_rule == "managed:approval"
    assert merged.policy_version == "b3-v1"


def test_approval_cannot_be_weakened_by_later_confirmation() -> None:
    merged = merge_tool_decisions(
        ToolDecision(action="ask", mode="approval"),
        ToolDecision(action="ask", mode="confirm"),
    )

    assert merged.action == "ask"
    assert merged.mode == "approval"


def test_confirmation_keeps_constraints_from_weaker_decisions() -> None:
    merged = merge_tool_decisions(
        ToolDecision(
            action="ask",
            mode="confirm",
            reason="confirmation_required",
            updated_input={"path": "/safe"},
        ),
        ToolDecision(
            action="constrain",
            updated_input={"recursive": False},
        ),
    )

    assert merged.action == "ask"
    assert merged.mode == "confirm"
    assert merged.reason == "confirmation_required"
    assert merged.updated_input == {"path": "/safe", "recursive": False}


def test_weaker_allow_cannot_override_stronger_constraint_input() -> None:
    merged = merge_tool_decisions(
        ToolDecision(
            action="constrain",
            updated_input={"path": "/safe", "recursive": False},
        ),
        ToolDecision(
            action="allow",
            updated_input={"path": "/unsafe", "force": False},
        ),
    )

    assert merged.action == "constrain"
    assert merged.updated_input == {
        "path": "/safe",
        "recursive": False,
        "force": False,
    }


def test_deny_is_stronger_than_approval() -> None:
    merged = merge_tool_decisions(
        ToolDecision(action="ask", mode="approval", grant_ref="approval-123"),
        ToolDecision(
            action="deny",
            reason="managed_deny",
            matched_rule="managed:deny",
            policy_version="b3-v2",
        ),
    )

    assert merged.action == "deny"
    assert merged.reason == "managed_deny"
    assert merged.matched_rule == "managed:deny"
    assert merged.policy_version == "b3-v2"


def test_malformed_expected_decision_fails_closed() -> None:
    decision = normalize_tool_decision(
        {"policy_engine_present": True, "decision": {"action": "typo"}},
        decision_expected=True,
    )

    assert decision.action == "deny"
    assert decision.reason == "invalid_policy_decision"


def test_missing_expected_decision_fails_closed() -> None:
    decision = normalize_tool_decision(
        {"policy_engine_present": True},
        decision_expected=True,
    )

    assert decision.action == "deny"
    assert decision.reason == "invalid_policy_decision"


def test_oss_output_without_active_policy_remains_passthrough() -> None:
    decision = normalize_tool_decision({"unrelated_hook_output": True})

    assert decision.action == "allow"
    assert decision.reason == ""


@pytest.mark.asyncio
async def test_pipeline_preserves_approval_against_later_allow() -> None:
    class _ApprovalHook(HookBase):
        async def tool_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {
                "action": "ask",
                "mode": "approval",
                "reason": "approval_required",
                "matched_rule": "managed:approval",
                "policy_version": "b3-v1",
            }

    class _LaterAllowHook(HookBase):
        async def tool_before(self, ctx) -> None:
            ctx.output["decision"] = {"action": "allow"}

    HookPipeline.register("b3-test-approval", _ApprovalHook(), order=1)
    HookPipeline.register("b3-test-later-allow", _LaterAllowHook(), order=2)
    try:
        hook_ctx = await HookPipeline.run_tool_before({"tool": {"name": "write"}})
    finally:
        HookPipeline.unregister("b3-test-approval")
        HookPipeline.unregister("b3-test-later-allow")

    assert hook_ctx.output["decision"]["action"] == "ask"
    assert hook_ctx.output["decision"]["mode"] == "approval"
    assert hook_ctx.output["decision"]["reason"] == "approval_required"
    assert hook_ctx.output["decision"]["matched_rule"] == "managed:approval"
    assert hook_ctx.output["decision"]["policy_version"] == "b3-v1"


@pytest.mark.asyncio
async def test_pipeline_rejects_active_hook_without_new_decision() -> None:
    class _OrdinaryAllowHook(HookBase):
        async def tool_before(self, ctx) -> None:
            ctx.output["decision"] = {
                "action": "allow",
                "reason": "ordinary_hook_allow",
            }

    class _MalformedActivePolicyHook(HookBase):
        async def tool_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True

    HookPipeline.register("b3-test-ordinary-allow", _OrdinaryAllowHook(), order=1)
    HookPipeline.register(
        "b3-test-malformed-active-policy",
        _MalformedActivePolicyHook(),
        order=2,
    )
    try:
        hook_ctx = await HookPipeline.run_tool_before({"tool": {"name": "write"}})
    finally:
        HookPipeline.unregister("b3-test-ordinary-allow")
        HookPipeline.unregister("b3-test-malformed-active-policy")

    assert hook_ctx.output["decision"]["action"] == "deny"
    assert hook_ctx.output["decision"]["reason"] == "invalid_policy_decision"
