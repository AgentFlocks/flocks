"""An empty streamed answer keeps its tools on the non-streaming fallback.

The fallback used to keep only ``content``: an answer made of tool calls alone
(content null) counted as empty, and the request was then repeated without its
tools, so an agent such as the code audit's threat modeler was told it had none.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.provider.provider import ChatMessage, ChatResponse
from flocks.provider.sdk.openai_base import OpenAIBaseProvider, chat_fallback_tool_call_chunks
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from flocks.session.lifecycle.retry import SessionRetry
from flocks.session.streaming.tool_accumulator import (
    StreamToolArgumentsTruncatedError,
    ToolCallAccumulator,
)


TOOLS = [{"type": "function", "function": {"name": "audit_repository_summary", "parameters": {"type": "object"}}}]
TOOL_CALLS = [{
    "index": 0,
    "id": "call_summary",
    "type": "function",
    "function": {"name": "audit_repository_summary", "arguments": "{}"},
}]


class _BaseProvider(OpenAIBaseProvider):
    DEFAULT_BASE_URL = "https://api.custom.com/v1"
    ENV_API_KEY = ["CUSTOM_API_KEY"]
    ENV_BASE_URL = "CUSTOM_BASE_URL"
    CATALOG_ID = ""

    def __init__(self):
        super().__init__(provider_id="custom-provider", name="Custom Provider")


def _with_client(provider):
    create = AsyncMock()
    provider._client = MagicMock()
    provider._client.chat.completions.create = create
    return provider, create


async def _empty_stream():
    if False:
        yield None


def _fallback(*, content="", tool_calls=None, reasoning=None, finish_reason=None, **extra) -> ChatResponse:
    return ChatResponse(
        id="resp_fallback",
        model="deepseek-v4-flash",
        content=content,
        finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        tool_calls=tool_calls,
        reasoning=reasoning,
        **extra,
    )


PROVIDERS = [
    pytest.param(OpenAICompatibleProvider, id="openai_compatible"),
    pytest.param(_BaseProvider, id="openai_base"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
async def test_tool_calls_of_the_chat_fallback_reach_the_stream(provider_class):
    provider, create = _with_client(provider_class())
    create.return_value = _empty_stream()
    provider.chat = AsyncMock(return_value=_fallback(tool_calls=TOOL_CALLS, reasoning="先看仓库概况"))

    chunks = [
        chunk async for chunk in provider.chat_stream(
            "deepseek-v4-flash",
            [ChatMessage(role="user", content="build the threat model")],
            tools=TOOLS,
        )
    ]

    assert [chunk.reasoning for chunk in chunks if chunk.event_type == "reasoning"] == ["先看仓库概况"]
    terminal = chunks[-1]
    assert terminal.tool_calls == TOOL_CALLS
    assert terminal.finish_reason == "tool_calls"
    provider.chat.assert_awaited_once()
    assert provider.chat.await_args.kwargs["tools"] == TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
async def test_a_blank_answer_is_not_retried_without_the_tools(provider_class):
    provider, create = _with_client(provider_class())
    create.return_value = _empty_stream()
    provider.chat = AsyncMock(return_value=_fallback(content=""))

    # "empty streaming response" is what the session retry treats as transient.
    with pytest.raises(ValueError, match="empty streaming response.*tools_declared=True"):
        _ = [
            chunk async for chunk in provider.chat_stream(
                "deepseek-v4-flash",
                [ChatMessage(role="user", content="build the threat model")],
                tools=TOOLS,
            )
        ]

    provider.chat.assert_awaited_once()
    assert provider.chat.await_args.kwargs["tools"] == TOOLS


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
async def test_chat_returns_the_tool_calls_and_reasoning_of_its_message(provider_class):
    provider, create = _with_client(provider_class())
    message = SimpleNamespace(
        content=None,
        reasoning_content="先看仓库概况",
        tool_calls=[SimpleNamespace(
            id="call_summary",
            function=SimpleNamespace(name="audit_repository_summary", arguments=""),
        )],
    )
    create.return_value = SimpleNamespace(
        id="resp_1",
        model="deepseek-v4-flash",
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        choices=[SimpleNamespace(finish_reason="tool_calls", message=message)],
    )

    response = await provider.chat(
        "deepseek-v4-flash",
        [ChatMessage(role="user", content="build the threat model")],
        tools=TOOLS,
    )

    assert response.content == ""
    assert response.reasoning == "先看仓库概况"
    assert response.reasoning_source == "reasoning_content"
    # Kept as sent: only the replay decides whether "" means "no parameters".
    assert response.tool_calls == [{
        "index": 0,
        "id": "call_summary",
        "type": "function",
        "function": {"name": "audit_repository_summary", "arguments": ""},
    }]


class _Events:
    session_id = "ses_fallback"

    def __init__(self) -> None:
        self.events = []

    async def process_event(self, event) -> None:
        self.events.append(event)


async def _accumulate(chunks):
    """Feed replayed chunks to the real accumulator, as the runner does."""
    processor = _Events()
    accumulator = ToolCallAccumulator(processor)
    finish_reason = None
    for chunk in chunks:
        for call in chunk.tool_calls or []:
            await accumulator.feed_chunk(call)
        finish_reason = chunk.finish_reason or finish_reason
    try:
        await accumulator.flush_remaining(finish_reason, output_limit=100, raised_output_limit=200)
    except StreamToolArgumentsTruncatedError:
        # The runner turns this into a tool error the model can react to.
        processor.events.append(SimpleNamespace(truncated=True))
    return [type(event).__name__ for event in processor.events], processor.events


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "max_tokens"])
async def test_a_cut_off_fallback_call_is_refused_not_run(finish_reason):
    chunks = chat_fallback_tool_call_chunks(
        _fallback(tool_calls=[{**TOOL_CALLS[0], "function": {"name": "audit_repository_summary", "arguments": ""}}],
                  finish_reason=finish_reason),
        provider_id="custom-h20",
        model_id="deepseek-v4-flash",
    )

    kinds, events = await _accumulate(chunks)

    assert "ToolCallEvent" not in kinds
    assert "ToolInputErrorEvent" in kinds
    assert getattr(events[-1], "truncated", False)


@pytest.mark.asyncio
async def test_a_finished_fallback_call_without_arguments_runs_with_none():
    chunks = chat_fallback_tool_call_chunks(
        _fallback(tool_calls=[{**TOOL_CALLS[0], "function": {"name": "audit_repository_summary", "arguments": ""}}]),
        provider_id="custom-h20",
        model_id="deepseek-v4-flash",
    )

    kinds, events = await _accumulate(chunks)

    calls = [event for event in events if type(event).__name__ == "ToolCallEvent"]
    assert [(call.tool_name, call.input) for call in calls] == [("audit_repository_summary", {})]


def test_reasoning_details_alone_still_make_a_reasoning_chunk():
    details = [{"type": "reasoning.encrypted", "data": "opaque"}]

    chunks = chat_fallback_tool_call_chunks(
        _fallback(tool_calls=TOOL_CALLS, reasoning_details=details),
        provider_id="openrouter",
        model_id="some-model",
    )

    reasoning = [chunk for chunk in chunks if chunk.event_type == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0].metadata["reasoningDetails"] == details


@pytest.mark.asyncio
async def test_empty_choices_on_the_fallback_are_retried_with_the_tools():
    provider, create = _with_client(OpenAICompatibleProvider())
    create.side_effect = [
        _empty_stream(),
        SimpleNamespace(id="resp_empty", model="deepseek-v4-flash", usage=None, choices=[]),
    ]

    with pytest.raises(ValueError, match="empty choices") as raised:
        _ = [
            chunk async for chunk in provider.chat_stream(
                "deepseek-v4-flash",
                [ChatMessage(role="user", content="build the threat model")],
                tools=TOOLS,
            )
        ]

    message = str(raised.value)
    assert SessionRetry.retryable({"name": "UnknownError", "message": message, "data": {"message": message}})
    assert create.await_count == 2
    assert all(call.kwargs.get("tools") == TOOLS for call in create.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
async def test_a_cut_off_call_from_the_endpoint_is_not_run_through_the_fallback(provider_class):
    provider, create = _with_client(provider_class())
    message = SimpleNamespace(
        content=None,
        tool_calls=[SimpleNamespace(
            id="call_summary",
            function=SimpleNamespace(name="audit_repository_summary", arguments=""),
        )],
    )
    create.side_effect = [
        _empty_stream(),
        SimpleNamespace(
            id="resp_cut", model="deepseek-v4-flash",
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            choices=[SimpleNamespace(finish_reason="length", message=message)],
        ),
    ]

    chunks = [
        chunk async for chunk in provider.chat_stream(
            "deepseek-v4-flash",
            [ChatMessage(role="user", content="build the threat model")],
            tools=TOOLS,
        )
    ]
    kinds, _events = await _accumulate(chunks)

    assert "ToolCallEvent" not in kinds
    assert "ToolInputErrorEvent" in kinds
