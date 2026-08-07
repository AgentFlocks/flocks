"""Public entry point and lifecycle owner for session execution."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from flocks.session.core.context import DefaultSessionContext
from flocks.session.core.status import (
    SessionStatus,
    SessionStatusBusy,
    SessionStatusIdle,
)
from flocks.session.core.turn_state import clear_turn_state
from flocks.session.message import Message
from flocks.session.runtime.agent_loop import AgentLoop
from flocks.session.runtime.continuation_policy import (
    DEFAULT_CONTINUATION_POLICY,
    ContinuationPolicy,
)
from flocks.session.runtime.contracts import (
    AgentRunOutcome,
    AgentRunStatus,
    RuntimeModel,
)
from flocks.session.runtime.event_sink import SessionEventSink
from flocks.session.runtime.model_policy import (
    DEFAULT_MODEL_ROUTING_POLICY,
    ModelRoutingPolicy,
)
from flocks.session.runtime.session_turn import (
    LoopContext,
    LoopCallbacks,
    LoopResult,
)
from flocks.session.runtime.step_engine import StepEngine
from flocks.session.session import (
    Session,
    is_model_auto_session_category,
)
from flocks.utils.log import Log


log = Log.create(service="session.loop")

@dataclass(frozen=True)
class _SessionLease:
    """One process-local ownership record."""

    session_id: str
    turn: LoopContext


class _SessionLeaseRegistry:
    """Keep lease bookkeeping out of the SessionLoop control flow."""

    def __init__(self, active_turns: MutableMapping[str, LoopContext]):
        self._active_turns = active_turns

    def get(self, session_id: str) -> Optional[LoopContext]:
        return self._active_turns.get(session_id)

    def acquire(
        self,
        session_id: str,
        turn: LoopContext,
    ) -> Optional[_SessionLease]:
        if session_id in self._active_turns:
            return None
        self._active_turns[session_id] = turn
        return _SessionLease(session_id=session_id, turn=turn)

    def release(self, lease: _SessionLease) -> None:
        if self._active_turns.get(lease.session_id) is lease.turn:
            self._active_turns.pop(lease.session_id, None)

    def owns(self, lease: _SessionLease) -> bool:
        return self._active_turns.get(lease.session_id) is lease.turn


class SessionLoop:
    """Decide whether a persistent session should continue or settle."""

    _active_turns: ClassVar[dict[str, LoopContext]] = {}
    _leases: ClassVar[_SessionLeaseRegistry] = _SessionLeaseRegistry(
        _active_turns,
    )
    _model_policy: ClassVar[ModelRoutingPolicy] = DEFAULT_MODEL_ROUTING_POLICY
    _continuation_policy: ClassVar[ContinuationPolicy] = (
        DEFAULT_CONTINUATION_POLICY
    )

    @classmethod
    async def run(
        cls,
        session_id: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[LoopCallbacks] = None,
        working_directory: Optional[str] = None,
        auto_failover: bool = False,
    ) -> LoopResult:
        """Run one session until queued and synthetic continuations settle."""
        active_turn = cls._leases.get(session_id)
        if active_turn is not None:
            log.info("session.already_running", {"session_id": session_id})
            cls._authorize_auto_failover(active_turn, auto_failover)
            return LoopResult(
                action="queued",
                error="Loop already running",
            )

        session = await Session.get_by_id(session_id)
        if session is None:
            log.warning("session.not_found", {"session_id": session_id})
            return LoopResult(
                action="error",
                error=f"Session {session_id} not found",
            )
        if session.status != "active":
            log.warning(
                "session.not_active",
                {"session_id": session_id, "status": session.status},
            )
            return LoopResult(
                action="error",
                error=f"Session {session_id} is {session.status}",
            )
        if working_directory:
            session = session.model_copy(
                update={"directory": working_directory},
            )

        if not provider_id or not model_id:
            resolved_provider, resolved_model = await cls._resolve_model(
                session,
                provider_id,
                model_id,
            )
            provider_id = provider_id or resolved_provider
            model_id = model_id or resolved_model

        primary_model = RuntimeModel(
            provider_id=provider_id,
            model_id=model_id,
        )
        auto_failover = bool(
            auto_failover
            and is_model_auto_session_category(
                getattr(session, "category", "user"),
            )
        )
        session.provider = provider_id
        session.model = model_id

        trace_offset = await cls._load_trace_offset(session_id)
        runtime_callbacks = callbacks or LoopCallbacks()
        turn = LoopContext(
            session=session,
            provider_id=provider_id,
            model_id=model_id,
            agent_name=agent_name or session.agent or "rex",
            callbacks=runtime_callbacks,
            session_store=DefaultSessionContext(session),
            trace_step_offset=trace_offset,
            auto_failover=auto_failover,
            auto_failover_allowed=auto_failover,
            model_candidates=[primary_model],
            candidate_index=0,
            session_start_pending=trace_offset == 0,
            model_policy=cls._model_policy,
            continuation_policy=cls._continuation_policy,
        )
        lease_or_result = await cls._acquire_lease(session_id, turn)
        if not isinstance(lease_or_result, _SessionLease):
            return lease_or_result
        lease = lease_or_result

        settled = False
        processed_user_id: Optional[str] = None
        try:
            await cls._mark_busy(session_id, runtime_callbacks)
            await cls._recover_orphan_tools(session_id)

            while True:
                continuation_policy = (
                    turn.continuation_policy or cls._continuation_policy
                )
                try:
                    await continuation_policy.prepare_logical_turn(turn)
                    processed_user_id = (
                        turn.prepared_user_id or processed_user_id
                    )
                    outcome = await cls._run_logical_input(turn)
                    if await cls._should_continue(
                        turn,
                        continuation_policy,
                        outcome,
                    ):
                        continue
                except Exception as exc:
                    outcome = await cls._handle_execution_error(
                        turn,
                        exc,
                    )

                if await cls._settle_or_continue(
                    lease,
                    runtime_callbacks,
                    processed_user_id,
                ):
                    continue

                settled = True
                return cls._to_loop_result(turn, outcome)
        finally:
            if not settled and cls._leases.owns(lease):
                await cls._release_session(lease, runtime_callbacks)

    @classmethod
    async def _run_logical_input(
        cls,
        turn: LoopContext,
    ) -> AgentRunOutcome[Any]:
        """Execute one prepared logical input through AgentLoop."""
        return await AgentLoop().run(
            turn,
            StepEngine.from_turn(turn),
        )

    @staticmethod
    async def _should_continue(
        turn: LoopContext,
        continuation_policy: ContinuationPolicy,
        outcome: AgentRunOutcome[Any],
    ) -> bool:
        """Resolve queued input, goal, and TurnFinish continuation."""
        if outcome.status == AgentRunStatus.INPUT_AVAILABLE:
            return True
        if (
            outcome.status == AgentRunStatus.COMPLETED
            and outcome.step_result is not None
        ):
            continuation = await continuation_policy.resolve(turn, outcome)
            return continuation.should_continue
        return False

    @classmethod
    def is_running(cls, session_id: str) -> bool:
        """Return whether this process owns the session."""
        return session_id in cls._active_turns

    @classmethod
    def get_context(cls, session_id: str) -> Optional[LoopContext]:
        """Return the active turn used by public session controls."""
        return cls._active_turns.get(session_id)

    @classmethod
    def abort(cls, session_id: str) -> bool:
        """Abort one active session run."""
        turn = cls._active_turns.get(session_id)
        if turn is None:
            return False
        turn.signal_abort()
        return True

    @classmethod
    def abort_children(cls, parent_session_id: str) -> int:
        """Abort all active descendants of one parent session."""
        aborted = 0
        child_ids = [
            session_id
            for session_id, turn in list(cls._active_turns.items())
            if getattr(turn.session, "parent_id", None) == parent_session_id
        ]
        for session_id in child_ids:
            turn = cls._active_turns.get(session_id)
            if turn is not None and not turn.aborted:
                turn.signal_abort()
                aborted += 1
            aborted += cls.abort_children(session_id)
        return aborted

    @classmethod
    def clear_auto_failover_state(cls, session_id: str) -> None:
        """Clear model-routing cooldown state for one session."""
        cls._model_policy.clear(session_id)

    @classmethod
    async def validate_runtime_model(
        cls,
        provider_id: str,
        model_id: str,
        *,
        config: Optional[Any] = None,
    ) -> tuple[bool, str]:
        """Validate one provider/model candidate."""
        return await cls._model_policy.validate_runtime_model(
            provider_id,
            model_id,
            config=config,
        )

    @classmethod
    async def validate_auto_configuration(cls) -> tuple[bool, str]:
        """Validate that Auto mode has an available primary model."""
        from flocks.config.config import Config

        default_llm = await Config.resolve_default_llm()
        if not default_llm:
            return False, "default_model_missing"
        available, reason = await cls.validate_runtime_model(
            default_llm["provider_id"],
            default_llm["model_id"],
        )
        if not available:
            return False, f"primary_{reason}"
        return True, "available"

    @staticmethod
    async def _resolve_model(
        session: Any,
        provider_id: Optional[str],
        model_id: Optional[str],
        *,
        include_source: bool = False,
    ) -> tuple:
        """Resolve the concrete model used to open a session turn."""
        import os

        resolved_provider = provider_id
        resolved_model = model_id
        source = "explicit" if provider_id and model_id else "unknown"

        if (
            (not resolved_provider or not resolved_model)
            and Session.has_pinned_model(session)
        ):
            resolved_provider = resolved_provider or session.provider
            resolved_model = resolved_model or session.model
            if resolved_provider and resolved_model:
                source = "session"

        if not resolved_provider or not resolved_model:
            agent_name = getattr(session, "agent", None)
            if agent_name:
                try:
                    from flocks.storage.storage import Storage

                    overrides = await Storage.read("agent/model_overrides")
                    if isinstance(overrides, dict) and agent_name in overrides:
                        override = overrides[agent_name]
                        override_provider = override.get("providerID")
                        override_model = override.get("modelID")
                        if override_provider and override_model:
                            resolved_provider = override_provider
                            resolved_model = override_model
                            source = "agent_override"
                except Exception as exc:
                    log.debug(
                        "loop.resolve_model.storage_override_failed",
                        {"error": str(exc)},
                    )

        if not resolved_provider or not resolved_model:
            agent_name = getattr(session, "agent", None)
            if agent_name:
                try:
                    from flocks.agent.registry import Agent

                    agent_info = await Agent.get(agent_name)
                    if agent_info and agent_info.model:
                        resolved_provider = (
                            resolved_provider
                            or agent_info.model.provider_id
                        )
                        resolved_model = (
                            resolved_model or agent_info.model.model_id
                        )
                        if resolved_provider and resolved_model:
                            source = "agent"
                except Exception as exc:
                    log.debug(
                        "loop.resolve_model.agent_model_failed",
                        {"error": str(exc)},
                    )

        if not resolved_provider or not resolved_model:
            parent_id = getattr(session, "parent_id", None)
            if parent_id:
                try:
                    parent = await Session.get_by_id(parent_id)
                    if Session.has_pinned_model(parent):
                        resolved_provider = resolved_provider or getattr(
                            parent,
                            "provider",
                            None,
                        )
                        resolved_model = resolved_model or getattr(
                            parent,
                            "model",
                            None,
                        )
                        if resolved_provider and resolved_model:
                            source = "parent_session"
                except Exception as exc:
                    log.debug(
                        "loop.resolve_model.parent_failed",
                        {"error": str(exc)},
                    )

        if not resolved_provider or not resolved_model:
            try:
                from flocks.config.config import Config

                default_llm = await Config.resolve_default_llm()
                if default_llm:
                    resolved_provider = (
                        resolved_provider or default_llm["provider_id"]
                    )
                    resolved_model = (
                        resolved_model or default_llm["model_id"]
                    )
                    if resolved_provider and resolved_model:
                        source = "config"
            except Exception as exc:
                log.debug(
                    "loop.resolve_model.config_default_failed",
                    {"error": str(exc)},
                )

        if not resolved_provider:
            resolved_provider = os.environ.get("LLM_PROVIDER")
        if not resolved_model:
            resolved_model = os.environ.get("LLM_MODEL")
        if resolved_provider and resolved_model and source == "unknown":
            source = "env_default"

        from flocks.session.core.defaults import (
            fallback_model_id,
            fallback_provider_id,
        )

        resolved_provider = resolved_provider or fallback_provider_id()
        resolved_model = resolved_model or fallback_model_id()
        if source == "unknown":
            source = "fallback"

        if include_source:
            return resolved_provider, resolved_model, source
        return resolved_provider, resolved_model

    @classmethod
    def _to_loop_result(
        cls,
        turn: LoopContext,
        outcome: AgentRunOutcome[Any],
    ) -> LoopResult:
        loop_error = (
            outcome.error
            if outcome.status
            in {
                AgentRunStatus.RETRYABLE_FAILURE,
                AgentRunStatus.FATAL_FAILURE,
                AgentRunStatus.CONTEXT_OVERFLOW,
            }
            else None
        )
        unhandled_runtime_error = outcome.unhandled_error
        return LoopResult(
            action=(
                "error"
                if (
                    unhandled_runtime_error
                    or (turn.auto_failover and loop_error)
                )
                else "stop"
            ),
            last_message=outcome.last_message,
            error=(
                loop_error
                if unhandled_runtime_error or turn.auto_failover
                else None
            ),
            provider_id=turn.provider_id,
            model_id=turn.model_id,
            metadata={
                "steps": turn.step,
                "session_id": turn.session.id,
                "last_compaction_step": turn.last_compaction_step,
                **(
                    {"aborted": True}
                    if outcome.status == AgentRunStatus.ABORTED
                    else {}
                ),
            },
        )

    @staticmethod
    def _authorize_auto_failover(
        turn: LoopContext,
        requested: bool,
    ) -> None:
        if requested and is_model_auto_session_category(
            getattr(turn.session, "category", "user"),
        ):
            turn.auto_failover_allowed = True

    @staticmethod
    async def _load_trace_offset(session_id: str) -> int:
        try:
            messages = await Message.list(session_id)
            return sum(
                1 for message in messages if message.role == "assistant"
            )
        except Exception as exc:
            log.debug("session.trace_offset.error", {"error": str(exc)})
            return 0

    @classmethod
    async def _acquire_lease(
        cls,
        session_id: str,
        turn: LoopContext,
    ) -> _SessionLease | LoopResult:
        async with Session.lifecycle_lock(session_id):
            latest_session = await Session.get_by_id(session_id)
            if latest_session is None:
                return LoopResult(
                    action="error",
                    error=f"Session {session_id} not found",
                )
            if latest_session.status != "active":
                return LoopResult(
                    action="error",
                    error=f"Session {session_id} is {latest_session.status}",
                )
            if Session.is_lifecycle_transitioning(session_id):
                return LoopResult(
                    action="error",
                    error=f"Session {session_id} is changing lifecycle state",
                )
            lease = cls._leases.acquire(session_id, turn)
            if lease is None:
                return LoopResult(
                    action="queued",
                    error="Loop already running",
                )
            return lease

    @staticmethod
    async def _mark_busy(
        session_id: str,
        callbacks: LoopCallbacks,
    ) -> None:
        SessionStatus.set(session_id, SessionStatusBusy())
        await SessionEventSink.session_status(callbacks, session_id, "busy")

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

    @staticmethod
    async def _handle_execution_error(
        turn: LoopContext,
        error: Exception,
    ) -> AgentRunOutcome[Any]:
        session_id = turn.session.id
        log.error(
            "session.execution_error",
            {"session_id": session_id, "error": str(error)},
        )
        if turn.callbacks.on_error:
            try:
                await turn.callbacks.on_error(str(error))
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
        return AgentRunOutcome(
            status=AgentRunStatus.FATAL_FAILURE,
            error=str(error),
            unhandled_error=True,
        )

    @classmethod
    async def _release_session(
        cls,
        lease: _SessionLease,
        callbacks: LoopCallbacks,
    ) -> None:
        async with Session.lifecycle_lock(lease.session_id):
            cls._finalize_release_state_locked(lease)
        await cls._publish_released(lease.turn, callbacks)

    @classmethod
    async def _settle_or_continue(
        cls,
        lease: _SessionLease,
        callbacks: LoopCallbacks,
        processed_user_id: Optional[str],
    ) -> bool:
        """Atomically keep ownership for late input or settle idle."""
        async with Session.lifecycle_lock(lease.session_id):
            if await lease.turn.has_late_input(processed_user_id):
                log.info(
                    "session.continuing_for_late_input",
                    {
                        "session_id": lease.session_id,
                        "processed_user_id": processed_user_id,
                    },
                )
                return True
            cls._finalize_release_state_locked(lease)

        await cls._publish_released(lease.turn, callbacks)
        return False

    @classmethod
    def _finalize_release_state_locked(cls, lease: _SessionLease) -> None:
        clear_turn_state(lease.session_id)
        SessionStatus.set(lease.session_id, SessionStatusIdle())
        cls._leases.release(lease)

    @staticmethod
    async def _publish_released(
        turn: LoopContext,
        callbacks: LoopCallbacks,
    ) -> None:
        session_id = turn.session.id
        await SessionEventSink.session_status(callbacks, session_id, "idle")
        try:
            await Session.touch(turn.session.project_id, session_id)
        except Exception as exc:
            log.warn(
                "session.touch_failed",
                {"session_id": session_id, "error": str(exc)},
            )

        try:
            from flocks.bus.bus import Bus
            from flocks.bus.events import SessionIdle

            await Bus.publish(SessionIdle, {"sessionID": session_id})
        except Exception as exc:
            log.warn("session.idle_event_failed", {"error": str(exc)})


__all__ = [
    "SessionLoop",
    "LoopContext",
    "LoopCallbacks",
    "LoopResult",
]
