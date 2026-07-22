import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.anthropic import AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_chat_forwards_structured_system_blocks():
    provider = AnthropicProvider()
    create_mock = AsyncMock(return_value=SimpleNamespace(
        id="resp_1",
        model="claude-sonnet",
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="ok")],
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=4,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=5,
        ),
    ))
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create_mock))

    system_blocks = [
        {"type": "text", "text": "provider prompt"},
        {
            "type": "text",
            "text": "context prompt",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "runtime prompt"},
    ]
    messages = [
        ChatMessage(role="system", content=system_blocks),
        ChatMessage(role="user", content="hello"),
    ]

    response = await provider.chat("claude-sonnet", messages)

    assert response.content == "ok"
    request_kwargs = create_mock.await_args.kwargs
    assert request_kwargs["system"] == system_blocks
