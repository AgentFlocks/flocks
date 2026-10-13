"""Cache usage read from every provider format, counted once, unknown kept apart from zero.

The wire samples marked "ThreatBook gateway" are the usage objects the gateway
returned on 2026-10-13 for deepseek-v4-flash-0731 and qwen3.6-plus.
"""

import pytest
from openai.types import CompletionUsage

from flocks.provider.cost_calculator import CostCalculator
from flocks.provider.token_usage import (
    UsageBreakdown,
    needs_request_check,
    normalize_gemini_usage,
    normalize_usage,
    resolve_usage,
    settle_cache_in_prompt,
)
from flocks.provider.types import PriceConfig
from flocks.session.runner import SessionRunner


def _breakdown(wire: dict) -> UsageBreakdown:
    return resolve_usage(normalize_usage(CompletionUsage(**wire)))


# The cases of the bug report: the same 10,000-token prompt reported four ways.
REPORT_SAMPLE = {"prompt_tokens": 10_000, "completion_tokens": 16, "total_tokens": 10_016}


@pytest.mark.parametrize(
    ("extra", "reported", "cache_read", "prompt"),
    [
        ({}, False, 0, 10_000),
        ({"prompt_tokens_details": {"cached_tokens": 0}}, True, 0, 10_000),
        ({"prompt_tokens_details": {"cached_tokens": 8_192}}, True, 8_192, 10_000),
        # Anthropic-named counter: reported on top of prompt_tokens.
        ({"cache_read_input_tokens": 8_192}, True, 8_192, 18_192),
    ],
    ids=["not-reported", "reported-zero", "nested-8192", "top-level-8192"],
)
def test_report_cases_keep_cache_hits_and_tell_unknown_from_zero(extra, reported, cache_read, prompt):
    usage = _breakdown({**REPORT_SAMPLE, **extra})

    assert usage.cache_reported is reported
    assert usage.cache_read == cache_read
    assert usage.prompt == prompt
    assert usage.output == 16


def test_nested_cache_hits_are_not_added_to_the_prompt_twice():
    tokens = SessionRunner._build_tokens_update(
        normalize_usage(CompletionUsage(**REPORT_SAMPLE, prompt_tokens_details={"cached_tokens": 8_192}))
    )

    assert tokens == {
        "input": 1_808,
        "output": 16,
        "reasoning": 0,
        "cache": {"read": 8_192, "write": 0, "reported": True},
    }
    # Context size: 10,000 + 16, not 18,208.
    assert tokens["input"] + tokens["cache"]["read"] + tokens["cache"]["write"] + tokens["output"] == 10_016


def test_unreported_cache_is_marked_unknown_in_message_tokens():
    tokens = SessionRunner._build_tokens_update(normalize_usage(CompletionUsage(**REPORT_SAMPLE)))

    assert tokens["cache"] == {"read": 0, "write": 0, "reported": False}
    assert tokens["input"] == 10_000


def test_threatbook_gateway_deepseek_usage():
    usage = normalize_usage(
        CompletionUsage(
            prompt_tokens=4_176,
            completion_tokens=16,
            total_tokens=4_192,
            prompt_tokens_details={"cached_tokens": 4_096},
            completion_tokens_details={"reasoning_tokens": 16},
        ),
        response_id="4d1f21d3-aa68-47fa-b127-6d1bd62ad607",
    )

    assert usage == {
        "prompt_tokens": 4_176,
        "completion_tokens": 0,
        "total_tokens": 4_192,
        "reasoning_tokens": 16,
        "cache_read_input_tokens": 4_096,
        "cache_creation_input_tokens": 0,
        "cache_tokens_in_prompt": 4_096,
        "response_id": "4d1f21d3-aa68-47fa-b127-6d1bd62ad607",
    }
    assert resolve_usage(usage) == UsageBreakdown(
        input=80, output=0, reasoning=16, cache_read=4_096, cache_write=0, cache_reported=True
    )


def test_threatbook_gateway_qwen_usage_does_not_report_the_cache():
    """prompt_tokens_details is there, without cached_tokens: unknown, not zero."""
    usage = normalize_usage(
        CompletionUsage(
            prompt_tokens=4_488,
            completion_tokens=288,
            total_tokens=4_776,
            prompt_tokens_details={"text_tokens": 4_488},
            completion_tokens_details={"reasoning_tokens": 283, "text_tokens": 288},
        )
    )

    assert "cache_read_input_tokens" not in usage
    assert "cache_creation_input_tokens" not in usage
    assert resolve_usage(usage).cache_reported is False


@pytest.mark.parametrize(
    ("wire", "read", "write", "prompt"),
    [
        # DeepSeek's own API: prompt_tokens = hits + misses.
        (
            {"prompt_tokens": 5_000, "completion_tokens": 10, "total_tokens": 5_010,
             "prompt_cache_hit_tokens": 4_608, "prompt_cache_miss_tokens": 392},
            4_608, 0, 5_000,
        ),
        # DashScope explicit cache: creation counted inside prompt_tokens.
        (
            {"prompt_tokens": 3_000, "completion_tokens": 10, "total_tokens": 3_010,
             "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_input_tokens": 2_005}},
            0, 2_005, 3_000,
        ),
        # Kimi / OpenRouter cache writes.
        (
            {"prompt_tokens": 3_000, "completion_tokens": 10, "total_tokens": 3_010,
             "prompt_tokens_details": {"cached_tokens": 1_000, "cache_write_tokens": 1_500}},
            1_000, 1_500, 3_000,
        ),
        # DashScope (some regions) and Moonshot: flat cached_tokens.
        (
            {"prompt_tokens": 3_019, "completion_tokens": 104, "total_tokens": 3_123, "cached_tokens": 2_048},
            2_048, 0, 3_019,
        ),
        # OpenAI Responses-style usage.
        (
            {"input_tokens": 3_000, "output_tokens": 10, "total_tokens": 3_010,
             "input_tokens_details": {"cached_tokens": 2_048}},
            2_048, 0, 3_000,
        ),
    ],
    ids=["deepseek", "dashscope-explicit", "cache-write-tokens", "flat-cached-tokens", "responses-api"],
)
def test_cache_counters_inside_prompt_tokens(wire, read, write, prompt):
    usage = resolve_usage(normalize_usage(wire))

    assert usage.cache_reported is True
    assert (usage.cache_read, usage.cache_write) == (read, write)
    assert usage.prompt == prompt


