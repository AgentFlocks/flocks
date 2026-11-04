"""OpenAI adapter regressions: reasoning-model parameters, stream finish reasons, stream_options fallback.

* o1/o3/o4/gpt-5 models reject ``max_tokens`` / ``temperature``; the adapter
  must send ``max_completion_tokens`` and drop temperature.
* A terminal stream chunk keeps the real ``finish_reason`` (``length`` while
  tool calls were being accumulated used to be rewritten to ``tool_calls``).
* ``chat_stream`` retries without ``stream_options`` when the gateway rejects
  the parameter (``create_chat_completion_with_fallbacks``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from flocks.provider.provider import ChatMessage, ProviderConfig


def _openai(create):
    from flocks.provider.sdk.openai import OpenAIProvider

    p = OpenAIProvider()
    p.configure(ProviderConfig(provider_id="openai", api_key="sk-x"))
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return p


class _AIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._it = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _chunk(content=None, finish=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None, model_extra={})
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None)


def _tc(index, id=None, name=None, args=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["o1-mini", "o3", "o4-mini", "gpt-5"])
async def test_reasoning_models_use_max_completion_tokens_without_temperature(model):
    resp = SimpleNamespace(id="r", model=model, choices=[SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")], usage=None)
    create = AsyncMock(return_value=resp)
    await _openai(create).chat(model, [ChatMessage(role="user", content="hi")], max_tokens=100, temperature=0.2)
    kw = create.await_args.kwargs
    assert kw.get("max_completion_tokens") == 100
    assert "max_tokens" not in kw and "temperature" not in kw


@pytest.mark.asyncio
async def test_regular_models_keep_max_tokens_and_temperature():
    resp = SimpleNamespace(id="r", model="gpt-4o", choices=[SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")], usage=None)
    create = AsyncMock(return_value=resp)
    await _openai(create).chat("gpt-4o", [ChatMessage(role="user", content="hi")], max_tokens=100, temperature=0.7)
    kw = create.await_args.kwargs
    assert kw.get("max_tokens") == 100 and kw.get("temperature") == 0.7 and "max_completion_tokens" not in kw


@pytest.mark.asyncio
async def test_stream_keeps_length_finish_reason_with_partial_tool_call():
    create = AsyncMock(return_value=_AIter([_chunk(tool_calls=[_tc(0, "c1", "f", '{"a"')]), _chunk(finish="length")]))
    chunks = [c async for c in _openai(create).chat_stream("gpt-4o", [ChatMessage(role="user", content="hi")])]
    assert chunks[-1].finish_reason == "length"
    assert chunks[-1].tool_calls


@pytest.mark.asyncio
async def test_stream_retries_without_stream_options_when_rejected():
    import openai as sdk

    calls: list[dict] = []

    async def create(**kw):
        calls.append(dict(kw))
        if "stream_options" in kw:
            raise sdk.BadRequestError(
                "Unknown parameter: stream_options",
                response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                body=None,
            )
        return _AIter([_chunk(content="ok", finish="stop")])

    chunks = [c async for c in _openai(create).chat_stream("gpt-4o", [ChatMessage(role="user", content="hi")])]
    assert len(calls) == 2 and "stream_options" in calls[0] and "stream_options" not in calls[1]
    assert [c.delta for c in chunks if c.delta] == ["ok"]
