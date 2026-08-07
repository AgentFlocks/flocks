"""State and persistence boundary for one logical session turn.

Implements model-turn preparation with support for:
- Message processing
- Tool execution
- Compaction
- Reminders
"""

import asyncio
import time
from typing import Optional, List, Dict, Any, Callable, Awaitable, Literal
from dataclasses import dataclass, field
from datetime import datetime

from flocks.session.runtime.contracts import (
    AgentRunState,
    ModelTurnBoundary,
    ModelTurnPreparation,
    ModelTurnSnapshot,
    QueuedInputBatch,
    RuntimeModel,
    StepAction,
    StepResult,
    TurnPreparationStatus,
)
from flocks.utils.log import Log
from flocks.session.session import (
    Session,
    SessionInfo,
)
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.runtime.event_sink import SessionEventSink
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


log = Log.create(service="session.loop")


MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3
POST_COMPACTION_COOLDOWN_STEPS = 2


@dataclass
class LoopCallbacks:
    """Callbacks for loop events"""

    on_step_start: Optional[Callable[[int], Awaitable[None]]] = None
    on_step_end: Optional[Callable[[int], Awaitable[None]]] = None
    on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_reasoning_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_tool_start: Optional[
        Callable[[str, Dict[str, Any]], Awaitable[None]]
    ] = None
    on_tool_end: Optional[Callable[[str, Any], Awaitable[None]]] = None
    on_permission_request: Optional[
        Callable[[Any], Awaitable[bool]]
    ] = None
    on_compaction: Optional[Callable[[], Awaitable[None]]] = None
    on_error: Optional[Callable[[str], Awaitable[None]]] = None
    on_reminder: Optional[Callable[[str], Awaitable[None]]] = None
    # SSE event publishing callback (for TUI/WebUI real-time updates)
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None


