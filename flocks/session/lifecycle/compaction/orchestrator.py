"""Shared orchestration helpers for explicit and loop-driven compaction."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, Optional, Sequence

from flocks.provider.provider import Provider
from flocks.session.core.status import (
    COMPACTING_DEFAULT_MESSAGE,
    SessionStatus,
    SessionStatusBusy,
    SessionStatusCompacting,
)
from flocks.session.lifecycle.compaction.compaction import SessionCompaction
from flocks.session.lifecycle.compaction.policy import CompactionPolicy
from flocks.utils.log import Log

log = Log.create(service="session.compaction.orchestrator")

EventPublishCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
StatusAfter = Literal["idle", "busy"]


def build_compaction_policy(provider_id: str, model_id: str) -> CompactionPolicy:
    """Resolve a policy from provider metadata, with a safe default fallback."""
    context_window, max_output_tokens, max_input_tokens = Provider.resolve_model_info(
        provider_id,
        model_id,
    )
    if context_window > 0:
        return CompactionPolicy.from_model(
            context_window=context_window,
            max_output_tokens=max_output_tokens or 4096,
            max_input_tokens=max_input_tokens,
        )

    log.warn("compaction.policy.fallback", {
        "provider_id": provider_id,
        "model_id": model_id,
        "reason": "context_window not found, using default policy",
    })
    return CompactionPolicy.default()


def _serialize_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            serialized.append(msg)
        elif hasattr(msg, "model_dump"):
            serialized.append(msg.model_dump())
        elif hasattr(msg, "__dict__"):
            serialized.append(msg.__dict__)
        else:
            serialized.append(vars(msg))
    return serialized


async def run_compaction(
    session_id: str,
    *,
    parent_message_id: str,
    messages: Sequence[Any],
    provider_id: str,
    model_id: str,
    auto: bool,
    event_publish_callback: Optional[EventPublishCallback] = None,
    policy: Optional[CompactionPolicy] = None,
    status_after: StatusAfter = "idle",
) -> Literal["continue", "stop"]:
    """Run compaction with shared status transitions and event publishing."""
    resolved_policy = policy
    if resolved_policy is None:
        resolved_policy = build_compaction_policy(provider_id, model_id)

    SessionStatus.set(
        session_id,
        SessionStatusCompacting(message=COMPACTING_DEFAULT_MESSAGE),
    )
    if event_publish_callback:
        await event_publish_callback("session.status", {
            "sessionID": session_id,
            "status": {
                "type": "compacting",
                "message": COMPACTING_DEFAULT_MESSAGE,
            },
        })

    try:
        return await SessionCompaction.process(
            session_id=session_id,
            parent_id=parent_message_id,
            messages=_serialize_messages(messages),
            model_id=model_id,
            provider_id=provider_id,
            auto=auto,
            policy=resolved_policy,
        )
    finally:
        if status_after == "busy":
            SessionStatus.set(session_id, SessionStatusBusy())
            status_payload = {"type": "busy"}
        else:
            SessionStatus.clear(session_id)
            status_payload = {"type": "idle"}

        if event_publish_callback:
            await event_publish_callback("session.status", {
                "sessionID": session_id,
                "status": status_payload,
            })
