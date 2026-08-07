"""
Session Loop Module

Core session execution loop logic extracted from runner.py.
Implements the main session processing loop with support for:
- Message processing
- Tool execution
- Compaction
- Subtask handling
- Reminders

Ported from original SessionPrompt.loop() pattern.
"""

import asyncio
import hashlib
import inspect
import time
from typing import Optional, List, Dict, Any, Callable, Awaitable, Literal
from dataclasses import dataclass, field
from datetime import datetime

from flocks.agent.runtime.agent_loop import AgentLoop
from flocks.agent.runtime.contracts import (
    AgentRunState,
    AgentRunStatus,
    ContinuationDecision,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
    QueuedInputBatch,
    RuntimeModel,
    StepResult,
    TurnPreparationStatus,
)
from flocks.agent.runtime.ports import ExternalRuntimePorts
from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.session import (
    Session,
    SessionInfo,
    is_model_auto_session_category,
)
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.core.status import SessionStatus, SessionStatusBusy
from flocks.session.core.task_utils import fire_and_forget
from flocks.session.core.turn_state import (
    set_turn_state,
    set_context_state,
)
from flocks.session.lifecycle.compaction import (
    SessionCompaction,
    CompactionPolicy,
    build_compaction_policy,
    run_compaction,
)
from flocks.session.lifecycle.compaction.compaction import _get_compaction_history
from flocks.session.prompt import SessionPrompt
from flocks.provider.provider import Provider
from flocks.session.goal import GoalManager


log = Log.create(service="session.loop")


MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3
POST_COMPACTION_COOLDOWN_STEPS = 2
RATE_LIMIT_COOLDOWN_SECONDS = 60.0
CHAIN_EXHAUSTION_COOLDOWN_SECONDS = 5.0


@dataclass
class AutoFailoverCooldown:
    """Process-local Hermes-style starting candidate cooldown."""

    model: RuntimeModel
    primary: RuntimeModel
    expires_at: float
    reason: str


@dataclass
class LoopContext:
    """Context for session loop execution"""
    session: SessionInfo
    provider_id: str
    model_id: str
    agent_name: str
    step: int = 0
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    # SessionContext interface for decoupled session access
    session_ctx: Optional[Any] = None  # Type: Optional[SessionContext]
    # Offset so observability step numbers are cumulative across the session
    trace_step_offset: int = 0
    # Track current step asyncio.Task so abort() can cancel it immediately
    _current_step_task: Optional[asyncio.Task] = field(default=None, repr=False)
    # Memory bootstrap data loaded once on step 1; passed to each SessionRunner
    memory_bootstrap_data: Optional[Dict[str, Any]] = field(default=None, repr=False)
    # Reusable runner artifacts that stay stable across steps in the same loop.
    runner_static_cache: Dict[str, Any] = field(default_factory=dict, repr=False)
    # Overflow compaction attempt counter (matches OpenClaw MAX_OVERFLOW_COMPACTION_ATTEMPTS)
    overflow_compaction_attempts: int = 0
    # Tool result truncation attempted once per run (matches OpenClaw toolResultTruncationAttempted)
    tool_result_truncation_attempted: bool = False
    # Cooldown window to prefer cheap cleanup over repeated full compaction.
    last_compaction_step: Optional[int] = None
    last_cleanup_step: Optional[int] = None
    # ``input + cache.read + output`` reported by the provider on the most
    # recent finished assistant turn.  When non-zero this beats the
    # synthetic estimate from ``estimate_full_context_tokens`` because it
    # is what the upstream will actually bill us for on the next turn
    # (matches the "observed value wins" rule from docs/design/context-compaction-v2.md §B3).
    last_observed_prompt_tokens: int = 0
    auto_failover: bool = False
    # Entrypoint authorization is separate from persisted model_auto. Only a
    # WebUI message route may set this bit; non-WebUI entrypoints use the default.
    auto_failover_allowed: bool = False
    model_candidates: List[RuntimeModel] = field(default_factory=list)
    candidate_index: int = 0
    model_candidate_policy: Literal["fixed", "automatic", "configured"] = "automatic"
    turn_user_id: Optional[str] = None
    turn_additional_context: Optional[str] = None
    stop_hook_active: bool = False
    session_start_pending: bool = False
    runtime_ports: Optional[ExternalRuntimePorts] = field(default=None, repr=False)

    @property
    def trace_step(self) -> int:
        """Session-cumulative step number for observability."""
        return self.trace_step_offset + self.step
    
    def should_abort(self) -> bool:
        """Check if loop should abort"""
        return self.abort_event.is_set()
    
    def signal_abort(self) -> None:
        """Signal abort to stop loop, and cancel the current step task if running."""
        self.abort_event.set()
        task = self._current_step_task
        if task is not None and not task.done():
            task.cancel()


@dataclass
class LoopCallbacks:
    """Callbacks for loop events"""
    on_step_start: Optional[Callable[[int], Awaitable[None]]] = None
    on_step_end: Optional[Callable[[int], Awaitable[None]]] = None
    on_compaction: Optional[Callable[[], Awaitable[None]]] = None
    on_error: Optional[Callable[[str], Awaitable[None]]] = None
    on_reminder: Optional[Callable[[str], Awaitable[None]]] = None
    # SSE event publishing callback (for TUI/WebUI real-time updates)
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    # Runner-level callbacks (text delta, tool events, permissions, etc.)
    # Type: Optional[RunnerCallbacks] - using Any to avoid circular import
    runner_callbacks: Optional[Any] = None


