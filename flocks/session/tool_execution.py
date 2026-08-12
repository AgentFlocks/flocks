"""Core helpers for unified session tool-execution contracts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from flocks.auth.context import API_TOKEN_SERVICE_USER_ID
from flocks.hooks.contracts import (
    ToolExecutionActor,
    ToolExecutionActorAgent,
    ToolExecutionActorSubject,
    ToolExecutionContext,
    ToolExecutionIdentity,
    ToolExecutionSafetyMode,
    ToolExecutionSession,
    ToolExecutionSessionProject,
    ToolExecutionTool,
    redact_sensitive,
    schema_digest,
    utc_now_iso,
)
from flocks.session.execution_profile import get_session_execution_profile


T = TypeVar("T")


def _resolve_actor_subject(profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
    subject_id = (
        str(
            profile.get("subject_id")
            or profile.get("owner_user_id")
            or profile.get("owner_username")
            or ""
        )
        .strip()
        or None
    )
    explicit_type = str(profile.get("subject_type") or "").strip().lower()
    if explicit_type:
        return subject_id, explicit_type
    if subject_id is None:
        return None, None
    if subject_id == API_TOKEN_SERVICE_USER_ID:
        return subject_id, "service"
    return subject_id, "human"


class ToolExecutionConfirmationRequired(RuntimeError):
    """Raised for non-Registry callers when a tool hook requests confirmation."""


async def run_tool_execution_lifecycle(
    payload: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    *,
    patched_effect: Callable[[Mapping[str, Any]], Awaitable[T]] | None = None,
) -> T:
    """Run an already-normalized operation through the canonical tool lifecycle.

    Non-Registry tool entry points use this adapter to share decision and
    terminal-outcome semantics without repurposing generic action hooks.
    """
    from flocks.hooks.contracts import HookDecisionAction, ToolDecision, ToolExecutionOutcome
    from flocks.hooks.execution import execution_stop_error
    from flocks.hooks.pipeline import HookPipeline

    started = time.perf_counter()
    result: T | None = None
    status = "error"
    error: BaseException | None = None
    try:
        ctx = await HookPipeline.run_tool_before(payload)
        raw_decision = ctx.output.get("decision")
        normalized_decision = (
            {
                key: value
                for key, value in raw_decision.items()
                if key in {"action", "reason", "labels", "validated_input_patch"}
            }
            if isinstance(raw_decision, Mapping)
            else {}
        )
        decision = ToolDecision.model_validate(
            normalized_decision
        )
        legacy_stop = execution_stop_error(ctx)
        if legacy_stop is not None:
            decision = ToolDecision(
                action=HookDecisionAction.DENY,
                reason=str(legacy_stop),
            )
        if decision.action is HookDecisionAction.DENY:
            status = "deny"
            raise PermissionError(decision.reason or "Tool execution denied by hook")
        if decision.action is HookDecisionAction.CONFIRM:
            status = "confirm"
            raise ToolExecutionConfirmationRequired(
                decision.reason or "Tool execution requires confirmation"
            )
        if decision.validated_input_patch:
            if patched_effect is None:
                raise ValueError(
                    "This tool entry point does not support validated_input_patch"
                )
            result = await patched_effect(decision.validated_input_patch)
        else:
            result = await effect()
        status = "success"
        return result
    except asyncio.CancelledError as exc:
        status = "cancelled"
        error = exc
        raise
    except BaseException as exc:
        error = exc
        if status not in {"deny", "confirm"}:
            status = "error"
        raise
    finally:
        outcome = ToolExecutionOutcome(
            status=status,
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
            result_summary=(
                {"type": type(result).__name__} if result is not None else None
            ),
        )
        try:
            await HookPipeline.run_tool_after({
                **payload,
                "outcome": outcome.model_dump(mode="json"),
            })
        except Exception:
            # An after hook is observational; it must never replace the
            # operation's terminal success, denial, or cancellation.
            pass


async def build_session_tool_execution_payload(
    *,
    session_id: str,
    message_id: str,
    agent: str,
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_schema: Mapping[str, Any],
    tool_context_extra: Mapping[str, Any] | None = None,
    validated_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical, versioned tool execution payload."""
    extra = dict(tool_context_extra or {})
    if not isinstance(extra.get("session_execution_profile"), dict):
        profile = await get_session_execution_profile(session_id)
        if isinstance(profile, dict):
            extra["session_execution_profile"] = profile
    profile = (
        dict(extra.get("session_execution_profile"))
        if isinstance(extra.get("session_execution_profile"), Mapping)
        else {}
    )
    execution_context = (
        dict(extra.get("execution_context"))
        if isinstance(extra.get("execution_context"), Mapping)
        else {}
    )
    profile_entry = str(profile.get("entry") or "unknown").strip() or "unknown"
    runtime_mode = str(profile.get("runtime_mode") or "exe-mode").strip().lower()
    permission_mode = str(profile.get("permission_mode") or "readonly").strip().lower()
    network_mode = str(profile.get("network_mode") or "require-confirm").strip().lower()
    actor_subject_id, actor_subject_type = _resolve_actor_subject(profile)
    workspace_root = str(
        (profile.get("workspace_dir") or extra.get("workspace_dir") or "")
    ).strip() or None
    raw_input = redact_sensitive(dict(tool_input or {}))
    normalized_input = redact_sensitive(dict(validated_input or {}))
    tool_schema_digest = schema_digest(dict(tool_schema))
    context = ToolExecutionContext(
        execution=ToolExecutionIdentity(
            id=str(execution_context.get("execution_id") or message_id),
            trace_id=(
                str(execution_context.get("trace_id") or "").strip() or message_id
            ),
            tool_call_id=str(extra.get("tool_call_id") or "").strip() or None,
            attempt=int(execution_context.get("attempt") or 1),
            started_at=utc_now_iso(),
        ),
        session=ToolExecutionSession(
            id=session_id,
            message_id=message_id,
            turn_id=(
                str(execution_context.get("turn_id") or "").strip() or message_id
            ),
            step=int(execution_context.get("step") or 0),
            entry=profile_entry,
            workspace_root=workspace_root,
            project=ToolExecutionSessionProject(
                id=str(profile.get("project_id") or "").strip() or None,
                root=str(profile.get("project_root") or "").strip() or None,
                revision=str(profile.get("project_revision") or "").strip() or None,
            ),
        ),
        actor=ToolExecutionActor(
            subject=ToolExecutionActorSubject(
                id=actor_subject_id,
                type=actor_subject_type,
            ),
            agent=ToolExecutionActorAgent(id=agent),
        ),
        tool=ToolExecutionTool(
            name=tool_name,
            source=str(extra.get("tool_source") or "").strip() or None,
            category=str(extra.get("tool_category") or "").strip() or None,
            raw_input=raw_input,
            validated_input=normalized_input,
            schema_digest=tool_schema_digest,
        ),
        safety_mode=ToolExecutionSafetyMode(
            runtime_mode=runtime_mode,
            permission_mode=permission_mode,
            network_mode=network_mode,
        ),
        extension_context=redact_sensitive(
            dict(extra.get("extension_context"))
            if isinstance(extra.get("extension_context"), Mapping)
            else {}
        ),
    )
    return {
        "operation": "tool.execute",
        "tool_execution": context.model_dump(mode="json"),
    }
