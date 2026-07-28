"""Lifecycle hook for turn-driven Skill evolution."""

from __future__ import annotations

import asyncio

from flocks.hooks.pipeline import HookBase, HookContext, HookPipeline
from flocks.session.background_tasks import track_background_task
from flocks.utils.log import Log


log = Log.create(service="hooks.session_evolution")


class SessionEvolutionHook(HookBase):
    """Schedule a background skill review after a successful assistant turn."""

    async def turn_finish(self, ctx: HookContext) -> None:
        session_category = str(
            ctx.input.get("sessionCategory") or ""
        )
        if session_category and session_category != "user":
            return
        session_id = str(ctx.input.get("sessionID") or "")
        model = ctx.input.get("model") or {}
        provider_id = str(model.get("providerID") or "")
        model_id = str(model.get("modelID") or "")
        user_message = ctx.input.get("userMessage") or {}
        assistant_message = ctx.input.get("assistantMessage") or {}
        user_message_id = str(user_message.get("id") or "")
        assistant_message_id = str(assistant_message.get("id") or "")
        if not all(
            (
                session_id,
                user_message_id,
                assistant_message_id,
                provider_id,
                model_id,
            )
        ):
            return

        schedule_skill_review(
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            provider_id=provider_id,
            model_id=model_id,
        )


def schedule_skill_review(
    *,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
    provider_id: str,
    model_id: str,
) -> None:
    """Schedule trigger detection and skill review without blocking the turn."""

    async def run() -> None:
        try:
            from flocks.memory.evolution.skill import process_skill_turn

            await process_skill_turn(
                session_id=session_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                provider_id=provider_id,
                model_id=model_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "skill_review.failed",
                {
                    "session_id": session_id,
                    "assistant_message_id": assistant_message_id,
                    "error": str(exc),
                },
            )

    task = asyncio.create_task(
        run(),
        name=f"skill-review:{session_id}:{assistant_message_id}",
    )
    track_background_task(task, session_id=session_id)


def register_session_evolution_hook() -> None:
    """Register the skill review hook idempotently."""
    HookPipeline.register(
        "builtin.session-evolution",
        SessionEvolutionHook(),
        order=200,
        timeout_seconds=2.0,
    )
