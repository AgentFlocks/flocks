"""Token usage of a model call, read the same way for every provider.

Providers report one call in different shapes.  OpenAI counts cached prompt
tokens inside ``prompt_tokens`` and lists them in
``prompt_tokens_details.cached_tokens``; DashScope, Kimi, GLM, Volcengine and
the ThreatBook gateway follow it.  DeepSeek's own API reports
``prompt_cache_hit_tokens``.  Anthropic reports ``cache_read_input_tokens`` /
``cache_creation_input_tokens`` on top of an uncached ``input_tokens``.
LiteLLM counts the cache into ``prompt_tokens`` and repeats it in those
Anthropic-named fields.

``normalize_usage`` reads all of them into the dict providers put on
``StreamChunk.usage``:

* ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` as the provider
  sent them, except that reasoning a provider counts inside
  ``completion_tokens`` is moved to ``reasoning_tokens``;
* ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` only when the
  provider reported cache usage.  A usage without them says nothing about the
  cache, which is not the same as a cache miss;
* ``cache_tokens_in_prompt``: how many of those cache tokens ``prompt_tokens``
  already counts (absent or 0: none, Anthropic's way);
* ``cache_in_prompt_unconfirmed``: ``cache_tokens_in_prompt`` was inferred from
  LiteLLM's shape, which a gateway passing Anthropic's own numbers also has.
  ``settle_cache_in_prompt`` checks it against the size of the request;
* ``response_id``: the provider's id for the response, to find the call in
  gateway logs.

``resolve_usage`` turns that dict into one breakdown (uncached input, cache
reads, cache writes, output, reasoning) that message tokens, usage records,
cost and the context meter all use, so the cache is counted once everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# A usage marked as counting the cache into prompt_tokens on LiteLLM's shape
# alone is only trusted when the request estimate is below this share of
# prompt_tokens plus the cache.  The estimate runs low on real tokenizers
# (code, JSON, blocks it leaves out), and trusting an Anthropic-style report
# would under-count.
CACHE_IN_PROMPT_REQUEST_RATIO = 0.7

CACHE_READ_KEY = "cache_read_input_tokens"
CACHE_WRITE_KEY = "cache_creation_input_tokens"
CACHE_IN_PROMPT_KEY = "cache_tokens_in_prompt"
CACHE_UNCONFIRMED_KEY = "cache_in_prompt_unconfirmed"
RESPONSE_ID_KEY = "response_id"

# Cache counters that providers count inside prompt_tokens, by precedence.
_READ_IN_PROMPT = (
    ("prompt_tokens_details", "cached_tokens"),  # OpenAI and its followers
    ("input_tokens_details", "cached_tokens"),  # OpenAI Responses-style usage
    (None, "prompt_cache_hit_tokens"),  # DeepSeek
    (None, "cached_tokens"),  # DashScope (some regions), Moonshot
)
_WRITE_IN_PROMPT = (
    ("prompt_tokens_details", "cache_creation_input_tokens"),  # DashScope explicit cache
    ("prompt_tokens_details", "cache_creation_tokens"),  # LiteLLM
    ("prompt_tokens_details", "cache_write_tokens"),  # Kimi, OpenRouter
    ("input_tokens_details", "cache_creation_input_tokens"),
    ("input_tokens_details", "cache_write_tokens"),
)


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(value: Any) -> Optional[int]:
    """A token count, or None when the value is missing or not a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _first_count(raw: Any, paths: tuple) -> Optional[int]:
    """The first counter of ``paths`` that the usage carries."""
    for parent, name in paths:
        holder = _field(raw, parent) if parent else raw
        value = _count(_field(holder, name))
        if value is not None:
            return value
    return None


def normalize_usage(raw_usage: Any, *, response_id: Any = None) -> Optional[Dict[str, Any]]:
    """Read an OpenAI-, DeepSeek-, LiteLLM- or Anthropic-shaped usage.

    Accepts SDK objects and plain dicts.  Returns None when the usage carries
    no counts at all.
    """
    if not raw_usage:
        return None

    prompt_tokens = _first_count(raw_usage, ((None, "prompt_tokens"), (None, "input_tokens"))) or 0
    completion_tokens = (
        _first_count(raw_usage, ((None, "completion_tokens"), (None, "output_tokens"))) or 0
    )
    nested_reasoning_tokens = (
        _count(_field(_field(raw_usage, "completion_tokens_details"), "reasoning_tokens"))
        or _count(_field(_field(raw_usage, "output_tokens_details"), "reasoning_tokens"))
        or 0
    )
    reasoning_tokens = _count(_field(raw_usage, "reasoning_tokens")) or nested_reasoning_tokens
    if nested_reasoning_tokens:
        # OpenAI reports reasoning_tokens as a breakdown already included in
        # completion_tokens / output_tokens. Flocks stores visible output and
        # reasoning separately, so remove that nested subset.
        completion_tokens = max(0, completion_tokens - nested_reasoning_tokens)
    total_tokens = _count(_field(raw_usage, "total_tokens")) or (
        prompt_tokens + completion_tokens + reasoning_tokens
    )

    top_read = _count(_field(raw_usage, CACHE_READ_KEY))
    top_write = _count(_field(raw_usage, CACHE_WRITE_KEY))
    nested_read = _first_count(raw_usage, _READ_IN_PROMPT)
    nested_write = _first_count(raw_usage, _WRITE_IN_PROMPT)
    cache_reported = any(
        value is not None for value in (top_read, top_write, nested_read, nested_write)
    )

    cache_read = cache_write = in_prompt = 0
    unconfirmed = False
    if top_read or top_write:
        # Anthropic-named counters, reported on top of prompt_tokens.  LiteLLM
        # counts them into prompt_tokens instead, and its Usage mirrors them
        # in prompt_tokens_details; a gateway passing Anthropic's own numbers
        # through LiteLLM looks the same, so the runner checks the guess
        # against the request it sent (settle_cache_in_prompt).
        cache_read, cache_write = top_read or 0, top_write or 0
        details_read = _first_count(raw_usage, _READ_IN_PROMPT[:1])
        details_write = _first_count(
            raw_usage, tuple(p for p in _WRITE_IN_PROMPT if p[0] == "prompt_tokens_details")
        )
        mirrored = 0
        if cache_read and details_read == cache_read:
            mirrored += cache_read
        if cache_write and details_write == cache_write:
            mirrored += cache_write
        # A prompt smaller than its cached part cannot contain it.
        if mirrored and prompt_tokens >= mirrored:
            in_prompt, unconfirmed = mirrored, True
    elif cache_reported:
        cache_read = nested_read or 0
        cache_write = nested_write or 0
        if prompt_tokens >= cache_read + cache_write:
            in_prompt = cache_read + cache_write

    if not any(
        [prompt_tokens, completion_tokens, reasoning_tokens, total_tokens, cache_read, cache_write]
    ):
        return None

    usage: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if reasoning_tokens:
        usage["reasoning_tokens"] = reasoning_tokens
    if cache_reported:
        usage[CACHE_READ_KEY] = cache_read
        usage[CACHE_WRITE_KEY] = cache_write
        if in_prompt:
            usage[CACHE_IN_PROMPT_KEY] = in_prompt
            if unconfirmed:
                usage[CACHE_UNCONFIRMED_KEY] = True
    if isinstance(response_id, str) and response_id:
        usage[RESPONSE_ID_KEY] = response_id
    return usage


