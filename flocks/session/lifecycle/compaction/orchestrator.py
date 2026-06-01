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
ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
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
    focus_instruction: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Literal["continue", "stop", "skipped"]:
    """Run compaction with shared status transitions and event publishing.

    Return values:
      ``"continue"`` — compaction completed; summary/archive were written.
      ``"stop"``     — compaction failed in a way that should abort the
                       caller's outer loop (provider unavailable, etc.).
      ``"skipped"``  — anti-thrashing or summary-provider cooldown fired;
                       no summary was written and the caller MUST NOT treat
                       this as success (no cooldown-step update, no
                       ``context.compacted`` event).

    ``focus_instruction`` is an optional free-form user directive (used
    by manual ``/compact <focus>`` invocations) forwarded verbatim to
    ``SessionCompaction.process`` so the summariser biases what
    information it preserves.

    ``progress_callback`` is forwarded verbatim to
    ``SessionCompaction.process`` and receives ``(stage, data)`` events
    for each pipeline phase (``load`` / ``strategy`` / ``chunk_done`` /
    ``merge_started`` / ``merge_done`` / ``summarize_done`` /
    ``complete``).  The route layer wires this onto an SSE event so the
    UI can render a live multi-stage panel.  ``None`` disables
    progress reporting (compaction itself is unaffected).
    """
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
        result = await SessionCompaction.process(
            session_id=session_id,
            parent_id=parent_message_id,
            messages=_serialize_messages(messages),
            model_id=model_id,
            provider_id=provider_id,
            auto=auto,
            policy=resolved_policy,
            focus_instruction=focus_instruction,
            progress_callback=progress_callback,
        )

        # E5: post-prune.  After the summary is written we still have the
        # PRESERVE_LAST tail in the conversation; those messages may
        # themselves carry stale tool outputs that the per-tool retention
        # policy could now expire (e.g. a ``bash`` from the turn that
        # just rolled out of its 1-turn window).  Running ``prune()``
        # again here is cheap and meaningfully lowers the chance the
        # very next turn re-triggers compaction.  Failures are swallowed
        # — they cannot invalidate the summary we just wrote.
        try:
            await SessionCompaction.prune(
                session_id,
                policy=resolved_policy,
            )
        except Exception as exc:
            log.warn("compaction.post_prune_failed", {
                "session_id": session_id,
                "error": str(exc),
            })

        return result
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