def test_cache_larger_than_the_prompt_is_not_taken_out_of_it():
    usage = resolve_usage(normalize_usage(
        {"prompt_tokens": 1_000, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4_096}}
    ))

    assert usage.input == 1_000
    assert usage.prompt == 5_096


def test_anthropic_usage_reports_the_cache_on_top():
    usage = resolve_usage(normalize_usage({
        "input_tokens": 1_000,
        "output_tokens": 300,
        "cache_read_input_tokens": 50_000,
        "cache_creation_input_tokens": 2_000,
    }))

    assert (usage.input, usage.cache_read, usage.cache_write) == (1_000, 50_000, 2_000)
    assert usage.prompt == 53_000


def test_anthropic_usage_without_cache_counters_is_unknown():
    usage = normalize_usage({
        "input_tokens": 1_000,
        "output_tokens": 300,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
    })

    assert resolve_usage(usage).cache_reported is False


def test_gemini_usage_metadata():
    usage = resolve_usage(normalize_gemini_usage({
        "prompt_token_count": 12_000,
        "cached_content_token_count": 8_000,
        "candidates_token_count": 50,
        "thoughts_token_count": 400,
        "total_token_count": 12_450,
    }))

    assert usage == UsageBreakdown(
        input=4_000, output=50, reasoning=400, cache_read=8_000, cache_write=0, cache_reported=True
    )
    assert resolve_usage(normalize_gemini_usage({
        "prompt_token_count": 12_000,
        "candidates_token_count": 50,
        "total_token_count": 12_050,
    })).cache_reported is False


def test_only_a_litellm_shaped_guess_needs_the_request_check():
    litellm = normalize_usage({
        "prompt_tokens": 53_000,
        "completion_tokens": 300,
        "cache_read_input_tokens": 50_000,
        "prompt_tokens_details": {"cached_tokens": 50_000},
    })
    openai = normalize_usage({
        "prompt_tokens": 53_000,
        "completion_tokens": 300,
        "prompt_tokens_details": {"cached_tokens": 50_000},
    })

    assert needs_request_check(litellm)
    assert not needs_request_check(openai)
    assert settle_cache_in_prompt(openai, 1) is openai
    # A request as large as prompt_tokens plus the cache: prompt_tokens did
    # not count the cache.
    dropped = settle_cache_in_prompt(litellm, 75_000)
    assert dropped["cache_tokens_in_prompt"] == 0
    assert not needs_request_check(dropped)
    # Merged over the unchecked snapshot, the settled one still wins.
    assert resolve_usage({**litellm, **dropped}).prompt == 103_000
    assert resolve_usage(dropped).prompt == 103_000
    kept = settle_cache_in_prompt(litellm, 50_000)
    assert kept["cache_tokens_in_prompt"] == 50_000
    assert resolve_usage(kept).prompt == 53_000


def test_response_id_is_kept_for_gateway_lookups():
    usage = normalize_usage(CompletionUsage(**REPORT_SAMPLE), response_id="chatcmpl-abc")

    assert usage["response_id"] == "chatcmpl-abc"
    assert "response_id" not in normalize_usage(CompletionUsage(**REPORT_SAMPLE), response_id=None)


def test_usage_without_counts_is_none():
    assert normalize_usage(None) is None
    assert normalize_usage({}) is None
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}) is None


class TestCost:
    PRICE = PriceConfig(input=2.0, output=12.0, cache_read=0.2, unit=1_000_000, currency="CNY")

    def test_cache_hits_are_charged_the_cache_price(self):
        usage = _breakdown({**REPORT_SAMPLE, "prompt_tokens_details": {"cached_tokens": 8_192}})
        cost = CostCalculator.calculate(
            input_tokens=usage.prompt,
            output_tokens=usage.output,
            cached_tokens=usage.cache_read,
            cache_write_tokens=usage.cache_write,
            pricing=self.PRICE,
        )

        # 1,808 uncached at 2.0, 8,192 cached at 0.2, 16 output at 12.0 (per million)
        assert cost.input_cost == pytest.approx(1_808 * 2.0 / 1e6)
        assert cost.cache_cost == pytest.approx(8_192 * 0.2 / 1e6)
        assert cost.total_cost == pytest.approx((1_808 * 2.0 + 8_192 * 0.2 + 16 * 12.0) / 1e6)

    def test_missing_cache_price_is_not_a_free_cache(self):
        price = PriceConfig(input=2.0, output=12.0, unit=1_000_000)
        cost = CostCalculator.calculate(
            input_tokens=10_000, output_tokens=0, cached_tokens=8_192, cache_write_tokens=500, pricing=price
        )

        assert cost.total_cost == pytest.approx(10_000 * 2.0 / 1e6)

    def test_reasoning_is_charged_as_output(self):
        cost = CostCalculator.calculate(
            input_tokens=0, output_tokens=6, reasoning_tokens=318, pricing=self.PRICE
        )

        assert cost.output_cost == pytest.approx(324 * 12.0 / 1e6)
