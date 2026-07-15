"""Tests for LLM lifecycle hooks in SessionRunner and HookPipeline."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.hooks.pipeline import HookBase, HookPipeline, normalize_tool_decision
from flocks.provider.provider import ChatMessage
from flocks.session.callable_schema import CallableSchemaResult
from flocks.session.runner import SessionRunner
from flocks.session.session import SessionInfo


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


def test_runner_construction_deep_copies_security_context() -> None:
    security_context = {"subject": {"id": "user-1"}}

    runner = SessionRunner(
        session=_make_session(),
        provider_id="anthropic",
        model_id="claude-sonnet",
        security_context=security_context,
    )

    security_context["subject"]["id"] = "changed-after-construction"

    assert runner._security_context == {"subject": {"id": "user-1"}}


@pytest.mark.asyncio
async def test_runner_passes_copied_trusted_context_to_callable_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SessionRunner(
        session=_make_session("ses_runner_capability_context"),
        provider_id="anthropic",
        model_id="claude-sonnet",
        security_context={"subject": {"subject_id": "user-1"}, "entry": "api"},
    )
    schema_resolver = AsyncMock(return_value=CallableSchemaResult(tool_infos=[], metadata={}))
    monkeypatch.setattr(runner_mod, "list_session_callable_tool_infos", schema_resolver)
    agent = SimpleNamespace(name="rex", tools=["read"])

    await runner._list_callable_tool_infos_for_turn(agent, messages=[])

    capability_context = schema_resolver.await_args.kwargs["capability_context"]
    assert capability_context == {
        "subject": {"subject_id": "user-1"},
        "entry": "api",
        "agent": "rex",
    }
    capability_context["subject"]["subject_id"] = "mutated"
    assert runner._security_context["subject"]["subject_id"] == "user-1"


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

    async def drain_parallel_tool_calls(self) -> None:
        return None


class _FakeToolAccumulator:
    def __init__(self, processor):
        self.processor = processor

    async def feed_chunk(self, tool_call) -> None:
        return None

    async def flush_remaining(self, finish_reason) -> None:
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


def test_normalize_tool_decision_compat_skip():
    decision = normalize_tool_decision({"skip": True})
    assert decision.action == "deny"
    assert decision.reason == "blocked_by_hook_skip"


@pytest.mark.asyncio
async def test_hook_pipeline_tool_before_deny_short_circuits():
    seen: list[str] = []

    class _DenyHook(HookBase):
        async def tool_before(self, ctx) -> None:
            seen.append("deny")
            ctx.output["decision"] = {"action": "deny", "reason": "blocked"}

    class _LaterHook(HookBase):
        async def tool_before(self, ctx) -> None:
            seen.append("later")
            ctx.output["decision"] = {"action": "allow"}

    HookPipeline.register("test-tool-before-deny", _DenyHook(), order=1)
    HookPipeline.register("test-tool-before-later", _LaterHook(), order=2)
    try:
        hook_ctx = await HookPipeline.run_tool_before({"tool": {"name": "read"}})
    finally:
        HookPipeline.unregister("test-tool-before-deny")
        HookPipeline.unregister("test-tool-before-later")

    assert seen == ["deny"]
    assert hook_ctx.output["decision"]["action"] == "deny"
    assert hook_ctx.output["decision"]["reason"] == "blocked"


@pytest.mark.asyncio
async def test_hook_pipeline_tool_before_skip_is_denied_decision():
    class _SkipHook(HookBase):
        async def tool_before(self, ctx) -> None:
            ctx.output["skip"] = True

    HookPipeline.register("test-tool-before-skip", _SkipHook())
    try:
        hook_ctx = await HookPipeline.run_tool_before({"tool": {"name": "write"}})
    finally:
        HookPipeline.unregister("test-tool-before-skip")

    assert hook_ctx.output["decision"]["action"] == "deny"
    assert hook_ctx.output["decision"]["reason"] == "blocked_by_hook_skip"


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
        "run_llm_before",
        AsyncMock(side_effect=_before),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_after",
        AsyncMock(side_effect=_after),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(return_value=True),
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
        "run_llm_before",
        AsyncMock(side_effect=_before),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "run_llm_after",
        AsyncMock(side_effect=_after),
    )
    monkeypatch.setattr(
        runner_mod.HookPipeline,
        "has_stage_handlers",
        AsyncMock(return_value=True),
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
