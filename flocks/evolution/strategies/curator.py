"""
L4 Curator - background skill maintenance: archive stale, merge umbrellas.

The default ``BuiltinIdleCurator`` runs in two passes:

  v1 (always-on, pure function):
      ``apply_automatic_transitions()`` walks the tracker report and moves
      skills active → stale → archived based on idle days, sparing pinned
      ones. Triggered by the existing ``command:new`` hook with strict
      throttling so it fires at most once per ``min_idle_hours`` window.

  v2 (LLM-driven, opt-in):
      ``run_llm_review()`` launches a hidden top-level session via
      ``BackgroundManager.launch()`` to invoke the curator subagent
      defined under ``flocks/agent/agents/curator/``. Reports are written
      under ``~/.flocks/data/evolution/curator/{stamp}/`` so users can
      audit edits made on their behalf.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from flocks.evolution.types import (
    CurationReport,
    CuratorContext,
    CuratorState,
    TransitionCounts,
)


class Curator(ABC):
    """Abstract base for L4 curator strategies."""

    name: str = ""
    is_noop: bool = False

    @abstractmethod
    def should_run(self, ctx: CuratorContext) -> bool:
        """Throttle gate: returns True only when enough idle time has elapsed."""
        ...

    @abstractmethod
    def apply_automatic_transitions(self) -> TransitionCounts:
        """Pure-function active → stale → archived sweep. Safe to call any time."""
        ...

    @abstractmethod
    async def run(self, ctx: CuratorContext) -> CurationReport:
        """Full curator pass. Must be idempotent and self-throttling."""
        ...

    @abstractmethod
    def load_state(self) -> CuratorState:
        """Persistent scheduler state."""
        ...

    @abstractmethod
    def save_state(self, state: CuratorState) -> None:
        """Persist updated scheduler state."""
        ...


class NoOpCurator(Curator):
    """Default no-op curator; never runs."""

    name = "_noop"
    is_noop = True

    def should_run(self, ctx: CuratorContext) -> bool:  # noqa: D401
        return False

    def apply_automatic_transitions(self) -> TransitionCounts:
        return TransitionCounts()

    async def run(self, ctx: CuratorContext) -> CurationReport:
        return CurationReport(
            llm_summary="evolution.curator is disabled (NoOp)",
        )

    def load_state(self) -> CuratorState:
        return CuratorState()

    def save_state(self, state: CuratorState) -> None:  # noqa: D401
        return None
