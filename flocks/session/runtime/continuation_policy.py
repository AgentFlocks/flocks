"""Session-level logical turn preparation and continuation policy."""

from __future__ import annotations

import inspect
from typing import Any, Optional

from flocks.hooks.pipeline import HookPipeline
from flocks.session.runtime.contracts import (
    AgentRunOutcome,
    ContinuationDecision,
)
from flocks.session.core.turn_state import set_turn_state
from flocks.session.runtime.event_sink import SessionEventSink
from flocks.session.goal import GoalManager
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.runtime.model_policy import (
    DEFAULT_MODEL_ROUTING_POLICY,
    ModelRoutingPolicy,
)
from flocks.utils.log import Log


log = Log.create(service="session.continuation_policy")


class ContinuationPolicy:
    """Own boundaries between durable logical user turns."""

    def __init__(self, model_policy: ModelRoutingPolicy) -> None:
        self._model_policy = model_policy

    async def publish_turn_stopped(
        self,
        turn: Any,
        *,
        stop_reason: str,
    ) -> None:
        """Publish the terminal state of one logical turn."""
        await SessionEventSink.turn_stopped(
            turn.callbacks,
            turn.session.id,
            step=turn.step,
            stop_reason=stop_reason,
        )

    @staticmethod
    async def detect_queued_user_message(
        _session_id: str,
        post_messages: list[MessageInfo],
        current_user_id: str,
        _last_message: Optional[MessageInfo],
    ) -> Optional[MessageInfo]:
        """Return the newest user message after the current logical input."""
        newest_user = next(
            (message for message in reversed(post_messages) if message.role == MessageRole.USER),
            None,
        )
        if newest_user is None or newest_user.id <= current_user_id:
            return None
        return newest_user

    async def prepare_logical_turn(self, context: Any) -> None:
        """Prepare model routing and UserPromptSubmit once per logical input."""
        if context.session_store:
            messages = await context.session_store.get_messages()
        else:
            messages = await Message.list(context.session.id)
        context.prepared_messages = list(messages)
        last_user = next(
            (message for message in reversed(messages) if message.role == MessageRole.USER),
            None,
        )
        if last_user is None or last_user.id == context.prepared_user_id:
            return

        is_real_user_turn = await self._model_policy.prepare_turn(
            context,
            last_user,
        )
        if is_real_user_turn:
            context.turn_additional_context = None
            await self.run_user_prompt_submit(context, last_user)
        context.prepared_user_id = last_user.id

    @staticmethod
    async def run_user_prompt_submit(context: Any, last_user: MessageInfo) -> None:
        """Run UserPromptSubmit at the session logical-turn boundary."""
        try:
            prompt = await Message.get_text_content(last_user)
            hook_context = await HookPipeline.run_user_prompt_submit(
                {
                    "sessionID": context.session.id,
                    "workspace": context.session.directory,
                    "agent": getattr(last_user, "agent", None) or context.agent_name,
                    "model": {
                        "providerID": context.provider_id,
                        "modelID": context.model_id,
                    },
                    "messageID": last_user.id,
                    "prompt": prompt,
                }
            )
            additional_context = hook_context.output.get("additionalContext")
            if isinstance(additional_context, str) and additional_context.strip():
                context.turn_additional_context = additional_context.strip()
        except Exception as exc:
            log.debug(
                "session.hook.user_prompt_submit.error",
                {
                    "session_id": context.session.id,
                    "message_id": last_user.id,
                    "error": str(exc),
                },
            )

    async def resolve(
        self,
        context: Any,
        outcome: AgentRunOutcome[MessageInfo],
    ) -> ContinuationDecision[MessageInfo]:
        """Resolve queued input and goal continuation, then observe turn completion."""
        last_user = outcome.last_user
        last_message = outcome.last_message
        if last_user is None or last_message is None:
            await self.publish_turn_stopped(
                context,
                stop_reason="stop",
            )
            return ContinuationDecision()

        queued_decision = await self._materialize_continuation(
            context,
            last_user,
            last_message,
        )
        if queued_decision.should_continue:
            return queued_decision

        try:
            content_result = Message.get_text_content(last_message)
            last_response = await content_result if inspect.isawaitable(content_result) else content_result
        except Exception as exc:
            log.warn(
                "session.goal.last_response_error",
                {
                    "session_id": context.session.id,
                    "message_id": getattr(last_message, "id", None),
                    "error": str(exc),
                },
            )
            last_response = getattr(last_message, "content", "") or ""

        pending_user_input = False
        try:
            from flocks.server.routes.question import has_pending_questions

            pending_user_input = has_pending_questions(context.session.id)
        except Exception as exc:
            log.warn(
                "session.goal.pending_question_check_error",
                {"session_id": context.session.id, "error": str(exc)},
            )

        goal_decision = await GoalManager.evaluate_after_turn(
            context.session.id,
            str(last_response or ""),
            pending_user_input=pending_user_input,
            provider_id=context.provider_id,
            model_id=context.model_id,
        )
        if goal_decision.status in {"completed", "blocked", "paused"} and goal_decision.objective:
            await SessionEventSink.emit(
                context.callbacks,
                "session.goal.updated",
                {
                    "sessionID": context.session.id,
                    "status": goal_decision.status,
                    "objective": goal_decision.objective,
                    "reason": goal_decision.reason,
                },
            )
        if goal_decision.should_continue and goal_decision.continuation_prompt:
            allow_synthetic = await self._synthetic_continuation_allowed(
                context,
                last_message,
            )
            goal_continuation = await self._materialize_continuation(
                context,
                last_user,
                last_message,
                candidate_reason="goal",
                content=goal_decision.continuation_prompt,
                agent=(
                    last_user.agent
                    if hasattr(last_user, "agent")
                    else context.agent_name
                ),
                model=(
                    last_user.model
                    if hasattr(last_user, "model")
                    else {
                        "providerID": context.provider_id,
                        "modelID": context.model_id,
                    }
                ),
                provider=(
                    last_user.provider
                    if hasattr(last_user, "provider")
                    else context.provider_id
                ),
                part_metadata={
                    "goalContinuation": True,
                    "goalVerdict": goal_decision.verdict,
                    "goalReason": goal_decision.reason,
                },
                event_metadata={"goalVerdict": goal_decision.verdict},
                allow_synthetic=allow_synthetic,
            )
            if goal_continuation.should_continue:
                return goal_continuation
            await self.publish_turn_stopped(context, stop_reason="stop")
            return ContinuationDecision()

        queued_decision = await self._materialize_continuation(
            context,
            last_user,
            last_message,
        )
        if queued_decision.should_continue:
            return queued_decision

        if not context.should_abort() and getattr(last_message, "finish", None) == "stop":
            await self.run_turn_after(
                context,
                last_user,
                last_message,
            )

            queued_decision = await self._materialize_continuation(
                context,
                last_user,
                last_message,
            )
            if queued_decision.should_continue:
                return queued_decision

        stop_reason = getattr(last_message, "finish", None) or "stop"
        await self.publish_turn_stopped(
            context,
            stop_reason=stop_reason,
        )
        return ContinuationDecision()

    async def run_turn_after(
        self,
        context: Any,
        last_user: MessageInfo,
        last_message: MessageInfo,
    ) -> None:
        """Publish terminal turn facts without changing continuation control flow."""
        try:
            hook_user = last_user
            if context.turn_user_id:
                hook_user = await Message.get(context.session.id, context.turn_user_id) or last_user
            user_text = await Message.get_text_content(hook_user)
            assistant_text = await Message.get_text_content(last_message)
            await HookPipeline.run_turn_after(
                {
                    "sessionID": context.session.id,
                    "workspace": context.session.directory,
                    "agent": getattr(last_message, "agent", None) or context.agent_name,
                    "model": {
                        "providerID": context.provider_id,
                        "modelID": context.model_id,
                    },
                    "step": context.trace_step,
                    "userMessage": {
                        "id": hook_user.id,
                        "content": user_text,
                    },
                    "assistantMessage": {
                        "id": last_message.id,
                        "content": assistant_text,
                    },
                    "terminalOutcome": {
                        "status": "success",
                        "finish_reason": "stop",
                    },
                }
            )
        except Exception as exc:
            log.debug(
                "session.hook.turn_after_error",
                {
                    "session_id": context.session.id,
                    "message_id": getattr(last_message, "id", None),
                    "error": str(exc),
                },
            )

    @staticmethod
    async def _synthetic_continuation_allowed(
        context: Any,
        last_message: MessageInfo,
    ) -> bool:
        """Protect all synthetic continuations with abort and step limits."""
        if context.should_abort():
            return False

        from flocks.agent.registry import Agent
        from flocks.session.core.defaults import DEFAULT_MAX_TOOL_STEPS

        try:
            agent = await Agent.get(
                getattr(last_message, "agent", None) or context.agent_name
            )
        except Exception as exc:
            log.debug(
                "session.continuation.agent_load_error",
                {"session_id": context.session.id, "error": str(exc)},
            )
            agent = None
        max_steps = (
            agent.steps
            if agent is not None and getattr(agent, "steps", None) is not None
            else DEFAULT_MAX_TOOL_STEPS
        )
        return context.trace_step < max_steps

    async def _materialize_continuation(
        self,
        context: Any,
        last_user: MessageInfo,
        last_message: MessageInfo,
        *,
        candidate_reason: Optional[str] = None,
        content: Optional[str] = None,
        agent: Optional[str] = None,
        model: Any = None,
        provider: Optional[str] = None,
        part_metadata: Optional[dict[str, Any]] = None,
        event_metadata: Optional[dict[str, Any]] = None,
        allow_synthetic: bool = True,
    ) -> ContinuationDecision[MessageInfo]:
        """Atomically let queued input preempt one synthetic candidate."""
        from flocks.session.session import Session

        try:
            async with Session.lifecycle_lock(context.session.id):
                if context.session_store:
                    messages = await context.session_store.get_messages()
                else:
                    messages = await Message.list(context.session.id)
                queued_user = await self.detect_queued_user_message(
                    context.session.id,
                    messages,
                    last_user.id,
                    last_message,
                )
                if queued_user is not None:
                    selected = ContinuationDecision(
                        messages=(queued_user,),
                        reason="queued_message",
                    )
                elif (
                    candidate_reason is None
                    or not content
                    or not allow_synthetic
                    or context.should_abort()
                ):
                    selected = ContinuationDecision()
                else:
                    create_kwargs = {
                        "session_id": context.session.id,
                        "role": MessageRole.USER,
                        "content": content,
                        "agent": agent or context.agent_name,
                        "model": model,
                        "synthetic": True,
                        "part_metadata": part_metadata or {},
                    }
                    if provider is not None:
                        create_kwargs["provider"] = provider
                    continuation = await Message.create(**create_kwargs)
                    selected = ContinuationDecision(
                        messages=(continuation,),
                        reason=candidate_reason,
                    )
        except Exception as exc:
            log.error(
                "session.continuation.materialize_error",
                {"session_id": context.session.id, "error": str(exc)},
            )
            return ContinuationDecision()

        if selected.should_continue:
            await self._publish_continuation(
                context,
                selected,
                event_metadata=event_metadata,
            )
        return selected

    @staticmethod
    async def _publish_continuation(
        context: Any,
        decision: ContinuationDecision[MessageInfo],
        *,
        event_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Publish the one continuation selected by the lifecycle boundary."""
        reason = decision.reason
        message = decision.messages[0]
        queued = reason == "queued_message"
        turn_state = set_turn_state(
            context.session.id,
            step=context.step,
            status="continued",
            continue_reason=reason,
            queued_message_detected=queued,
        )
        message_id_key = {
            "queued_message": "queuedUserMessageID",
            "goal": "goalMessageID",
        }.get(reason, "continuationMessageID")
        await SessionEventSink.emit(
            context.callbacks,
            "turn.continued",
            {
                **turn_state.model_dump(by_alias=True),
                message_id_key: message.id,
                **(event_metadata or {}),
            },
        )


DEFAULT_CONTINUATION_POLICY = ContinuationPolicy(DEFAULT_MODEL_ROUTING_POLICY)
