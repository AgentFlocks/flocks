"""Lifecycle hook for Dream and skill self-evolution."""

from __future__ import annotations

import asyncio

from flocks.hooks.pipeline import HookBase, HookContext, HookPipeline
from flocks.hooks.registry import register_hook
from flocks.hooks.types import HookEvent
from flocks.session.background_tasks import track_background_task
from flocks.utils.log import Log


log = Log.create(service="hooks.session_learning")
_catch_up_task: asyncio.Task[None] | None = None


class SessionLearningHook(HookBase):
    """Schedule learning after a successful, terminal assistant turn."""

    async def turn_finish(self, ctx: HookContext) -> None:
        session_id = str(ctx.input.get("sessionID") or "")
        workspace = str(ctx.input.get("workspace") or ".")
        model = ctx.input.get("model") or {}
        provider_id = str(model.get("providerID") or "")
        model_id = str(model.get("modelID") or "")
        if not all((session_id, provider_id, model_id)):
            return

        schedule_session_learning(
            session_id=session_id,
            workspace=workspace,
            provider_id=provider_id,
            model_id=model_id,
        )


def schedule_session_learning(
    *,
    session_id: str,
    workspace: str,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> None:
    """Schedule a checkpointed learning run without blocking the lifecycle."""

    async def run() -> None:
        try:
            from flocks.memory.learning import process_completed_session

            await process_completed_session(
                session_id=session_id,
                workspace=workspace,
                provider_id=provider_id,
                model_id=model_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "session_learning.failed",
                {"session_id": session_id, "error": str(exc)},
            )

    task = asyncio.create_task(run(), name=f"session-learning:{session_id}")
    track_background_task(task, session_id=session_id)


async def _handle_new_command(event: HookEvent) -> None:
    if event.type != "command" or event.action != "new":
        return
    previous_session_id = event.context.get("previous_session_id")
    if not previous_session_id:
        return
    schedule_session_learning(
        session_id=str(previous_session_id),
        workspace=str(event.context.get("workspace_dir") or "."),
    )


def _schedule_catch_up() -> None:
    global _catch_up_task

    if _catch_up_task is not None and not _catch_up_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def run() -> None:
        try:
            from flocks.memory.learning import catch_up_completed_sessions

            await catch_up_completed_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn("session_learning.catch_up_failed", {"error": str(exc)})

    _catch_up_task = loop.create_task(run(), name="session-learning:catch-up")
    track_background_task(_catch_up_task)


def register_session_learning_hook() -> None:
    """Register the learning hook idempotently."""
    HookPipeline.register(
        "builtin.session-learning",
        SessionLearningHook(),
        order=200,
        timeout_seconds=2.0,
    )
    register_hook(
        event_key="command:new",
        handler=_handle_new_command,
        metadata={
            "name": "session-learning",
            "description": "Run Dream and skill evolution for the prior session",
            "priority": 200,
        },
    )
    _schedule_catch_up()
