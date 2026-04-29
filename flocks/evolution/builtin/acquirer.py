"""
Built-in L1 capability acquirer.

Wraps Flocks' existing self-enhance subagent flow as the default
"builtin" strategy so users can opt into the evolution module without
losing the bundled capability acquisition agent.

Implementation note
-------------------
This class is a *passthrough sentinel*. The interceptor in
``flocks/tool/agent/delegate_task.py`` checks ``passthrough`` and lets
the original ``delegate_task(subagent_type="self-enhance")`` flow run
when this acquirer is active, instead of calling ``acquire()``.

That keeps three guarantees intact:
  1. ``ctx.ask`` permission prompt fires once.
  2. Dedup table records the call as a normal delegate_task.
  3. Parent session linkage (``parent_session_id``) stays correct.

A third-party acquirer that wants to *replace* the subagent flow
overrides ``passthrough = False`` and provides a real ``acquire()``.
"""

from __future__ import annotations

from flocks.evolution.strategies import CapabilityAcquirer
from flocks.evolution.types import AcquireContext, AcquireResult, CapabilityGap


class BuiltinSelfEnhanceAcquirer(CapabilityAcquirer):
    """Default builtin acquirer that delegates to Flocks' self-enhance subagent."""

    name = "builtin"
    priority = 100
    passthrough = True
    """When True, the delegate_task interceptor skips ``acquire()`` and
    runs the bundled subagent flow as if no acquirer were configured."""

    async def can_handle(self, gap: CapabilityGap) -> bool:  # noqa: D401
        return True

    async def acquire(self, gap: CapabilityGap, ctx: AcquireContext) -> AcquireResult:
        """Defensive fallback: should never be called when passthrough=True.

        If a caller bypasses the passthrough check (e.g. unit tests, CLI
        ``flocks evolution acquire``), report a graceful error rather than
        attempting to spin up the subagent here — that path lives inside
        delegate_task and would re-enter the interceptor.
        """
        return AcquireResult(
            acquired=False,
            notes=(
                "BuiltinSelfEnhanceAcquirer is a passthrough sentinel; "
                "invoke delegate_task(subagent_type='self-enhance', ...) instead."
            ),
            attempted=["passthrough"],
        )
