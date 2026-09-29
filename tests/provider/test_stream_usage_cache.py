"""Prompt size from OpenAI-format usage that also carries Anthropic-style cache fields.

The context meter and the compaction trigger read a call's prompt size back as
``input + cache.read + cache.write`` of the assistant message tokens.
"""

import json

import pytest
from openai.types import CompletionUsage

from flocks.provider.sdk.openai_base import _normalize_stream_usage
from flocks.session.runner import SessionRunner

# What a LiteLLM proxy (1.83) returns for Claude with a warm cache: 1,000 new
# prompt tokens, 50,000 read from the cache and 2,000 written to it.  It counts
# the cache into prompt_tokens as OpenAI does and repeats it at the top level.
LITELLM_CLAUDE_WARM = {
    "prompt_tokens": 53_000,
    "completion_tokens": 300,
    "total_tokens": 53_300,
    "cache_read_input_tokens": 50_000,
    "cache_creation_input_tokens": 2_000,
    "prompt_tokens_details": {"cached_tokens": 50_000, "cache_creation_tokens": 2_000},
}
# First call after the cache expired: everything cacheable is written.
LITELLM_CLAUDE_COLD = {
    "prompt_tokens": 51_000,
    "completion_tokens": 300,
    "total_tokens": 51_300,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 50_000,
    "prompt_tokens_details": {"cache_creation_tokens": 50_000},
}
# Gemini context caching through LiteLLM.
LITELLM_GEMINI = {
    "prompt_tokens": 53_000,
    "completion_tokens": 300,
    "total_tokens": 53_300,
    "cache_read_input_tokens": 50_000,
    "prompt_tokens_details": {"cached_tokens": 50_000},
}
# A gateway that passes Anthropic's own numbers through: prompt_tokens is only
# the uncached part.
ANTHROPIC_STYLE = {
    "prompt_tokens": 1_000,
    "completion_tokens": 300,
    "total_tokens": 1_300,
    "cache_read_input_tokens": 50_000,
    "cache_creation_input_tokens": 2_000,
}
# OpenAI itself: cached tokens only in the breakdown.
OPENAI_CACHED = {
    "prompt_tokens": 53_000,
    "completion_tokens": 300,
    "total_tokens": 53_300,
    "prompt_tokens_details": {"cached_tokens": 50_000},
}


def _tokens(wire: dict) -> dict:
    return SessionRunner._build_tokens_update(_normalize_stream_usage(CompletionUsage(**wire)))


def _prompt_size(wire: dict) -> int:
    tokens = _tokens(wire)
    return tokens["input"] + tokens["cache"]["read"] + tokens["cache"]["write"]


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        (LITELLM_CLAUDE_WARM, 53_000),
        (LITELLM_CLAUDE_COLD, 51_000),
        (LITELLM_GEMINI, 53_000),
        (ANTHROPIC_STYLE, 53_000),
        (OPENAI_CACHED, 53_000),
    ],
    ids=["litellm-claude-warm", "litellm-claude-cold", "litellm-gemini", "anthropic-style", "openai"],
)
def test_prompt_size_counts_the_cache_once(wire, expected):
    assert _prompt_size(wire) == expected


def test_litellm_cache_is_split_out_of_the_uncached_input():
    tokens = _tokens(LITELLM_CLAUDE_WARM)

    assert tokens["input"] == 1_000
    assert tokens["cache"] == {"read": 50_000, "write": 2_000}


def test_reported_prompt_tokens_stay_as_the_gateway_sent_them():
    """Usage records and cost keep reading the gateway's own prompt_tokens."""
    usage = _normalize_stream_usage(CompletionUsage(**LITELLM_CLAUDE_WARM))

    assert usage["prompt_tokens"] == 53_000
    assert usage["cache_tokens_in_prompt"] == 52_000
    assert "cache_tokens_in_prompt" not in _normalize_stream_usage(CompletionUsage(**ANTHROPIC_STYLE))
    assert "cache_tokens_in_prompt" not in _normalize_stream_usage(CompletionUsage(**OPENAI_CACHED))


def test_breakdown_larger_than_the_prompt_is_not_subtracted():
    """Anthropic-style numbers plus a matching breakdown: prompt_tokens is too
    small to contain the cache, so nothing is taken out of it."""
    wire = {
        **ANTHROPIC_STYLE,
        "prompt_tokens_details": {"cached_tokens": 50_000, "cache_creation_tokens": 2_000},
    }

    assert _prompt_size(wire) == 53_000


