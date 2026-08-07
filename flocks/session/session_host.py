"""Session lifecycle host for one resumable agent execution."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, replace
from typing import Any, Optional

from flocks.agent.runtime.contracts import (
    ModelTurnSnapshot,
    RuntimeModel,
    StepResult,
)
from flocks.session.core.context import DefaultSessionContext
from flocks.session.core.status import (
    SessionStatus,
    SessionStatusBusy,
    SessionStatusIdle,
)
from flocks.session.core.turn_state import clear_turn_state
from flocks.session.message import Message
from flocks.session.runtime_adapters import create_default_runtime_ports
from flocks.session.session import Session, is_model_auto_session_category
from flocks.utils.log import Log


log = Log.create(service="session.host")


@dataclass(frozen=True)
class SessionHostDependencies:
    """Compatibility adapters supplied by the public SessionLoop facade."""

    create_context: Callable[..., Any]
    create_callbacks: Callable[[], Any]
    create_result: Callable[..., Any]
    resolve_model: Callable[..., Awaitable[tuple[str, str]]]
    run_logical_turn: Callable[[Any, Any], Awaitable[Any]]
    publish_session_status: Callable[[Any, str, str], Awaitable[None]]


@dataclass(frozen=True)
class SessionLease:
    """Process-local ownership record protected by Session.lifecycle_lock."""

    session_id: str
    context: Any


class SessionLeaseRegistry:
    """Manage active session ownership without exposing lifecycle policy."""

    def __init__(self, active_contexts: MutableMapping[str, Any]):
        self._active_contexts = active_contexts

    def get(self, session_id: str) -> Optional[Any]:
        """Return the active context, if this process owns the session."""
        return self._active_contexts.get(session_id)

    def acquire(self, session_id: str, context: Any) -> Optional[SessionLease]:
        """Acquire process-local ownership; caller holds the lifecycle lock."""
        if session_id in self._active_contexts:
            return None
        self._active_contexts[session_id] = context
        return SessionLease(session_id=session_id, context=context)

    def release(self, lease: SessionLease) -> None:
        """Release ownership only when the stored context is still ours."""
        if self._active_contexts.get(lease.session_id) is lease.context:
            self._active_contexts.pop(lease.session_id, None)


class SessionHostStepEngine:
    """Apply host-owned cross-model recovery around one-candidate attempts."""

    def __init__(
        self,
        *,
        context: Any,
        callbacks: Any,
        attempt_engine: Any,
        cooldowns: MutableMapping[str, Any],
        cooldown_factory: Callable[..., Any],
        select_candidate: Callable[[Any, int], None],
        finalize_failure: Callable[[Any, Any, Any], Awaitable[None]],
        publish_event: Callable[[Any, str, dict[str, Any]], Awaitable[None]],
        rate_limit_cooldown_seconds: float,
        chain_exhaustion_cooldown_seconds: float,
    ):
        self._context = context
        self._callbacks = callbacks
        self._attempt_engine = attempt_engine
        self._cooldowns = cooldowns
        self._cooldown_factory = cooldown_factory
        self._select_candidate = select_candidate
        self._finalize_failure = finalize_failure
        self._publish_event = publish_event
        self._rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self._chain_exhaustion_cooldown_seconds = chain_exhaustion_cooldown_seconds

    async def run(self, snapshot: ModelTurnSnapshot[Any]) -> StepResult:
        """Retry a replay-safe snapshot across the configured model chain."""
        while True:
            active_model = RuntimeModel(
                self._context.provider_id,
                self._context.model_id,
            )
            result = await self._attempt_engine.run(
                replace(snapshot, active_model=active_model),
            )
            failure = result.failure
            if not self._context.auto_failover or failure is None:
                return result

            next_index = self._context.candidate_index + 1
            has_next = next_index < len(self._context.model_candidates)
            if not failure.allow_fallback or not failure.attempt_state.replay_safe or not has_next:
                self._record_chain_exhaustion(failure, has_next)
                await self._finalize_failure(
                    self._context,
                    failure,
                    snapshot.last_user,
                )
                return result

            if not await self._remove_failed_attempt(failure):
                await self._finalize_failure(
                    self._context,
                    failure,
                    snapshot.last_user,
                )
                return result

            await self._switch_candidate(next_index, failure.reason)

    def _record_chain_exhaustion(self, failure: Any, has_next: bool) -> None:
        context = self._context
        if not (
            context.model_candidate_policy == "automatic"
            and failure.allow_fallback
            and failure.attempt_state.replay_safe
            and not has_next
            and context.candidate_index > 0
            and failure.reason not in {"rate_limit", "billing"}
        ):
            return

        expires_at = time.monotonic() + self._chain_exhaustion_cooldown_seconds
        existing = self._cooldowns.get(context.session.id)
        if existing and existing.expires_at > expires_at:
            return
        self._cooldowns[context.session.id] = self._cooldown_factory(
            model=context.model_candidates[context.candidate_index],
            primary=context.model_candidates[0],
            expires_at=expires_at,
            reason="chain_exhausted",
        )

    async def _remove_failed_attempt(self, failure: Any) -> bool:
        message_id = failure.assistant_message_id
        if not message_id:
            return True
        try:
            deleted = await Message.delete(self._context.session.id, message_id)
        except Exception as exc:
            deleted = False
            log.error(
                "session.model.fallback_cleanup_failed",
                {
                    "session_id": self._context.session.id,
                    "message_id": message_id,
                    "error": str(exc),
                },
            )
        if not deleted:
            return False
        await self._publish_event(
            self._callbacks,
            "message.removed",
            {
                "sessionID": self._context.session.id,
                "messageID": message_id,
            },
        )
        return True

    async def _switch_candidate(self, next_index: int, reason: str) -> None:
        context = self._context
        previous = context.model_candidates[context.candidate_index]
        next_candidate = context.model_candidates[next_index]
        if context.model_candidate_policy == "automatic":
            if context.candidate_index == 0 and reason in {
                "rate_limit",
                "billing",
            }:
                self._cooldowns[context.session.id] = self._cooldown_factory(
                    model=next_candidate,
                    primary=context.model_candidates[0],
                    expires_at=(time.monotonic() + self._rate_limit_cooldown_seconds),
                    reason=reason,
                )
            else:
                cooldown = self._cooldowns.get(context.session.id)
                if cooldown and cooldown.expires_at > time.monotonic():
                    cooldown.model = next_candidate

        self._select_candidate(context, next_index)
        payload = {
            "sessionID": context.session.id,
            "from": {
                "providerID": previous.provider_id,
                "modelID": previous.model_id,
            },
            "to": {
                "providerID": next_candidate.provider_id,
                "modelID": next_candidate.model_id,
            },
            "reason": reason,
            "candidateIndex": next_index,
        }
        log.warn(
            "session.model.fallback",
            {
                "from": payload["from"],
                "to": payload["to"],
                "reason": reason,
                "candidateIndex": next_index,
            },
        )
        await self._publish_event(
            self._callbacks,
            "session.model.fallback",
            payload,
        )


class SessionHost:
    """Own session acquisition, recovery, execution, and final cleanup."""

    def __init__(
        self,
        dependencies: SessionHostDependencies,
        active_contexts: MutableMapping[str, Any],
    ):
        self._dependencies = dependencies
        self._leases = SessionLeaseRegistry(active_contexts)

    async def run(
        self,
        session_id: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[Any] = None,
        working_directory: Optional[str] = None,
        auto_failover: bool = False,
    ) -> Any:
        """Acquire and host one session until its logical turn settles."""
        active_context = self._leases.get(session_id)
        if active_context is not None:
            log.info("session.already_running", {"session_id": session_id})
            self._authorize_auto_failover(active_context, auto_failover)
            return self._dependencies.create_result(
                action="queued",
                error="Loop already running",
            )

        session = await Session.get_by_id(session_id)
        if session is None:
            log.warning("session.not_found", {"session_id": session_id})
            return self._dependencies.create_result(
                action="error",
                error=f"Session {session_id} not found",
            )
        if session.status != "active":
            log.warning(
                "session.not_active",
                {"session_id": session_id, "status": session.status},
            )
            return self._dependencies.create_result(
                action="error",
                error=f"Session {session_id} is {session.status}",
            )
        if working_directory:
            session = session.model_copy(update={"directory": working_directory})

        if not provider_id or not model_id:
            resolved_provider, resolved_model = await self._dependencies.resolve_model(
                session,
                provider_id,
                model_id,
            )
            provider_id = provider_id or resolved_provider
            model_id = model_id or resolved_model

        primary_model = RuntimeModel(provider_id=provider_id, model_id=model_id)
        auto_failover = bool(
            auto_failover
            and is_model_auto_session_category(
                getattr(session, "category", "user"),
            )
        )
        session.provider = provider_id
        session.model = model_id

        trace_offset = await self._load_trace_offset(session_id)
        context = self._dependencies.create_context(
            session=session,
            provider_id=provider_id,
            model_id=model_id,
            agent_name=agent_name or session.agent or "rex",
            session_ctx=DefaultSessionContext(session),
            trace_step_offset=trace_offset,
            auto_failover=auto_failover,
            auto_failover_allowed=auto_failover,
            model_candidates=[primary_model],
            candidate_index=0,
            session_start_pending=trace_offset == 0,
            runtime_ports=create_default_runtime_ports(),
        )
        lease_or_result = await self._acquire_lease(session_id, context)
        if not isinstance(lease_or_result, SessionLease):
            return lease_or_result
        lease = lease_or_result

        runtime_callbacks = callbacks or self._dependencies.create_callbacks()
        await self._mark_busy(session_id, runtime_callbacks)
        await self._recover_orphan_tools(session_id)

        try:
            return await self._dependencies.run_logical_turn(
                context,
                runtime_callbacks,
            )
        except Exception as exc:
            return await self._handle_execution_error(
                context,
                callbacks,
                exc,
            )
        finally:
            await self._release_session(
                lease,
                session,
                runtime_callbacks,
            )

    @staticmethod
    def _authorize_auto_failover(context: Any, requested: bool) -> None:
        if requested and is_model_auto_session_category(
            getattr(context.session, "category", "user"),
        ):
            context.auto_failover_allowed = True

    @staticmethod
    async def _load_trace_offset(session_id: str) -> int:
        try:
            messages = await Message.list(session_id)
            return sum(1 for message in messages if message.role == "assistant")
        except Exception as exc:
            log.debug("session.trace_offset.error", {"error": str(exc)})
            return 0

    async def _acquire_lease(
        self,
        session_id: str,
        context: Any,
    ) -> SessionLease | Any:
        async with Session.lifecycle_lock(session_id):
            latest_session = await Session.get_by_id(session_id)
            if latest_session is None:
                log.warning(
                    "session.not_found_before_lease",
                    {"session_id": session_id},
                )
                return self._dependencies.create_result(
                    action="error",
                    error=f"Session {session_id} not found",
                )
            if latest_session.status != "active":
                log.warning(
                    "session.not_active_before_lease",
                    {
                        "session_id": session_id,
                        "status": latest_session.status,
                    },
                )
                return self._dependencies.create_result(
                    action="error",
                    error=f"Session {session_id} is {latest_session.status}",
                )
            if Session.is_lifecycle_transitioning(session_id):
                return self._dependencies.create_result(
                    action="error",
                    error=f"Session {session_id} is changing lifecycle state",
                )
            lease = self._leases.acquire(session_id, context)
            if lease is None:
                return self._dependencies.create_result(
                    action="queued",
                    error="Loop already running",
                )
            return lease

    async def _mark_busy(self, session_id: str, callbacks: Any) -> None:
        SessionStatus.set(session_id, SessionStatusBusy())
        await self._dependencies.publish_session_status(
            callbacks,
            session_id,
            "busy",
        )

    @staticmethod
    async def _recover_orphan_tools(session_id: str) -> None:
        try:
            from flocks.session.orphan_tools import abort_orphan_running_parts

            await abort_orphan_running_parts(session_id)
        except Exception as exc:
            log.warn(
                "session.orphan_cleanup_failed",
                {"session_id": session_id, "error": str(exc)},
            )

    async def _handle_execution_error(
        self,
        context: Any,
        callbacks: Optional[Any],
        error: Exception,
    ) -> Any:
        session_id = context.session.id
        log.error(
            "session.execution_error",
            {"session_id": session_id, "error": str(error)},
        )
        if callbacks and callbacks.on_error:
            try:
                await callbacks.on_error(str(error))
            except Exception as callback_error:
                log.debug(
                    "session.error_callback_failed",
                    {"error": str(callback_error)},
                )
        try:
            from flocks.bus.bus import Bus
            from flocks.bus.events import SessionError

            await Bus.publish(
                SessionError,
                {"sessionID": session_id, "error": str(error)},
            )
        except Exception as publish_error:
            log.warn(
                "session.error_event_failed",
                {"error": str(publish_error)},
            )
        return self._dependencies.create_result(
            action="error",
            error=str(error),
            provider_id=context.provider_id,
            model_id=context.model_id,
        )

    async def _release_session(
        self,
        lease: SessionLease,
        session: Any,
        callbacks: Any,
    ) -> None:
        self._leases.release(lease)
        clear_turn_state(lease.session_id)
        SessionStatus.set(lease.session_id, SessionStatusIdle())
        await self._dependencies.publish_session_status(
            callbacks,
            lease.session_id,
            "idle",
        )
        await Session.touch(session.project_id, lease.session_id)

        try:
            from flocks.bus.bus import Bus
            from flocks.bus.events import SessionIdle

            await Bus.publish(SessionIdle, {"sessionID": lease.session_id})
        except Exception as exc:
            log.warn("session.idle_event_failed", {"error": str(exc)})