def normalize_gemini_usage(metadata: Any, *, response_id: Any = None) -> Optional[Dict[str, Any]]:
    """Read Gemini ``usage_metadata``.

    ``prompt_token_count`` includes ``cached_content_token_count``, and
    thinking is reported apart from the answer in ``thoughts_token_count``.
    """
    if not metadata:
        return None
    raw: Dict[str, Any] = {
        "prompt_tokens": _count(_field(metadata, "prompt_token_count")),
        "completion_tokens": _count(_field(metadata, "candidates_token_count")),
        "reasoning_tokens": _count(_field(metadata, "thoughts_token_count")),
        "total_tokens": _count(_field(metadata, "total_token_count")),
    }
    cached = _count(_field(metadata, "cached_content_token_count"))
    if cached is not None:
        raw["prompt_tokens_details"] = {"cached_tokens": cached}
    return normalize_usage(raw, response_id=response_id)


def needs_request_check(usage: Optional[Mapping[str, Any]]) -> bool:
    """Whether the cache-in-prompt guess still has to be checked."""
    return bool(usage and usage.get(CACHE_UNCONFIRMED_KEY) and usage.get(CACHE_IN_PROMPT_KEY))


def settle_cache_in_prompt(
    usage: Optional[Dict[str, Any]],
    request_tokens: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Confirm or drop an unconfirmed cache-in-prompt guess.

    ``request_tokens`` is an estimate of the request that produced the usage.
    The cache is taken out of prompt_tokens only when that request is clearly
    smaller than prompt_tokens plus the cache: taking it out of an
    Anthropic-style prompt_tokens would under-count, and a prompt that runs
    past the window is not recovered from, while counting the cache twice only
    compacts early.  Without an estimate the guess stands.  Returns the usage
    unchanged when there is nothing to settle.

    A dropped guess is set to 0 rather than removed, so it also replaces the
    guess in usage snapshots merged into one another.
    """
    if not needs_request_check(usage):
        return usage
    settled = dict(usage)
    settled[CACHE_UNCONFIRMED_KEY] = False
    if request_tokens:
        prompt_tokens = _count(usage.get("prompt_tokens")) or 0
        cached = (_count(usage.get(CACHE_READ_KEY)) or 0) + (_count(usage.get(CACHE_WRITE_KEY)) or 0)
        if request_tokens >= CACHE_IN_PROMPT_REQUEST_RATIO * (prompt_tokens + cached):
            settled[CACHE_IN_PROMPT_KEY] = 0
    return settled


@dataclass(frozen=True)
class UsageBreakdown:
    """One call's tokens, every part counted once."""

    input: int = 0
    """Prompt tokens neither read from nor written to the cache."""
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_reported: bool = False
    """Whether the provider reported cache usage; without it the cache
    counts are unknown, not zero."""

    @property
    def prompt(self) -> int:
        """The whole prompt: uncached input plus cache reads and writes."""
        return self.input + self.cache_read + self.cache_write


def resolve_usage(usage: Optional[Mapping[str, Any]]) -> UsageBreakdown:
    """Break a usage dict (``normalize_usage`` shape) into its parts.

    An unsettled cache-in-prompt guess is taken as made.
    """
    if not usage:
        return UsageBreakdown()
    prompt_tokens = _count(usage.get("prompt_tokens")) or 0
    cache_read = _count(usage.get(CACHE_READ_KEY)) or 0
    cache_write = _count(usage.get(CACHE_WRITE_KEY)) or 0
    in_prompt = min(_count(usage.get(CACHE_IN_PROMPT_KEY)) or 0, cache_read + cache_write)
    return UsageBreakdown(
        input=max(0, prompt_tokens - in_prompt),
        output=_count(usage.get("completion_tokens")) or 0,
        reasoning=_count(usage.get("reasoning_tokens")) or 0,
        cache_read=cache_read,
        cache_write=cache_write,
        cache_reported=(
            usage.get(CACHE_READ_KEY) is not None or usage.get(CACHE_WRITE_KEY) is not None
        ),
    )
