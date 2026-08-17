from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.anthropic import AnthropicProvider


class _FakeAsyncStream:
    def __init__(self, events):
        self._events = list(events)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_anthropic_formatter_includes_preserved_thinking_blocks():
    message = ChatMessage(
        role="assistant",
        content="Done",
        tool_calls=[
            {
                "id": "tool_1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q":"weather"}'},
            }
        ],
        custom_settings={
            "anthropic_thinking_blocks": [
                {"type": "thinking", "thinking": "plan", "signature": "sig123"}
            ]
        },
    )

    formatted = AnthropicProvider._format_messages_anthropic([message])

    assert formatted[0]["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig123",
    }
    assert formatted[0]["content"][1] == {"type": "text", "text": "Done"}
    assert formatted[0]["content"][2]["type"] == "tool_use"


def test_anthropic_formatter_includes_redacted_thinking_blocks():
    message = ChatMessage(
        role="assistant",
        content="Done",
        custom_settings={
            "anthropic_thinking_blocks": [
                {"type": "redacted_thinking", "data": "opaque_blob"}
            ]
        },
    )

    formatted = AnthropicProvider._format_messages_anthropic([message])

    assert formatted[0]["content"][0] == {
        "type": "redacted_thinking",
        "data": "opaque_blob",
    }
    assert formatted[0]["content"][1] == {"type": "text", "text": "Done"}


def test_anthropic_formatter_strips_stale_signed_thinking_from_older_turns():
    older = ChatMessage(
        role="assistant",
        content="Older",
        custom_settings={
            "anthropic_thinking_blocks": [
                {"type": "thinking", "thinking": "old-plan", "signature": "sig-old"}
            ]
        },
    )
    latest = ChatMessage(
        role="assistant",
        content="Latest",
        custom_settings={
            "anthropic_thinking_blocks": [
                {"type": "thinking", "thinking": "latest-plan", "signature": "sig-new"}
            ]
        },
    )

    formatted = AnthropicProvider._format_messages_anthropic([older, latest])

    assert formatted[0]["content"] == [{"type": "text", "text": "Older"}]
    assert formatted[1]["content"][0]["signature"] == "sig-new"


def test_anthropic_formatter_preserves_unsigned_thinking_for_deepseek_compatible_endpoint():
    message = ChatMessage(
        role="assistant",
        content="Done",
        custom_settings={
            "anthropic_thinking_blocks": [
                {"type": "thinking", "thinking": "unsigned-plan"},
                {"type": "thinking", "thinking": "signed-plan", "signature": "sig123"},
            ]
        },
    )

    formatted = AnthropicProvider._format_messages_anthropic(
        [message],
        base_url="https://api.deepseek.com/anthropic",
        model_id="deepseek-v4-flash",
    )

    assert formatted[0]["content"][0] == {
        "type": "thinking",
        "thinking": "unsigned-plan",
    }
    assert all(block.get("signature") != "sig123" for block in formatted[0]["content"])


def test_anthropic_tool_conversion_strips_schema_defaults_without_mutating_input():
    provider = AnthropicProvider()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "offset": {"type": "integer", "default": 0},
                        "default": {"type": "string", "default": "kept-property"},
                    },
                },
            },
        }
    ]

    converted = provider._convert_tools(tools)

    assert converted is not None
    properties = converted[0]["input_schema"]["properties"]
    assert properties["offset"] == {"type": "integer"}
    assert properties["default"] == {"type": "string"}
    assert tools[0]["function"]["parameters"]["properties"]["offset"]["default"] == 0