@dataclass
class LoopResult:
    """Result of loop execution"""

    action: str  # "stop", "continue", "compact", "error", "queued"
    last_message: Optional[MessageInfo] = None
    error: Optional[str] = None
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopContext:
    """Own state and persistence boundaries for one session loop run.

    Supports:
    - Logical user-turn and message iteration
    - Compaction triggers
    - Reminder injection
    """

    session: SessionInfo
    provider_id: str
    model_id: str
    agent_name: str
    callbacks: LoopCallbacks = field(default_factory=LoopCallbacks, repr=False)
    step: int = 0
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_store: Optional[Any] = None
    trace_step_offset: int = 0
    _current_step_task: Optional[asyncio.Task] = field(default=None, repr=False)
    memory_bootstrap_data: Optional[Dict[str, Any]] = field(default=None, repr=False)
    step_static_cache: Dict[str, Any] = field(default_factory=dict, repr=False)
    overflow_compaction_attempts: int = 0
    tool_result_truncation_attempted: bool = False
    last_compaction_step: Optional[int] = None
    last_cleanup_step: Optional[int] = None
    last_observed_prompt_tokens: int = 0
    auto_failover: bool = False
    auto_failover_allowed: bool = False
    model_candidates: List[RuntimeModel] = field(default_factory=list)
    candidate_index: int = 0
    model_candidate_policy: Literal["fixed", "automatic", "configured"] = "automatic"
    turn_user_id: Optional[str] = None
    turn_additional_context: Optional[str] = None
    stop_hook_active: bool = False
    prepared_user_id: Optional[str] = None
    prepared_messages: Optional[List[MessageInfo]] = field(default=None, repr=False)
    session_start_pending: bool = False
    model_policy: Optional[Any] = field(default=None, repr=False)
    continuation_policy: Optional[Any] = field(default=None, repr=False)
    state: AgentRunState[MessageInfo] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Start a fresh AgentLoop state for the current logical input."""
        self.state = AgentRunState[MessageInfo](
            session_id=self.session.id,
            agent_name=self.agent_name,
            active_model=RuntimeModel(self.provider_id, self.model_id),
            model_turn_index=self.step,
            trace_step_offset=self.trace_step_offset,
            current_user_id=self.turn_user_id,
        )

    @property
    def trace_step(self) -> int:
        """Return the session-cumulative step number for observability."""
        return self.trace_step_offset + self.step

    @property
    def aborted(self) -> bool:
        """Return whether this turn was asked to stop."""
        return self.abort_event.is_set()

    def should_abort(self) -> bool:
        """Keep the existing callable abort boundary for infrastructure."""
        return self.aborted

    def signal_abort(self) -> None:
        """Stop the turn and cancel its active model step immediately."""
        self.abort_event.set()
        task = self._current_step_task
        if task is not None and not task.done():
            task.cancel()

    def _has_recent_compaction_cooldown(self) -> bool:
        return (
            self.last_compaction_step is not None
            and (self.step - self.last_compaction_step) <= POST_COMPACTION_COOLDOWN_STEPS
        )


    async def finalize_failure(
        self,
        failure: Any,
        last_user: MessageInfo,
    ) -> None:
        """Persist only the final Auto candidate failure."""
        if not failure.assistant_message_id:
            assistant = await Message.create(
                session_id=self.session.id,
                role=MessageRole.ASSISTANT,
                content="",
                agent=getattr(last_user, "agent", None) or self.agent_name or "rex",
                model_id=self.model_id,
                provider_id=self.provider_id,
                parent_id=last_user.id,
                error=failure.error_data,
                finish="error",
            )
            failure.assistant_message_id = assistant.id
            return
        await Message.update(
            self.session.id,
            failure.assistant_message_id,
            error=failure.error_data,
            finish="error",
        )

    async def prepare_step(
        self,
    ) -> ModelTurnPreparation[MessageInfo]:
        """Prepare one immutable model-turn snapshot from session state."""
        state = self.state
        SessionStatus.set(self.session.id, SessionStatusBusy())
        self.step += 1
        state.model_turn_index = self.step
        turn_state = set_turn_state(
            self.session.id,
            step=self.step,
            status="started",
            queued_message_detected=False,
        )
        await SessionEventSink.emit(
            self.callbacks,
            "turn.started",
            turn_state.model_dump(by_alias=True),
        )
        log.info(
            "loop.step",
            {"session_id": self.session.id, "step": self.step},
        )
        if self.callbacks.on_step_start:
            await self.callbacks.on_step_start(self.step)

        messages_started_at = asyncio.get_running_loop().time()
        if self.prepared_messages is not None:
            messages = self.prepared_messages
            self.prepared_messages = None
        elif self.session_store:
            messages = await self.session_store.get_messages()
        else:
            messages = await Message.list(self.session.id)
        log.debug(
            "loop.messages_loaded",
            {
                "session_id": self.session.id,
                "step": self.step,
                "message_count": len(messages),
                "duration_ms": int((asyncio.get_running_loop().time() - messages_started_at) * 1000),
            },
        )
        if not messages:
            log.info("loop.no_messages", {"session_id": self.session.id})
            await SessionEventSink.turn_stopped(
                self.callbacks,
                self.session.id,
                step=self.step,
                stop_reason="no_messages",
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

        last_user: Optional[MessageInfo] = None
        last_assistant: Optional[MessageInfo] = None
        last_finished: Optional[MessageInfo] = None
        pending_compactions: List[Any] = []
        scan_started_at = asyncio.get_running_loop().time()
        for message in reversed(messages):
            if last_user is None and message.role == MessageRole.USER:
                last_user = message
            if last_assistant is None and message.role == MessageRole.ASSISTANT:
                last_assistant = message
            if last_finished is None and message.role == MessageRole.ASSISTANT and getattr(message, "finish", None):
                last_finished = message
            if last_user is not None and last_finished is not None:
                break
            if last_finished is None:
                for part in await Message.parts(message.id, self.session.id):
                    if part.type == "compaction":
                        pending_compactions.append(part)
        log.debug(
            "loop.message_scan_complete",
            {
                "session_id": self.session.id,
                "step": self.step,
                "compaction_count": len(pending_compactions),
                "duration_ms": int((asyncio.get_running_loop().time() - scan_started_at) * 1000),
            },
        )

        if last_user is None:
            log.info(
                "loop.no_user_message",
                {
                    "session_id": self.session.id,
                    "message_count": len(messages),
                    "roles": [str(getattr(message, "role", "")) for message in messages[-5:]],
                },
            )
            await SessionEventSink.turn_stopped(
                self.callbacks,
                self.session.id,
                step=self.step,
                stop_reason="no_user_message",
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

        last_assistant_parts = await Message.parts(last_assistant.id, self.session.id) if last_assistant else []
        if self._should_exit(last_user, last_assistant, last_assistant_parts):
            log.info(
                "loop.exit_condition",
                {
                    "session_id": self.session.id,
                    "last_user_id": last_user.id,
                    "last_assistant_id": (last_assistant.id if last_assistant else None),
                    "finish": last_assistant.finish if last_assistant else None,
                    "has_tool_parts": any(getattr(part, "type", None) == "tool" for part in last_assistant_parts),
                },
            )
            return ModelTurnPreparation(
                status=TurnPreparationStatus.COMPLETE,
                last_message=last_assistant,
            )

        state.current_user_id = last_user.id
        state.metadata["last_user"] = last_user
        await self._prepare_memory()
        self._schedule_title_generation(last_user, messages)

        if pending_compactions:
            compaction_preparation = await self._prepare_pending_compaction(
                messages,
                last_user,
                pending_compactions.pop(),
            )
            if compaction_preparation is not None:
                return compaction_preparation

        context_preparation = await self._prepare_context_window(
            messages,
            last_user,
            last_finished,
        )
        if context_preparation is not None:
            return context_preparation

        active_model = RuntimeModel(self.provider_id, self.model_id)
        state.active_model = active_model
        state.messages = list(messages)
        return ModelTurnPreparation(
            status=TurnPreparationStatus.READY,
            snapshot=ModelTurnSnapshot(
                session_id=self.session.id,
                agent_name=self.agent_name,
                active_model=active_model,
                model_turn_index=self.step,
                trace_step=self.trace_step,
                messages=tuple(messages),
                last_user=last_user,
            ),
        )

    async def commit_step(
        self,
        step_result: StepResult,
    ) -> ModelTurnBoundary[MessageInfo]:
        """Commit one executed step and expose its next control boundary."""
        if self.callbacks.on_step_end:
            await self.callbacks.on_step_end(self.step)
        if step_result.error and self.callbacks.on_error:
            await self.callbacks.on_error(step_result.error)

        SessionStatus.set(self.session.id, SessionStatusBusy())
        if self.session_store:
            post_messages = await self.session_store.get_messages()
        else:
            post_messages = await Message.list(self.session.id)

        last_user = self.state.metadata.get("last_user")
        last_message = next(
            (
                message
                for message in reversed(post_messages)
                if message.role == MessageRole.ASSISTANT
                and (
                    not self.auto_failover
                    or last_user is None
                    or getattr(message, "parentID", None) == last_user.id
                )
            ),
            None,
        )

        queued_user = None
        if last_user is not None:
            policy = self.continuation_policy
            if policy is None:
                from flocks.session.runtime.continuation_policy import (
                    DEFAULT_CONTINUATION_POLICY,
                )

                policy = DEFAULT_CONTINUATION_POLICY
            queued_user = await policy.detect_queued_user_message(
                self.session.id,
                post_messages,
                last_user.id,
                last_message,
            )

        if queued_user is not None:
            turn_state = set_turn_state(
                self.session.id,
                step=self.step,
                status="continued",
                continue_reason="queued_message",
                queued_message_detected=True,
            )
            await SessionEventSink.emit(
                self.callbacks,
                "turn.continued",
                {
                    **turn_state.model_dump(by_alias=True),
                    "queuedUserMessageID": queued_user.id,
                },
            )
            log.info(
                "session.turn.queued_input",
                {
                    "session_id": self.session.id,
                    "queued_user_id": queued_user.id,
                    "last_assistant_id": (
                        last_message.id if last_message else None
                    ),
                },
            )
        elif step_result.action == StepAction.CONTINUE:
            turn_state = set_turn_state(
                self.session.id,
                step=self.step,
                status="continued",
                continue_reason="tool_calls",
                queued_message_detected=False,
            )
            await SessionEventSink.emit(
                self.callbacks,
                "turn.continued",
                turn_state.model_dump(by_alias=True),
            )
        elif step_result.error:
            await SessionEventSink.turn_stopped(
                self.callbacks,
                self.session.id,
                step=self.step,
                stop_reason=step_result.error,
            )

        return ModelTurnBoundary(
            messages=tuple(post_messages),
            last_message=last_message,
            queued_inputs=QueuedInputBatch(
                messages=(queued_user,) if queued_user is not None else (),
            ),
        )

    async def has_late_input(self, processed_user_id: Optional[str]) -> bool:
        """Return whether a newer persisted user input arrived before settle."""
        if processed_user_id is None:
            return False
        messages = await Message.list(self.session.id)
        latest_user_id = next(
            (
                message.id
                for message in reversed(messages)
                if getattr(message, "role", None) == "user"
            ),
            None,
        )
        return latest_user_id is not None and latest_user_id != processed_user_id

    async def _prepare_memory(self) -> None:
        """Load memory once before the first model turn."""
        if self.step != 1 or not self.session.memory_enabled or self.memory_bootstrap_data is not None:
            return
        try:
            from flocks.memory.bootstrap import MemoryBootstrap

            self.memory_bootstrap_data = await MemoryBootstrap(
                project_id=self.session.project_id,
            ).bootstrap(load_daily=False)
            log.info(
                "loop.memory_bootstrap_done",
                {
                    "session_id": self.session.id,
                    "has_main": (self.memory_bootstrap_data.get("main_memory") is not None),
                },
            )
        except Exception as exc:
            log.error("loop.memory_bootstrap_error", {"error": str(exc)})

    def _schedule_title_generation(
        self,
        last_user: MessageInfo,
        messages: List[MessageInfo],
    ) -> None:
        """Start optimistic first-turn title generation without blocking."""
        if self.step != 1 or self.auto_failover:
            return
        try:
            from flocks.session.lifecycle.title import SessionTitle

            user_model = getattr(last_user, "model", None)
            if isinstance(user_model, dict):
                title_model_id = user_model.get("modelID", self.model_id)
                title_provider_id = user_model.get(
                    "providerID",
                    self.provider_id,
                )
            else:
                title_model_id = self.model_id
                title_provider_id = self.provider_id
            fire_and_forget(
                SessionTitle.ensure_title(
                    session_id=self.session.id,
                    model_id=title_model_id,
                    provider_id=title_provider_id,
                    messages=messages,
                    event_publish_callback=self.callbacks.event_publish_callback,
                ),
                label="title_generation",
                name=f"title:{self.session.id}",
            )
        except Exception as exc:
            log.error("loop.title_generation.error", {"error": str(exc)})

    async def _prepare_pending_compaction(
        self,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        compaction_part: Any,
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Finish persisted compaction work before the model turn."""
        log.info(
            "loop.compaction_pending",
            {
                "session_id": self.session.id,
                "step": self.step,
                "auto": getattr(compaction_part, "auto", False),
            },
        )
        if self.callbacks.on_compaction:
            await self.callbacks.on_compaction()

        publish = self.callbacks.event_publish_callback
        progress_callback = None
        if publish is not None:

            async def progress_callback(stage: str, data: dict) -> None:
                await publish(
                    "session.compaction_progress",
                    {
                        "sessionID": self.session.id,
                        "stage": stage,
                        "data": data,
                    },
                )

        try:
            compaction_result = await run_compaction(
                self.session.id,
                parent_message_id=last_user.id,
                messages=messages,
                provider_id=self.provider_id,
                model_id=self.model_id,
                auto=getattr(compaction_part, "auto", False),
                event_publish_callback=publish,
                status_after="busy",
                policy=self._build_compaction_policy(),
                progress_callback=progress_callback,
            )
            if compaction_result == "stop":
                log.error(
                    "loop.compaction_failed",
                    {"session_id": self.session.id},
                )
                if self.callbacks.on_error:
                    await self.callbacks.on_error("Compaction failed")
                return ModelTurnPreparation(
                    status=TurnPreparationStatus.COMPLETE,
                )
            if compaction_result == "skipped":
                log.info(
                    "loop.manual_compaction_skipped",
                    {"session_id": self.session.id, "step": self.step},
                )
            return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)
        except Exception as exc:
            log.error("loop.compaction_error", {"error": str(exc)})
            if self.callbacks.on_error:
                await self.callbacks.on_error(f"Compaction error: {exc}")
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)

    async def _prepare_context_window(
        self,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        last_finished: Optional[MessageInfo],
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Recover a near-overflow context before the next model turn."""
        if last_finished is None or getattr(last_finished, "summary", False):
            return None

        model_context, model_output, model_input = Provider.resolve_model_info(
            self.provider_id,
            self.model_id,
        )
        if model_context <= 0:
            return None

        policy = CompactionPolicy.from_model(
            context_window=model_context,
            max_output_tokens=model_output or 4096,
            max_input_tokens=model_input,
        )
        tokens = self._normalise_token_usage(last_finished)
        input_tokens = tokens.get("input", 0)
        cache = tokens.get("cache") or {}
        cache_read = cache.get("read", 0) if isinstance(cache, dict) else 0
        output_tokens = tokens.get("output", 0)
        reported_total = input_tokens + cache_read + output_tokens
        if reported_total > 0:
            self.last_observed_prompt_tokens = reported_total
            log.info(
                "loop.tokens_decision",
                {
                    "session_id": self.session.id,
                    "source": "observed",
                    "effective_tokens": input_tokens + cache_read,
                    "overflow_threshold": policy.overflow_threshold,
                },
            )
        else:
            estimated_tokens = await SessionPrompt.estimate_full_context_tokens(
                self.session.id,
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
                    "session_id": self.session.id,
                    "source": "estimated",
                    "effective_tokens": estimated_tokens,
                    "message_count": len(messages),
                    "overflow_threshold": policy.overflow_threshold,
                },
            )

        try:
            cache = tokens.get("cache") or {}
            current_input_tokens = tokens.get("input", 0) + (cache.get("read", 0) if isinstance(cache, dict) else 0)
            recent_compaction = self._has_recent_compaction_cooldown()
            near_overflow = current_input_tokens >= policy.preemptive_threshold
            if near_overflow and self.last_cleanup_step != self.step:
                cleanup_result = await self._prepare_tool_result_cleanup(
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
                    "session_id": self.session.id,
                    "step": self.step,
                    "tokens": tokens,
                    "tier": policy.tier.value,
                    "overflow_compaction_attempts": (self.overflow_compaction_attempts),
                },
            )
            if self.overflow_compaction_attempts >= MAX_OVERFLOW_COMPACTION_ATTEMPTS:
                await self._report_compaction_exhausted(
                    tokens,
                )
                return ModelTurnPreparation(
                    status=TurnPreparationStatus.COMPLETE,
                )

            if not self.tool_result_truncation_attempted:
                self.tool_result_truncation_attempted = True
                try:
                    truncation_count = await SessionCompaction.truncate_oversized_tool_outputs(
                        self.session.id,
                        context_window_tokens=model_context,
                    )
                    if truncation_count > 0:
                        log.info(
                            "loop.oversized_tool_truncated",
                            {
                                "session_id": self.session.id,
                                "truncated": truncation_count,
                            },
                        )
                        estimated_tokens = await SessionPrompt.estimate_full_context_tokens(
                            self.session.id,
                            messages,
                            policy=policy,
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
                                {"session_id": self.session.id},
                            )
                            return ModelTurnPreparation(
                                status=TurnPreparationStatus.CONTINUE,
                            )
                except Exception as exc:
                    log.warn(
                        "loop.oversized_truncation_error",
                        {"session_id": self.session.id, "error": str(exc)},
                    )

            return await self._prepare_full_compaction(
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

    async def _prepare_tool_result_cleanup(
        self,
        model_context: int,
        policy: CompactionPolicy,
        current_input_tokens: int,
        recent_compaction: bool,
    ) -> Optional[ModelTurnPreparation[MessageInfo]]:
        """Apply the cheap tool-result cleanup before full compaction."""
        try:
            truncation_count = await SessionCompaction.truncate_oversized_tool_outputs(
                self.session.id,
                context_window_tokens=model_context,
            )
            self.last_cleanup_step = self.step
            if truncation_count <= 0:
                return None

            set_context_state(
                self.session.id,
                tool_results_compacted=True,
                last_compaction_step=self.last_compaction_step,
                last_compaction_reason="pre_compact_cleanup",
            )
            await SessionEventSink.emit(
                self.callbacks,
                "context.compacted",
                {
                    "sessionID": self.session.id,
                    "step": self.step,
                    "reason": "pre_compact_cleanup",
                    "truncatedToolResults": truncation_count,
                    "cooldownActive": recent_compaction,
                },
            )
            log.info(
                "loop.pre_compact_cleanup_applied",
                {
                    "session_id": self.session.id,
                    "step": self.step,
                    "truncated": truncation_count,
                    "preemptive_threshold": policy.preemptive_threshold,
                    "input_tokens": current_input_tokens,
                    "cooldown_active": recent_compaction,
                },
            )
            turn_state = set_turn_state(
                self.session.id,
                step=self.step,
                status="continued",
                continue_reason="pre_compact_cleanup",
                queued_message_detected=False,
            )
            await SessionEventSink.emit(
                self.callbacks,
                "turn.continued",
                turn_state.model_dump(by_alias=True),
            )
            return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)
        except Exception as exc:
            log.warn(
                "loop.pre_compact_cleanup_error",
                {"session_id": self.session.id, "error": str(exc)},
            )
            return None

    async def _report_compaction_exhausted(
        self,
        tokens: Dict[str, Any],
    ) -> None:
        """Surface whether exhaustion came from context or provider health."""
        history = _get_compaction_history(self.session.id)
        provider_error = history.summary_last_error
        in_cooldown = history.summary_cooldown_until > 0 and history.summary_cooldown_until > time.monotonic()
        cooldown_seconds = max(
            0,
            round(history.summary_cooldown_until - time.monotonic()),
        )
        if in_cooldown or provider_error:
            notice = (
                "摘要模型暂时不可用，上下文压缩跳过了本轮压缩。"
                + (f"冷却剩余约 {cooldown_seconds} 秒，" if in_cooldown else "")
                + "建议稍后继续，或切换到其他模型重试。"
            )
            error = (
                "Compaction skipped: summary provider unavailable "
                f"({provider_error or 'cooldown active'})."
                + (f" Cooldown expires in ~{cooldown_seconds}s." if in_cooldown else "")
                + " Wait for the provider to recover or switch models."
            )
        else:
            notice = "当前任务上下文过重，已经多次 compact 仍接近上限。建议收敛工具输出、缩小搜索范围，或开启新会话。"
            error = (
                "Context overflow: prompt too large for the model after "
                f"{self.overflow_compaction_attempts} compaction attempts. "
                "Try starting a new session or use a larger-context model."
            )

        await SessionEventSink.notice(
            self.callbacks,
            self.session.id,
            level="warning",
            message=notice,
            details={
                "attempts": self.overflow_compaction_attempts,
                "maxAttempts": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                "tokens": tokens,
                "providerError": provider_error or None,
                "cooldownRemainingSeconds": (cooldown_seconds if in_cooldown else 0),
            },
        )
        log.error(
            "loop.overflow_compaction_exhausted",
            {
                "session_id": self.session.id,
                "attempts": self.overflow_compaction_attempts,
                "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
                "tokens": tokens,
                "in_cooldown": in_cooldown,
                "provider_error": provider_error or None,
            },
        )
        if self.callbacks.on_error:
            await self.callbacks.on_error(error)

    async def _prepare_full_compaction(
        self,
        messages: List[MessageInfo],
        last_user: MessageInfo,
        policy: CompactionPolicy,
    ) -> ModelTurnPreparation[MessageInfo]:
        """Run full compaction and request preparation to reload the session."""
        self.overflow_compaction_attempts += 1
        if self.overflow_compaction_attempts >= 2:
            await SessionEventSink.notice(
                self.callbacks,
                self.session.id,
                level="info",
                message=("本轮上下文持续接近模型上限，系统将优先尝试压缩历史工具输出。"),
                details={
                    "attempt": self.overflow_compaction_attempts,
                    "threshold": policy.overflow_threshold,
                    "buffer": policy.overflow_buffer,
                },
            )
        log.warn(
            "loop.overflow_compaction_attempt",
            {
                "session_id": self.session.id,
                "attempt": self.overflow_compaction_attempts,
                "max": MAX_OVERFLOW_COMPACTION_ATTEMPTS,
            },
        )
        if self.callbacks.on_compaction:
            await self.callbacks.on_compaction()
        await SessionCompaction.prune(self.session.id, policy=policy)

        publish = self.callbacks.event_publish_callback
        progress_callback = None
        if publish is not None:

            async def progress_callback(stage: str, data: dict) -> None:
                await publish(
                    "session.compaction_progress",
                    {
                        "sessionID": self.session.id,
                        "stage": stage,
                        "data": data,
                    },
                )

        result = await run_compaction(
            self.session.id,
            parent_message_id=last_user.id,
            messages=messages,
            provider_id=self.provider_id,
            model_id=self.model_id,
            auto=True,
            event_publish_callback=publish,
            status_after="busy",
            policy=policy,
            progress_callback=progress_callback,
        )
        if result == "stop":
            log.error(
                "loop.compaction_failed",
                {"session_id": self.session.id},
            )
            if self.callbacks.on_error:
                await self.callbacks.on_error("Compaction failed")
            return ModelTurnPreparation(status=TurnPreparationStatus.COMPLETE)
        if result == "skipped":
            log.info(
                "loop.compaction_skipped",
                {"session_id": self.session.id, "step": self.step},
            )
        else:
            self.last_compaction_step = self.step
            set_context_state(
                self.session.id,
                compaction_performed=True,
                last_compaction_step=self.step,
                last_compaction_reason="full_compaction",
            )
            await SessionEventSink.emit(
                self.callbacks,
                "context.compacted",
                {
                    "sessionID": self.session.id,
                    "step": self.step,
                    "reason": "full_compaction",
                    "attempt": self.overflow_compaction_attempts,
                    "cooldownUntilStep": (self.step + POST_COMPACTION_COOLDOWN_STEPS),
                },
            )
        return ModelTurnPreparation(status=TurnPreparationStatus.CONTINUE)

    def _build_compaction_policy(self) -> CompactionPolicy:
        """
        Construct a CompactionPolicy from the current model's info.

        Falls back to ``CompactionPolicy.default()`` when the model info
        cannot be resolved (e.g. unknown provider or missing context_window).
        """
        return build_compaction_policy(self.provider_id, self.model_id)

    @staticmethod
    def _should_exit(
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

        if any(getattr(part, "type", None) == "tool" for part in (last_assistant_parts or [])):
            return False

        # Check finish reason
        if last_assistant.finish:
            if last_assistant.finish not in ("tool-calls", "unknown", "summary"):
                # Assistant finished with stop/error/etc
                if last_user.id < last_assistant.id:
                    # Assistant responded after user
                    return True

        return False

    async def _check_reminders(
        self,
        messages: List[MessageInfo],
    ) -> None:
        """
        Check and inject reminders (P1 feature)

        Reminders are system messages injected periodically to:
        - Remind agent of task goals
        - Prevent drift from original intent
        - Nudge towards completion
        """
        from flocks.session.features.reminders import SessionReminders, ReminderContext

        # Calculate elapsed time
        if messages:
            first_msg = messages[0]
            if hasattr(first_msg, "time") and hasattr(first_msg.time, "created"):
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
            session_id=self.session.id,
            step_count=self.step,
            message_count=len(messages),
            elapsed_ms=elapsed_ms,
            original_task=original_task,
        )

        # Check if reminder should be injected
        if SessionReminders.should_remind(self.session.id, reminder_ctx):
            # Create and inject reminder
            reminder_msg = await SessionReminders.create_reminder(
                self.session.id,
                reminder_ctx,
            )

            if reminder_msg and self.callbacks.on_reminder:
                await self.callbacks.on_reminder(
                    await Message.get_text_content(reminder_msg),
                )
