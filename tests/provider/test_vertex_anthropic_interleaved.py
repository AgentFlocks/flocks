import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.vertex_anthropic import VertexAnthropicProvider


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = list(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(self, lines, calls):
        self._lines = lines
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers=None, json=None, timeout=None):
        self._calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return _FakeStreamResponse(self._lines)

    async def post(self, url, headers=None, json=None, timeout=None):
        self._calls.append({
            "method": "POST",
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "id": "resp_1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "search",
                        "input": {"q": "weather"},
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
                "stop_reason": "tool_use",
            },
            text="",
        )


@pytest.mark.asyncio
async def test_vertex_anthropic_stream_emits_reasoning_and_tool_chunks(monkeypatch):
    lines = [
        'data: {"type":"content_block_start","content_block":{"type":"thinking"}}',
        'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"plan"}}',
        'data: {"type":"content_block_delta","delta":{"type":"signature_delta","signature":"sig123"}}',
        'data: {"type":"content_block_stop"}',
        'data: {"type":"content_block_start","content_block":{"type":"tool_use","id":"tool_1","name":"search"}}',
        'data: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"weather\\"}"}}',
        'data: {"type":"content_block_stop"}',
        'data: {"type":"message_stop"}',
        "data: [DONE]",
    ]
    calls = []

    def _client_factory(*_args, **_kwargs):
        return _FakeAsyncClient(lines, calls)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    provider = VertexAnthropicProvider(project="demo-project", location="global")
    provider._get_access_token = AsyncMock(return_value="token")

    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            "claude-3-5-sonnet@20241022",
            [ChatMessage(role="user", content="hello")],
            thinking={"type": "enabled", "budget_tokens": 2048},
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
        )
    ]

    assert calls[0]["json"]["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert calls[0]["json"]["tools"][0]["name"] == "search"
    assert chunks[0].event_type == "reasoning-start"
    assert chunks[1].event_type == "reasoning"
    assert chunks[1].reasoning == "plan"
    assert chunks[2].event_type == "reasoning-end"
    assert chunks[2].metadata["thinkingSignature"] == "sig123"
    assert chunks[3].tool_calls[0]["id"] == "tool_1"
    assert chunks[-1].finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_vertex_anthropic_chat_serializes_tool_use_without_name_error(monkeypatch):
    calls = []

    def _client_factory(*_args, **_kwargs):
        return _FakeAsyncClient([], calls)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    provider = VertexAnthropicProvider(project="demo-project", location="global")
    provider._get_access_token = AsyncMock(return_value="token")

    response = await provider.chat(
        "claude-3-5-sonnet@20241022",
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
    )

    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0]["function"]["arguments"] == '{"q": "weather"}'


@pytest.mark.asyncio
async def test_vertex_anthropic_chat_replays_preserved_thinking_blocks(monkeypatch):
    calls = []

    def _client_factory(*_args, **_kwargs):
        return _FakeAsyncClient([], calls)

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_client_factory))

    provider = VertexAnthropicProvider(project="demo-project", location="global")
    provider._get_access_token = AsyncMock(return_value="token")

    await provider.chat(
        "claude-3-5-sonnet@20241022",
        [
            ChatMessage(
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
        ],
        thinking={"type": "enabled", "budget_tokens": 2048},
    )

    assert calls[0]["json"]["messages"][0]["content"][0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig123",
    }
    assert calls[0]["json"]["messages"][0]["content"][1] == {
        "type": "text",
        "text": "Done",
    }
    assert calls[0]["json"]["messages"][0]["content"][2]["type"] == "tool_use"
