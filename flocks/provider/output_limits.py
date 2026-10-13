"""Output-token limits raised at runtime for models whose limit is too low.

A model's configured ``max_output_tokens`` is often a guess: a custom model the
catalogs do not know is saved with an 8192 fallback, and a reasoning model
spends part of that budget before it writes anything.  A large ``write`` then
gets cut off partway through its arguments, and resending the same call hits
the same limit at the same place.  So after such a cut-off, later requests for
that model ask for more: double the limit that was hit, up to the global
``OUTPUT_TOKEN_MAX`` and the room the context window leaves above the overflow
threshold.  A provider that rejects the raised value pins the model back to
its configured limit for the rest of the process.
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

_Key = Tuple[str, str]

_raised: Dict[_Key, int] = {}
# Configured limits seen by ``effective_max_tokens``, i.e. models whose
# requests actually pick up a raised limit.
_configured: Dict[_Key, int] = {}
# Every raised value handed out per model.  A request carries one of these
# (or the configured limit) only when its limit came from this module; a
# provider branch that sets its own ``max_tokens`` never matches.
_raised_values: Dict[_Key, Set[int]] = {}
_rejected: Set[_Key] = set()

# Words providers use when refusing an output-token limit, or the prompt plus
# that limit, as too large.
_REJECTION_MARKERS = (
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
    "maxoutputtokens",
    "max tokens",
    "output tokens",
    "completion tokens",
    "maximum context",
    "context length",
    "context_length_exceeded",
    "context window",
)
_REJECTION_STATUSES = {400, 413, 422}
_RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "per minute", "too many requests")


class OutputLimitRejectedError(RuntimeError):
    """The provider refused a raised output limit; retry with the configured one."""


def _ceiling() -> int:
    from flocks.session.prompt import OUTPUT_TOKEN_MAX

    return OUTPUT_TOKEN_MAX


def effective_max_tokens(
    provider_id: str,
    model_id: str,
    configured: Optional[int],
) -> Optional[int]:
    """The ``max_tokens`` to send: the configured limit, or a raised one."""
    key = (provider_id, model_id)
    if configured:
        _configured[key] = configured
    raised = _raised.get(key)
    if configured and raised and raised > configured and key not in _rejected:
        return raised
    return configured


def next_max_tokens(
    provider_id: str,
    model_id: str,
    sent: Optional[int],
    *,
    output_room: Optional[int] = None,
) -> Optional[int]:
    """The limit to use after a response was cut off at ``sent``.

    ``output_room`` is what the context window leaves above the overflow
    threshold; the prompt may grow to that threshold, so asking for more
    output could push prompt plus output past the window.

    None when there is nothing to raise: no limit was sent (the cut came from
    somewhere else), the request's limit does not come from the model config
    that picks up raised limits, it is already at the ceiling, or the
    provider refused a raised limit for this model before.
    """
    key = (provider_id, model_id)
    if not sent or sent <= 0 or key in _rejected:
        return None
    if sent != _configured.get(key) and sent not in _raised_values.get(key, ()):
        return None
    ceiling = _ceiling()
    if output_room is not None and output_room > 0:
        ceiling = min(ceiling, output_room)
    raised = min(ceiling, sent * 2)
    return raised if raised > sent else None


def raise_max_tokens(provider_id: str, model_id: str, limit: int) -> None:
    key = (provider_id, model_id)
    _raised[key] = max(_raised.get(key, 0), limit)
    _raised_values.setdefault(key, set()).add(limit)


def is_raised(provider_id: str, model_id: str, sent: Optional[int]) -> bool:
    """Whether ``sent`` is a limit raised by this module, not a configured one.

    Membership rather than "equals the current raise": another session may
    have raised the limit further while this request was in flight.
    """
    key = (provider_id, model_id)
    return bool(sent) and sent in _raised_values.get(key, ()) and key not in _rejected


def reject_raised(provider_id: str, model_id: str) -> None:
    key = (provider_id, model_id)
    _raised.pop(key, None)
    _rejected.add(key)


def _status_code(exc: BaseException) -> Optional[int]:
    for value in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def looks_like_limit_rejection(exc: BaseException) -> bool:
    """Whether a request error can be the provider refusing a raised limit.

    Only asked when a raised limit was sent and nothing came back, so a plain
    400 counts too: many gateways reject an oversized ``max_tokens`` (or
    prompt plus ``max_tokens``) with a bare "Bad Request".  Rate limits never
    count, even when their text mentions output tokens.
    """
    rejected = False
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = _status_code(current)
        text = str(current).lower()
        if status == 429 or any(marker in text for marker in _RATE_LIMIT_MARKERS):
            return False
        if status in _REJECTION_STATUSES or any(marker in text for marker in _REJECTION_MARKERS):
            rejected = True
        current = current.__cause__ or current.__context__
    return rejected


def reset() -> None:
    """Forget all raised, configured and rejected limits."""
    _raised.clear()
    _configured.clear()
    _raised_values.clear()
    _rejected.clear()

