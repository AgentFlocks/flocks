"""
L1 acquirer dispatch: builtin is passthrough, custom plugins handle gaps.
"""

from __future__ import annotations

import pytest

from flocks.evolution import (
    AcquireContext,
    AcquireResult,
    CapabilityAcquirer,
    CapabilityGap,
    EvolutionEngine,
)
from flocks.evolution.builtin.acquirer import BuiltinSelfEnhanceAcquirer


def test_builtin_acquirer_is_passthrough_sentinel():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "acquirer": {"use": "builtin"}})

    assert isinstance(engine.acquirer, BuiltinSelfEnhanceAcquirer)
    assert engine.acquirer.passthrough is True
    assert engine.acquirer.is_noop is False


@pytest.mark.asyncio
async def test_custom_acquirer_intercepts_via_engine():
    captured = {}

    class StubAcquirer(CapabilityAcquirer):
        name = "stub"
        is_noop = False
        passthrough = False

        async def can_handle(self, gap):
            return True

        async def acquire(self, gap, ctx):
            captured["gap_desc"] = gap.description
            captured["session_id"] = ctx.session_id
            return AcquireResult(
                acquired=True,
                tool_name="stub_tool",
                notes="ok",
                attempted=["lookup", "install"],
            )

    EvolutionEngine.register_acquirer("stub", StubAcquirer)
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "acquirer": {"use": "stub"}})

    gap = CapabilityGap.from_prompt("send email via smtp")
    ctx = AcquireContext(session_id="sess-1", message_id="msg-1", agent="rex")
    result = await engine.acquirer.acquire(gap, ctx)

    assert result.acquired is True
    assert result.tool_name == "stub_tool"
    assert captured == {"gap_desc": "send email via smtp", "session_id": "sess-1"}


def test_capability_gap_extracts_keywords():
    gap = CapabilityGap.from_prompt("Need a tool to do screenshot via browser")
    assert "browser" in gap.keywords
    assert "screenshot" in gap.keywords
    assert gap.raw_prompt is not None