@pytest.mark.asyncio
async def test_anthropic_chat_forwards_disabled_thinking_and_temperature_without_beta():
    provider = AnthropicProvider()
    messages_api = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                id="msg_1",
                model="claude-opus-4-8",
                content=[SimpleNamespace(type="text", text="OK")],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=2, output_tokens=1),
            )
        )
    )
    beta_messages_api = SimpleNamespace(create=AsyncMock())
    provider._client = SimpleNamespace(
        messages=messages_api,
        beta=SimpleNamespace(messages=beta_messages_api),
    )

    await provider.chat(
        "claude-opus-4-8",
        [ChatMessage(role="user", content="hello")],
        thinking={"type": "disabled"},
        temperature=0.2,
    )

    kwargs = messages_api.create.await_args.kwargs
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.2
    beta_messages_api.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_anthropic_stream_forwards_disabled_thinking_and_temperature_without_beta():
    provider = AnthropicProvider()
    messages_api = MagicMock()
    messages_api.stream.return_value = _FakeAsyncStream(
        [SimpleNamespace(type="message_stop")]
    )
    beta_messages_api = MagicMock()
    provider._client = SimpleNamespace(
        messages=messages_api,
        beta=SimpleNamespace(messages=beta_messages_api),
    )

    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            "claude-opus-4-8",
            [ChatMessage(role="user", content="hello")],
            thinking={"type": "disabled"},
            temperature=0.2,
        )
    ]

    kwargs = messages_api.stream.call_args.kwargs
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0.2
    beta_messages_api.stream.assert_not_called()
    assert chunks[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_anthropic_stream_uses_beta_interleaved_and_yields_tools_on_block_stop():
    provider = AnthropicProvider()
    beta_stream = MagicMock()
    beta_stream.stream.return_value = _FakeAsyncStream(
        [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=11,
                        output_tokens=0,
                        cache_read_input_tokens=2,
                        cache_creation_input_tokens=0,
                    )
                ),
            ),
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="thinking"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="thinking_delta", thinking="plan"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="signature_delta", signature="sig123"),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="tool_use", id="tool_1", name="search"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"q":"weather"}'),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(output_tokens=7),
            ),
            SimpleNamespace(type="message_stop"),
        ]
    )
    provider._client = SimpleNamespace(
        beta=SimpleNamespace(messages=beta_stream),
        messages=SimpleNamespace(stream=AsyncMock()),
    )

    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            "claude-sonnet-4-6",
            [ChatMessage(role="user", content="hello")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "search",
                        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                    },
                }
            ],
            thinking={"type": "enabled", "budget_tokens": 2048},
        )
    ]

    kwargs = beta_stream.stream.call_args.kwargs
    assert kwargs["betas"] == [
        "interleaved-thinking-2025-05-14",
        "fine-grained-tool-streaming-2025-05-14",
    ]

    assert chunks[0].event_type == "reasoning-start"
    assert chunks[1].event_type == "reasoning"
    assert chunks[1].reasoning == "plan"
    assert chunks[2].event_type == "reasoning-end"
    assert chunks[2].metadata["thinkingSignature"] == "sig123"
    assert chunks[3].tool_calls[0]["id"] == "tool_1"
    assert chunks[3].finish_reason is None
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.asyncio
async def test_anthropic_stream_preserves_redacted_thinking_metadata():
    provider = AnthropicProvider()
    beta_stream = MagicMock()
    beta_stream.stream.return_value = _FakeAsyncStream(
        [
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="redacted_thinking", data="opaque_blob"),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="tool_use", id="tool_1", name="search"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="input_json_delta", partial_json='{"q":"weather"}'),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(type="message_stop"),
        ]
    )
    provider._client = SimpleNamespace(
        beta=SimpleNamespace(messages=beta_stream),
        messages=SimpleNamespace(stream=AsyncMock()),
    )

    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            "claude-sonnet-4-6",
            [ChatMessage(role="user", content="hello")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "search",
                        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                    },
                }
            ],
            thinking={"type": "enabled", "budget_tokens": 2048},
        )
    ]

    assert chunks[0].event_type == "reasoning-start"
    assert chunks[0].metadata["redactedThinkingData"] == "opaque_blob"
    assert chunks[1].event_type == "reasoning-end"
    assert chunks[1].metadata["redactedThinkingData"] == "opaque_blob"
    assert chunks[2].tool_calls[0]["id"] == "tool_1"
