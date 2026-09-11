"""The Anthropic streaming path must report why generation stopped.

``stop_reason`` only ever appears on ``message_delta``; ``message_stop`` used to
hard-code ``finish_reason="stop"``, so a ``max_tokens`` cut was indistinguishable
from a clean finish and the tool-argument truncation guard could never fire on
Anthropic models.
"""

from types import SimpleNamespace

import pytest

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.anthropic import AnthropicProvider


class _FakeStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for event in self._events:
                yield event

        return gen()


def _events(stop_reason: str, *, tool_call: bool = True):
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=0,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                )
            ),
        )
    ]
    if tool_call:
        events += [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="write"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                index=0,
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"filePath": "/tmp/x.txt", "content": "line 1\\nline 2',
                ),
            ),
            SimpleNamespace(type="content_block_stop", index=0),
        ]
    events += [
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason=stop_reason, stop_sequence=None),
            usage=SimpleNamespace(output_tokens=4096),
        ),
        SimpleNamespace(type="message_stop"),
    ]
    return events


async def _finish_reason_for(stop_reason: str, **kwargs) -> str:
    provider = AnthropicProvider()
    provider._client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStream(_events(stop_reason, **kwargs)))
    )
    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            "claude-sonnet-5",
            [ChatMessage(role="user", content="write a long file")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "write",
                        "description": "write a file",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]
    finishes = [c.finish_reason for c in chunks if c.finish_reason]
    return finishes[-1] if finishes else ""


class TestAnthropicStreamStopReason:
    @pytest.mark.asyncio
    async def test_max_tokens_surfaces_as_length(self):
        """This is the value tool_accumulator's truncation guard keys on."""
        assert await _finish_reason_for("max_tokens") == "length"

    @pytest.mark.asyncio
    async def test_tool_use_surfaces_as_tool_calls(self):
        assert await _finish_reason_for("tool_use") == "tool_calls"

    @pytest.mark.asyncio
    async def test_end_turn_still_surfaces_as_stop(self):
        assert await _finish_reason_for("end_turn", tool_call=False) == "stop"

    @pytest.mark.asyncio
    async def test_missing_stop_reason_defaults_to_stop(self):
        assert await _finish_reason_for("", tool_call=False) == "stop"
