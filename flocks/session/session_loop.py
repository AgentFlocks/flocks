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
from typing import Optional, List, Dict, Any, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.session import Session, SessionInfo
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.core.status import SessionStatus, SessionStatusBusy, SessionStatusIdle
from flocks.session.core.task_utils import fire_and_forget
from flocks.session.core.turn_state import (
    set_turn_state,
    set_context_state,
    clear_turn_state,
)
from flocks.session.lifecycle.compaction import (
    SessionCompaction,
    CompactionPolicy,
    build_compaction_policy,
    run_compaction,
)
from flocks.session.prompt import SessionPrompt
from flocks.provider.provider import Provider


log = Log.create(service="session.loop")


MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3
POST_COMPACTION_COOLDOWN_STEPS = 2


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
            sid for sid, ctx in cls._active_loops.items()
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
        last_message: Optional[MessageInfo],
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
        if last_message is None:
            return newest_user
        if newest_user.id > last_message.id:
            return newest_user
        return None
    
    @classmethod
    async def run(
        cls,
        session_id: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[LoopCallbacks] = None,
    ) -> LoopResult:
        """
        Run session loop
        
        Main entry point matching Flocks' SessionPrompt.loop()
        
        When provider_id/model_id are not provided, resolves from:
        1. Session's stored model (if set during creation)
        2. Global default LLM (default_models.llm -> config.model)
        3. Environment variables
        4. Hardcoded fallback
        
        Args:
            session_id: Session ID to process
            provider_id: Provider ID
            model_id: Model ID
            agent_name: Agent name (default: build)
            callbacks: Loop callbacks
            
        Returns:
            LoopResult with final state
        """
        # Check if already running.
        # Return action="queued" (not "error") so the route layer knows to skip
        # creating a spurious empty assistant message.  The new user message is
        # already persisted in the DB; the active loop will pick it up on its
        # next iteration once it finishes the current step.
        if cls.is_running(session_id):
            log.info("loop.already_running", {"session_id": session_id})
            return LoopResult(
                action="queued",
                error="Loop already running",
            )
        
        # Get session
        session = await Session.get_by_id(session_id)
        if not session:
            log.warning("loop.session_not_found", {"session_id": session_id})
            return LoopResult(
                action="error",
                error=f"Session {session_id} not found",
            )
        
        # Resolve model when not explicitly provided
        if not provider_id or not model_id:
            resolved_provider, resolved_model = await cls._resolve_model(
                session, provider_id, model_id
            )
            provider_id = provider_id or resolved_provider
            model_id = model_id or resolved_model
        
        # Keep the in-memory session aligned with the runtime model so
        # downstream helpers (title generation, compaction checks, etc.) see
        # the model actually selected for this loop iteration. Unpinned
        # sessions must not persist these values; otherwise switching the
        # global default model would keep older sessions stuck on stale data.
        if provider_id:
            session.provider = provider_id
        if model_id:
            session.model = model_id
        
        # Create SessionContext interface for decoupled access
        from flocks.session.core.context import DefaultSessionContext
        session_ctx = DefaultSessionContext(session)
        
        # Compute trace step offset from existing assistant messages so
        # observability step numbers are cumulative across the whole session.
        trace_offset = 0
        try:
            existing_msgs = await Message.list(session_id)
            trace_offset = sum(1 for m in existing_msgs if m.role == "assistant")
        except Exception as _trace_err:
            log.debug("loop.trace_offset.error", {"error": str(_trace_err)})
        
        # Create context
        ctx = LoopContext(
            session=session,
            provider_id=provider_id,
            model_id=model_id,
            agent_name=agent_name or session.agent or "rex",
            session_ctx=session_ctx,
            trace_step_offset=trace_offset,
        )
        
        # Register context
        cls._active_loops[session_id] = ctx
        
        # Set status to busy
        SessionStatus.set(session_id, SessionStatusBusy())
        
        # Mark orphaned running tool parts as error (e.g. from server restart).
        # Wrapped in try/except so cleanup failures never block the session loop.
        try:
            await cls._abort_orphan_running_parts(session_id)
        except Exception as exc:
            log.warn("loop.orphan_cleanup_failed", {
                "session_id": session_id,
                "error": str(exc),
            })
        
        try:
            # Run loop iteration
            result = await cls._run_loop(ctx, callbacks or LoopCallbacks())
            return result
        except Exception as e:
            log.error("loop.error", {"session_id": session_id, "error": str(e)})
            # Report error to callbacks so CLI/TUI can display it
            if callbacks and callbacks.on_error:
                try:
                    await callbacks.on_error(str(e))
                except Exception as _cb_err:
                    log.debug("loop.error.callback_failed", {"error": str(_cb_err)})
            try:
                from flocks.bus.bus import Bus
                from flocks.bus.events import SessionError
                await Bus.publish(SessionError, {
                    "sessionID": session_id,
                    "error": str(e),
                })
            except Exception as exc:
                log.warn("loop.error.event_error", {"error": str(exc)})
            return LoopResult(action="error", error=str(e))
        finally:
            # Clean up
            if session_id in cls._active_loops:
                del cls._active_loops[session_id]
            clear_turn_state(session_id)
            
            # Set status to idle
            SessionStatus.set(session_id, SessionStatusIdle())
            
            # Touch session (update timestamp)
            await Session.touch(session.project_id, session_id)

            # Publish idle event
            try:
                from flocks.bus.bus import Bus
                from flocks.bus.events import SessionIdle
                await Bus.publish(SessionIdle, {"sessionID": session_id})
            except Exception as exc:
                log.warn("loop.idle.event_error", {"error": str(exc)})
    
    @classmethod
    async def _abort_orphan_running_parts(cls, session_id: str) -> None:
        """Mark any tool parts stuck in 'running' status as error.

        When the server restarts while a synchronous tool (e.g. delegate_task)
        is executing, the tool part stays 'running' in storage forever.  On the
        next session loop start we know nothing is actually executing yet, so
        any 'running' parts are orphans.
        """
        import time as _time
        from flocks.session.message import (
            ToolPart, ToolStateError,
        )

        messages = await Message.list(session_id)
        now_ms = int(_time.time() * 1000)
        repaired = 0

        for msg in messages:
            parts = await Message.parts(msg.id, session_id)
            for part in parts:
                if not isinstance(part, ToolPart):
                    continue
                state = part.state
                if getattr(state, "status", None) != "running":
                    continue

                time_info = getattr(state, "time", {}) or {}
                start_ms = time_info.get("start", now_ms)

                error_state = ToolStateError(
                    status="error",
                    input=getattr(state, "input", {}),
                    error="Interrupted by server restart",
                    time={"start": start_ms, "end": now_ms},
                )
                # Preserve metadata (e.g. sessionId) so the card still works
                meta = getattr(state, "metadata", None)
                if meta:
                    error_state.metadata = meta

                part.state = error_state
                await Message.store_part(session_id, msg.id, part)
                repaired += 1

        if repaired:
            log.info("loop.orphan_parts_aborted", {
                "session_id": session_id,
                "count": repaired,
            })

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
    async def _run_loop(
        cls,
        ctx: LoopContext,
        callbacks: LoopCallbacks,
    ) -> LoopResult:
        """
        Main loop iteration logic
        
        完全匹配 TUI SessionPrompt.loop() 的结构:
        1. Get messages and analyze (lastUser, lastAssistant, lastFinished)
        2. Check exit conditions
        3. Generate title on first step
        4. Check for pending tasks (subtask/compaction)
        5. Check context overflow (compaction before step)
        6. Process step (call LLM + tools)
        7. Loop until complete
        """
        last_message: Optional[MessageInfo] = None
        
        while not ctx.should_abort():
            # Set status to busy
            SessionStatus.set(ctx.session.id, SessionStatusBusy())
            
            ctx.step += 1
            turn_state = set_turn_state(
                ctx.session.id,
                step=ctx.step,
                status="started",
                queued_message_detected=False,
            )
            await cls._publish_runtime_event(callbacks, "turn.started", turn_state.model_dump(by_alias=True))
            log.info("loop.step", {
                "session_id": ctx.session.id,
                "step": ctx.step,
            })
            
            # Callback: step start
            if callbacks.on_step_start:
                await callbacks.on_step_start(ctx.step)
            
            # Get messages via SessionContext interface
            if ctx.session_ctx:
                messages = await ctx.session_ctx.get_messages()
            else:
                messages = await Message.list(ctx.session.id)
            if not messages:
                log.info("loop.no_messages", {"session_id": ctx.session.id})
                break
            
            # Analyze messages (matching TUI lines 277-292)
            last_user: Optional[MessageInfo] = None
            last_assistant: Optional[MessageInfo] = None
            last_finished: Optional[MessageInfo] = None
            tasks: List[tuple[str, Any]] = []  # (type, part) - compaction or subtask
            
            for msg in reversed(messages):
                # Find lastUser
                if not last_user and msg.role == MessageRole.USER:
                    last_user = msg
                
                # Find lastAssistant
                if not last_assistant and msg.role == MessageRole.ASSISTANT:
                    last_assistant = msg
                
                # Find lastFinished
                if not last_finished and msg.role == MessageRole.ASSISTANT and hasattr(msg, 'finish') and msg.finish:
                    last_finished = msg
                
                # Stop when we have both lastUser and lastFinished
                if last_user and last_finished:
                    break
                
                # Collect pending tasks before lastFinished
                if not last_finished:
                    parts = await Message.parts(msg.id, ctx.session.id)
                    for part in parts:
                        if part.type == "compaction":
                            tasks.append(("compaction", part))
                        elif part.type == "subtask":
                            tasks.append(("subtask", part))
            
            # Check if we have a user message
            if not last_user:
                log.error("loop.no_user_message", {"session_id": ctx.session.id})
                break
            
            # Check exit conditions (matching TUI lines 295-302)
            if cls._should_exit(last_user, last_assistant):
                log.info("loop.exit_condition", {
                    "session_id": ctx.session.id,
                    "last_user_id": last_user.id,
                    "last_assistant_id": last_assistant.id if last_assistant else None,
                    "finish": last_assistant.finish if last_assistant else None,
                })
                last_message = last_assistant
                break
            
            # Bootstrap memory on first step (once per loop, stored in ctx)
            if ctx.step == 1 and ctx.session.memory_enabled and ctx.memory_bootstrap_data is None:
                try:
                    from flocks.memory.bootstrap import MemoryBootstrap
                    ctx.memory_bootstrap_data = await MemoryBootstrap().bootstrap()
                    log.info("loop.memory_bootstrap_done", {
                        "session_id": ctx.session.id,
                        "has_main": ctx.memory_bootstrap_data.get("main_memory") is not None,
                    })
                except Exception as e:
                    log.error("loop.memory_bootstrap_error", {"error": str(e)})

            # Early title generation: fire concurrently with the first LLM call so
            # the title is ready (or nearly so) by the time the response completes.
            # This is an optimistic fast-path — CLISessionRunner._process_message()
            # also calls generate_title_after_first_message() after the loop as a
            # guaranteed safety net (handles single-run mode where asyncio cleanup
            # may cancel this task before it finishes).
            # generate_title_after_first_message is idempotent: if this task saves
            # the title first, the safety-net call returns immediately.
            if ctx.step == 1:
                try:
                    from flocks.session.lifecycle.title import SessionTitle
                    # UserMessageInfo.model is Dict[str, str] {"providerID": ..., "modelID": ...}
                    user_model = getattr(last_user, 'model', None) if last_user else None
                    if isinstance(user_model, dict):
                        title_model_id = user_model.get("modelID", ctx.model_id)
                        title_provider_id = user_model.get("providerID", ctx.provider_id)
                    else:
                        title_model_id = ctx.model_id
                        title_provider_id = ctx.provider_id
                    fire_and_forget(
                        SessionTitle.ensure_title(
                            session_id=ctx.session.id,
                            model_id=title_model_id,
                            provider_id=title_provider_id,
                            messages=messages,
                            event_publish_callback=callbacks.event_publish_callback if callbacks else None,
                        ),
                        label="title_generation",
                        name=f"title:{ctx.session.id}",
                    )
                except Exception as e:
                    log.error("loop.title_generation.error", {"error": str(e)})
            
            # Check for pending tasks (matching TUI lines 314-493)
            if tasks:
                task_type, task_part = tasks.pop()
                
                # Handle pending subtask (matching TUI lines 316-481)
                if task_type == "subtask":
                    log.info("loop.subtask_detected", {
                        "session_id": ctx.session.id,
                        "step": ctx.step,
                    })
                    
                    # Execute subtask using tool execution
                    await cls._execute_subtask(ctx, last_user, task_part)
                    
                    # Continue to next iteration
                    continue
                
                # Handle pending compaction (matching TUI lines 483-494)
                elif task_type == "compaction":
                    log.info("loop.compaction_pending", {
                        "session_id": ctx.session.id,
                        "step": ctx.step,
                        "auto": getattr(task_part, 'auto', False),
                    })
                    
                    # Callback: compaction
                    if callbacks.on_compaction:
                        await callbacks.on_compaction()
                    
                    # Build dynamic CompactionPolicy from model info
                    compaction_policy = cls._build_compaction_policy(ctx)
                    
                    # Auto-compaction also surfaces a "Compacting..."
                    # banner on the UI (driven by ``session.status`` →
                    # ``compacting``), so we wire the same SSE progress
                    # adapter as the manual ``/compact`` route.  The
                    # closure captures ``ctx.session.id`` and the
                    # publish callback explicitly to keep behaviour
                    # identical between loop and route paths.
                    _publish = callbacks.event_publish_callback if callbacks else None
                    _session_id_for_progress = ctx.session.id
                    progress_callback = None
                    if _publish is not None:
                        async def progress_callback(stage: str, data: dict) -> None:
                            await _publish("session.compaction_progress", {
                                "sessionID": _session_id_for_progress,
                                "stage": stage,
                                "data": data,
                            })

                    # Process compaction
                    try:
                        compaction_result = await run_compaction(
                            ctx.session.id,
                            parent_message_id=last_user.id,
                            messages=messages,
                            provider_id=ctx.provider_id,
                            model_id=ctx.model_id,
                            auto=getattr(task_part, 'auto', False),
                            event_publish_callback=_publish,
                            status_after="busy",
                            policy=compaction_policy,
                            progress_callback=progress_callback,
                        )
                        
                        if compaction_result == "stop":
                            log.error("loop.compaction_failed", {"session_id": ctx.session.id})
                            if callbacks.on_error:
                                await callbacks.on_error("Compaction failed")
                            break
                        
                        # Continue after compaction
                        continue
                        
                    except Exception as e:
                        log.error("loop.compaction_error", {"error": str(e)})
                        if callbacks.on_error:
                            await callbacks.on_error(f"Compaction error: {str(e)}")
                        break
            
            # ----------------------------------------------------------------
            # Context overflow detection & recovery
            #
            # Matches OpenClaw run.ts overflow recovery cascade:
            #   1. Detect overflow
            #   2. Try tool result truncation (once per run)
            #   3. Full compaction (up to MAX_OVERFLOW_COMPACTION_ATTEMPTS)
            #   4. Give up with error if still overflowing
            # ----------------------------------------------------------------
            if last_finished and not getattr(last_finished, 'summary', False):
                # Get model context limit from flocks.json / provider registry
                model_context, model_output, model_input = Provider.resolve_model_info(
                    ctx.provider_id, ctx.model_id
                )
                
                # Check for overflow using dynamic CompactionPolicy
                if model_context > 0:
                    compaction_policy = CompactionPolicy.from_model(
                        context_window=model_context,
                        max_output_tokens=model_output or 4096,
                        max_input_tokens=model_input,
                    )
                    
                    # Build tokens_dict from last_finished.tokens if available
                    tokens_dict = {}
                    if hasattr(last_finished, 'tokens') and last_finished.tokens and isinstance(last_finished.tokens, dict):
                        tokens_dict = last_finished.tokens
                    
                    # Check if provider returned actual usage data (not all zeros)
                    input_tokens = tokens_dict.get("input", 0)
                    cache_read = tokens_dict.get("cache", {}).get("read", 0) if isinstance(tokens_dict.get("cache"), dict) else 0
                    output_tokens = tokens_dict.get("output", 0)
                    reported_total = input_tokens + cache_read + output_tokens
                    
                    # Fallback: if provider didn't report usage (all zeros),
                    # estimate token count from full message history including parts
                    if reported_total == 0:
                        estimated_tokens = await SessionPrompt.estimate_full_context_tokens(
                            ctx.session.id, messages
                        )
                        tokens_dict = {"input": estimated_tokens, "output": 0, "cache": {"read": 0, "write": 0}}
                        log.info("loop.tokens_estimated_from_messages", {
                            "session_id": ctx.session.id,
                            "estimated_tokens": estimated_tokens,
                            "message_count": len(messages),
                            "overflow_threshold": compaction_policy.overflow_threshold,
                        })
                    
                    try:
                        current_input_tokens = (
                            tokens_dict.get("input", 0)
                            + (tokens_dict.get("cache", {}).get("read", 0) if isinstance(tokens_dict.get("cache"), dict) else 0)
                        )
                        recent_compaction = cls._has_recent_compaction_cooldown(ctx)
                        near_overflow = current_input_tokens >= compaction_policy.preemptive_threshold

                        if near_overflow and ctx.last_cleanup_step != ctx.step:
                            try:
                                trunc_count = await SessionCompaction.truncate_oversized_tool_outputs(
                                    ctx.session.id,
                                    context_window_tokens=model_context,
                                )
                                ctx.last_cleanup_step = ctx.step
                                if trunc_count > 0:
                                    set_context_state(
                                        ctx.session.id,
                                        tool_results_compacted=True,
                                        last_compaction_step=ctx.last_compaction_step,
                                        last_compaction_reason="pre_compact_cleanup",
                                    )
                                    await cls._publish_runtime_event(callbacks, "context.compacted", {
                                        "sessionID": ctx.session.id,
                                        "step": ctx.step,
                                        "reason": "pre_compact_cleanup",
                                        "truncatedToolResults": trunc_count,
                                        "cooldownActive": recent_compaction,
                                    })
                                    log.info("loop.pre_compact_cleanup_applied", {
                                        "session_id": ctx.session.id,
                                        "step": ctx.step,
                                        "truncated": trunc_count,
                                        "preemptive_threshold": compaction_policy.preemptive_threshold,
                                        "input_tokens": current_input_tokens,
                                        "cooldown_active": recent_compaction,
                                    })
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
                                    continue
                            except Exception as trunc_err:
                                log.warn("loop.pre_compact_cleanup_error", {
                                    "session_id": ctx.session.id,
                                    "error": str(trunc_err),
                                })

                        is_overflow = await SessionCompaction.is_overflow(
                            tokens=tokens_dict,
                            model_context=model_context,
                            policy=compaction_policy,
                        )
                        
                        if is_overflow:
                            log.info("loop.context_overflow_detected", {
                                "session_id": ctx.session.id,
                                "step": ctx.step,
                                "tokens": tokens_dict,
                                "tier": compaction_policy.tier.value,
                                "overflow_compaction_attempts": ctx.overflow_compaction_attempts,
                            })

                            # Check if we've exhausted compaction attempts
                            # (matches OpenClaw MAX_OVERFLOW_COMPACTION_ATTEMPTS)
                            if ctx.overflow_compaction_attempts >= MAX_OVERFLOW_COMPACTION_ATTEMPTS:
                                await cls._publish_session_notice(
                                    callbacks,
                                    ctx.session.id,
                                    level="warning",
                                    message=(
                                        "当前任务上下文过重，已经多次 compact 仍接近上限。"
                                        "建议收敛工具输出、缩小搜索范围，或开启新会话。"
                                    ),
                                    details={
                                        "attempts": ctx.overflow_compaction_attempts,
                                        "maxAttempts": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                                        "tokens": tokens_dict,
                                    },
                                )
                                log.error("loop.overflow_compaction_exhausted", {
                                    "session_id": ctx.session.id,
                                    "attempts": ctx.overflow_compaction_attempts,
                                    "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                                    "tokens": tokens_dict,
                                })
                                if callbacks.on_error:
                                    await callbacks.on_error(
                                        "Context overflow: prompt too large for the model after "
                                        f"{ctx.overflow_compaction_attempts} compaction attempts. "
                                        "Try starting a new session or use a larger-context model."
                                    )
                                break

                            # Recovery step 1: try truncating oversized tool
                            # results (once per run, matches OpenClaw
                            # toolResultTruncationAttempted)
                            if not ctx.tool_result_truncation_attempted:
                                ctx.tool_result_truncation_attempted = True
                                try:
                                    trunc_count = await SessionCompaction.truncate_oversized_tool_outputs(
                                        ctx.session.id,
                                        context_window_tokens=model_context,
                                    )
                                    if trunc_count > 0:
                                        log.info("loop.oversized_tool_truncated", {
                                            "session_id": ctx.session.id,
                                            "truncated": trunc_count,
                                        })
                                        # Re-check overflow after truncation
                                        re_est = await SessionPrompt.estimate_full_context_tokens(
                                            ctx.session.id, messages
                                        )
                                        re_tokens = {"input": re_est, "output": 0, "cache": {"read": 0, "write": 0}}
                                        still_overflow = await SessionCompaction.is_overflow(
                                            tokens=re_tokens,
                                            model_context=model_context,
                                            policy=compaction_policy,
                                        )
                                        if not still_overflow:
                                            log.info("loop.overflow_resolved_by_truncation", {
                                                "session_id": ctx.session.id,
                                            })
                                            # Do NOT reset overflow_compaction_attempts
                                            # (matches OpenClaw OC-65)
                                            continue
                                except Exception as trunc_err:
                                    log.warn("loop.oversized_truncation_error", {
                                        "session_id": ctx.session.id,
                                        "error": str(trunc_err),
                                    })
                            
                            # Recovery step 2: full compaction
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
                                        "threshold": compaction_policy.overflow_threshold,
                                        "buffer": compaction_policy.overflow_buffer,
                                    },
                                )
                            log.warn("loop.overflow_compaction_attempt", {
                                "session_id": ctx.session.id,
                                "attempt": ctx.overflow_compaction_attempts,
                                "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                            })

                            # --- Compaction start: notify all UIs ---
                            if callbacks.on_compaction:
                                await callbacks.on_compaction()
                            
                            # Prune first, then summarize
                            await SessionCompaction.prune(
                                ctx.session.id,
                                policy=compaction_policy,
                            )
                            
                            # Same SSE progress adapter as the manual
                            # /compact route — mirrored here so the
                            # overflow-driven path also drives the
                            # multi-stage UI panel.
                            _publish_overflow = callbacks.event_publish_callback if callbacks else None
                            _session_id_overflow = ctx.session.id
                            progress_callback_overflow = None
                            if _publish_overflow is not None:
                                async def progress_callback_overflow(stage: str, data: dict) -> None:
                                    await _publish_overflow("session.compaction_progress", {
                                        "sessionID": _session_id_overflow,
                                        "stage": stage,
                                        "data": data,
                                    })

                            # Trigger compaction (summarization + memory flush)
                            compaction_result = await run_compaction(
                                ctx.session.id,
                                parent_message_id=last_user.id,
                                messages=messages,
                                provider_id=ctx.provider_id,
                                model_id=ctx.model_id,
                                auto=True,
                                event_publish_callback=_publish_overflow,
                                status_after="busy",
                                policy=compaction_policy,
                                progress_callback=progress_callback_overflow,
                            )
                            ctx.last_compaction_step = ctx.step
                            set_context_state(
                                ctx.session.id,
                                compaction_performed=True,
                                last_compaction_step=ctx.step,
                                last_compaction_reason="full_compaction",
                            )
                            await cls._publish_runtime_event(callbacks, "context.compacted", {
                                "sessionID": ctx.session.id,
                                "step": ctx.step,
                                "reason": "full_compaction",
                                "attempt": ctx.overflow_compaction_attempts,
                                "cooldownUntilStep": ctx.step + POST_COMPACTION_COOLDOWN_STEPS,
                            })
                            
                            if compaction_result == "stop":
                                log.error("loop.compaction_failed", {"session_id": ctx.session.id})
                                if callbacks.on_error:
                                    await callbacks.on_error("Compaction failed")
                                break
                            
                            # Continuation user message is now created inside
                            # SessionCompaction.process() (matching Flocks).
                            # Just continue — the new user message flips the
                            # ID ordering so _should_exit() won't trigger.
                            continue
                    except Exception as e:
                        log.error("loop.compaction_overflow_check_error", {"error": str(e)})
            
            # Process step - delegate to runner (matching TUI SessionProcessor.process)
            from flocks.session.runner import SessionRunner, RunnerCallbacks
            
            # Build runner callbacks from loop callbacks
            runner_cbs = callbacks.runner_callbacks
            if runner_cbs is None:
                runner_cbs = RunnerCallbacks()
            # Ensure event_publish_callback is propagated
            if callbacks.event_publish_callback and not runner_cbs.event_publish_callback:
                runner_cbs.event_publish_callback = callbacks.event_publish_callback
            
            runner = SessionRunner(
                session=ctx.session,
                provider_id=ctx.provider_id,
                model_id=ctx.model_id,
                agent_name=ctx.agent_name,
                abort_event=ctx.abort_event,
                callbacks=runner_cbs,
                session_ctx=ctx.session_ctx,
                memory_bootstrap_data=ctx.memory_bootstrap_data,
                static_cache=ctx.runner_static_cache,
            )
            # Use session-cumulative step number for observability.
            runner._step = ctx.trace_step
            
            # Process single step — wrap in a Task so abort() can cancel it immediately
            # rather than waiting for the current tool call to finish.
            step_task = asyncio.create_task(runner._process_step(messages, last_user))
            ctx._current_step_task = step_task
            try:
                step_result = await step_task
            except asyncio.CancelledError:
                log.info("loop.step_cancelled", {"session_id": ctx.session.id, "step": ctx.step})
                break
            finally:
                ctx._current_step_task = None
            
            # Callback: step end
            if callbacks.on_step_end:
                await callbacks.on_step_end(ctx.step)
            
            # Handle result
            if step_result.action == "stop":
                # Report error if step failed
                if step_result.error and callbacks.on_error:
                    await callbacks.on_error(step_result.error)
                
                # Get last assistant message via SessionContext
                if ctx.session_ctx:
                    post_messages = await ctx.session_ctx.get_messages()
                else:
                    post_messages = await Message.list(ctx.session.id)
                for msg in reversed(post_messages):
                    if msg.role == MessageRole.ASSISTANT:
                        last_message = msg
                        break

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
                    await cls._publish_runtime_event(callbacks, "turn.continued", {
                        **turn_state.model_dump(by_alias=True),
                        "queuedUserMessageID": queued_user.id,
                    })
                    log.info("loop.continuing_for_queued_message", {
                        "session_id": ctx.session.id,
                        "queued_user_id": queued_user.id,
                        "last_assistant_id": last_message.id if last_message else None,
                    })
                    continue

                stop_reason = step_result.error or (getattr(last_message, "finish", None) if last_message else None) or "stop"
                turn_state = set_turn_state(
                    ctx.session.id,
                    step=ctx.step,
                    status="stopped",
                    stop_reason=stop_reason,
                    queued_message_detected=False,
                )
                await cls._publish_runtime_event(callbacks, "turn.stopped", turn_state.model_dump(by_alias=True))

                break
            
            elif step_result.action == "continue":
                turn_state = set_turn_state(
                    ctx.session.id,
                    step=ctx.step,
                    status="continued",
                    continue_reason="tool_calls",
                    queued_message_detected=False,
                )
                await cls._publish_runtime_event(callbacks, "turn.continued", turn_state.model_dump(by_alias=True))
                # Continue to next iteration
                continue
            
            else:
                # Unknown action
                log.warn("loop.unknown_action", {
                    "session_id": ctx.session.id,
                    "action": step_result.action,
                })
                break
        
        # Return result
        return LoopResult(
            action="stop",
            last_message=last_message,
            metadata={
                "steps": ctx.step,
                "session_id": ctx.session.id,
                "last_compaction_step": ctx.last_compaction_step,
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
    ) -> bool:
        """
        Check if loop should exit
        
        Ported from original exit logic:
        - Exit if assistant has responded with finish != tool-calls
        - Exit if assistant message is after user message
        """
        if not last_assistant:
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
