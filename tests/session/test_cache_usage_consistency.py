"""One provider usage, read the same way by message tokens, usage records,
stats, cost and the context meter.

The SSE frames replay what the ThreatBook gateway returned on 2026-10-13
(ids, usage and frame layout kept, answer text shortened): deepseek reports
cache hits in prompt_tokens_details.cached_tokens on the final content frame,
qwen sends a usage-only frame without any cache counter.
"""
import json

import httpx
import pytest
from openai import AsyncOpenAI

from flocks.provider.sdk.openai_base import OpenAIBaseProvider
from flocks.provider.types import PriceConfig
from tests.cli.test_exec_command import runtime  # noqa: F401
from tests.cli.test_review_fixes import provider_runtime  # noqa: F401

DEEPSEEK_FRAMES = [
    {
        "id": "4d1f21d3-aa68-47fa-b127-6d1bd62ad607",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-v4-flash-0731",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "好"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 4176,
            "completion_tokens": 16,
            "total_tokens": 4192,
            "prompt_tokens_details": {"cached_tokens": 4096},
            "completion_tokens_details": {"reasoning_tokens": 16},
        },
    },
]
QWEN_FRAMES = [
    {
        "id": "chatcmpl-4fc70ca9-23d6-96a4-b03f-f6a0f678b828",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3.6-plus",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "好"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-4fc70ca9-23d6-96a4-b03f-f6a0f678b828",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3.6-plus",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
    {
        "id": "chatcmpl-4fc70ca9-23d6-96a4-b03f-f6a0f678b828",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "qwen3.6-plus",
        "choices": [],
        "usage": {
            "prompt_tokens": 4488,
            "completion_tokens": 288,
            "total_tokens": 4776,
            "completion_tokens_details": {"reasoning_tokens": 283, "text_tokens": 288},
            "prompt_tokens_details": {"text_tokens": 4488},
        },
    },
]
# A gateway passing Anthropic's numbers: cache reads and writes on top of an
# uncached prompt_tokens.
ANTHROPIC_STYLE_FRAMES = [
    {
        "id": "msg_01AnthropicStyle",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "claude-via-gateway",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "好"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 5,
            "total_tokens": 1005,
            "cache_read_input_tokens": 50000,
            "cache_creation_input_tokens": 2000,
        },
    },
]
PRICE = PriceConfig(input=1, output=2, cache_read=0.2, unit=1, currency="CNY")


class _GatewayProvider(OpenAIBaseProvider):
    DEFAULT_BASE_URL = "http://gateway.test/v1"
    CATALOG_ID = ""

    def __init__(self, frames):
        super().__init__(provider_id="test", name="Gateway")
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

        self._client = AsyncOpenAI(
            api_key="test",
            base_url=self.DEFAULT_BASE_URL,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,
        )

    def is_configured(self):
        return True


async def _run_turn(provider_runtime, monkeypatch, frames):  # noqa: F811
    import flocks.provider.usage_service as usage_service
    from flocks.project.instance import Instance
    from flocks.provider.provider import Provider
    from flocks.session.message import Message, MessageRole
    from flocks.session.session import Session
    from flocks.session.session_loop import SessionLoop

    directory, _tool = provider_runtime
    provider = _GatewayProvider(frames)
    monkeypatch.setattr(Provider, "get", lambda _: provider)
    monkeypatch.setattr(usage_service, "resolve_usage_pricing", lambda *_: PRICE)
    session = await Session.create(project_id="default", directory=str(directory), agent="rex")
    await Message.create(session.id, MessageRole.USER, "回答：好")
    outcome = await Instance.provide(str(directory), fn=lambda: SessionLoop.run(
        session_id=session.id, provider_id="test", model_id="model", agent_name="rex",
        working_directory=str(directory),
    ))
    assert outcome.action == "stop" and not outcome.error, outcome
    messages = await Message.list(session.id)
    records = await usage_service.get_usage_records(session_ids=[session.id])
    stats = await usage_service.get_usage_stats(session_ids=[session.id])
    return messages, records, stats


async def test_reported_cache_hits_reach_message_record_stats_and_meter(provider_runtime, monkeypatch):  # noqa: F811
    from flocks.session.context_usage import latest_observation

    messages, records, stats = await _run_turn(provider_runtime, monkeypatch, DEEPSEEK_FRAMES)

    usage = messages[-1].tokens
    assert (usage.input, usage.output, usage.reasoning) == (80, 0, 16)
    assert (usage.cache.read, usage.cache.write, usage.cache.reported) == (4096, 0, True)

    [record] = records
    assert (record.input_tokens, record.cached_tokens, record.cache_write_tokens) == (4176, 4096, 0)
    assert record.cache_reported is True
    assert record.reasoning_tokens == 16
    assert record.total_tokens == 4192  # what the gateway reported
    assert record.response_id == "4d1f21d3-aa68-47fa-b127-6d1bd62ad607"
    # 80 uncached at 1, 4,096 cached at 0.2, 16 reasoning at the output price 2
    assert record.total_cost == pytest.approx(80 + 4096 * 0.2 + 16 * 2)

    summary = stats.summary
    assert (summary.total_cache_read_tokens, summary.total_cache_write_tokens) == (4096, 0)
    assert (summary.cache_reported_requests, summary.cache_reported_input_tokens) == (1, 4176)

    # The context meter counts the 4,176-token prompt once.
    assert latest_observation(messages).prompt_tokens == 4176


async def test_unreported_cache_stays_unknown_everywhere(provider_runtime, monkeypatch):  # noqa: F811
    messages, records, stats = await _run_turn(provider_runtime, monkeypatch, QWEN_FRAMES)

    usage = messages[-1].tokens
    assert (usage.input, usage.output, usage.reasoning) == (4488, 5, 283)
    assert usage.cache.reported is False

    [record] = records
    assert record.input_tokens == 4488
    assert record.cache_reported is False
    assert record.total_tokens == 4776
    assert record.response_id == "chatcmpl-4fc70ca9-23d6-96a4-b03f-f6a0f678b828"
    # No cache report: the whole prompt at the input price, reasoning as output.
    assert record.total_cost == pytest.approx(4488 + (5 + 283) * 2)

    summary = stats.summary
    assert summary.total_requests == 1
    assert summary.cache_reported_requests == 0


async def test_cache_on_top_of_prompt_tokens_is_part_of_the_recorded_input(provider_runtime, monkeypatch):  # noqa: F811
    from flocks.session.context_usage import latest_observation

    messages, records, stats = await _run_turn(provider_runtime, monkeypatch, ANTHROPIC_STYLE_FRAMES)

    usage = messages[-1].tokens
    assert (usage.input, usage.cache.read, usage.cache.write) == (1000, 50000, 2000)

    [record] = records
    # The record keeps the whole prompt, so totals and cost see the cache once.
    assert (record.input_tokens, record.cached_tokens, record.cache_write_tokens) == (53000, 50000, 2000)
    assert record.total_tokens == 53005
    # 1,000 uncached at 1, 50,000 cache reads at 0.2, 2,000 writes at the
    # input price (no write price), 5 output at 2
    assert record.total_cost == pytest.approx(1000 + 50000 * 0.2 + 2000 + 5 * 2)
    assert stats.summary.total_input_tokens == 53000
    assert latest_observation(messages).prompt_tokens == 53000
