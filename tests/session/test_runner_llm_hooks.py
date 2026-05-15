"""Tests for LLM lifecycle hooks in SessionRunner and HookPipeline."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.session.runner as runner_mod
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.provider.provider import ChatMessage
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
