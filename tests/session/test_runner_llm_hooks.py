"""Tests for LLM lifecycle hooks in SessionRunner and HookPipeline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.provider.provider import ChatMessage
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.session.runner import SessionRunner
from flocks.session.session import SessionInfo
from flocks.tool.registry import ToolResult


def _make_session(session_id: str = "ses_runner_llm_hooks") -> SessionInfo:
    return SessionInfo.model_construct(
        id=session_id,
        slug="test",
        project_id="proj_runner",
        directory="/tmp",
        title="Runner Hook Test",
    )


def _make_runner(session_id: str = "ses_runner_llm_hooks") -> SessionRunner:
    return SessionRunner(
        session=_make_session(session_id),
        provider_id="anthropic",
        model_id="claude-sonnet",
    )


class _FakeProcessor:
    def __init__(self, **_: object):
        self._text_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self.finish_reason = "stop"
        self.tool_calls = {}
        self._langfuse_generation = None

    async def process_event(self, event) -> None:
        event_name = type(event).__name__
        if event_name == "TextDeltaEvent":
            self._text_parts.append(event.text)
        elif event_name == "ReasoningDeltaEvent":
            self._reasoning_parts.append(event.text)
        elif event_name == "FinishEvent":
            self.finish_reason = event.finish_reason

    def get_text_content(self) -> str:
        return "".join(self._text_parts)

    def get_reasoning_content(self) -> str:
        return "".join(self._reasoning_parts)

    def get_finish_reason(self):
        return self.finish_reason

    async def drain_parallel_tool_calls(self, *, execute_deferred: bool = True) -> None:
        return None


class _FakeToolAccumulator:
    def __init__(self, processor):
        self.processor = processor

    async def feed_chunk(self, tool_call) -> None:
        return None

    async def flush_remaining(
        self,
        finish_reason,
        *,
        output_limit=None,
        raised_output_limit=None,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_hook_pipeline_runs_llm_stages():
    seen: list[tuple[str, str]] = []

    class _RecordingHook(HookBase):
        async def llm_before(self, ctx) -> None:
            seen.append((ctx.stage, ctx.input["request_id"]))

        async def llm_after(self, ctx) -> None:
            seen.append((ctx.stage, ctx.output["status"]))

    HookPipeline.register("test-llm-stage-hook", _RecordingHook())
    try:
        await HookPipeline.run_llm_before({"request_id": "req-1"})
        await HookPipeline.run_llm_after({"request_id": "req-1"}, {"status": "ok"})
    finally:
        HookPipeline.unregister("test-llm-stage-hook")

    assert seen == [
        ("llm.call.before", "req-1"),
        ("llm.call.after", "ok"),
    ]


@pytest.mark.asyncio
async def test_hook_pipeline_timeout_isolated_by_default():
    seen: list[str] = []

    class _SlowHook(HookBase):
        async def llm_before(self, ctx) -> None:
            await asyncio.sleep(0.05)
            seen.append("slow")

    class _FastHook(HookBase):
        async def llm_before(self, ctx) -> None:
            seen.append("fast")

    HookPipeline.register("test-slow-hook", _SlowHook(), timeout_seconds=0.01)
    HookPipeline.register("test-fast-hook", _FastHook())
    try:
        await HookPipeline.run_llm_before({"request_id": "req-timeout"})
    finally:
        HookPipeline.unregister("test-slow-hook")
        HookPipeline.unregister("test-fast-hook")

    assert seen == ["fast"]


@pytest.mark.asyncio
async def test_hook_pipeline_timeout_can_propagate():
    class _SlowHook(HookBase):
        async def llm_before(self, ctx) -> None:
            await asyncio.sleep(0.05)

    HookPipeline.register(
        "test-critical-slow-hook",
        _SlowHook(),
        timeout_seconds=0.01,
        fail_policy="propagate",
    )
    try:
        with pytest.raises(asyncio.TimeoutError):
            await HookPipeline.run_llm_before({"request_id": "req-timeout"})
    finally:
        HookPipeline.unregister("test-critical-slow-hook")


@pytest.mark.asyncio
async def test_call_llm_emits_hooks_on_success(monkeypatch: pytest.MonkeyPatch):
    runner = _make_runner("ses_runner_llm_hooks_success")
    assistant_msg = SimpleNamespace(id="msg_assistant_success")
    agent = SimpleNamespace(name="rex")
    usage = {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18}
    order: list[str] = []

    async def _before(payload):
        order.append("before")
        assert payload["request"]["toolCount"] == 1
        assert payload["request"]["providerToolsEnabled"] is True

    async def _after(payload, result):
        order.append("after")
        assert payload["sessionID"] == runner.session.id
        assert result["action"] == "stop"
        assert result["finishReason"] == "stop"
        assert result["contentLength"] == len("hello")
        assert result["reasoningLength"] == len("thinking")
        assert result["toolCallCount"] == 0
        assert result["usage"] == usage
        assert result["chunkCounts"] == {"total": 1, "reasoning": 1, "text": 1, "tool": 0}

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_before",
        AsyncMock(side_effect=_before),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_after",
        AsyncMock(side_effect=_after),
    )
    monkeypatch.setattr(
        runner_mod.SessionRunner,
        "_end_observability",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {"temperature": 0.2},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner_mod,
        "trace_scope",
        lambda **kwargs: SimpleNamespace(observation=None),
    )
    monkeypatch.setattr(
        runner_mod,
        "generation_scope",
        lambda **kwargs: SimpleNamespace(observation=None),
    )

    class _Provider:
        def chat_stream(self, **kwargs):
            assert kwargs["model_id"] == runner.model_id
            assert kwargs["session_id"] == runner.session.id

            async def _gen():
                order.append("provider")
                yield SimpleNamespace(
                    delta="hello",
                    reasoning="thinking",
                    tool_calls=None,
                    event_type=None,
                    finish_reason="stop",
                    usage=usage,
                )

            return _gen()

    result = await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hello from user")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "description": "Search docs",
                    "parameters": {"type": "object"},
                },
            }
        ],
        agent=agent,
        assistant_msg=assistant_msg,
    )

    assert result.action == "stop"
    assert result.content == "hello"
    assert result.usage == usage
    assert order == ["before", "provider", "after"]


@pytest.mark.asyncio
async def test_call_llm_blocks_provider_when_llm_before_hook_fails(monkeypatch: pytest.MonkeyPatch):
    runner = _make_runner("ses_runner_llm_before_fail_closed")
    assistant_msg = SimpleNamespace(id="msg_assistant_before_fail")
    agent = SimpleNamespace(name="rex")

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(side_effect=lambda stage, _metadata=None: stage == runner_mod.HookStage.LLM_BEFORE),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_before",
        AsyncMock(side_effect=RuntimeError("redaction unavailable")),
    )
    monkeypatch.setattr(
        runner_mod,
        "langfuse_is_active",
        lambda: False,
    )
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )

    class _Provider:
        def chat_stream(self, **kwargs):
            raise AssertionError("provider must not be called when llm_before fails")

    with pytest.raises(RuntimeError, match="request was not sent"):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="email alice@example.com")],
            tools=[],
            agent=agent,
            assistant_msg=assistant_msg,
        )


@pytest.mark.asyncio
async def test_call_llm_blocks_provider_when_hook_stage_probe_fails(monkeypatch: pytest.MonkeyPatch):
    runner = _make_runner("ses_runner_hook_probe_fail_closed")
    assistant_msg = SimpleNamespace(id="msg_assistant_probe_fail")
    agent = SimpleNamespace(name="rex")

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(side_effect=RuntimeError("hook registry unavailable")),
    )
    monkeypatch.setattr(
        runner_mod,
        "langfuse_is_active",
        lambda: False,
    )
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )

    class _Provider:
        def chat_stream(self, **kwargs):
            raise AssertionError("provider must not be called when hook probe fails")

    with pytest.raises(RuntimeError, match="stage probe failed"):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="email alice@example.com")],
            tools=[],
            agent=agent,
            assistant_msg=assistant_msg,
        )


@pytest.mark.asyncio
async def test_call_llm_initializes_langfuse_after_llm_before_redaction(monkeypatch: pytest.MonkeyPatch):
    runner = _make_runner("ses_runner_langfuse_redacted")
    assistant_msg = SimpleNamespace(id="msg_assistant_langfuse_redacted")
    agent = SimpleNamespace(name="rex")
    generation_inputs: list[list[dict[str, object]]] = []
    trace_inputs: list[dict[str, object]] = []

    async def _before(payload):
        raw_messages = payload["request"]["messages"]
        assert raw_messages[0]["content"] == "email alice@example.com"
        return SimpleNamespace(
            output={
                "request": {
                    **payload["request"],
                    "messages": [{"role": "user", "content": "email [[V_EMAIL_1]]"}],
                    "providerOptions": {},
                },
                "redaction": {
                    "streamTextReplacements": [
                        {"placeholder": "[[V_EMAIL_1]]", "value": "alice@example.com"}
                    ],
                },
            }
        )

    def _trace_scope(**kwargs):
        trace_inputs.append(kwargs["input"])
        return SimpleNamespace(observation="trace")

    def _generation_scope(**kwargs):
        generation_inputs.append(kwargs["input"])
        return SimpleNamespace(observation="generation")

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(side_effect=lambda stage, _metadata=None: stage == runner_mod.HookStage.LLM_BEFORE),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_before",
        AsyncMock(side_effect=_before),
    )
    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: True)
    monkeypatch.setattr(runner_mod, "trace_scope", _trace_scope)
    monkeypatch.setattr(runner_mod, "generation_scope", _generation_scope)
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))

    class _Provider:
        def chat_stream(self, **kwargs):
            assert kwargs["messages"][0].content == "email [[V_EMAIL_1]]"

            async def _gen():
                yield SimpleNamespace(delta="done", finish_reason="stop")

            return _gen()

    result = await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="email alice@example.com")],
        tools=[],
        agent=agent,
        assistant_msg=assistant_msg,
    )

    assert result.action == "stop"
    assert generation_inputs
    assert trace_inputs
    assert "alice@example.com" not in str(generation_inputs)
    assert "alice@example.com" not in str(trace_inputs)
    assert "[[V_EMAIL_1]]" in str(generation_inputs)


@pytest.mark.asyncio
async def test_call_llm_emits_after_hook_on_error(monkeypatch: pytest.MonkeyPatch):
    runner = _make_runner("ses_runner_llm_hooks_error")
    assistant_msg = SimpleNamespace(id="msg_assistant_error")
    agent = SimpleNamespace(name="rex")
    order: list[str] = []

    async def _before(payload):
        order.append("before")
        assert payload["request"]["messageCount"] == 1

    async def _after(payload, result):
        order.append("after")
        assert payload["messageID"] == assistant_msg.id
        assert result["chunkCounts"] == {"total": 0, "reasoning": 0, "text": 0, "tool": 0}
        assert result["error"]["type"] == "RuntimeError"
        assert "provider boom" in result["error"]["message"]

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_before",
        AsyncMock(side_effect=_before),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_after",
        AsyncMock(side_effect=_after),
    )
    monkeypatch.setattr(
        runner_mod.SessionRunner,
        "_end_observability",
        staticmethod(lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.tool_accumulator.ToolCallAccumulator",
        _FakeToolAccumulator,
    )
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner_mod,
        "trace_scope",
        lambda **kwargs: SimpleNamespace(observation=None),
    )
    monkeypatch.setattr(
        runner_mod,
        "generation_scope",
        lambda **kwargs: SimpleNamespace(observation=None),
    )

    class _Provider:
        def chat_stream(self, **kwargs):
            assert kwargs["model_id"] == runner.model_id

            async def _gen():
                order.append("provider")
                raise RuntimeError("provider boom")
                yield  # pragma: no cover

            return _gen()

    with pytest.raises(RuntimeError, match="provider boom"):
        await runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="hello from user")],
            tools=[],
            agent=agent,
            assistant_msg=assistant_msg,
        )

    assert order == ["before", "provider", "after"]


@pytest.mark.asyncio
async def test_call_llm_drains_started_delegate_before_raising_provider_error(
    monkeypatch: pytest.MonkeyPatch,
):
    runner = _make_runner("ses_runner_delegate_provider_error")
    assistant_msg = SimpleNamespace(id="msg_assistant_delegate_provider_error")
    agent = SimpleNamespace(name="rex")
    delegate_started = asyncio.Event()
    release_delegate = asyncio.Event()

    async def _execute_delegate(tool_name, ctx, **_kwargs):
        assert tool_name == "delegate_task"
        assert ctx.call_id == "call-delegate"
        delegate_started.set()
        await release_delegate.wait()
        return ToolResult(success=True, output="child done")

    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(runner_mod.HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "flocks.provider.options.build_provider_options",
        lambda provider_id, model_id: {},
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.store_part",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.Message.parts",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "flocks.session.streaming.stream_processor.ToolRegistry.execute",
        _execute_delegate,
    )
    monkeypatch.setattr(
        StreamProcessor,
        "_resolve_sandbox_meta",
        AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
    )

    class _Provider:
        def chat_stream(self, **_kwargs):
            async def _gen():
                yield SimpleNamespace(
                    delta="",
                    reasoning=None,
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call-delegate",
                            "function": {
                                "name": "delegate_task",
                                "arguments": ('{"subagent_type":"explore","prompt":"inspect the failure"}'),
                            },
                        }
                    ],
                    event_type=None,
                    finish_reason=None,
                    usage=None,
                )
                await delegate_started.wait()
                raise RuntimeError("provider stream failed after delegate start")

            return _gen()

    call_task = asyncio.create_task(
        runner._call_llm(
            provider=_Provider(),
            messages=[ChatMessage(role="user", content="delegate the investigation")],
            tools=[],
            agent=agent,
            assistant_msg=assistant_msg,
        )
    )
    # First tool-schema lookup can include plugin discovery on a cold test run.
    await asyncio.wait_for(delegate_started.wait(), timeout=5)
    await asyncio.sleep(0)
    provider_error_waited_for_delegate = not call_task.done()

    release_delegate.set()
    with pytest.raises(RuntimeError, match="provider stream failed after delegate start"):
        await call_task

    assert provider_error_waited_for_delegate is True


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "invalid", "failure"])
async def test_call_llm_only_exempts_successful_sleep_from_loop_guard(monkeypatch, outcome):
    from flocks.tool.agent.communication import sleep

    runner = _make_runner(f"ses_runner_sleep_result_{outcome}")
    store_part = AsyncMock()

    async def execute(tool_name, ctx, **kwargs):
        assert tool_name == "sleep"
        if outcome == "failure":
            raise RuntimeError("sleep failed")
        return await sleep(ctx, **kwargs)

    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.Message, "store_part", store_part)
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock())
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda *_args: {})
    monkeypatch.setattr("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute)
    monkeypatch.setattr(
        StreamProcessor, "_resolve_sandbox_meta",
        AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
    )

    class Provider:
        async def chat_stream(self, **_kwargs):
            yield SimpleNamespace(tool_calls=[{
                "index": 0,
                "id": "call-sleep",
                "function": {
                    "name": "sleep",
                    "arguments": '{"timeout_s":0}' if outcome == "invalid" else '{"timeout_s":0.001}',
                },
            }])

    result = await runner._call_llm(
        provider=Provider(), messages=[ChatMessage(role="user", content="wait for a message")],
        tools=[], agent=SimpleNamespace(name="rex"), assistant_msg=SimpleNamespace(id="msg_sleep"),
    )

    assert result._successful_sleep is (outcome == "success")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "sleep"
    assert store_part.await_args.args[2].state.status == ("completed" if outcome == "success" else "error")
    decisions = [runner._update_tool_loop_guard(result, last_user_id="user-1") for _ in range(3)]
    assert [decision["action"] for decision in decisions] == (
        ["allow"] * 3 if outcome == "success" else ["allow", "warn", "halt"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["provider_error", "cancel", "flush_error"])
async def test_call_llm_discards_deferred_sleep_on_unsuccessful_stream(monkeypatch, exit_kind):
    runner = _make_runner(f"ses_runner_sleep_{exit_kind}")
    runner.callbacks.event_publish_callback = AsyncMock()
    store_part = AsyncMock()
    interruption = (
        asyncio.CancelledError("provider stream cancelled")
        if exit_kind == "cancel"
        else RuntimeError("provider stream failed after sleep call")
    )

    async def unexpected_sleep(**_kwargs):
        await asyncio.Event().wait()

    execute = AsyncMock(side_effect=unexpected_sleep)
    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.Message, "store_part", store_part)
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock())
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda *_args: {})
    monkeypatch.setattr("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute)
    monkeypatch.setattr(
        StreamProcessor, "_resolve_sandbox_meta",
        AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
    )

    class Provider:
        async def chat_stream(self, **_kwargs):
            yield SimpleNamespace(
                tool_calls=[{
                    "index": 0,
                    "id": "call-sleep",
                    "function": {"name": "sleep", "arguments": '{"timeout_s":3600}'},
                }],
            )
            if exit_kind == "flush_error":
                yield SimpleNamespace(
                    tool_calls=[{
                        "index": 1,
                        "id": "call-truncated",
                        "function": {"name": "read", "arguments": '{"file_path":"unfinished'},
                    }],
                    finish_reason="length",
                )
            else:
                raise interruption

    call = runner._call_llm(
        provider=Provider(), messages=[ChatMessage(role="user", content="wait for a message")],
        tools=[], agent=SimpleNamespace(name="rex"), assistant_msg=SimpleNamespace(id="msg_sleep"),
    )
    if exit_kind == "flush_error":
        # A cut-off tool call finishes the step instead of failing it; the
        # batch's deferred wait must still not start.
        result = await asyncio.wait_for(call, timeout=1)
        assert result.tool_args_truncated is True
        truncated_parts = [
            c.args[2] for c in store_part.await_args_list
            if getattr(c.args[2], "callID", None) == "call-truncated"
        ]
        assert truncated_parts[-1].state.status == "error"
    else:
        with pytest.raises(type(interruption)) as raised:
            await asyncio.wait_for(call, timeout=1)
        assert raised.value is interruption
    execute.assert_not_awaited()
    assert runner._attempt_state.tool_execution_started is False
    sleep_parts = [
        call.args[2] for call in store_part.await_args_list
        if getattr(call.args[2], "callID", None) == "call-sleep"
    ]
    assert [part.state.status for part in sleep_parts] == ["pending", "error"]
    assert sleep_parts[0].id == sleep_parts[1].id
    assert sleep_parts[-1].state.input == {"timeout_s": 3600}
    assert sleep_parts[-1].state.metadata["interrupted"] is True
    published_sleep = [
        call.args[1]["part"] for call in runner.callbacks.event_publish_callback.await_args_list
        if call.args[1].get("part", {}).get("callID") == "call-sleep"
    ]
    assert published_sleep[-1]["state"]["status"] == "error"
    assert published_sleep[-1]["state"]["metadata"]["interrupted"] is True


@pytest.mark.asyncio
async def test_call_llm_cancel_during_first_deferred_sleep_finalizes_both(monkeypatch):
    runner = _make_runner("ses_runner_two_sleeps")
    runner.callbacks.event_publish_callback = AsyncMock()
    store = AsyncMock()
    started = asyncio.Event()
    executed = []

    async def execute(tool_name, ctx, **_kwargs):
        executed.append(ctx.call_id)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runner_mod, "langfuse_is_active", lambda: False)
    monkeypatch.setattr(runner_mod.Message, "store_part", store)
    monkeypatch.setattr(runner_mod.Message, "parts", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock())
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda *_args: {})
    monkeypatch.setattr("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute)
    monkeypatch.setattr(
        StreamProcessor, "_resolve_sandbox_meta",
        AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
    )

    class Provider:
        async def chat_stream(self, **_kwargs):
            for index in (1, 2):
                yield SimpleNamespace(tool_calls=[{
                    "index": index, "id": f"sleep-{index}",
                    "function": {"name": "sleep", "arguments": f'{{"timeout_s":{index}}}'},
                }])

    call = asyncio.create_task(runner._call_llm(
        provider=Provider(), messages=[ChatMessage(role="user", content="wait twice")],
        tools=[], agent=SimpleNamespace(name="rex"), assistant_msg=SimpleNamespace(id="msg-two-sleeps"),
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert executed == ["sleep-1"]
    for index in (1, 2):
        call_id = f"sleep-{index}"
        parts = [
            entry.args[2] for entry in store.await_args_list
            if getattr(entry.args[2], "callID", None) == call_id
        ]
        assert [part.state.status for part in parts] == (
            ["pending", "running", "error"] if index == 1 else ["pending", "error"]
        )
        assert parts[-1].id == parts[0].id
        assert parts[-1].state.input == {"timeout_s": index}
        assert parts[-1].state.metadata["interrupted"] is True
        events = [
            entry.args[1]["part"] for entry in runner.callbacks.event_publish_callback.await_args_list
            if entry.args[1].get("part", {}).get("callID") == call_id
        ]
        assert events[-1]["id"] == parts[0].id
        assert events[-1]["state"]["status"] == "error"


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_model_timing_closes_on_every_stream_exit(monkeypatch, outcome):
    runner = _make_runner("ses_model_timing")
    assistant_msg = SimpleNamespace(id="msg_model_timing")
    saved = []
    now = [1.0]

    async def store(_session, _message, part):
        saved.append(part.model_dump(mode="json"))

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(runner_mod.Message, "store_part", store)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock())
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=False))
    monkeypatch.setattr("flocks.session.streaming.model_activity.time.time", lambda: now[0])

    class Provider:
        async def chat_stream(self, **_kwargs):
            assert saved[0]["time"]["start"] == 1000  # Before first response.
            for _ in range(100):
                yield SimpleNamespace(delta="a", reasoning=None, tool_calls=None)
            now[0] = 11.0
            if outcome == "error":
                raise RuntimeError("provider failed")
            if outcome == "cancel":
                raise asyncio.CancelledError()

    call = runner._call_llm(
        provider=Provider(), messages=[ChatMessage(role="user", content="test")],
        tools=[], agent=SimpleNamespace(name="rex"), assistant_msg=assistant_msg,
    )
    if outcome == "success":
        await call
    else:
        with pytest.raises(RuntimeError if outcome == "error" else asyncio.CancelledError):
            await call
    assert len(saved) == 2  # No per-token writes.
    assert saved[-1]["time"]["end"] == 11000
    assert saved[-1]["id"] == saved[0]["id"]