def test_missing_prompt_tokens_do_not_break_the_step():
    """Some provider paths report ``prompt_tokens: None``."""
    tokens = SessionRunner._build_tokens_update({"prompt_tokens": None, "completion_tokens": 5})

    assert tokens["input"] == 0
    assert tokens["output"] == 5


def _prompt_size_for_request(wire: dict, request_tokens: int) -> int:
    tokens = SessionRunner._build_tokens_update(
        _normalize_stream_usage(CompletionUsage(**wire)),
        request_tokens=request_tokens,
    )
    return tokens["input"] + tokens["cache"]["read"] + tokens["cache"]["write"]


# The request estimate (chars/4, one token per CJK character) runs low on
# real tokenizers; these ratios are estimate / real prompt.
ESTIMATE_RATIOS = (0.75, 0.9, 1.0)


@pytest.mark.parametrize("ratio", ESTIMATE_RATIOS)
@pytest.mark.parametrize(
    ("wire", "real"),
    [(LITELLM_CLAUDE_WARM, 53_000), (LITELLM_CLAUDE_COLD, 51_000), (LITELLM_GEMINI, 53_000)],
    ids=["litellm-claude-warm", "litellm-claude-cold", "litellm-gemini"],
)
def test_request_size_confirms_litellm_counted_the_cache_in(wire, real, ratio):
    assert _prompt_size_for_request(wire, int(real * ratio)) == real


# A gateway passing Anthropic's own numbers (prompt_tokens = uncached) through
# LiteLLM's Usage gets the same details copy.  Only the system prompt and
# tools are cached, the conversation is not.
ANTHROPIC_BEHIND_LITELLM = {
    "prompt_tokens": 60_000,
    "completion_tokens": 300,
    "total_tokens": 60_300,
    "cache_read_input_tokens": 15_000,
    "prompt_tokens_details": {"cached_tokens": 15_000},
}


@pytest.mark.parametrize("ratio", ESTIMATE_RATIOS)
def test_anthropic_numbers_behind_litellm_are_not_under_counted(ratio):
    assert _prompt_size_for_request(ANTHROPIC_BEHIND_LITELLM, int(75_000 * ratio)) == 75_000


def test_small_cache_share_falls_back_to_counting_it_twice():
    """LiteLLM with only 10% of a long prompt cached: the two readings are too
    close to tell apart, so the cache is added on top (early compaction)
    rather than risking an under-count."""
    wire = {
        "prompt_tokens": 150_000,
        "completion_tokens": 300,
        "total_tokens": 150_300,
        "cache_read_input_tokens": 15_000,
        "prompt_tokens_details": {"cached_tokens": 15_000},
    }

    assert _prompt_size_for_request(wire, 135_000) == 165_000


def test_only_the_cache_fields_mirrored_in_the_details_are_taken_out():
    """Reads counted into prompt_tokens (and mirrored), writes reported on
    top: only the reads come out of prompt_tokens."""
    wire = {
        "prompt_tokens": 53_000,
        "completion_tokens": 300,
        "total_tokens": 53_300,
        "cache_read_input_tokens": 50_000,
        "cache_creation_input_tokens": 2_000,
        "prompt_tokens_details": {"cached_tokens": 50_000},
    }

    assert _prompt_size(wire) == 55_000


def test_request_estimate_counts_text_calls_and_tools_but_not_images():
    from flocks.provider.provider import ChatMessage
    from flocks.session.prompt import SessionPrompt

    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 40_000}}
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"filePath": "a.md"}'}}
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    messages = [
        ChatMessage(role="system", content="s" * 400),
        ChatMessage(role="user", content=[{"type": "text", "text": "看看这张图"}, image]),
        ChatMessage(role="assistant", content="", reasoning_content="r" * 40, tool_calls=[tool_call]),
        {"role": "tool", "content": "o" * 800, "tool_call_id": "call_1"},
    ]

    expected = (
        100
        + SessionPrompt.count_tokens("看看这张图")
        + 10
        + SessionPrompt.count_tokens(json.dumps([tool_call], ensure_ascii=False))
        + 200
        + SessionPrompt.count_tokens(json.dumps(tools, ensure_ascii=False))
    )
    assert SessionRunner._estimate_request_tokens(messages, tools) == expected