@dataclass
class LoopResult:
    """Result of loop execution"""
    action: str  # "stop", "continue", "compact", "error", "queued"
    last_message: Optional[MessageInfo] = None
    error: Optional[str] = None
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionLoop:
    """
    Session loop manager
    
    Handles the main session execution loop with support for:
    - Message iteration
    - Compaction triggers
    - Subtask management
    - Reminder injection
    - Loop control (abort, pause, resume)
    """
    
    # Active loop contexts by session ID
    _active_loops: Dict[str, LoopContext] = {}
    _auto_failover_cooldowns: Dict[str, AutoFailoverCooldown] = {}

    @classmethod
    def clear_auto_failover_state(cls, session_id: str) -> None:
        """Clear process-local routing state when WebUI Auto is disabled."""
        cls._auto_failover_cooldowns.pop(session_id, None)

    @classmethod
    async def validate_runtime_model(
        cls,
        provider_id: str,
        model_id: str,
        *,
        config: Optional[Any] = None,
    ) -> tuple[bool, str]:
        """Validate a configured LLM candidate without a network health probe."""
        from flocks.config.config import Config
        from flocks.provider.model_manager import get_model_manager
        from flocks.provider.types import ModelType

        Provider._ensure_initialized()
        config = config or await Config.get()
        if provider_id in (getattr(config, "disabled_providers", None) or []):
            return False, "provider_disabled"
        enabled_providers = getattr(config, "enabled_providers", None) or []
        if enabled_providers and provider_id not in enabled_providers:
            return False, "provider_disabled"
        try:
            await Provider.apply_config(config, provider_id=provider_id)
        except Exception as exc:
            log.warn("session.model.candidate_config_failed", {
                "provider_id": provider_id,
                "model_id": model_id,
                "error": str(exc),
            })
            return False, "provider_config_error"

        provider = Provider.get(provider_id)
        if provider is None:
            return False, "provider_not_found"

        definition = get_model_manager().get_model(provider_id, model_id)
        if definition is None:
            return False, "model_not_found"
        if getattr(definition, "model_type", None) != ModelType.LLM:
            return False, "not_llm"

        setting = get_model_manager().get_setting(provider_id, model_id)
        if setting is not None and not setting.enabled:
            return False, "model_disabled"
        if not provider.is_configured():
            return False, "provider_not_configured"
        return True, "available"

    @classmethod
    async def _build_model_candidates(
        cls,
        primary: RuntimeModel,
        *,
        route_seed: str,
        preferred: Optional[RuntimeModel] = None,
        config: Optional[Any] = None,
    ) -> List[RuntimeModel]:
        """Build a configured chain or the stable automatic discovery chain."""
        from flocks.config.config import Config
        from flocks.provider.model_manager import get_model_manager
        from flocks.provider.types import ModelType

        config = config or await Config.get()
        await Provider.apply_config(config)

        configured_fallbacks = getattr(config, "fallback_providers", None) or []
        if configured_fallbacks:
            candidates = [primary]
            seen = {(primary.provider_id, primary.model_id)}
            for index, raw in enumerate(configured_fallbacks):
                provider_id = (
                    raw.get("provider_id")
                    if isinstance(raw, dict)
                    else raw.provider_id
                )
                model_id = (
                    raw.get("model_id")
                    if isinstance(raw, dict)
                    else raw.model_id
                )
                candidate = RuntimeModel(
                    provider_id=provider_id,
                    model_id=model_id,
                )
                identity = (candidate.provider_id, candidate.model_id)
                if identity in seen:
                    continue
                seen.add(identity)

                available, reason = await cls.validate_runtime_model(
                    candidate.provider_id,
                    candidate.model_id,
                    config=config,
                )
                if not available:
                    log.warn("session.model.fallback_skipped", {
                        "provider_id": candidate.provider_id,
                        "model_id": candidate.model_id,
                        "configured_index": index,
                        "reason": reason,
                    })
                    continue
                candidates.append(candidate)
            return candidates

        definitions = get_model_manager().list_models(
            model_type=ModelType.LLM,
            enabled_only=True,
        )
        discovered = {
            RuntimeModel(definition.provider_id, definition.id)
            for definition in definitions
        }
        discovered.discard(primary)

        same_provider: List[RuntimeModel] = []
        other_providers: List[RuntimeModel] = []
        for candidate in sorted(
            discovered,
            key=lambda item: (item.provider_id, item.model_id),
        ):
            available, reason = await cls.validate_runtime_model(
                candidate.provider_id,
                candidate.model_id,
                config=config,
            )
            if not available:
                log.debug("session.model.fallback_skipped", {
                    "provider_id": candidate.provider_id,
                    "model_id": candidate.model_id,
                    "reason": reason,
                })
                continue

            if candidate.provider_id == primary.provider_id:
                same_provider.append(candidate)
            else:
                other_providers.append(candidate)

        candidates = [primary]
        for tier, pool in (
            ("same_provider", same_provider),
            ("other_provider", other_providers),
        ):
            if not pool:
                continue
            selected = (
                preferred
                if preferred is not None and preferred in pool
                else cls._stable_candidate_choice(pool, route_seed, tier)
            )
            candidates.append(selected)
        return candidates

    @staticmethod
    def _stable_candidate_choice(
        candidates: List[RuntimeModel],
        route_seed: str,
        tier: str,
    ) -> RuntimeModel:
        """Choose pseudo-randomly without Python's process-randomized hash()."""
        ordered = sorted(
            candidates,
            key=lambda item: (item.provider_id, item.model_id),
        )
        digest = hashlib.sha256(
            f"{route_seed}\0{tier}".encode("utf-8")
        ).digest()
        index = int.from_bytes(digest[:8], "big") % len(ordered)
        return ordered[index]

    @classmethod
    async def validate_auto_configuration(cls) -> tuple[bool, str]:
        """Validate that a newly selected Auto mode has a usable chain."""
        from flocks.config.config import Config

        default_llm = await Config.resolve_default_llm()
        if not default_llm:
            return False, "default_model_missing"
        primary = RuntimeModel(
            default_llm["provider_id"],
            default_llm["model_id"],
        )
        available, reason = await cls.validate_runtime_model(
            primary.provider_id,
            primary.model_id,
        )
        if not available:
            return False, f"primary_{reason}"
        return True, "available"

    @classmethod
    def _active_cooldown_model(
        cls,
        session_id: str,
        primary: RuntimeModel,
    ) -> Optional[RuntimeModel]:
        """Return a still-valid cooldown target for the current primary."""
        cooldown = cls._auto_failover_cooldowns.get(session_id)
        if cooldown is None:
            return None
        if cooldown.expires_at <= time.monotonic() or cooldown.primary != primary:
            cls._auto_failover_cooldowns.pop(session_id, None)
            return None
        return cooldown.model

    @classmethod
    def _cooldown_candidate_index(
        cls,
        session_id: str,
        candidates: List[RuntimeModel],
    ) -> int:
        if not candidates:
            return 0
        cooldown_model = cls._active_cooldown_model(session_id, candidates[0])
        if cooldown_model is None:
            return 0
        try:
            return candidates.index(cooldown_model)
        except ValueError:
            cls._auto_failover_cooldowns.pop(session_id, None)
            return 0

    @classmethod
    def _select_candidate(cls, ctx: LoopContext, index: int) -> None:
        candidate = ctx.model_candidates[index]
        ctx.candidate_index = index
        ctx.provider_id = candidate.provider_id
        ctx.model_id = candidate.model_id
        ctx.session.provider = candidate.provider_id
        ctx.session.model = candidate.model_id
        # Prompt and model-capability caches are keyed in most places, but a
        # fresh dict makes the runtime rebuild guarantee explicit. The tool
        # loop guard is turn state rather than model state, so it must survive
        # a provider switch to keep repeated-tool protection effective.
        tool_loop_guard = ctx.runner_static_cache.get("tool_loop_guard")
        ctx.runner_static_cache.clear()
        if tool_loop_guard is not None:
            ctx.runner_static_cache["tool_loop_guard"] = tool_loop_guard
    
    @classmethod
    def is_running(cls, session_id: str) -> bool:
        """Check if loop is running for session"""
        return session_id in cls._active_loops
    
    @classmethod
    def get_context(cls, session_id: str) -> Optional[LoopContext]:
        """Get loop context for session"""
        return cls._active_loops.get(session_id)
    
    @classmethod
    def abort(cls, session_id: str) -> bool:
        """Abort running loop"""
        ctx = cls._active_loops.get(session_id)
        if ctx:
            ctx.signal_abort()
            return True
        return False
    
    @classmethod
    def abort_children(cls, parent_session_id: str) -> int:
        """Abort all child loops whose session.parent_id matches, recursively."""
        aborted = 0
        child_ids = [
            sid for sid, ctx in list(cls._active_loops.items())
            if getattr(ctx.session, 'parent_id', None) == parent_session_id
        ]
        for sid in child_ids:
            ctx = cls._active_loops.get(sid)
            if ctx and not ctx.should_abort():
                ctx.signal_abort()
                aborted += 1
            aborted += cls.abort_children(sid)
        return aborted

    @classmethod
    async def _publish_runtime_event(
        cls,
        callbacks: "LoopCallbacks",
        event_name: str,
        payload: Dict[str, Any],
    ) -> None:
        if not callbacks.event_publish_callback:
            return
        try:
            await callbacks.event_publish_callback(event_name, payload)
        except Exception as exc:
            log.debug("loop.runtime_event.publish_failed", {
                "event": event_name,
                "error": str(exc),
            })

    @classmethod
    async def _publish_turn_stopped(
        cls,
        callbacks: "LoopCallbacks",
        session_id: str,
        *,
        step: int,
        stop_reason: str,
    ) -> None:
        turn_state = set_turn_state(
            session_id,
            step=step,
            status="stopped",
            stop_reason=stop_reason,
            queued_message_detected=False,
        )
        await cls._publish_runtime_event(
            callbacks,
            "turn.stopped",
            turn_state.model_dump(by_alias=True),
        )

    @classmethod
    async def _publish_session_status(
        cls,
        callbacks: "LoopCallbacks",
        session_id: str,
        status: str,
    ) -> None:
        if not callbacks.event_publish_callback:
            return
        try:
            await callbacks.event_publish_callback("session.status", {
                "sessionID": session_id,
                "status": {"type": status},
            })
        except Exception as exc:
            log.debug("loop.session_status.publish_failed", {
                "session_id": session_id,
                "status": status,
                "error": str(exc),
            })

    @classmethod
    async def _publish_session_notice(
        cls,
        callbacks: "LoopCallbacks",
        session_id: str,
        *,
        level: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not callbacks.event_publish_callback:
            return
        try:
            await callbacks.event_publish_callback("session.notice", {
                "sessionID": session_id,
                "level": level,
                "message": message,
                "details": details or {},
            })
        except Exception as exc:
            log.debug("loop.session_notice.publish_failed", {"error": str(exc)})

    @classmethod
    def _has_recent_compaction_cooldown(cls, ctx: LoopContext) -> bool:
        return (
            ctx.last_compaction_step is not None
            and (ctx.step - ctx.last_compaction_step) <= POST_COMPACTION_COOLDOWN_STEPS
        )

    @classmethod
    async def _detect_queued_user_message(
        cls,
        _session_id: str,
        post_messages: List[MessageInfo],
        current_user_id: str,
        _last_message: Optional[MessageInfo],
    ) -> Optional[MessageInfo]:
        if not post_messages:
            return None

        newest_user = None
        for msg in reversed(post_messages):
            if msg.role == MessageRole.USER:
                newest_user = msg
                break

        if newest_user is None:
            return None
        if newest_user.id <= current_user_id:
            return None
        # A fallback assistant is created after a user message that arrived
        # while the primary model was running. Its newer ID must not make that
        # user message look handled; the current turn's user ID is the stable
        # boundary for queued work.
        return newest_user
    
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
        """Run one session through the lifecycle-owned SessionHost."""
        from flocks.session.session_host import (
            SessionHost,
            SessionHostDependencies,
        )

        host = SessionHost(
            dependencies=SessionHostDependencies(
                create_context=LoopContext,
                create_callbacks=LoopCallbacks,
                create_result=LoopResult,
                resolve_model=cls._resolve_model,
                run_logical_turn=cls._run_loop,
                publish_session_status=cls._publish_session_status,
            ),
            active_contexts=cls._active_loops,
        )
        return await host.run(
            session_id=session_id,
            provider_id=provider_id,
            model_id=model_id,
            agent_name=agent_name,
            callbacks=callbacks,
            working_directory=working_directory,
            auto_failover=auto_failover,
        )
    
    @staticmethod
    async def _resolve_model(
        session: Any,
        provider_id: Optional[str],
        model_id: Optional[str],
        *,
        include_source: bool = False,
    ) -> tuple:
        """
        Resolve provider_id and model_id for session execution.
        
        Priority:
        1. Explicitly passed provider_id / model_id (already handled by caller)
        2. Session's stored model/provider (set during Session.create)
        3. Agent model override from Storage (set via WebUI)
        4. Agent-specific model from AgentInfo.model (agent.yaml / config)
        5. Parent session's model/provider (inherits from parent — TUI/CLI default)
        6. Global default LLM (default_models.llm -> config.model)
        7. Environment variables
        8. Hardcoded fallback
        
        Returns:
            (provider_id, model_id) tuple
        """
        import os
        
        resolved_provider = provider_id
        resolved_model = model_id
        source = "explicit" if provider_id and model_id else "unknown"
        
        # Priority 2: Session's stored model/provider
        if (not resolved_provider or not resolved_model) and Session.has_pinned_model(session):
            resolved_provider = resolved_provider or session.provider
            resolved_model = resolved_model or session.model
            if resolved_provider and resolved_model:
                source = "session"
        
        # Priority 3: Agent model override from Storage (set via WebUI)
        if not resolved_provider or not resolved_model:
            agent_name = getattr(session, 'agent', None)
            if agent_name:
                try:
                    from flocks.storage.storage import Storage
                    overrides = await Storage.read("agent/model_overrides")
                    if isinstance(overrides, dict) and agent_name in overrides:
                        override = overrides[agent_name]
                        override_provider = override.get('providerID')
                        override_model = override.get('modelID')
                        if override_provider and override_model:
                            resolved_provider = override_provider
                            resolved_model = override_model
                            source = "agent_override"
                except Exception as _e:
                    log.debug("loop.resolve_model.storage_override_failed", {"error": str(_e)})
        
        # Priority 4: Agent-specific model from AgentInfo
        if not resolved_provider or not resolved_model:
            agent_name = getattr(session, 'agent', None)
            if agent_name:
                try:
                    from flocks.agent.registry import Agent
                    agent_info = await Agent.get(agent_name)
                    if agent_info and agent_info.model:
                        resolved_provider = resolved_provider or agent_info.model.provider_id
                        resolved_model = resolved_model or agent_info.model.model_id
                        if resolved_provider and resolved_model:
                            source = "agent"
                except Exception as _e:
                    log.debug("loop.resolve_model.agent_model_failed", {"error": str(_e)})
        
        # Priority 5: Parent session's model/provider (inherit from Rex etc.)
        if not resolved_provider or not resolved_model:
            parent_id = getattr(session, 'parent_id', None)
            if parent_id:
                try:
                    parent = await Session.get_by_id(parent_id)
                    if Session.has_pinned_model(parent):
                        resolved_provider = resolved_provider or getattr(parent, 'provider', None)
                        resolved_model = resolved_model or getattr(parent, 'model', None)
                        if resolved_provider and resolved_model:
                            source = "parent_session"
                except Exception as _e:
                    log.debug("loop.resolve_model.parent_failed", {"error": str(_e)})
        
        # Priority 6: Global default LLM (default_models.llm -> config.model)
        if not resolved_provider or not resolved_model:
            try:
                from flocks.config.config import Config
                default_llm = await Config.resolve_default_llm()
                if default_llm:
                    resolved_provider = resolved_provider or default_llm["provider_id"]
                    resolved_model = resolved_model or default_llm["model_id"]
                    if resolved_provider and resolved_model:
                        source = "config"
            except Exception as _e:
                log.debug("loop.resolve_model.config_default_failed", {"error": str(_e)})
        
        # Priority 7: Environment variables
        if not resolved_provider:
            resolved_provider = os.environ.get("LLM_PROVIDER")
        if not resolved_model:
            resolved_model = os.environ.get("LLM_MODEL")
        if resolved_provider and resolved_model and source == "unknown":
            source = "env_default"
        
        # Priority 8: Hardcoded fallback
        from flocks.session.core.defaults import fallback_provider_id, fallback_model_id
        resolved_provider = resolved_provider or fallback_provider_id()
        resolved_model = resolved_model or fallback_model_id()
        if source == "unknown":
            source = "fallback"

        if include_source:
            return resolved_provider, resolved_model, source
        return resolved_provider, resolved_model

    @classmethod
    async def _reset_auto_turn_candidates(
        cls,
        ctx: LoopContext,
        primary: RuntimeModel,
        user_message_id: str,
        config: Any,
    ) -> int:
        """Rebuild and activate the configured or automatic chain for one turn."""
        configured = bool(getattr(config, "fallback_providers", None))
        if configured:
            cls.clear_auto_failover_state(ctx.session.id)
            preferred = None
        else:
            preferred = cls._active_cooldown_model(ctx.session.id, primary)

        ctx.model_candidates = await cls._build_model_candidates(
            primary,
            route_seed=f"{ctx.session.id}:{user_message_id}",
            preferred=preferred,
            config=config,
        )
        ctx.model_candidate_policy = (
            "configured" if configured else "automatic"
        )
        ctx.auto_failover = True
        next_index = (
            0
            if configured
            else cls._cooldown_candidate_index(
                ctx.session.id,
                ctx.model_candidates,
            )
        )
        cls._select_candidate(ctx, next_index)
        return next_index

    @classmethod
    async def _prepare_auto_turn(
        cls,
        ctx: LoopContext,
        last_user: MessageInfo,
    ) -> bool:
        """Synchronize routing when the loop advances to a real WebUI turn.

        Returns:
            True when ``last_user`` starts a new non-synthetic user turn.
        """
        if last_user.id == ctx.turn_user_id:
            return False

        parts = await Message.parts(last_user.id, ctx.session.id)
        if any(bool(getattr(part, "synthetic", False)) for part in parts):
            return False

        if ctx.turn_user_id is None:
            ctx.turn_user_id = last_user.id
            if ctx.auto_failover and ctx.auto_failover_allowed:
                from flocks.config.config import Config

                primary = ctx.model_candidates[0]
                config = await Config.get()
                await cls._reset_auto_turn_candidates(
                    ctx,
                    primary,
                    last_user.id,
                    config=config,
                )
            return True

        ctx.turn_user_id = last_user.id
        persisted_session = await Session.get_by_id(ctx.session.id)
        persisted_model_auto = bool(
            persisted_session
            and is_model_auto_session_category(
                getattr(persisted_session, "category", "user")
            )
            and getattr(persisted_session, "model_auto", False)
        )
        persisted_auto = persisted_model_auto and ctx.auto_failover_allowed

        user_model = getattr(last_user, "model", None)
        user_provider_id = None
        user_model_id = None
        if isinstance(user_model, dict):
            user_provider_id = user_model.get("providerID") or user_model.get("provider_id")
            user_model_id = user_model.get("modelID") or user_model.get("model_id")

        if not persisted_auto:
            ctx.auto_failover = False
            if not persisted_model_auto:
                cls.clear_auto_failover_state(ctx.session.id)
                ctx.auto_failover_allowed = False
            provider_id = (
                getattr(persisted_session, "provider", None)
                if Session.has_pinned_model(persisted_session)
                else user_provider_id
            ) or ctx.provider_id
            model_id = (
                getattr(persisted_session, "model", None)
                if Session.has_pinned_model(persisted_session)
                else user_model_id
            ) or ctx.model_id
            ctx.model_candidates = [RuntimeModel(provider_id, model_id)]
            ctx.model_candidate_policy = "fixed"
            cls._select_candidate(ctx, 0)
            log.info("session.model.auto_disabled_for_turn", {
                "session_id": ctx.session.id,
                "provider_id": provider_id,
                "model_id": model_id,
            })
            return True

        from flocks.config.config import Config

        config = await Config.get()
        previous = RuntimeModel(ctx.provider_id, ctx.model_id)
        default_llm = await Config.resolve_default_llm()
        primary = RuntimeModel(
            provider_id=(default_llm or {}).get("provider_id") or user_provider_id or ctx.provider_id,
            model_id=(default_llm or {}).get("model_id") or user_model_id or ctx.model_id,
        )
        next_index = await cls._reset_auto_turn_candidates(
            ctx,
            primary,
            last_user.id,
            config=config,
        )
        active = ctx.model_candidates[next_index]
        log.info("session.model.auto_turn_reset", {
            "session_id": ctx.session.id,
            "from_provider_id": previous.provider_id,
            "from_model_id": previous.model_id,
            "to_provider_id": active.provider_id,
            "to_model_id": active.model_id,
            "cooldown_active": next_index > 0,
        })
        return True

    @classmethod
    async def _run_user_prompt_submit_hook(
        cls,
        ctx: LoopContext,
        last_user: MessageInfo,
    ) -> None:
        """Run UserPromptSubmit once for a newly observed real user turn."""
        try:
            from flocks.hooks.pipeline import HookPipeline

            prompt = await Message.get_text_content(last_user)
            hook_ctx = await HookPipeline.run_user_prompt_submit({
                "sessionID": ctx.session.id,
                "workspace": ctx.session.directory,
                "agent": getattr(last_user, "agent", None) or ctx.agent_name,
                "model": {
                    "providerID": ctx.provider_id,
                    "modelID": ctx.model_id,
                },
                "messageID": last_user.id,
                "prompt": prompt,
            })
            additional_context = hook_ctx.output.get("additionalContext")
            if isinstance(additional_context, str) and additional_context.strip():
                ctx.turn_additional_context = additional_context.strip()
        except Exception as exc:
            log.debug("loop.hook.user_prompt_submit.error", {
                "session_id": ctx.session.id,
                "message_id": last_user.id,
                "error": str(exc),
            })

    @classmethod
    async def _run_turn_finish_hook(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        last_user: MessageInfo,
        last_message: MessageInfo,
    ) -> bool:
        """Run TurnFinish and continue the loop when the hook blocks stopping."""
        try:
            from flocks.hooks.pipeline import HookPipeline

            hook_user = last_user
            if ctx.turn_user_id:
                hook_user = (
                    await Message.get(ctx.session.id, ctx.turn_user_id)
                    or last_user
                )
            user_text = await Message.get_text_content(hook_user)
            assistant_text = await Message.get_text_content(last_message)
            hook_ctx = await HookPipeline.run_turn_finish({
                "sessionID": ctx.session.id,
                "workspace": ctx.session.directory,
                "agent": getattr(last_message, "agent", None) or ctx.agent_name,
                "model": {
                    "providerID": ctx.provider_id,
                    "modelID": ctx.model_id,
                },
                "step": ctx.trace_step,
                "userMessage": {
                    "id": hook_user.id,
                    "content": user_text,
                },
                "assistantMessage": {
                    "id": last_message.id,
                    "content": assistant_text,
                },
                "finishReason": "stop",
                "stopHookActive": ctx.stop_hook_active,
            })
        except Exception as exc:
            log.debug("loop.hook.turn_finish.error", {
                "session_id": ctx.session.id,
                "message_id": getattr(last_message, "id", None),
                "error": str(exc),
            })
            return False

        decision = str(hook_ctx.output.get("decision") or "").strip().lower()
        reason = str(hook_ctx.output.get("reason") or "").strip()
        if decision != "block":
            return False
        if not reason:
            log.warn("loop.hook.turn_finish.missing_reason", {
                "session_id": ctx.session.id,
                "message_id": last_message.id,
            })
            return False
        if ctx.should_abort():
            log.info("loop.hook.turn_finish.ignored_after_abort", {
                "session_id": ctx.session.id,
                "message_id": last_message.id,
            })
            return False

        try:
            if ctx.session_ctx:
                post_hook_messages = await ctx.session_ctx.get_messages()
            else:
                post_hook_messages = await Message.list(ctx.session.id)
            queued_user = await cls._detect_queued_user_message(
                ctx.session.id,
                post_hook_messages,
                last_user.id,
                last_message,
            )
        except Exception as exc:
            queued_user = None
            log.debug("loop.hook.turn_finish.queued_recheck_error", {
                "session_id": ctx.session.id,
                "error": str(exc),
            })
        if queued_user is not None:
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="continued",
                continue_reason="queued_message",
                queued_message_detected=True,
            )
            await cls._publish_runtime_event(callbacks, "turn.continued", {
                **turn_state.model_dump(by_alias=True),
                "queuedUserMessageID": queued_user.id,
            })
            log.info("loop.hook.turn_finish.queued_message_won", {
                "session_id": ctx.session.id,
                "queued_user_id": queued_user.id,
                "source_assistant_message_id": last_message.id,
            })
            return True

        from flocks.agent.registry import Agent
        from flocks.session.core.defaults import DEFAULT_MAX_TOOL_STEPS

        try:
            agent = await Agent.get(
                getattr(last_message, "agent", None) or ctx.agent_name
            )
        except Exception as exc:
            log.debug("loop.hook.turn_finish.agent_load_error", {
                "session_id": ctx.session.id,
                "error": str(exc),
            })
            agent = None
        max_steps = (
            agent.steps
            if agent is not None and getattr(agent, "steps", None) is not None
            else DEFAULT_MAX_TOOL_STEPS
        )
        if ctx.trace_step >= max_steps:
            log.warn("loop.hook.turn_finish.step_limit", {
                "session_id": ctx.session.id,
                "step": ctx.trace_step,
                "max_steps": max_steps,
            })
            return False

        try:
            continuation = await Message.create(
                session_id=ctx.session.id,
                role=MessageRole.USER,
                content=reason,
                agent=getattr(hook_user, "agent", None) or ctx.agent_name,
                model={
                    "providerID": ctx.provider_id,
                    "modelID": ctx.model_id,
                },
                synthetic=True,
                part_metadata={
                    "turnFinishContinuation": True,
                    "stopHookActive": True,
                    "sourceAssistantMessageID": last_message.id,
                },
            )
        except Exception as exc:
            log.error("loop.hook.turn_finish.continuation_error", {
                "session_id": ctx.session.id,
                "error": str(exc),
            })
            return False
        ctx.stop_hook_active = True
        turn_state = set_turn_state(
            ctx.session.id,
            step=ctx.step,
            status="continued",
            continue_reason="turn_finish_hook",
            queued_message_detected=False,
        )
        await cls._publish_runtime_event(callbacks, "turn.continued", {
            **turn_state.model_dump(by_alias=True),
            "turnFinishMessageID": continuation.id,
        })
        log.info("loop.continuing_for_turn_finish_hook", {
            "session_id": ctx.session.id,
            "continuation_message_id": continuation.id,
            "source_assistant_message_id": last_message.id,
        })
        return True

    @classmethod
    async def _finalize_deferred_failure(
        cls,
        ctx: LoopContext,
        failure: Any,
        last_user: MessageInfo,
    ) -> None:
        """Persist only the final Auto candidate failure."""
        if not failure.assistant_message_id:
            assistant = await Message.create(
                session_id=ctx.session.id,
                role=MessageRole.ASSISTANT,
                content="",
                agent=getattr(last_user, "agent", None) or ctx.agent_name or "rex",
                model_id=ctx.model_id,
                provider_id=ctx.provider_id,
                parent_id=last_user.id,
                error=failure.error_data,
                finish="error",
            )
            failure.assistant_message_id = assistant.id
            return
        await Message.update(
            ctx.session.id,
            failure.assistant_message_id,
            error=failure.error_data,
            finish="error",
        )

    @classmethod
    async def _process_model_step(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        snapshot: ModelTurnSnapshot[MessageInfo],
    ) -> StepResult:
        """Run one candidate attempt; provider-local retries stay in runner."""
        from flocks.session.runner import RunnerCallbacks, SessionRunner
        from flocks.session.step_engine import SessionStepEngine

        runner_callbacks = callbacks.runner_callbacks
        if runner_callbacks is None:
            runner_callbacks = RunnerCallbacks()
        if (
            callbacks.event_publish_callback
            and not runner_callbacks.event_publish_callback
        ):
            runner_callbacks.event_publish_callback = (
                callbacks.event_publish_callback
            )

        runner = SessionRunner(
            session=ctx.session,
            provider_id=ctx.provider_id,
            model_id=ctx.model_id,
            agent_name=ctx.agent_name,
            abort_event=ctx.abort_event,
            callbacks=runner_callbacks,
            session_ctx=ctx.session_ctx,
            memory_bootstrap_data=ctx.memory_bootstrap_data,
            static_cache=ctx.runner_static_cache,
            defer_step_errors=ctx.auto_failover,
            failover_available=(
                ctx.auto_failover
                and ctx.candidate_index + 1 < len(ctx.model_candidates)
            ),
            turn_additional_context=ctx.turn_additional_context,
            session_start_pending=ctx.session_start_pending,
            runtime_ports=ctx.runtime_ports,
        )
        step_engine = SessionStepEngine(runner)
        result = await step_engine.run(snapshot)
        if step_engine.session_start_fired:
            ctx.session_start_pending = False
        return result

    @classmethod
    def _create_host_step_engine(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
    ) -> Any:
        """Compose one-candidate execution with host-owned model recovery."""
        from flocks.session.runtime_services import SessionLoopStepEngine
        from flocks.session.session_host import SessionHostStepEngine

        return SessionHostStepEngine(
            context=ctx,
            callbacks=callbacks,
            attempt_engine=SessionLoopStepEngine(ctx, callbacks, cls),
            cooldowns=cls._auto_failover_cooldowns,
            cooldown_factory=AutoFailoverCooldown,
            select_candidate=cls._select_candidate,
            finalize_failure=cls._finalize_deferred_failure,
            publish_event=cls._publish_runtime_event,
            rate_limit_cooldown_seconds=RATE_LIMIT_COOLDOWN_SECONDS,
            chain_exhaustion_cooldown_seconds=(
                CHAIN_EXHAUSTION_COOLDOWN_SECONDS
            ),
        )

    @classmethod
    async def _process_step_with_failover(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        messages: List[MessageInfo],
        last_user: MessageInfo,
    ) -> StepResult:
        """Compatibility entry point for host-owned cross-model recovery."""
        snapshot = ModelTurnSnapshot(
            session_id=ctx.session.id,
            agent_name=ctx.agent_name,
            active_model=RuntimeModel(ctx.provider_id, ctx.model_id),
            model_turn_index=ctx.step,
            trace_step=ctx.trace_step,
            messages=tuple(messages),
            last_user=last_user,
        )
        return await cls._create_host_step_engine(ctx, callbacks).run(snapshot)

    @staticmethod
    def _log_step_complete(ctx: LoopContext, duration_ms: int) -> None:
        """Record model-turn latency from the session StepEngine adapter."""
        log.debug(
            "loop.step_complete",
            {
                "session_id": ctx.session.id,
                "step": ctx.step,
                "duration_ms": duration_ms,
            },
        )

    @classmethod
    async def _complete_model_turn(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        state: AgentRunState[MessageInfo],
        step_result: StepResult,
    ) -> ModelTurnBoundary[MessageInfo]:
        """Expose the persisted state written by one completed model turn."""
        if callbacks.on_step_end:
            await callbacks.on_step_end(ctx.step)

        if step_result.error and callbacks.on_error:
            await callbacks.on_error(step_result.error)

        if ctx.session_ctx:
            post_messages = await ctx.session_ctx.get_messages()
        else:
            post_messages = await Message.list(ctx.session.id)

        last_user = state.metadata.get("last_user")
        last_message = next(
            (
                message
                for message in reversed(post_messages)
                if message.role == MessageRole.ASSISTANT
                and (
                    not ctx.auto_failover
                    or last_user is None
                    or getattr(message, "parentID", None) == last_user.id
                )
            ),
            None,
        )
        state.metadata["last_message"] = last_message

        queued_user = None
        if last_user is not None:
            queued_user = await cls._detect_queued_user_message(
                ctx.session.id,
                post_messages,
                last_user.id,
                last_message,
            )

        if queued_user is not None:
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="continued",
                continue_reason="queued_message",
                queued_message_detected=True,
            )
            await cls._publish_runtime_event(
                callbacks,
                "turn.continued",
                {
                    **turn_state.model_dump(by_alias=True),
                    "queuedUserMessageID": queued_user.id,
                },
            )
            log.info(
                "loop.continuing_for_queued_message",
                {
                    "session_id": ctx.session.id,
                    "queued_user_id": queued_user.id,
                    "last_assistant_id": (
                        last_message.id if last_message else None
                    ),
                },
            )
        elif step_result.action == "continue":
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="continued",
                continue_reason="tool_calls",
                queued_message_detected=False,
            )
            await cls._publish_runtime_event(
                callbacks,
                "turn.continued",
                turn_state.model_dump(by_alias=True),
            )
        elif step_result.error:
            await cls._publish_turn_stopped(
                callbacks,
                ctx.session.id,
                step=ctx.step,
                stop_reason=step_result.error,
            )

        return ModelTurnBoundary(
            messages=tuple(post_messages),
            last_message=last_message,
            queued_inputs=QueuedInputBatch(
                messages=(queued_user,) if queued_user is not None else (),
                cursor=queued_user.id if queued_user is not None else None,
            ),
        )

    @classmethod
    async def _resolve_continuation(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        state: AgentRunState[MessageInfo],
        step_result: StepResult,
    ) -> ContinuationDecision[MessageInfo]:
        """Resolve goal and TurnFinish continuation after a natural stop."""
        last_user = state.metadata.get("last_user")
        last_message = state.metadata.get("last_message")
        if last_user is None or last_message is None:
            await cls._publish_turn_stopped(
                callbacks,
                ctx.session.id,
                step=ctx.step,
                stop_reason="stop",
            )
            return ContinuationDecision()

        try:
            content_result = Message.get_text_content(last_message)
            last_response = (
                await content_result
                if inspect.isawaitable(content_result)
                else content_result
            )
        except Exception as exc:
            log.warn(
                "goal.last_response.error",
                {
                    "session_id": ctx.session.id,
                    "message_id": getattr(last_message, "id", None),
                    "error": str(exc),
                },
            )
            last_response = getattr(last_message, "content", "") or ""

        pending_user_input = False
        try:
            from flocks.server.routes.question import has_pending_questions

            pending_user_input = has_pending_questions(ctx.session.id)
        except Exception as exc:
            log.warn(
                "goal.pending_question_check.error",
                {"session_id": ctx.session.id, "error": str(exc)},
            )

        goal_decision = await GoalManager.evaluate_after_turn(
            ctx.session.id,
            str(last_response or ""),
            pending_user_input=pending_user_input,
            provider_id=ctx.provider_id,
            model_id=ctx.model_id,
        )
        if (
            goal_decision.status in {"completed", "blocked", "paused"}
            and goal_decision.objective
        ):
            await cls._publish_runtime_event(
                callbacks,
                "session.goal.updated",
                {
                    "sessionID": ctx.session.id,
                    "status": goal_decision.status,
                    "objective": goal_decision.objective,
                    "reason": goal_decision.reason,
                },
            )
        if goal_decision.should_continue and goal_decision.continuation_prompt:
            goal_user = await Message.create(
                session_id=ctx.session.id,
                role=MessageRole.USER,
                content=goal_decision.continuation_prompt,
                agent=(
                    last_user.agent
                    if hasattr(last_user, "agent")
                    else ctx.agent_name
                ),
                model=(
                    last_user.model
                    if hasattr(last_user, "model")
                    else {
                        "providerID": ctx.provider_id,
                        "modelID": ctx.model_id,
                    }
                ),
                provider=(
                    last_user.provider
                    if hasattr(last_user, "provider")
                    else ctx.provider_id
                ),
                synthetic=True,
                part_metadata={
                    "goalContinuation": True,
                    "goalVerdict": goal_decision.verdict,
                    "goalReason": goal_decision.reason,
                },
            )
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="continued",
                continue_reason="goal",
                queued_message_detected=False,
            )
            await cls._publish_runtime_event(
                callbacks,
                "turn.continued",
                {
                    **turn_state.model_dump(by_alias=True),
                    "goalMessageID": goal_user.id,
                    "goalVerdict": goal_decision.verdict,
                },
            )
            log.info(
                "loop.continuing_for_goal",
                {
                    "session_id": ctx.session.id,
                    "goal_message_id": goal_user.id,
                    "reason": goal_decision.reason,
                },
            )
            return ContinuationDecision(
                messages=(goal_user,),
                reason="goal",
            )

        if (
            not ctx.should_abort()
            and getattr(last_message, "finish", None) == "stop"
            and await cls._run_turn_finish_hook(
                ctx,
                callbacks,
                last_user,
                last_message,
            )
        ):
            if ctx.session_ctx:
                post_hook_messages = await ctx.session_ctx.get_messages()
            else:
                post_hook_messages = await Message.list(ctx.session.id)
            existing_ids = {message.id for message in state.messages}
            new_messages = tuple(
                message
                for message in post_hook_messages
                if message.id not in existing_ids
            )
            return ContinuationDecision(
                messages=new_messages,
                reason="turn_finish_hook",
            )

        stop_reason = getattr(last_message, "finish", None) or "stop"
        await cls._publish_turn_stopped(
            callbacks,
            ctx.session.id,
            step=ctx.step,
            stop_reason=stop_reason,
        )
        return ContinuationDecision()

    @classmethod
    async def _prepare_model_turn(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        state: AgentRunState[MessageInfo],
    ) -> ModelTurnPreparation[MessageInfo]:
        """Prepare one immutable model-turn snapshot from session state."""
        SessionStatus.set(ctx.session.id, SessionStatusBusy())
        ctx.step += 1
        state.model_turn_index = ctx.step
        turn_state = set_turn_state(
            ctx.session.id,
            step=ctx.step,
            status="started",
            queued_message_detected=False,
        )
        await cls._publish_runtime_event(
            callbacks,
            "turn.started",
            turn_state.model_dump(by_alias=True),
        )
        log.info(
            "loop.step",
            {"session_id": ctx.session.id, "step": ctx.step},
        )
        if callbacks.on_step_start:
            await callbacks.on_step_start(ctx.step)

        messages_started_at = asyncio.get_running_loop().time()
        if ctx.session_ctx:
            messages = await ctx.session_ctx.get_messages()
        else:
            messages = await Message.list(ctx.session.id)
        log.debug(
            "loop.messages_loaded",
            {
                "session_id": ctx.session.id,
                "step": ctx.step,
                "message_count": len(messages),
                "duration_ms": int(
                    (asyncio.get_running_loop().time() - messages_started_at)
                    * 1000
                ),
            },
        )
        if not messages:
            log.info("loop.no_messages", {"session_id": ctx.session.id})
            await cls._publish_turn_stopped(
                callbacks,
                ctx.session.id,
                step=ctx.step,
                stop_reason="no_messages",
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

        last_user: Optional[MessageInfo] = None
        last_assistant: Optional[MessageInfo] = None
        last_finished: Optional[MessageInfo] = None
        tasks: List[tuple[str, Any]] = []
        scan_started_at = asyncio.get_running_loop().time()
        for message in reversed(messages):
            if last_user is None and message.role == MessageRole.USER:
                last_user = message
            if last_assistant is None and message.role == MessageRole.ASSISTANT:
                last_assistant = message
            if (
                last_finished is None
                and message.role == MessageRole.ASSISTANT
                and getattr(message, "finish", None)
            ):
                last_finished = message
            if last_user is not None and last_finished is not None:
                break
            if last_finished is None:
                for part in await Message.parts(message.id, ctx.session.id):
                    if part.type == "compaction":
                        tasks.append(("compaction", part))
                    elif part.type == "subtask":
                        tasks.append(("subtask", part))
        log.debug(
            "loop.message_scan_complete",
            {
                "session_id": ctx.session.id,
                "step": ctx.step,
                "task_count": len(tasks),
                "duration_ms": int(
                    (asyncio.get_running_loop().time() - scan_started_at) * 1000
                ),
            },
        )

        if last_user is None:
            log.info(
                "loop.no_user_message",
                {
                    "session_id": ctx.session.id,
                    "message_count": len(messages),
                    "roles": [
                        str(getattr(message, "role", ""))
                        for message in messages[-5:]
                    ],
                },
            )
            await cls._publish_turn_stopped(
                callbacks,
                ctx.session.id,
                step=ctx.step,
                stop_reason="no_user_message",
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

        last_assistant_parts = (
            await Message.parts(last_assistant.id, ctx.session.id)
            if last_assistant
            else []
        )
        if cls._should_exit(last_user, last_assistant, last_assistant_parts):
            log.info(
                "loop.exit_condition",
                {
                    "session_id": ctx.session.id,
                    "last_user_id": last_user.id,
                    "last_assistant_id": (
                        last_assistant.id if last_assistant else None
                    ),
                    "finish": last_assistant.finish if last_assistant else None,
                    "has_tool_parts": any(
                        getattr(part, "type", None) == "tool"
                        for part in last_assistant_parts
                    ),
                },
            )
            return ModelTurnPreparation(
                status=TurnPreparationStatus.COMPLETE,
                last_message=last_assistant,
            )

        if await cls._prepare_auto_turn(ctx, last_user):
            ctx.turn_additional_context = None
            ctx.stop_hook_active = False
            await cls._run_user_prompt_submit_hook(ctx, last_user)

        state.current_user_id = last_user.id
        state.metadata["last_user"] = last_user
        await cls._prepare_memory(ctx)
        cls._schedule_title_generation(ctx, callbacks, last_user, messages)

        if tasks:
            task_preparation = await cls._prepare_pending_task(
                ctx,
                callbacks,
                messages,
                last_user,
                tasks.pop(),
            )
            if task_preparation is not None:
                return task_preparation

        context_preparation = await cls._prepare_context_window(
            ctx,
            callbacks,
            messages,
            last_user,
            last_finished,
        )
        if context_preparation is not None:
            return context_preparation

        active_model = RuntimeModel(ctx.provider_id, ctx.model_id)
        state.active_model = active_model
        state.messages = list(messages)
        return ModelTurnPreparation(
            status=TurnPreparationStatus.READY,
            snapshot=ModelTurnSnapshot(
                session_id=ctx.session.id,
                agent_name=ctx.agent_name,
                active_model=active_model,
                model_turn_index=ctx.step,
                trace_step=ctx.trace_step,
                messages=tuple(messages),
                last_user=last_user,
            ),
        )

    @staticmethod
    async def _prepare_memory(ctx: LoopContext) -> None:
        """Load memory once before the first model turn."""
        if (
            ctx.step != 1
            or not ctx.session.memory_enabled
            or ctx.memory_bootstrap_data is not None
        ):
            return
        try:
            from flocks.memory.bootstrap import MemoryBootstrap

            ctx.memory_bootstrap_data = await MemoryBootstrap(
                project_id=ctx.session.project_id,
            ).bootstrap(load_daily=False)
            log.info(
                "loop.memory_bootstrap_done",
                {
                    "session_id": ctx.session.id,
                    "has_main": (
                        ctx.memory_bootstrap_data.get("main_memory") is not None
                    ),
                },
            )
        except Exception as exc:
            log.error("loop.memory_bootstrap_error", {"error": str(exc)})

    @staticmethod
    def _schedule_title_generation(
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        last_user: MessageInfo,
        messages: List[MessageInfo],
    ) -> None:
        """Start optimistic first-turn title generation without blocking."""
        if ctx.step != 1 or ctx.auto_failover:
            return
        try:
            from flocks.session.lifecycle.title import SessionTitle

            user_model = getattr(last_user, "model", None)
            if isinstance(user_model, dict):
                title_model_id = user_model.get("modelID", ctx.model_id)
                title_provider_id = user_model.get(
                    "providerID",
                    ctx.provider_id,
                )
            else:
                title_model_id = ctx.model_id
                title_provider_id = ctx.provider_id
            fire_and_forget(
                SessionTitle.ensure_title(
                    session_id=ctx.session.id,
                    model_id=title_model_id,
                    provider_id=title_provider_id,
                    messages=messages,
                    event_publish_callback=callbacks.event_publish_callback,
                ),
                label="title_generation",
                name=f"title:{ctx.session.id}",
            )
        except Exception as exc:
            log.error("loop.title_generation.error", {"error": str(exc)})

    @classmethod
    async def _prepare_pending_task(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        task: tuple[str, Any],
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Finish persisted subtask or compaction work before the model turn."""
        task_type, task_part = task
        if task_type == "subtask":
            log.info(
                "loop.subtask_detected",
                {"session_id": ctx.session.id, "step": ctx.step},
            )
            await cls._execute_subtask(ctx, last_user, task_part)
            return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)

        log.info(
            "loop.compaction_pending",
            {
                "session_id": ctx.session.id,
                "step": ctx.step,
                "auto": getattr(task_part, "auto", False),
            },
        )
        if callbacks.on_compaction:
            await callbacks.on_compaction()

        publish = callbacks.event_publish_callback
        progress_callback = None
        if publish is not None:

            async def progress_callback(stage: str, data: dict) -> None:
                await publish(
                    "session.compaction_progress",
                    {
                        "sessionID": ctx.session.id,
                        "stage": stage,
                        "data": data,
                    },
                )

        try:
            compaction_result = await run_compaction(
                ctx.session.id,
                parent_message_id=last_user.id,
                messages=messages,
                provider_id=ctx.provider_id,
                model_id=ctx.model_id,
                auto=getattr(task_part, "auto", False),
                event_publish_callback=publish,
                status_after="busy",
                policy=cls._build_compaction_policy(ctx),
                progress_callback=progress_callback,
            )
            if compaction_result == "stop":
                log.error(
                    "loop.compaction_failed",
                    {"session_id": ctx.session.id},
                )
                if callbacks.on_error:
                    await callbacks.on_error("Compaction failed")
                return ModelTurnPreparation(
                    status=TurnPreparationStatus.COMPLETE,
                )
            if compaction_result == "skipped":
                log.info(
                    "loop.manual_compaction_skipped",
                    {"session_id": ctx.session.id, "step": ctx.step},
                )
            return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)
        except Exception as exc:
            log.error("loop.compaction_error", {"error": str(exc)})
            if callbacks.on_error:
                await callbacks.on_error(f"Compaction error: {exc}")
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

    @classmethod
    async def _prepare_context_window(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        last_finished: Optional[MessageInfo],
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Recover a near-overflow context before the next model turn."""
        if last_finished is None or getattr(last_finished, "summary", False):
            return None

        model_context, model_output, model_input = Provider.resolve_model_info(
            ctx.provider_id,
            ctx.model_id,
        )
        if model_context <= 0:
            return None

        policy = CompactionPolicy.from_model(
            context_window=model_context,
            max_output_tokens=model_output or 4096,
            max_input_tokens=model_input,
        )
        tokens = cls._normalise_token_usage(last_finished)
        input_tokens = tokens.get("input", 0)
        cache = tokens.get("cache") or {}
        cache_read = cache.get("read", 0) if isinstance(cache, dict) else 0
        output_tokens = tokens.get("output", 0)
        reported_total = input_tokens + cache_read + output_tokens
        if reported_total > 0:
            ctx.last_observed_prompt_tokens = reported_total
            log.info(
                "loop.tokens_decision",
                {
                    "session_id": ctx.session.id,
                    "source": "observed",
                    "effective_tokens": input_tokens + cache_read,
                    "overflow_threshold": policy.overflow_threshold,
                },
            )
        else:
            estimated_tokens = await SessionPrompt.estimate_full_context_tokens(
                ctx.session.id,
                messages,
                policy=policy,
            )
            tokens = {
                "input": estimated_tokens,
                "output": 0,
                "cache": {"read": 0, "write": 0},
            }
            log.info(
                "loop.tokens_decision",
                {
                    "session_id": ctx.session.id,
                    "source": "estimated",
                    "effective_tokens": estimated_tokens,
                    "message_count": len(messages),
                    "overflow_threshold": policy.overflow_threshold,
                },
            )

        try:
            cache = tokens.get("cache") or {}
            current_input_tokens = tokens.get("input", 0) + (
                cache.get("read", 0) if isinstance(cache, dict) else 0
            )
            recent_compaction = cls._has_recent_compaction_cooldown(ctx)
            near_overflow = current_input_tokens >= policy.preemptive_threshold
            if near_overflow and ctx.last_cleanup_step != ctx.step:
                cleanup_result = await cls._prepare_tool_result_cleanup(
                    ctx,
                    callbacks,
                    model_context,
                    policy,
                    current_input_tokens,
                    recent_compaction,
                )
                if cleanup_result is not None:
                    return cleanup_result

            is_overflow = await SessionCompaction.is_overflow(
                tokens=tokens,
                model_context=model_context,
                policy=policy,
            )
            if not is_overflow:
                return None

            log.info(
                "loop.context_overflow_detected",
                {
                    "session_id": ctx.session.id,
                    "step": ctx.step,
                    "tokens": tokens,
                    "tier": policy.tier.value,
                    "overflow_compaction_attempts": (
                        ctx.overflow_compaction_attempts
                    ),
                },
            )
            if (
                ctx.overflow_compaction_attempts
                >= MAX_OVERFLOW_COMPACTION_ATTEMPTS
            ):
                await cls._report_compaction_exhausted(
                    ctx,
                    callbacks,
                    tokens,
                )
                return ModelTurnPreparation(
                    status=TurnPreparationStatus.COMPLETE,
                )

            if not ctx.tool_result_truncation_attempted:
                ctx.tool_result_truncation_attempted = True
                try:
                    truncation_count = (
                        await SessionCompaction.truncate_oversized_tool_outputs(
                            ctx.session.id,
                            context_window_tokens=model_context,
                        )
                    )
                    if truncation_count > 0:
                        log.info(
                            "loop.oversized_tool_truncated",
                            {
                                "session_id": ctx.session.id,
                                "truncated": truncation_count,
                            },
                        )
                        estimated_tokens = (
                            await SessionPrompt.estimate_full_context_tokens(
                                ctx.session.id,
                                messages,
                                policy=policy,
                            )
                        )
                        still_overflow = await SessionCompaction.is_overflow(
                            tokens={
                                "input": estimated_tokens,
                                "output": 0,
                                "cache": {"read": 0, "write": 0},
                            },
                            model_context=model_context,
                            policy=policy,
                        )
                        if not still_overflow:
                            log.info(
                                "loop.overflow_resolved_by_truncation",
                                {"session_id": ctx.session.id},
                            )
                            return ModelTurnPreparation(
                                status=TurnPreparationStatus.CONTINUE,
                            )
                except Exception as exc:
                    log.warn(
                        "loop.oversized_truncation_error",
                        {"session_id": ctx.session.id, "error": str(exc)},
                    )

            return await cls._prepare_full_compaction(
                ctx,
                callbacks,
                messages,
                last_user,
                policy,
            )
        except Exception as exc:
            log.error(
                "loop.compaction_overflow_check_error",
                {"error": str(exc)},
            )
            return None

    @staticmethod
    def _normalise_token_usage(message: MessageInfo) -> Dict[str, Any]:
        """Normalise provider token usage into the legacy mapping shape."""
        raw_tokens = getattr(message, "tokens", None)
        if not raw_tokens:
            return {}
        if isinstance(raw_tokens, dict):
            return raw_tokens
        if hasattr(raw_tokens, "model_dump"):
            return raw_tokens.model_dump()
        if hasattr(raw_tokens, "__dict__"):
            return vars(raw_tokens)
        return {}

    @classmethod
    async def _prepare_tool_result_cleanup(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        model_context: int,
        policy: CompactionPolicy,
        current_input_tokens: int,
        recent_compaction: bool,
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Apply the cheap tool-result cleanup before full compaction."""
        try:
            truncation_count = (
                await SessionCompaction.truncate_oversized_tool_outputs(
                    ctx.session.id,
                    context_window_tokens=model_context,
                )
            )
            ctx.last_cleanup_step = ctx.step
            if truncation_count <= 0:
                return None

            set_context_state(
                ctx.session.id,
                tool_results_compacted=True,
                last_compaction_step=ctx.last_compaction_step,
                last_compaction_reason="pre_compact_cleanup",
            )
            await cls._publish_runtime_event(
                callbacks,
                "context.compacted",
                {
                    "sessionID": ctx.session.id,
                    "step": ctx.step,
                    "reason": "pre_compact_cleanup",
                    "truncatedToolResults": truncation_count,
                    "cooldownActive": recent_compaction,
                },
            )
            log.info(
                "loop.pre_compact_cleanup_applied",
                {
                    "session_id": ctx.session.id,
                    "step": ctx.step,
                    "truncated": truncation_count,
                    "preemptive_threshold": policy.preemptive_threshold,
                    "input_tokens": current_input_tokens,
                    "cooldown_active": recent_compaction,
                },
            )
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="continued",
                continue_reason="pre_compact_cleanup",
                queued_message_detected=False,
            )
            await cls._publish_runtime_event(
                callbacks,
                "turn.continued",
                turn_state.model_dump(by_alias=True),
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)
        except Exception as exc:
            log.warn(
                "loop.pre_compact_cleanup_error",
                {"session_id": ctx.session.id, "error": str(exc)},
            )
            return None

    @classmethod
    async def _report_compaction_exhausted(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        tokens: Dict[str, Any],
    ) -> None:
        """Surface whether exhaustion came from context or provider health."""
        history = _get_compaction_history(ctx.session.id)
        provider_error = history.summary_last_error
        in_cooldown = (
            history.summary_cooldown_until > 0
            and history.summary_cooldown_until > time.monotonic()
        )
        cooldown_seconds = max(
            0,
            round(history.summary_cooldown_until - time.monotonic()),
        )
        if in_cooldown or provider_error:
            notice = (
                "摘要模型暂时不可用，上下文压缩跳过了本轮压缩。"
                + (
                    f"冷却剩余约 {cooldown_seconds} 秒，"
                    if in_cooldown
                    else ""
                )
                + "建议稍后继续，或切换到其他模型重试。"
            )
            error = (
                "Compaction skipped: summary provider unavailable "
                f"({provider_error or 'cooldown active'})."
                + (
                    f" Cooldown expires in ~{cooldown_seconds}s."
                    if in_cooldown
                    else ""
                )
                + " Wait for the provider to recover or switch models."
            )
        else:
            notice = (
                "当前任务上下文过重，已经多次 compact 仍接近上限。"
                "建议收敛工具输出、缩小搜索范围，或开启新会话。"
            )
            error = (
                "Context overflow: prompt too large for the model after "
                f"{ctx.overflow_compaction_attempts} compaction attempts. "
                "Try starting a new session or use a larger-context model."
            )

        await cls._publish_session_notice(
            callbacks,
            ctx.session.id,
            level="warning",
            message=notice,
            details={
                "attempts": ctx.overflow_compaction_attempts,
                "maxAttempts": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                "tokens": tokens,
                "providerError": provider_error or None,
                "cooldownRemainingSeconds": (
                    cooldown_seconds if in_cooldown else 0
                ),
            },
        )
        log.error(
            "loop.overflow_compaction_exhausted",
            {
                "session_id": ctx.session.id,
                "attempts": ctx.overflow_compaction_attempts,
                "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                "tokens": tokens,
                "in_cooldown": in_cooldown,
                "provider_error": provider_error or None,
            },
        )
        if callbacks.on_error:
            await callbacks.on_error(error)

    @classmethod
    async def _prepare_full_compaction(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        policy: CompactionPolicy,
    ) -> ModelTurnPreparation[MessageInfo]:
        """Run full compaction and request preparation to reload the session."""
        ctx.overflow_compaction_attempts += 1
        if ctx.overflow_compaction_attempts >= 2:
            await cls._publish_session_notice(
                callbacks,
                ctx.session.id,
                level="info",
                message=(
                    "本轮上下文持续接近模型上限，系统将优先尝试压缩历史工具输出。"
                ),
                details={
                    "attempt": ctx.overflow_compaction_attempts,
                    "threshold": policy.overflow_threshold,
                    "buffer": policy.overflow_buffer,
                },
            )
        log.warn(
            "loop.overflow_compaction_attempt",
            {
                "session_id": ctx.session.id,
                "attempt": ctx.overflow_compaction_attempts,
                "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
            },
        )
        if callbacks.on_compaction:
            await callbacks.on_compaction()
        await SessionCompaction.prune(ctx.session.id, policy=policy)

        publish = callbacks.event_publish_callback
        progress_callback = None
        if publish is not None:

            async def progress_callback(stage: str, data: dict) -> None:
                await publish(
                    "session.compaction_progress",
                    {
                        "sessionID": ctx.session.id,
                        "stage": stage,
                        "data": data,
                    },
                )

        result = await run_compaction(
            ctx.session.id,
            parent_message_id=last_user.id,
            messages=messages,
            provider_id=ctx.provider_id,
            model_id=ctx.model_id,
            auto=True,
            event_publish_callback=publish,
            status_after="busy",
            policy=policy,
            progress_callback=progress_callback,
        )
        if result == "stop":
            log.error(
                "loop.compaction_failed",
                {"session_id": ctx.session.id},
            )
            if callbacks.on_error:
                await callbacks.on_error("Compaction failed")
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)
        if result == "skipped":
            log.info(
                "loop.compaction_skipped",
                {"session_id": ctx.session.id, "step": ctx.step},
            )
        else:
            ctx.last_compaction_step = ctx.step
            set_context_state(
                ctx.session.id,
                compaction_performed=True,
                last_compaction_step=ctx.step,
                last_compaction_reason="full_compaction",
            )
            await cls._publish_runtime_event(
                callbacks,
                "context.compacted",
                {
                    "sessionID": ctx.session.id,
                    "step": ctx.step,
                    "reason": "full_compaction",
                    "attempt": ctx.overflow_compaction_attempts,
                    "cooldownUntilStep": (
                        ctx.step + POST_COMPACTION_COOLDOWN_STEPS
                    ),
                },
            )
        return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)

    @classmethod
    async def _run_loop(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
    ) -> LoopResult:
        """Run the host-neutral AgentLoop against session-owned adapters."""
        from flocks.session.runtime_services import (
            SessionRuntimeServices,
            SessionStepCancelled,
        )

        state = AgentRunState[MessageInfo](
            session_id=ctx.session.id,
            agent_name=ctx.agent_name,
            active_model=RuntimeModel(ctx.provider_id, ctx.model_id),
            model_turn_index=ctx.step,
            trace_step_offset=ctx.trace_step_offset,
            current_user_id=ctx.turn_user_id,
        )
        services = SessionRuntimeServices(ctx, callbacks, cls)
        step_engine = cls._create_host_step_engine(ctx, callbacks)
        try:
            outcome = await AgentLoop(
                step_engine,
                services,
                abort_requested=ctx.should_abort,
            ).run(state)
        except SessionStepCancelled:
            log.info(
                "loop.step_cancelled",
                {"session_id": ctx.session.id, "step": ctx.step},
            )
            return LoopResult(
                action="stop",
                provider_id=ctx.provider_id,
                model_id=ctx.model_id,
                metadata={
                    "steps": ctx.step,
                    "session_id": ctx.session.id,
                    "last_compaction_step": ctx.last_compaction_step,
                    "aborted": True,
                },
            )

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
        return LoopResult(
            action="error" if ctx.auto_failover and loop_error else "stop",
            last_message=outcome.last_message,
            error=loop_error if ctx.auto_failover else None,
            provider_id=ctx.provider_id,
            model_id=ctx.model_id,
            metadata={
                "steps": ctx.step,
                "session_id": ctx.session.id,
                "last_compaction_step": ctx.last_compaction_step,
                **(
                    {"aborted": True}
                    if outcome.status == AgentRunStatus.ABORTED
                    else {}
                ),
            },
        )

    @classmethod
    def _build_compaction_policy(cls, ctx: LoopContext) -> CompactionPolicy:
        """
        Construct a CompactionPolicy from the current model's info.
        
        Falls back to ``CompactionPolicy.default()`` when the model info
        cannot be resolved (e.g. unknown provider or missing context_window).
        """
        return build_compaction_policy(ctx.provider_id, ctx.model_id)
    
    @classmethod
    def _should_exit(
        cls,
        last_user: MessageInfo,
        last_assistant: Optional[MessageInfo],
        last_assistant_parts: Optional[List[Any]] = None,
    ) -> bool:
        """
        Check if loop should exit
        
        Ported from original exit logic:
        - Exit if assistant has responded with finish != tool-calls
        - Exit if assistant message is after user message
        """
        if not last_assistant:
            return False

        if any(
            getattr(part, "type", None) == "tool"
            for part in (last_assistant_parts or [])
        ):
            return False
        
        # Check finish reason
        if last_assistant.finish:
            if last_assistant.finish not in ("tool-calls", "unknown", "summary"):
                # Assistant finished with stop/error/etc
                if last_user.id < last_assistant.id:
                    # Assistant responded after user
                    return True
        
        return False
    
    @classmethod
    async def _check_reminders(
        cls,
        ctx: LoopContext,
        messages: List[MessageInfo],
        callbacks: LoopCallbacks,
    ) -> None:
        """
        Check and inject reminders (P1 feature)
        
        Reminders are system messages injected periodically to:
        - Remind agent of task goals
        - Prevent drift from original intent
        - Nudge towards completion
        """
        from flocks.session.features.reminders import SessionReminders, ReminderContext, ReminderConfig
        
        # Calculate elapsed time
        if messages:
            first_msg = messages[0]
            if hasattr(first_msg, 'time') and hasattr(first_msg.time, 'created'):
                first_time = first_msg.time.created
                current_time = int(datetime.now().timestamp() * 1000)
                elapsed_ms = current_time - first_time
            else:
                elapsed_ms = 0
        else:
            elapsed_ms = 0
        
        # Extract original task
        original_task = await SessionReminders.extract_original_task(messages)
        
        # Create reminder context
        reminder_ctx = ReminderContext(
            session_id=ctx.session.id,
            step_count=ctx.step,
            message_count=len(messages),
            elapsed_ms=elapsed_ms,
            original_task=original_task,
        )
        
        # Check if reminder should be injected
        if SessionReminders.should_remind(ctx.session.id, reminder_ctx):
            # Create and inject reminder
            reminder_msg = await SessionReminders.create_reminder(
                ctx.session.id,
                reminder_ctx,
            )
            
            if reminder_msg and callbacks.on_reminder:
                await callbacks.on_reminder(await Message.get_text_content(reminder_msg))
    
    @classmethod
    async def _execute_subtask(
        cls,
        ctx: LoopContext,
        last_user: MessageInfo,
        task_part: Any,
    ) -> None:
        """
        Execute subtask (matching TUI lines 316-481)
        
        完全匹配 TUI 的 subtask 执行流程:
        1. 创建 assistant message
        2. 创建 tool part (Task tool)
        3. 执行 Task tool
        4. 更新 part 状态
        5. 创建 synthetic user message
        """
        from flocks.tool.registry import ToolRegistry
        from flocks.agent.registry import Agent
        
        # Extract subtask information from part
        agent_name = getattr(task_part, 'agent', 'hephaestus')
        prompt = getattr(task_part, 'prompt', '')
        description = getattr(task_part, 'description', '')
        command = getattr(task_part, 'command', None)
        model_info = getattr(task_part, 'model', None)
        
        # Get agent
        agent = await Agent.get(agent_name) or await Agent.get("rex")
        
        # Determine model
        if model_info:
            provider_id = model_info.get('providerID', ctx.provider_id)
            model_id = model_info.get('modelID', ctx.model_id)
        else:
            provider_id = ctx.provider_id
            model_id = ctx.model_id
        
        # Create assistant message for subtask
        assistant_msg = await Message.create(
            session_id=ctx.session.id,
            role=MessageRole.ASSISTANT,
            content="",
            agent=agent_name,
            model=model_id,
            provider=provider_id,
            parent_id=last_user.id,
        )
        
        # Create tool part for Task
        tool_call_id = Identifier.create("call")
        from flocks.session.message import ToolPart, ToolStateRunning
        
        tool_part = ToolPart(
            id=Identifier.ascending("part"),
            sessionID=ctx.session.id,
            messageID=assistant_msg.id,
            type="tool",
            callID=tool_call_id,
            tool="task",
            state=ToolStateRunning(
                status="running",
                input={
                    "prompt": prompt,
                    "description": description,
                    "subagent_type": agent_name,
                    "command": command,
                },
                time={"start": int(datetime.now().timestamp() * 1000)},
            ),
        )
        
        # Add part to message
        await Message.add_part(ctx.session.id, assistant_msg.id, tool_part)
        
        # Get Task tool
        task_tool = ToolRegistry.get("task")
        if not task_tool:
            log.error("loop.subtask.task_tool_not_found", {"session_id": ctx.session.id})
            return
        
        # Execute Task tool
        task_args = {
            "prompt": prompt,
            "description": description,
            "subagent_type": agent_name,
            "command": command,
        }
        
        # Create tool context
        from flocks.tool.registry import ToolContext
        
        tool_ctx = ToolContext(
            session_id=ctx.session.id,
            message_id=assistant_msg.id,
            agent=agent_name,
            abort_event=ctx.abort_event,
        )
        
        execution_error: Optional[Exception] = None
        result = None
        
        try:
            result = await task_tool.execute(tool_ctx, **task_args)
        except Exception as e:
            execution_error = e
            log.error("loop.subtask.execution_failed", {
                "error": str(e),
                "agent": agent_name,
                "description": description,
            })
        
        # Update message finish
        await Message.update(ctx.session.id, assistant_msg.id, finish="tool-calls")
        
        # Update tool part status
        from flocks.session.message import ToolStateCompleted, ToolStateError
        
        if result:
            # Create completed state
            completed_state = ToolStateCompleted(
                status="completed",
                input={
                    "prompt": prompt,
                    "description": description,
                    "subagent_type": agent_name,
                    "command": command,
                },
                output=result.output if hasattr(result, 'output') else str(result),
                title=result.title if hasattr(result, 'title') else None,
                metadata=result.metadata if hasattr(result, 'metadata') else {},
                time={
                    "start": tool_part.state.time.get("start"),
                    "end": int(datetime.now().timestamp() * 1000),
                },
            )
            await Message.update_part(
                session_id=ctx.session.id,
                message_id=assistant_msg.id,
                part_id=tool_part.id,
                state=completed_state,
            )
        else:
            # Create error state
            error_msg = str(execution_error) if execution_error else "Tool execution failed"
            error_state = ToolStateError(
                status="error",
                error=f"Tool execution failed: {error_msg}",
                time={
                    "start": tool_part.state.time.get("start"),
                    "end": int(datetime.now().timestamp() * 1000),
                },
                metadata={},
                input={
                    "prompt": prompt,
                    "description": description,
                    "subagent_type": agent_name,
                    "command": command,
                },
            )
            await Message.update_part(
                session_id=ctx.session.id,
                message_id=assistant_msg.id,
                part_id=tool_part.id,
                state=error_state,
            )
        
        # Create synthetic user message (matching TUI lines 457-478)
        # This prevents reasoning models from erroring due to missing user messages
        synthetic_user_msg = await Message.create(
            session_id=ctx.session.id,
            role=MessageRole.USER,
            content="Summarize the task tool output above and continue with your task.",
            agent=last_user.agent if hasattr(last_user, 'agent') else agent_name,
            model=last_user.model if hasattr(last_user, 'model') else model_id,
            provider=last_user.provider if hasattr(last_user, 'provider') else provider_id,
            synthetic=True,
        )
        
        log.info("loop.subtask.completed", {
            "session_id": ctx.session.id,
            "agent": agent_name,
            "success": result is not None,
        })
    


# Export
__all__ = [
    "SessionLoop",
    "LoopContext",
    "LoopCallbacks",
    "LoopResult",
]
