"""Output-token limits raised after a response is cut off mid tool call."""

from unittest.mock import MagicMock

import httpx
import openai

from flocks.provider import output_limits
from flocks.provider.options import build_provider_options


def _status_error(cls, status: int, message: str):
    request = httpx.Request("POST", "https://gateway.test/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


def test_cut_off_doubles_the_limit_up_to_the_global_cap(monkeypatch):
    monkeypatch.setattr("flocks.session.prompt.OUTPUT_TOKEN_MAX", 32_000)
    output_limits.effective_max_tokens("gw", "astra", 8192)
    output_limits.effective_max_tokens("gw", "big", 20_000)
    output_limits.effective_max_tokens("gw", "max", 32_000)

    assert output_limits.next_max_tokens("gw", "astra", 8192) == 16_384
    assert output_limits.next_max_tokens("gw", "big", 20_000) == 32_000
    # Already at the cap, or no limit was sent (the cut came from elsewhere).
    assert output_limits.next_max_tokens("gw", "max", 32_000) is None
    assert output_limits.next_max_tokens("gw", "astra", None) is None


def test_raise_stays_inside_the_room_the_window_leaves():
    """The overflow check lets the prompt reach 85% of the window, so the
    output limit may not take more than the remaining 15%."""
    output_limits.effective_max_tokens("gw", "astra", 8192)

    # 128K window: 128,000 - 108,800 = 19,200.
    assert output_limits.next_max_tokens("gw", "astra", 8192, output_room=19_200) == 16_384
    output_limits.raise_max_tokens("gw", "astra", 16_384)
    assert output_limits.next_max_tokens("gw", "astra", 16_384, output_room=19_200) == 19_200
    output_limits.raise_max_tokens("gw", "astra", 19_200)
    assert output_limits.next_max_tokens("gw", "astra", 19_200, output_room=19_200) is None
    # A 40K window leaves 6,000: an 8192 limit cannot grow at all.
    assert output_limits.next_max_tokens("gw", "astra", 8192, output_room=6_000) is None


def test_limits_not_from_the_model_config_are_never_raised():
    # e.g. the Anthropic thinking branch sets its own max_tokens, which never
    # picks up a raised value, so the model must not be told it was raised.
    assert output_limits.next_max_tokens("anthropic", "claude-x", 24_192) is None
    # Also after the same model once went through the config path (reasoning
    # off), then switched to thinking with its own limit.
    output_limits.effective_max_tokens("anthropic", "claude-x", 8192)
    assert output_limits.next_max_tokens("anthropic", "claude-x", 24_192) is None
    assert not output_limits.is_raised("anthropic", "claude-x", 24_192)


def test_raised_limit_applies_to_later_requests_of_that_model_only():
    output_limits.effective_max_tokens("gw", "astra", 8192)
    output_limits.raise_max_tokens("gw", "astra", 16_384)

    assert output_limits.effective_max_tokens("gw", "astra", 8192) == 16_384
    assert output_limits.effective_max_tokens("gw", "other", 8192) == 8192
    # A configured limit that is already higher wins.
    assert output_limits.effective_max_tokens("gw", "astra", 64_000) == 64_000


def test_every_limit_handed_out_counts_as_raised():
    """Another session may have raised the limit further while this request
    was in flight; its rejection still has to be recognised."""
    output_limits.effective_max_tokens("gw", "astra", 8192)
    output_limits.raise_max_tokens("gw", "astra", 32_000)

    output_limits.raise_max_tokens("gw", "astra", 16_384)

    assert output_limits.is_raised("gw", "astra", 16_384)
    assert output_limits.is_raised("gw", "astra", 32_000)
    assert not output_limits.is_raised("gw", "astra", 8192)


def test_rejected_raise_pins_the_model_to_its_configured_limit():
    output_limits.effective_max_tokens("gw", "astra", 8192)
    output_limits.raise_max_tokens("gw", "astra", 16_384)
    output_limits.reject_raised("gw", "astra")

    assert output_limits.effective_max_tokens("gw", "astra", 8192) == 8192
    assert not output_limits.is_raised("gw", "astra", 16_384)
    assert output_limits.next_max_tokens("gw", "astra", 8192) is None


def test_limit_rejection_is_recognised_through_wrapping_errors():
    wrapped = RuntimeError("stream failed")
    wrapped.__cause__ = _status_error(
        openai.BadRequestError,
        400,
        "max_tokens is too large: 16384. This model supports at most 8192 completion tokens",
    )

    assert output_limits.looks_like_limit_rejection(wrapped)
    assert output_limits.looks_like_limit_rejection(
        RuntimeError("This model's maximum context length is 128000 tokens")
    )
    # Many gateways answer an oversized max_tokens with a bare 400.
    assert output_limits.looks_like_limit_rejection(
        _status_error(openai.BadRequestError, 400, "Bad Request")
    )
    assert not output_limits.looks_like_limit_rejection(RuntimeError("invalid api key"))


def test_rate_limits_are_not_limit_rejections():
    assert not output_limits.looks_like_limit_rejection(
        _status_error(
            openai.RateLimitError,
            429,
            "This request would exceed the rate limit of 8,000 output tokens per minute",
        )
    )
    assert not output_limits.looks_like_limit_rejection(
        RuntimeError("Rate limit reached for max_tokens on requests per minute")
    )


def test_provider_options_send_the_raised_limit(monkeypatch):
    model = MagicMock()
    model.id = "astra"
    model.capabilities.max_tokens = 8192
    provider = MagicMock()
    provider._config_models = [model]
    monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda _pid: provider)
    monkeypatch.setattr(
        "flocks.provider.options._resolve_reasoning_transport",
        lambda *_args: None,
    )

    assert build_provider_options("gw", "astra")["max_tokens"] == 8192

    output_limits.raise_max_tokens("gw", "astra", 16_384)

    assert build_provider_options("gw", "astra")["max_tokens"] == 16_384
