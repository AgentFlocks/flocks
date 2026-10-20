"""Anthropic streaming usage: cache counters on top of input_tokens, unknown kept unknown."""

from types import SimpleNamespace

from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.anthropic import AnthropicProvider
from flocks.session.runner import SessionRunner


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


def _events(start_usage, delta_usage):
    return [
        SimpleNamespace(type="message_start", message=SimpleNamespace(id="msg_01Gateway", usage=start_usage)),
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text", text="")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="ok")),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn", stop_sequence=None),
            usage=delta_usage,
        ),
        SimpleNamespace(type="message_stop"),
    ]


async def _final_usage(start_usage, delta_usage):
    provider = AnthropicProvider()
    provider._client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStream(_events(start_usage, delta_usage)))
    )
    chunks = [
        chunk
        async for chunk in provider.chat_stream("claude-sonnet-5", [ChatMessage(role="user", content="hi")])
    ]
    return [chunk.usage for chunk in chunks if chunk.usage][-1]


async def test_cache_reads_and_writes_come_on_top_of_input():
    usage = await _final_usage(
        SimpleNamespace(
            input_tokens=1_000,
            output_tokens=1,
            cache_read_input_tokens=50_000,
            cache_creation_input_tokens=2_000,
        ),
        SimpleNamespace(output_tokens=300),
    )

    assert usage["response_id"] == "msg_01Gateway"
    tokens = SessionRunner._build_tokens_update(usage)
    assert tokens["input"] == 1_000
    assert tokens["output"] == 300
    assert tokens["cache"] == {"read": 50_000, "write": 2_000, "reported": True}


async def test_final_counts_on_message_delta_are_used():
    """Some Anthropic-compatible servers report the prompt side only at the end."""
    usage = await _final_usage(
        SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=None,
                        cache_creation_input_tokens=None),
        SimpleNamespace(output_tokens=40, input_tokens=1_200, cache_read_input_tokens=800,
                        cache_creation_input_tokens=None),
    )

    tokens = SessionRunner._build_tokens_update(usage)
    assert (tokens["input"], tokens["output"]) == (1_200, 40)
    assert tokens["cache"] == {"read": 800, "write": 0, "reported": True}


async def test_final_zero_counts_replace_the_start_estimate():
    """message_delta counts are cumulative and final, zero included."""
    usage = await _final_usage(
        SimpleNamespace(input_tokens=1_000, output_tokens=1, cache_read_input_tokens=None,
                        cache_creation_input_tokens=None),
        SimpleNamespace(output_tokens=30, input_tokens=0, cache_read_input_tokens=1_000,
                        cache_creation_input_tokens=0),
    )

    tokens = SessionRunner._build_tokens_update(usage)
    assert (tokens["input"], tokens["output"]) == (0, 30)
    assert tokens["cache"] == {"read": 1_000, "write": 0, "reported": True}


async def test_missing_cache_counters_are_not_reported_as_a_miss():
    usage = await _final_usage(
        SimpleNamespace(input_tokens=1_000, output_tokens=0, cache_read_input_tokens=None,
                        cache_creation_input_tokens=None),
        SimpleNamespace(output_tokens=20),
    )

    tokens = SessionRunner._build_tokens_update(usage)
    assert tokens["cache"] == {"read": 0, "write": 0, "reported": False}
