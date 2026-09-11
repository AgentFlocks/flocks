"""Regression tests for silently half-written files from truncated tool calls.

A ``write`` whose argument JSON was cut off mid-content used to be repaired
(closing quote + brace appended) and then executed, so the file landed on disk
with half its content and the tool reported "Wrote file successfully.".  The
existing guard only covered ``finish_reason`` in ("length", "max_tokens"), which
neither a gateway that drops the connection nor the Anthropic streaming path
ever produces.

Also covers the adapter side: ``openai_base`` now forwards tool-call argument
fragments as they arrive, and its terminal chunk must carry only the remainder
so the accumulator does not append everything twice.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.streaming.tool_accumulator import (
    StreamToolArgumentsTruncatedError,
    ToolCallAccumulator,
)


WRITE_ARGS = json.dumps(
    {"filePath": "/tmp/report.md", "content": "".join(f"line {i}\n" for i in range(400))}
)


def _make_accumulator():
    processor = MagicMock()
    processor.process_event = AsyncMock()
    return ToolCallAccumulator(processor), processor


def _tool_call_events(processor):
    return [
        c.args[0]
        for c in processor.process_event.call_args_list
        if c.args[0].type == "tool-call"
    ]


class TestTruncatedArgumentsAreNotExecuted:
    @pytest.mark.parametrize("finish_reason", ["stop", None, "tool_calls"])
    @pytest.mark.asyncio
    async def test_cut_off_write_is_not_executed(self, finish_reason):
        """The shape of the JSON, not finish_reason, decides that it was cut."""
        acc, proc = _make_accumulator()
        truncated = WRITE_ARGS[: len(WRITE_ARGS) // 2]
        acc._accumulator["call_write"] = {
            "id": "call_write",
            "name": "write",
            "arguments_str": truncated,
            "completed": False,
        }

        with pytest.raises(StreamToolArgumentsTruncatedError) as err:
            await acc.flush_remaining(finish_reason)

        assert err.value.tool_name == "write"
        assert err.value.arguments_len == len(truncated)
        assert not _tool_call_events(proc), "a truncated write must not execute"
        errors = [
            c.args[0]
            for c in proc.process_event.call_args_list
            if c.args[0].type == "tool-input-error"
        ]
        assert errors and "not executed" in errors[0].error

    @pytest.mark.asyncio
    async def test_complete_write_still_executes(self):
        acc, proc = _make_accumulator()
        acc._accumulator["call_write"] = {
            "id": "call_write",
            "name": "write",
            "arguments_str": WRITE_ARGS,
            "completed": False,
        }

        await acc.flush_remaining("stop")

        calls = _tool_call_events(proc)
        assert [c.tool_name for c in calls] == ["write"]
        assert calls[0].input == json.loads(WRITE_ARGS)

    @pytest.mark.asyncio
    async def test_malformed_but_not_truncated_still_uses_invalid_path(self):
        """Garbage that stays unparseable after closing brackets is not truncation."""
        acc, proc = _make_accumulator()
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as reg:
            reg.get_schema.return_value = None
            reg.get.return_value = None
            with patch(
                "flocks.session.streaming.tool_accumulator._find_similar_tool",
                return_value=None,
            ):
                acc._accumulator["call_bad"] = {
                    "id": "call_bad",
                    "name": "some_tool",
                    "arguments_str": "{{broken json{{",
                    "completed": False,
                }
                await acc.flush_remaining("stop")

        assert any(c.tool_name == "invalid" for c in _tool_call_events(proc))


class TestAdapterFragmentsReachAccumulatorExactlyOnce:
    @pytest.mark.asyncio
    async def test_streamed_fragments_are_not_duplicated(self):
        """Drive the real adapter into the real accumulator.

        Fix 1 streams the argument fragments *and* still emits a terminal
        tool_calls chunk. If that terminal chunk repeated the whole argument
        string, the accumulator would concatenate the payload twice and the
        file would be written with duplicated content.
        """
        from flocks.provider.provider import ChatMessage
        from flocks.provider.sdk.openai_base import OpenAIBaseProvider

        class _Provider(OpenAIBaseProvider):
            DEFAULT_BASE_URL = "https://api.example.com/v1"
            ENV_API_KEY = ["EXAMPLE_API_KEY"]
            ENV_BASE_URL = "EXAMPLE_BASE_URL"
            CATALOG_ID = ""

            def __init__(self):
                super().__init__(provider_id="example", name="Example")

        provider = _Provider()
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(
            return_value=_fake_openai_stream(WRITE_ARGS, n_fragments=25)
        )

        acc, proc = _make_accumulator()
        n_arg_chunks = 0
        with patch("flocks.session.streaming.tool_accumulator.ToolRegistry") as reg:
            reg.get_schema.return_value = None
            async for chunk in provider.chat_stream(
                "some-model",
                [ChatMessage(role="user", content="write a long file")],
                tools=[{"type": "function", "function": {"name": "write"}}],
            ):
                for tc in chunk.tool_calls or []:
                    if (tc.get("function") or {}).get("arguments"):
                        n_arg_chunks += 1
                    await acc.feed_chunk(tc)

        assert n_arg_chunks > 3, "arguments must reach the runner as several chunks"
        calls = _tool_call_events(proc)
        assert [c.tool_name for c in calls] == ["write"]
        assert calls[0].input == json.loads(WRITE_ARGS)


def _fake_openai_stream(full_args: str, n_fragments: int):
    """An OpenAI-style SSE stream that emits one tool call in many fragments."""
    from types import SimpleNamespace

    def _chunk(*, arguments=None, name=None, tc_id=None, finish=None):
        tool_calls = None
        if arguments is not None or name or tc_id:
            tool_calls = [
                SimpleNamespace(
                    index=0,
                    id=tc_id,
                    function=SimpleNamespace(name=name, arguments=arguments),
                )
            ]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, tool_calls=tool_calls),
                    finish_reason=finish,
                )
            ],
            usage=None,
        )

    async def gen():
        step = max(1, len(full_args) // n_fragments)
        sent = 0
        first = True
        while sent < len(full_args):
            yield _chunk(
                arguments=full_args[sent : sent + step],
                name="write" if first else None,
                tc_id="call_write" if first else None,
            )
            sent += step
            first = False
        yield _chunk(finish="tool_calls")

    return gen()
