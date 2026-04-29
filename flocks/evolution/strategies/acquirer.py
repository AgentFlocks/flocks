"""
L1 CapabilityAcquirer - close capability gaps detected by the agent.

Triggered when Rex (or any primary agent) calls
``delegate_task(subagent_type="self-enhance", ...)`` and a non-default
acquirer is configured. The interceptor in
``flocks/tool/agent/delegate_task.py`` forwards the call to
``EvolutionEngine.acquirer.acquire()`` instead of running the original
self_enhance subagent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from flocks.evolution.types import AcquireContext, AcquireResult, CapabilityGap


class CapabilityAcquirer(ABC):
    """Abstract base for L1 capability acquisition strategies."""

    name: str = ""
    """Unique strategy name. Must match ``evolution.acquirer.use`` in config."""

    priority: int = 100
    """Lower number tried first when multiple acquirers are eligible."""

    is_noop: bool = False
    """True only for the NoOp default; real implementations leave as False."""

    @abstractmethod
    async def can_handle(self, gap: CapabilityGap) -> bool:
        """Whether this strategy is eligible to attempt the given gap."""
        ...

    @abstractmethod
    async def acquire(self, gap: CapabilityGap, ctx: AcquireContext) -> AcquireResult:
        """Attempt to acquire the missing capability and report the outcome."""
        ...


class NoOpAcquirer(CapabilityAcquirer):
    """Default no-op acquirer; always reports CAPABILITY NOT ACQUIRED."""

    name = "_noop"
    priority = 10_000
    is_noop = True

    async def can_handle(self, gap: CapabilityGap) -> bool:  # noqa: D401
        return False

    async def acquire(self, gap: CapabilityGap, ctx: AcquireContext) -> AcquireResult:
        return AcquireResult(
            acquired=False,
            tool_name=None,
            notes="evolution.acquirer is disabled (NoOp)",
            attempted=[],
            error=None,
        )
