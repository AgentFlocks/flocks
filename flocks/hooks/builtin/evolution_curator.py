"""
Evolution Curator Hook - trigger BuiltinIdleCurator on new sessions.

Subscribes to ``command:new`` (the same event session_memory.py listens
to) and asks the L4 curator to do a pass. The curator's
``should_run()`` method enforces ``min_idle_hours`` throttling, so this
handler is safe to fire on every new session even on busy installs.

Why command:new?
----------------
Flocks does not expose a ``session:end`` event yet. The next-best signal
of "the user is starting fresh, agent is briefly idle" is ``command:new``
which fires when the user begins a new conversation. Curator runs
between conversations are an ideal moment to compact procedural memory.
"""

from __future__ import annotations

from flocks.evolution import EvolutionEngine
from flocks.evolution.types import CuratorContext
from flocks.hooks.registry import register_hook
from flocks.hooks.types import HookEvent
from flocks.utils.log import Log

log = Log.create(service="hooks.evolution_curator")


class EvolutionCuratorHook:
    """Run the L4 curator when a new session command is dispatched."""

    @staticmethod
    async def handler(event: HookEvent) -> None:
        if event.type != "command" or event.action != "new":
            return

        try:
            engine = EvolutionEngine.get()
            curator = engine.curator
            if curator.is_noop:
                return

            ctx = CuratorContext(
                triggered_by="command:new",
                session_id=event.session_id,
                extra={"context": event.context or {}},
            )
            if not curator.should_run(ctx):
                log.debug("evolution_curator.throttled", {"session_id": event.session_id})
                return

            report = await curator.run(ctx)
            log.info(
                "evolution_curator.ran",
                {
                    "session_id": event.session_id,
                    "summary": report.llm_summary,
                    "duration_s": report.duration_seconds,
                },
            )
        except Exception as exc:
            # Never let a curator crash break a new session.
            log.error(
                "evolution_curator.handler_error",
                {"error": str(exc), "session_id": event.session_id},
            )


def register_evolution_curator_hook() -> None:
    """Idempotent hook registration helper."""
    register_hook(
        event_key="command:new",
        handler=EvolutionCuratorHook.handler,
        metadata={
            "name": "evolution-curator",
            "description": "Trigger L4 curator pass on new session (throttled).",
            "priority": 200,  # after session-memory (100) so memory writes commit first
        },
    )
    log.info("evolution_curator.registered")
