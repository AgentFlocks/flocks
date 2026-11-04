"""
Tests for the single pre-execution tool checkpoint (flocks.hooks.tool_gate).
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.contracts import HookDecisionAction
from flocks.hooks.tool_gate import (
    ToolGateVerdict,
    carry_decision,
    normalize_decision,
    run_tool_gate,
    take_carried_decision,
)


class _Ctx:
    """Stand-in for a HookContext with whatever handlers left behind."""

    def __init__(self, output: Dict[str, Any] | None = None, input_: Dict[str, Any] | None = None):
        self.output = output or {}
        self.input = input_ or {}
        self.execution_stop_requested = False
        self.execution_stop_detail = None


def _payload(tool_input: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "sessionID": "s1",
        "agent": "rex",
        "tool": {"name": "ip_query", "input": tool_input or {"ip": "1.1.1.1"}, "callID": "call-1"},
    }


# ---------------------------------------------------------------------------
# G1-G4: one contract, however the handler expressed it
# ---------------------------------------------------------------------------

async def test_mapping_decision_denies():
    """G1: the canonical mapping contract is honoured."""
    ctx = _Ctx(output={"decision": {"action": "deny", "reason": "policy"}})
    with patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock(return_value=ctx)):
        verdict = await run_tool_gate(_payload(), call_id="call-1")

    assert verdict.denied
    assert verdict.reason == "policy"


async def test_legacy_string_block_denies():
    """G2: a handler predating the contract wrote the bare string "block".

    The streaming path used to accept only this shape and the registry only the
    mapping, so a handler was enforced in one place and ignored in the other.
    Both now resolve to the same verdict.
    """
    ctx = _Ctx(output={"decision": "block"})
    with patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock(return_value=ctx)):
        verdict = await run_tool_gate(_payload(), call_id="call-1")

    assert verdict.denied


async def test_legacy_skip_flag_denies():
    """G3: the older ``skip`` flag still means "do not run"."""
    ctx = _Ctx(output={"skip": True, "reason": "not allowed here"})
    with patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock(return_value=ctx)):
        verdict = await run_tool_gate(_payload(), call_id="call-1")

    assert verdict.denied
    assert verdict.reason == "not allowed here"


async def test_no_decision_allows():
    """G4: handlers that express no opinion let the call through."""
    with patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock(return_value=_Ctx())):
        verdict = await run_tool_gate(_payload(), call_id="call-1")

    assert verdict.allowed


def test_normalize_decision_shapes():
    """G1-G4 at the unit level, including shapes that must not read as deny."""
    assert normalize_decision({"action": "deny"}).action is HookDecisionAction.DENY
    assert normalize_decision("block").action is HookDecisionAction.DENY
    assert normalize_decision("DENY").action is HookDecisionAction.DENY
    assert normalize_decision("confirm").action is HookDecisionAction.CONFIRM
    assert normalize_decision("allow").action is HookDecisionAction.ALLOW
    assert normalize_decision(None).action is HookDecisionAction.ALLOW
    assert normalize_decision(123).action is HookDecisionAction.ALLOW


# ---------------------------------------------------------------------------
# G5-G6: stage failure and input rewrite
# ---------------------------------------------------------------------------

async def test_stage_failure_is_fail_closed():
    """G5: if the check itself breaks, the call does not proceed unchecked.

    Running the tool anyway would make every policy handler advisory: an
    attacker able to break the check would also be able to bypass it.
    """
    with patch(
        "flocks.hooks.pipeline.HookPipeline.run_tool_before",
        new=AsyncMock(side_effect=RuntimeError("backend down")),
    ):
        verdict = await run_tool_gate(_payload(), call_id="call-1")

    assert verdict.denied
    assert verdict.stage_failed is True


async def test_handler_input_rewrite_is_returned():
    """G6: a rewritten input reaches the caller, which uses it for the call."""
    ctx = _Ctx(input_={"tool": {"input": {"ip": "10.0.0.1"}}})
    with patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=AsyncMock(return_value=ctx)):
        verdict = await run_tool_gate(
            _payload({"ip": "[[IP_1]]"}),
            call_id="call-1",
            tool_input={"ip": "[[IP_1]]"},
        )

    assert verdict.tool_input == {"ip": "10.0.0.1"}


# ---------------------------------------------------------------------------
# G7-G8: firing exactly once per tool call
# ---------------------------------------------------------------------------

async def test_carried_verdict_is_reused():
    """G7: a verdict already made for this call is not re-derived.

    Handlers have side effects -- audit records, counters, confirmation
    prompts -- so asking them twice for one tool call duplicates those.
    """
    extra: Dict[str, Any] = {}
    verdict = ToolGateVerdict(decision=normalize_decision({"action": "deny"}), call_id="call-1")
    carry_decision(extra, verdict)

    reused = take_carried_decision(extra, call_id="call-1")

    assert reused is verdict


async def test_carried_verdict_from_another_call_is_ignored():
    """G8: a stale verdict must not stand in for a different call."""
    extra: Dict[str, Any] = {}
    carry_decision(extra, ToolGateVerdict(decision=normalize_decision({"action": "deny"}), call_id="call-1"))

    assert take_carried_decision(extra, call_id="call-2") is None


async def test_absent_carrier_returns_none():
    """G8: contexts with nothing carried fall through to a fresh check."""
    assert take_carried_decision({}, call_id="call-1") is None
    assert take_carried_decision(None, call_id="call-1") is None
