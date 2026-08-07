"""Focused tests for WebUI Auto runtime model failover."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.session.runtime.continuation_policy import DEFAULT_CONTINUATION_POLICY
from flocks.session.runtime.agent_loop import AgentLoop
from flocks.session.runtime.contracts import ModelTurnSnapshot, RuntimeModel
from flocks.session.message import Message, MessageRole
from flocks.session.runtime.model_policy import (
    DEFAULT_MODEL_ROUTING_POLICY,
    AutoFailoverCooldown,
)
from flocks.session.runtime.step_engine import (
    LlmAttemptState,
    StepEngine,
    StepFailure,
    StepResult,
)
from flocks.session.session import Session, SessionInfo
from flocks.session.session_loop import (
    LoopCallbacks,
    LoopContext,
    LoopResult,
    SessionLoop,
)
from tests.session_runtime_testkit import run_logical_turns


def _session(**updates) -> SessionInfo:
    values = {
        "id": "ses_auto",
        "projectID": "project",
        "directory": "/tmp/project",
        "agent": "rex",
        "provider": "primary",
        "model": "primary-model",
        "model_pinned": False,
        "model_auto": True,
    }
    values.update(updates)
    return SessionInfo.model_construct(**values)


def _ctx(
    *,
    auto: bool = True,
    index: int = 0,
    category: str = "user",
) -> LoopContext:
    candidates = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("fallback", "fallback-model"),
    ]
    active = candidates[index]
    return LoopContext(
        session=_session(
            provider=active.provider_id,
            model=active.model_id,
            category=category,
        ),
        provider_id=active.provider_id,
        model_id=active.model_id,
        agent_name="rex",
        auto_failover=auto,
        auto_failover_allowed=auto,
        model_candidates=candidates if auto else [active],
        candidate_index=index if auto else 0,
    )


async def _build_model_candidates(
    primary: RuntimeModel,
    *,
    route_seed: str,
    preferred: RuntimeModel | None = None,
    config=None,
):
    return await DEFAULT_MODEL_ROUTING_POLICY.build_candidates(
        primary,
        route_seed=route_seed,
        preferred=preferred,
        config=config,
        validate_model=SessionLoop.validate_runtime_model,
    )


def _failure(
    *,
    assistant_id: str,
    reason: str = "server_error",
    safe: bool = True,
) -> StepResult:
    state = LlmAttemptState(observable_output_started=not safe)
    message = "provider failed"
    return StepResult(
        action="stop",
        error=message,
        failure=StepFailure(
            message=message,
            error_data={"name": "APIError", "data": {"message": message}},
            assistant_message_id=assistant_id,
            reason=reason,
            allow_fallback=safe,
            attempt_state=state,
            attempts=1,
        ),
    )


async def _process_step_with_failover(
    turn: LoopContext,
    callbacks: LoopCallbacks,
    messages,
    last_user,
) -> StepResult:
    turn.callbacks = callbacks
    return await StepEngine.from_turn(turn).run(
        ModelTurnSnapshot(
            session_id=turn.session.id,
            agent_name=turn.agent_name,
            active_model=RuntimeModel(turn.provider_id, turn.model_id),
            model_turn_index=turn.step,
            trace_step=turn.trace_step,
            messages=tuple(messages),
            last_user=last_user,
        ),
    )


@pytest.fixture(autouse=True)
def _clear_cooldowns():
    DEFAULT_MODEL_ROUTING_POLICY.cooldowns.clear()
    yield
    DEFAULT_MODEL_ROUTING_POLICY.cooldowns.clear()


@pytest.mark.parametrize(
    ("status_code", "message", "reason"),
    [
        (401, "Unauthorized", "auth"),
        (402, "Payment required", "billing"),
        (429, "Too many requests", "rate_limit"),
        (403, "Quota exceeded", "rate_limit"),
        (403, "Insufficient quota", "billing"),
        (408, "Request timeout", "timeout"),
        (404, "Route not found", "unknown_api"),
        (500, "Internal server error", "server_error"),
        (502, "Bad gateway", "server_error"),
        (529, "Provider overloaded", "overloaded"),
    ],
)
def test_failover_classifier(
    status_code: int,
    message: str,
    reason: str,
):
    decision = StepEngine.classify_failover_error(
        {
            "name": "APIError",
            "data": {"message": message, "statusCode": status_code},
        }
    )

    assert decision.eligible is True
    assert decision.reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_calls"),
    [
        (401, 1),
        (402, 1),
        (429, 6),
        (408, 1),
        (404, 1),
        (500, 6),
        (502, 6),
        (529, 1),
    ],
)
async def test_auto_runner_uses_standard_retry_policy(
    monkeypatch,
    status_code: int,
    expected_calls: int,
):
    runner = StepEngine(
        session=_session(),
        provider_id="primary",
        model_id="primary-model",
        defer_step_errors=True,
        failover_available=True,
    )
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant = SimpleNamespace(id="msg_assistant")
    failure = RuntimeError(f"Provider HTTP {status_code}")
    failure.status_code = status_code
    call_llm = AsyncMock(side_effect=failure)

    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.Agent.get",
        AsyncMock(
            return_value=SimpleNamespace(
                name="rex",
                steps=None,
                mode="primary",
                prompt="",
                tools=[],
            )
        ),
    )
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.apply_config", AsyncMock())
    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.SessionPrompt.build_system_prompts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]),
    )
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=assistant))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", call_llm)
    monkeypatch.setattr("flocks.session.runtime.step_engine.SessionRetry.sleep", AsyncMock())

    result = await runner._process_step([last_user], last_user)

    assert result.failure is not None
    assert call_llm.await_count == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_calls"),
    [
        (429, 6),
        (503, 6),
    ],
)
async def test_last_auto_candidate_uses_standard_retry_policy(
    monkeypatch,
    status_code: int,
    expected_calls: int,
):
    """The last candidate uses the same retry policy as every other mode."""
    runner = StepEngine(
        session=_session(),
        provider_id="fallback",
        model_id="fallback-model",
        defer_step_errors=True,
        failover_available=False,
    )
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant = SimpleNamespace(id="msg_assistant")
    failure = RuntimeError(f"Provider HTTP {status_code}")
    failure.status_code = status_code
    call_llm = AsyncMock(side_effect=failure)

    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.Agent.get",
        AsyncMock(
            return_value=SimpleNamespace(
                name="rex",
                steps=None,
                mode="primary",
                prompt="",
                tools=[],
            )
        ),
    )
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.apply_config", AsyncMock())
    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.SessionPrompt.build_system_prompts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]),
    )
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=assistant))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", call_llm)
    monkeypatch.setattr("flocks.session.runtime.step_engine.SessionRetry.sleep", AsyncMock())

    result = await runner._process_step([last_user], last_user)

    assert result.failure is not None
    assert call_llm.await_count == expected_calls


@pytest.mark.parametrize(
    ("exception", "status_code", "reason"),
    [
        (
            type("GoogleSdkError", (RuntimeError,), {"code": 429})("Resource exhausted"),
            429,
            "rate_limit",
        ),
        (
            type(
                "ResponseSdkError",
                (RuntimeError,),
                {
                    "response": SimpleNamespace(
                        status_code=503,
                        headers={"retry-after": "1"},
                    )
                },
            )("Service unavailable"),
            503,
            "overloaded",
        ),
    ],
)
def test_exception_status_is_normalized_from_sdk_shapes(
    exception: Exception,
    status_code: int,
    reason: str,
):
    runner = StepEngine(
        session=_session(),
        provider_id="primary",
        model_id="primary-model",
    )

    error = runner._exception_to_error_dict(exception)

    assert error["data"]["statusCode"] == status_code
    assert StepEngine.classify_failover_error(error).reason == reason


def test_exception_status_is_normalized_from_cause_chain():
    inner = type("GoogleSdkError", (RuntimeError,), {"code": 401})("Unauthenticated")
    outer = RuntimeError("Provider wrapper failed")
    outer.__cause__ = inner
    runner = StepEngine(
        session=_session(),
        provider_id="primary",
        model_id="primary-model",
    )

    error = runner._exception_to_error_dict(outer)

    assert error["data"]["statusCode"] == 401
    assert StepEngine.classify_failover_error(error).reason == "auth"


def test_local_validation_error_never_fails_over():
    decision = StepEngine.classify_failover_error(
        {
            "name": "ValidationError",
            "data": {"message": "Local prompt schema validation failed"},
        }
    )

    assert decision.eligible is False
    assert decision.reason == "local_error"


def test_model_not_found_without_status_fails_over():
    decision = StepEngine.classify_failover_error(
        {
            "name": "ValueError",
            "data": {"message": "Model acme-v2 not found for provider custom"},
        }
    )

    assert decision.eligible is True
    assert decision.reason == "model_not_found"


def test_content_filter_error_fails_over_immediately():
    decision = StepEngine.classify_failover_error(
        {
            "name": "BadRequestError",
            "data": {"message": "Response blocked by content_filter"},
        }
    )

    assert decision.eligible is True
    assert decision.reason == "content_policy"


def test_candidate_switch_keeps_tool_loop_guard_only():
    ctx = _ctx()
    tool_loop_guard = {
        "last_user_id": "msg_user",
        "signature": "same-tool-call",
        "count": 2,
    }
    ctx.step_static_cache.update(
        {
            "tool_loop_guard": tool_loop_guard,
            "tool_schema_cache": {"primary": "schema"},
            "chat_context_cache": {"primary": "context"},
            "system_prompt": "primary prompt",
        }
    )

    DEFAULT_MODEL_ROUTING_POLICY.select_candidate(ctx, 1)

    assert ctx.step_static_cache == {"tool_loop_guard": tool_loop_guard}
    assert ctx.step_static_cache["tool_loop_guard"] is tool_loop_guard


@pytest.mark.asyncio
async def test_reasoning_only_empty_response_is_not_replayed(monkeypatch):
    runner = StepEngine(
        session=_session(),
        provider_id="primary",
        model_id="primary-model",
        defer_step_errors=True,
        failover_available=True,
    )
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    provider = MagicMock()
    provider.is_configured.return_value = True
    assistant = SimpleNamespace(id="msg_assistant")
    call_count = 0

    async def call_llm(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        runner._attempt_state.observable_output_started = True
        return StepResult(action="stop", content="")

    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.Agent.get",
        AsyncMock(
            return_value=SimpleNamespace(
                name="rex",
                steps=None,
                mode="primary",
                prompt="",
                tools=[],
            )
        ),
    )
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.apply_config", AsyncMock())
    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.SessionPrompt.build_system_prompts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]),
    )
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=assistant))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr(runner, "_call_llm", call_llm)
    sleep = AsyncMock()
    monkeypatch.setattr("flocks.session.runtime.step_engine.SessionRetry.sleep", sleep)

    result = await runner._process_step([last_user], last_user)

    assert call_count == 1
    assert result.failure is not None
    assert result.failure.allow_fallback is False
    sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_kind", ["text", "reasoning", "tool"])
async def test_real_stream_activity_prevents_retry_and_fallback(
    monkeypatch,
    chunk_kind: str,
):
    """Exercise the real stream loop, then fail after an observable fragment."""

    class FakeStreamProcessor:
        def __init__(self, *args, tool_start_callback=None, **kwargs):
            self.tool_start_callback = tool_start_callback
            self.tool_calls = {}
            self._text = []
            self._reasoning = []

        async def process_event(self, event):
            event_name = type(event).__name__
            if event_name == "TextDeltaEvent":
                self._text.append(event.text)
            elif event_name == "ReasoningDeltaEvent":
                self._reasoning.append(event.text)
            elif event_name == "ToolCallEvent":
                if self.tool_start_callback:
                    await self.tool_start_callback(event.tool_name, event.input)
                self.tool_calls[event.tool_call_id] = SimpleNamespace(
                    id=event.tool_call_id,
                    name=event.tool_name,
                    input=event.input,
                )

        def get_text_content(self):
            return "".join(self._text)

        def get_reasoning_content(self):
            return "".join(self._reasoning)

    if chunk_kind == "text":
        chunk = SimpleNamespace(
            delta="visible text",
            reasoning=None,
            event_type=None,
            metadata={},
            tool_calls=None,
            finish_reason=None,
            usage=None,
        )
    elif chunk_kind == "reasoning":
        chunk = SimpleNamespace(
            delta="visible reasoning",
            reasoning=None,
            event_type="reasoning",
            metadata={},
            tool_calls=None,
            finish_reason=None,
            usage=None,
        )
    else:
        chunk = SimpleNamespace(
            delta="",
            reasoning=None,
            event_type=None,
            metadata={},
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "example_tool", "arguments": "{}"},
                }
            ],
            finish_reason=None,
            usage=None,
        )

    class FailingStreamProvider:
        def __init__(self):
            self.calls = 0

        def is_configured(self):
            return True

        def chat_stream(self, **_kwargs):
            self.calls += 1

            async def stream():
                yield chunk
                failure = RuntimeError("Provider HTTP 500")
                failure.status_code = 500
                raise failure

            return stream()

    provider = FailingStreamProvider()
    runner = StepEngine(
        session=_session(),
        provider_id="primary",
        model_id="primary-model",
        defer_step_errors=True,
        failover_available=True,
    )
    last_user = SimpleNamespace(id="msg_user", agent="rex", role="user")
    assistant = SimpleNamespace(id="msg_assistant")

    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.Agent.get",
        AsyncMock(
            return_value=SimpleNamespace(
                name="rex",
                steps=None,
                mode="primary",
                prompt="",
                tools=[],
            )
        ),
    )
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.get", lambda _provider_id: provider)
    monkeypatch.setattr("flocks.session.runtime.step_engine.Provider.apply_config", AsyncMock())
    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.SessionPrompt.build_system_prompts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(runner, "_build_callable_tool_schema", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        runner,
        "_to_chat_messages",
        AsyncMock(return_value=[SimpleNamespace(role="user", content="hello")]),
    )
    monkeypatch.setattr(runner, "_should_use_text_tool_call_mode", lambda: False)
    monkeypatch.setattr(Message, "get_text_content", AsyncMock(return_value="hello"))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "create", AsyncMock(return_value=assistant))
    monkeypatch.setattr(Message, "update", AsyncMock())
    monkeypatch.setattr("flocks.session.runtime.step_engine.StreamProcessor", FakeStreamProcessor)
    monkeypatch.setattr(
        "flocks.session.runtime.step_engine.HookPipeline.has_stage_handlers",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("flocks.session.runtime.step_engine.langfuse_is_active", lambda: False)
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda _provider_id, _model_id: {},
    )
    sleep = AsyncMock()
    monkeypatch.setattr("flocks.session.runtime.step_engine.SessionRetry.sleep", sleep)

    result = await runner._process_step([last_user], last_user)

    assert provider.calls == 1
    assert result.failure is not None
    assert result.failure.allow_fallback is False
    assert result.failure.attempt_state.received_chunk is True
    assert result.failure.attempt_state.observable_output_started is True
    assert result.failure.attempt_state.tool_execution_started is (chunk_kind == "tool")
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_failure_switches_candidate_and_removes_blank_message(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")
    events = []

    async def process_step(runner, _messages, _last_user):
        if runner.provider_id == "primary":
            return _failure(assistant_id="msg_failed")
        return StepResult(action="stop", content="recovered")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(Message, "delete", delete)

    async def publish(event, payload):
        events.append((event, payload))

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(event_publish_callback=publish),
        [last_user],
        last_user,
    )

    assert result.content == "recovered"
    assert (ctx.provider_id, ctx.model_id) == ("fallback", "fallback-model")
    delete.assert_awaited_once_with("ses_auto", "msg_failed")
    assert any(event == "message.removed" for event, _ in events)
    assert any(event == "session.model.fallback" for event, _ in events)


@pytest.mark.asyncio
async def test_queued_user_is_detected_before_replacement_assistant():
    current_user = SimpleNamespace(id="msg_001", role=MessageRole.USER)
    queued_user = SimpleNamespace(id="msg_002", role=MessageRole.USER)
    replacement_assistant = SimpleNamespace(
        id="msg_003",
        role=MessageRole.ASSISTANT,
    )

    detected = await DEFAULT_CONTINUATION_POLICY.detect_queued_user_message(
        "ses_auto",
        [current_user, queued_user, replacement_assistant],
        current_user.id,
        replacement_assistant,
    )

    assert detected is queued_user


@pytest.mark.asyncio
async def test_preflight_chain_exhaustion_persists_final_error(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(
        id="msg_user",
        agent="rex",
        role=MessageRole.USER,
    )

    async def preflight_failure(_runner, _messages, _last_user):
        message = "Provider not configured"
        return StepResult(
            action="stop",
            error=message,
            failure=StepFailure(
                message=message,
                error_data={
                    "name": "ProviderUnavailableError",
                    "data": {"message": message},
                },
                assistant_message_id=None,
                reason="provider_unavailable",
                allow_fallback=True,
                attempt_state=LlmAttemptState(),
                attempts=0,
            ),
        )

    final_assistant = SimpleNamespace(id="msg_final_error")
    create = AsyncMock(return_value=final_assistant)
    monkeypatch.setattr(StepEngine, "_process_step", preflight_failure)
    monkeypatch.setattr(Message, "create", create)

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert result.error == "Provider not configured"
    assert result.failure is not None
    assert result.failure.assistant_message_id == final_assistant.id
    create.assert_awaited_once_with(
        session_id="ses_auto",
        role=MessageRole.ASSISTANT,
        content="",
        agent="rex",
        model_id="fallback-model",
        provider_id="fallback",
        parent_id=last_user.id,
        error={
            "name": "ProviderUnavailableError",
            "data": {"message": "Provider not configured"},
        },
        finish="error",
    )


@pytest.mark.asyncio
async def test_failed_blank_message_deletion_stops_switch(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")
    monkeypatch.setattr(
        StepEngine,
        "_process_step",
        AsyncMock(return_value=_failure(assistant_id="msg_failed")),
    )
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=False))
    update = AsyncMock()
    monkeypatch.setattr(Message, "update", update)

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert result.error == "provider failed"
    assert ctx.provider_id == "primary"
    update.assert_awaited_once_with(
        "ses_auto",
        "msg_failed",
        error={"name": "APIError", "data": {"message": "provider failed"}},
        finish="error",
    )


@pytest.mark.asyncio
async def test_fallbacks_are_attempted_in_candidate_order(monkeypatch):
    ctx = _ctx()
    ctx.model_candidates = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("fallback-1", "model-1"),
        RuntimeModel("fallback-2", "model-2"),
    ]
    last_user = SimpleNamespace(id="msg_user", agent="rex")
    attempts = []

    async def process_step(runner, _messages, _last_user):
        attempts.append((runner.provider_id, runner.model_id))
        if runner.provider_id != "fallback-2":
            return _failure(assistant_id=f"msg_{runner.provider_id}")
        return StepResult(action="stop", content="recovered")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(Message, "delete", delete)

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert result.content == "recovered"
    assert attempts == [
        ("primary", "primary-model"),
        ("fallback-1", "model-1"),
        ("fallback-2", "model-2"),
    ]
    assert delete.await_count == 2


@pytest.mark.asyncio
async def test_chain_exhaustion_finalizes_only_last_candidate(monkeypatch):
    ctx = _ctx()
    ctx.model_candidates = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("fallback-1", "model-1"),
        RuntimeModel("fallback-2", "model-2"),
    ]
    last_user = SimpleNamespace(id="msg_user", agent="rex")

    async def process_step(runner, _messages, _last_user):
        return _failure(assistant_id=f"msg_{runner.provider_id}")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    delete = AsyncMock(return_value=True)
    update = AsyncMock()
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(Message, "update", update)

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert result.error == "provider failed"
    assert delete.await_count == 2
    update.assert_awaited_once()
    assert update.await_args.args[1] == "msg_fallback-2"
    cooldown = DEFAULT_MODEL_ROUTING_POLICY.cooldowns[ctx.session.id]
    assert cooldown.model == RuntimeModel("fallback-2", "model-2")
    assert cooldown.reason == "chain_exhausted"


@pytest.mark.asyncio
async def test_full_loop_reports_chain_exhaustion_once(monkeypatch):
    ctx = _ctx()
    ctx.session.memory_enabled = False
    user = SimpleNamespace(
        id="msg_user",
        role=MessageRole.USER,
        agent="rex",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    # This test exercises exhaustion of an already fixed per-turn chain.
    ctx.turn_user_id = user.id
    final_assistant = SimpleNamespace(
        id="msg_fallback",
        role=MessageRole.ASSISTANT,
        parentID=user.id,
        finish="error",
    )
    ctx.session_store = SimpleNamespace(
        get_messages=AsyncMock(
            side_effect=[
                [user],
                [user, final_assistant],
            ]
        )
    )
    attempts = []

    async def process_step(runner, _messages, _last_user):
        attempts.append((runner.provider_id, runner.model_id))
        return _failure(assistant_id=f"msg_{runner.provider_id}")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=True))
    update = AsyncMock()
    monkeypatch.setattr(Message, "update", update)
    on_error = AsyncMock()

    result = await run_logical_turns(
        ctx,
        LoopCallbacks(
            on_error=on_error,
            event_publish_callback=AsyncMock(),
        ),
    )

    assert result.action == "error"
    assert result.error == "provider failed"
    assert result.last_message is final_assistant
    assert (result.provider_id, result.model_id) == (
        "fallback",
        "fallback-model",
    )
    assert attempts == [
        ("primary", "primary-model"),
        ("fallback", "fallback-model"),
    ]
    on_error.assert_awaited_once_with("provider failed")
    update.assert_awaited_once()
    assert update.await_args.args[1] == "msg_fallback"


@pytest.mark.asyncio
async def test_observable_failure_is_finalized_without_replay(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")
    monkeypatch.setattr(
        StepEngine,
        "_process_step",
        AsyncMock(return_value=_failure(assistant_id="msg_partial", safe=False)),
    )
    delete = AsyncMock(return_value=True)
    update = AsyncMock()
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(Message, "update", update)

    result = await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert result.error == "provider failed"
    assert ctx.provider_id == "primary"
    delete.assert_not_awaited()
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_switch_sets_primary_cooldown(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")

    async def process_step(runner, _messages, _last_user):
        if runner.provider_id == "primary":
            return _failure(assistant_id="msg_rate", reason="rate_limit")
        return StepResult(action="stop", content="recovered")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=True))

    await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    cooldown = DEFAULT_MODEL_ROUTING_POLICY.cooldowns[ctx.session.id]
    assert cooldown.model == RuntimeModel("fallback", "fallback-model")
    assert cooldown.reason == "rate_limit"
    assert (
        DEFAULT_MODEL_ROUTING_POLICY.cooldown_candidate_index(
            ctx.session.id,
            ctx.model_candidates,
        )
        == 1
    )


@pytest.mark.asyncio
async def test_configured_switch_does_not_set_cross_turn_cooldown(monkeypatch):
    ctx = _ctx()
    ctx.model_candidate_policy = "configured"
    last_user = SimpleNamespace(id="msg_user", agent="rex")

    async def process_step(runner, _messages, _last_user):
        if runner.provider_id == "primary":
            return _failure(assistant_id="msg_rate", reason="rate_limit")
        return StepResult(action="stop", content="recovered")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=True))

    await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    assert (ctx.provider_id, ctx.model_id) == ("fallback", "fallback-model")
    assert ctx.session.id not in DEFAULT_MODEL_ROUTING_POLICY.cooldowns


@pytest.mark.asyncio
async def test_403_quota_failure_sets_primary_cooldown(monkeypatch):
    decision = StepEngine.classify_failover_error(
        {
            "name": "APIError",
            "data": {"message": "Quota exceeded", "statusCode": 403},
        }
    )
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")

    async def process_step(runner, _messages, _last_user):
        if runner.provider_id == "primary":
            return _failure(
                assistant_id="msg_quota",
                reason=decision.reason,
            )
        return StepResult(action="stop", content="recovered")

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=True))

    await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    cooldown = DEFAULT_MODEL_ROUTING_POLICY.cooldowns[ctx.session.id]
    assert cooldown.reason == "rate_limit"
    assert cooldown.model == RuntimeModel("fallback", "fallback-model")
    assert cooldown.expires_at > time.monotonic() + 50


@pytest.mark.asyncio
async def test_chain_exhaustion_does_not_shorten_rate_limit_cooldown(monkeypatch):
    ctx = _ctx()
    last_user = SimpleNamespace(id="msg_user", agent="rex")

    async def process_step(runner, _messages, _last_user):
        reason = "rate_limit" if runner.provider_id == "primary" else "server_error"
        return _failure(
            assistant_id=f"msg_{runner.provider_id}",
            reason=reason,
        )

    monkeypatch.setattr(StepEngine, "_process_step", process_step)
    monkeypatch.setattr(Message, "delete", AsyncMock(return_value=True))
    monkeypatch.setattr(Message, "update", AsyncMock())

    await _process_step_with_failover(
        ctx,
        LoopCallbacks(),
        [last_user],
        last_user,
    )

    cooldown = DEFAULT_MODEL_ROUTING_POLICY.cooldowns[ctx.session.id]
    assert cooldown.reason == "rate_limit"
    assert cooldown.model == RuntimeModel("fallback", "fallback-model")
    # A 5s anti-replay window must not replace the primary's 60s cooldown.
    assert cooldown.expires_at > time.monotonic() + 50


@pytest.mark.asyncio
async def test_candidate_builder_discovers_one_model_per_tier_with_stable_seed(
    monkeypatch,
):
    config = SimpleNamespace()
    model_manager = MagicMock()
    model_manager.list_models.return_value = [
        SimpleNamespace(provider_id="primary", id="primary-model"),
        SimpleNamespace(provider_id="primary", id="same-a"),
        SimpleNamespace(provider_id="primary", id="same-b"),
        SimpleNamespace(provider_id="other-a", id="other-a-model"),
        SimpleNamespace(provider_id="other-b", id="other-b-model"),
        SimpleNamespace(provider_id="missing", id="missing-model"),
    ]
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    monkeypatch.setattr(
        "flocks.provider.provider.Provider.apply_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.provider.model_manager.get_model_manager",
        lambda: model_manager,
    )

    async def validate(provider_id, _model_id, **_kwargs):
        available = provider_id != "missing"
        return available, "available" if available else "provider_not_configured"

    monkeypatch.setattr(SessionLoop, "validate_runtime_model", validate)
    primary = RuntimeModel("primary", "primary-model")

    first = await _build_model_candidates(
        primary,
        route_seed="ses_auto:msg_1",
    )
    repeated = await _build_model_candidates(
        primary,
        route_seed="ses_auto:msg_1",
    )

    assert first == repeated
    assert first[0] == primary
    assert len(first) == 3
    assert first[1].provider_id == "primary"
    assert first[1].model_id in {"same-a", "same-b"}
    assert first[2].provider_id in {"other-a", "other-b"}
    assert all(candidate.provider_id != "missing" for candidate in first)

    selections = {
        tuple(
            await _build_model_candidates(
                primary,
                route_seed=f"ses_auto:msg_{index}",
            )
        )
        for index in range(12)
    }
    assert len(selections) > 1


@pytest.mark.asyncio
async def test_candidate_builder_keeps_active_cooldown_model_in_its_tier(
    monkeypatch,
):
    config = SimpleNamespace()
    model_manager = MagicMock()
    model_manager.list_models.return_value = [
        SimpleNamespace(provider_id="primary", id="primary-model"),
        SimpleNamespace(provider_id="primary", id="same-a"),
        SimpleNamespace(provider_id="primary", id="same-b"),
        SimpleNamespace(provider_id="other", id="other-a"),
        SimpleNamespace(provider_id="other", id="other-b"),
    ]
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    monkeypatch.setattr(
        "flocks.provider.provider.Provider.apply_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.provider.model_manager.get_model_manager",
        lambda: model_manager,
    )
    monkeypatch.setattr(
        SessionLoop,
        "validate_runtime_model",
        AsyncMock(return_value=(True, "available")),
    )
    primary = RuntimeModel("primary", "primary-model")
    cooldown_model = RuntimeModel("other", "other-b")

    candidates = await _build_model_candidates(
        primary,
        route_seed="ses_auto:new-turn",
        preferred=cooldown_model,
    )

    assert candidates[0] == primary
    assert candidates[1].provider_id == "primary"
    assert candidates[2] == cooldown_model


@pytest.mark.asyncio
async def test_auto_configuration_only_requires_available_primary(monkeypatch):
    monkeypatch.setattr(
        "flocks.config.config.Config.resolve_default_llm",
        AsyncMock(
            return_value={
                "provider_id": "primary",
                "model_id": "primary-model",
            }
        ),
    )
    monkeypatch.setattr(
        SessionLoop,
        "validate_runtime_model",
        AsyncMock(return_value=(True, "available")),
    )
    assert await SessionLoop.validate_auto_configuration() == (True, "available")


@pytest.mark.asyncio
async def test_candidate_builder_allows_primary_only_chain(monkeypatch):
    model_manager = MagicMock()
    model_manager.list_models.return_value = [
        SimpleNamespace(provider_id="primary", id="primary-model"),
    ]
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "flocks.provider.provider.Provider.apply_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.provider.model_manager.get_model_manager",
        lambda: model_manager,
    )

    primary = RuntimeModel("primary", "primary-model")

    assert await _build_model_candidates(
        primary,
        route_seed="ses_auto:msg_primary_only",
    ) == [primary]


@pytest.mark.asyncio
async def test_candidate_builder_uses_configured_order_without_discovery(
    monkeypatch,
):
    config = SimpleNamespace(
        fallback_providers=[
            SimpleNamespace(provider_id="other", model_id="model-b"),
            SimpleNamespace(provider_id="primary", model_id="model-a"),
            SimpleNamespace(provider_id="missing", model_id="missing-model"),
        ]
    )
    model_manager = MagicMock()
    monkeypatch.setattr(
        "flocks.provider.provider.Provider.apply_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.provider.model_manager.get_model_manager",
        lambda: model_manager,
    )

    async def validate(provider_id, _model_id, **_kwargs):
        available = provider_id != "missing"
        return available, "available" if available else "provider_not_configured"

    monkeypatch.setattr(SessionLoop, "validate_runtime_model", validate)
    primary = RuntimeModel("primary", "primary-model")

    candidates = await _build_model_candidates(
        primary,
        route_seed="unused-for-configured",
        preferred=RuntimeModel("other", "model-b"),
        config=config,
    )

    assert candidates == [
        primary,
        RuntimeModel("other", "model-b"),
        RuntimeModel("primary", "model-a"),
    ]
    model_manager.list_models.assert_not_called()


@pytest.mark.asyncio
async def test_configured_chain_with_no_available_fallbacks_keeps_primary_only(
    monkeypatch,
):
    config = SimpleNamespace(
        fallback_providers=[
            SimpleNamespace(provider_id="missing", model_id="missing-model"),
        ]
    )
    monkeypatch.setattr(
        "flocks.provider.provider.Provider.apply_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        SessionLoop,
        "validate_runtime_model",
        AsyncMock(return_value=(False, "provider_not_configured")),
    )
    primary = RuntimeModel("primary", "primary-model")

    assert await _build_model_candidates(
        primary,
        route_seed="unused-for-configured",
        config=config,
    ) == [primary]


def test_cooldown_is_cleared_when_primary_changes():
    candidates = [
        RuntimeModel("new-primary", "new-model"),
        RuntimeModel("fallback", "fallback-model"),
    ]
    DEFAULT_MODEL_ROUTING_POLICY.cooldowns["ses_auto"] = AutoFailoverCooldown(
        model=RuntimeModel("fallback", "fallback-model"),
        primary=RuntimeModel("old-primary", "old-model"),
        expires_at=float("inf"),
        reason="rate_limit",
    )

    assert DEFAULT_MODEL_ROUTING_POLICY.cooldown_candidate_index("ses_auto", candidates) == 0
    assert "ses_auto" not in DEFAULT_MODEL_ROUTING_POLICY.cooldowns


@pytest.mark.asyncio
async def test_synthetic_subtask_continuation_keeps_fallback(monkeypatch):
    ctx = _ctx(index=1)
    ctx.model_candidate_policy = "configured"
    ctx.turn_user_id = "msg_real"
    synthetic_user = SimpleNamespace(
        id="msg_subtask_continue",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    monkeypatch.setattr(
        Message,
        "parts",
        AsyncMock(return_value=[SimpleNamespace(synthetic=True)]),
    )

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, synthetic_user)

    assert ctx.auto_failover is True
    assert ctx.turn_user_id == "msg_real"
    assert (ctx.provider_id, ctx.model_id) == ("fallback", "fallback-model")


@pytest.mark.asyncio
async def test_first_real_turn_builds_stable_chain_from_user_id(monkeypatch):
    ctx = _ctx()
    ctx.turn_user_id = None
    ctx.model_candidates = [RuntimeModel("primary", "primary-model")]
    first_user = SimpleNamespace(
        id="msg_first",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    rebuilt = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("primary", "same-provider-model"),
        RuntimeModel("other", "other-provider-model"),
    ]
    config = SimpleNamespace(fallback_providers=None)
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    build = AsyncMock(return_value=rebuilt)
    monkeypatch.setattr(DEFAULT_MODEL_ROUTING_POLICY, "build_candidates", build)

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, first_user)

    assert ctx.turn_user_id == "msg_first"
    assert ctx.model_candidates == rebuilt
    build.assert_awaited_once_with(
        RuntimeModel("primary", "primary-model"),
        route_seed="ses_auto:msg_first",
        preferred=None,
        config=config,
    )


@pytest.mark.asyncio
async def test_configured_first_real_turn_ignores_cooldown_and_starts_primary(
    monkeypatch,
):
    ctx = _ctx(index=1)
    ctx.turn_user_id = None
    first_user = SimpleNamespace(
        id="msg_first",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    config = SimpleNamespace(
        fallback_providers=[
            SimpleNamespace(provider_id="fallback", model_id="fallback-model"),
        ]
    )
    rebuilt = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("fallback", "fallback-model"),
    ]
    DEFAULT_MODEL_ROUTING_POLICY.cooldowns[ctx.session.id] = AutoFailoverCooldown(
        model=rebuilt[1],
        primary=rebuilt[0],
        expires_at=float("inf"),
        reason="rate_limit",
    )
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    monkeypatch.setattr(
        DEFAULT_MODEL_ROUTING_POLICY,
        "build_candidates",
        AsyncMock(return_value=rebuilt),
    )

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, first_user)

    assert ctx.model_candidate_policy == "configured"
    assert ctx.candidate_index == 0
    assert (ctx.provider_id, ctx.model_id) == ("primary", "primary-model")
    assert ctx.session.id not in DEFAULT_MODEL_ROUTING_POLICY.cooldowns


@pytest.mark.asyncio
async def test_queued_explicit_model_disables_auto(monkeypatch):
    ctx = _ctx(index=1)
    ctx.turn_user_id = "msg_real"
    queued_user = SimpleNamespace(
        id="msg_explicit",
        model={"providerID": "explicit", "modelID": "explicit-model"},
    )
    persisted = _session(
        provider="explicit",
        model="explicit-model",
        model_pinned=True,
        model_auto=False,
    )
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=persisted),
    )

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, queued_user)

    assert ctx.auto_failover is False
    assert ctx.model_candidates == [RuntimeModel("explicit", "explicit-model")]
    assert (ctx.provider_id, ctx.model_id) == ("explicit", "explicit-model")


@pytest.mark.asyncio
async def test_non_webui_loop_cannot_activate_persisted_auto(monkeypatch):
    ctx = _ctx(auto=False)
    ctx.turn_user_id = "msg_real"
    ctx.auto_failover_allowed = False
    queued_user = SimpleNamespace(
        id="msg_non_webui",
        model={"providerID": "direct", "modelID": "direct-model"},
    )
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=_session(model_auto=True)),
    )

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, queued_user)

    assert ctx.auto_failover is False
    assert ctx.auto_failover_allowed is False
    assert ctx.model_candidates == [RuntimeModel("direct", "direct-model")]


@pytest.mark.asyncio
async def test_queued_webui_turn_rebuilds_auto_chain(monkeypatch):
    ctx = _ctx(auto=False)
    ctx.turn_user_id = "msg_real"
    ctx.auto_failover_allowed = True
    queued_user = SimpleNamespace(
        id="msg_auto",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    rebuilt = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("new-fallback", "new-fallback-model"),
    ]
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=_session(model_auto=True)),
    )
    monkeypatch.setattr(
        "flocks.config.config.Config.resolve_default_llm",
        AsyncMock(
            return_value={
                "provider_id": "primary",
                "model_id": "primary-model",
            }
        ),
    )
    config = SimpleNamespace(fallback_providers=None)
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    build = AsyncMock(return_value=rebuilt)
    monkeypatch.setattr(DEFAULT_MODEL_ROUTING_POLICY, "build_candidates", build)

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, queued_user)

    assert ctx.auto_failover is True
    assert ctx.model_candidates == rebuilt
    build.assert_awaited_once_with(
        RuntimeModel("primary", "primary-model"),
        route_seed="ses_auto:msg_auto",
        preferred=None,
        config=config,
    )


@pytest.mark.asyncio
async def test_queued_configured_turn_restarts_from_primary(monkeypatch):
    ctx = _ctx(index=1)
    ctx.turn_user_id = "msg_previous"
    ctx.auto_failover_allowed = True
    ctx.model_candidate_policy = "configured"
    queued_user = SimpleNamespace(
        id="msg_next",
        model={"providerID": "primary", "modelID": "primary-model"},
    )
    rebuilt = [
        RuntimeModel("primary", "primary-model"),
        RuntimeModel("fallback", "fallback-model"),
    ]
    config = SimpleNamespace(
        fallback_providers=[
            SimpleNamespace(provider_id="fallback", model_id="fallback-model"),
        ]
    )
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=_session(model_auto=True)),
    )
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=config),
    )
    monkeypatch.setattr(
        "flocks.config.config.Config.resolve_default_llm",
        AsyncMock(
            return_value={
                "provider_id": "primary",
                "model_id": "primary-model",
            }
        ),
    )
    monkeypatch.setattr(
        DEFAULT_MODEL_ROUTING_POLICY,
        "build_candidates",
        AsyncMock(return_value=rebuilt),
    )

    await DEFAULT_MODEL_ROUTING_POLICY.prepare_turn(ctx, queued_user)

    assert ctx.model_candidate_policy == "configured"
    assert ctx.candidate_index == 0
    assert (ctx.provider_id, ctx.model_id) == ("primary", "primary-model")


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["user", "entity-config", "workflow"])
async def test_queued_webui_auto_authorizes_active_loop(category):
    ctx = _ctx(auto=False, category=category)
    SessionLoop._active_turns[ctx.session.id] = ctx
    try:
        result = await SessionLoop.run(ctx.session.id, auto_failover=True)
    finally:
        SessionLoop._active_turns.pop(ctx.session.id, None)

    assert result.action == "queued"
    assert ctx.auto_failover_allowed is True


@pytest.mark.asyncio
async def test_unsupported_session_loop_ignores_auto_authorization(
    monkeypatch,
):
    task_session = _session(category="task")
    captured_ctx = None

    async def run_turn(_loop, ctx, _engine):
        from flocks.session.runtime.contracts import (
            AgentRunOutcome,
            AgentRunState,
            AgentRunStatus,
        )

        nonlocal captured_ctx
        captured_ctx = ctx
        state = AgentRunState(
            session_id=ctx.session.id,
            agent_name=ctx.agent_name,
            active_model=RuntimeModel(ctx.provider_id, ctx.model_id),
        )
        return AgentRunOutcome(
            status=AgentRunStatus.ABORTED,
            state=state,
        )

    build_candidates = AsyncMock()
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=task_session),
    )
    monkeypatch.setattr(
        DEFAULT_MODEL_ROUTING_POLICY,
        "build_candidates",
        build_candidates,
    )
    monkeypatch.setattr(AgentLoop, "run", run_turn)
    monkeypatch.setattr(Message, "list", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "flocks.session.orphan_tools.abort_orphan_running_parts",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.session.session.Session.touch",
        AsyncMock(),
    )
    monkeypatch.setattr("flocks.bus.bus.Bus.publish", AsyncMock())

    await SessionLoop.run(
        task_session.id,
        provider_id="primary",
        model_id="primary-model",
        auto_failover=True,
    )

    assert captured_ctx is not None
    assert captured_ctx.auto_failover is False
    assert captured_ctx.auto_failover_allowed is False
    assert captured_ctx.model_candidates == [RuntimeModel("primary", "primary-model")]
    build_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_unsupported_loop_rejects_auto_authorization():
    ctx = _ctx(auto=False, category="task")
    SessionLoop._active_turns[ctx.session.id] = ctx
    try:
        result = await SessionLoop.run(ctx.session.id, auto_failover=True)
    finally:
        SessionLoop._active_turns.pop(ctx.session.id, None)

    assert result.action == "queued"
    assert ctx.auto_failover_allowed is False


@pytest.mark.asyncio
async def test_session_delete_clears_auto_failover_cooldown(monkeypatch):
    session = _session()
    DEFAULT_MODEL_ROUTING_POLICY.cooldowns[session.id] = AutoFailoverCooldown(
        model=RuntimeModel("fallback", "fallback-model"),
        primary=RuntimeModel("primary", "primary-model"),
        expires_at=float("inf"),
        reason="rate_limit",
    )
    monkeypatch.setattr(Session, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(Session, "children", AsyncMock(return_value=[]))
    monkeypatch.setattr(Session, "update", AsyncMock(return_value=session))
    monkeypatch.setattr(Message, "clear", AsyncMock(return_value=0))
    monkeypatch.setattr(
        "flocks.session.callable_state.clear_session_callable_tools",
        AsyncMock(),
    )
    monkeypatch.setattr("flocks.bus.bus.Bus.publish", AsyncMock())

    assert await Session.delete("project", session.id) is True
    assert session.id not in DEFAULT_MODEL_ROUTING_POLICY.cooldowns
