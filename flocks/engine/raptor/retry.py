"""
Raptor retry helpers: provider fallback chain and error classification.

The base ``SessionRunner._process_step()`` already handles same-provider retries
for transient API errors (429 / 500) with exponential backoff (up to 7 attempts).
This module adds two complementary recovery strategies used only by the raptor
engine:

1. **Provider fallback chain** – when the primary provider exhausts its retry
   budget, switch to a configured fallback provider/model pair and restart the
   LLM call on that new endpoint.

2. **Context-overflow detection** – distinguish "context too long" (400) errors
   from generic 400s so the caller can trigger compaction before retrying.

Configuration (environment variables)
--------------------------------------
``RAPTOR_FALLBACK_PROVIDERS``
    Comma-separated list of ``provider_id:model_id`` pairs, tried in order
    when the primary provider fails repeatedly.  Example::

        RAPTOR_FALLBACK_PROVIDERS=openai:gpt-4o,deepseek:deepseek-chat

``RAPTOR_TOOL_FOLD_THRESHOLD``
    Override the default (30) tool-count threshold above which tool folding
    activates.  See ``tool_fold.py``.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.retry")


# ---------------------------------------------------------------------------
# Provider fallback chain
# ---------------------------------------------------------------------------

def _parse_fallback_chain() -> List[Tuple[str, str]]:
    """Parse ``RAPTOR_FALLBACK_PROVIDERS`` into ``[(provider_id, model_id), ...]``."""
    raw = os.environ.get("RAPTOR_FALLBACK_PROVIDERS", "").strip()
    if not raw:
        return []
    chain: List[Tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            provider, model = entry.split(":", 1)
            chain.append((provider.strip(), model.strip()))
        else:
            # Provider-only entry — model resolved at call time.
            chain.append((entry, ""))
    return chain


def get_fallback_chain(
    primary_provider: str,
    primary_model: str,
) -> List[Tuple[str, str]]:
    """Return an ordered fallback list, *excluding* the primary pair.

    Each entry is ``(provider_id, model_id)``.  ``model_id`` may be an empty
    string when the config omits it; callers should substitute the primary
    model in that case.
    """
    chain = _parse_fallback_chain()
    return [
        (p, m or primary_model)
        for p, m in chain
        if p != primary_provider or (m and m != primary_model)
    ]


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def looks_like_rate_limit(message: str) -> bool:
    """Return ``True`` when an error message represents rate limiting."""
    msg = message.lower()
    return any(
        kw in msg
        for kw in (
            "rate limit",
            "rate_limit",
            "ratelimit",
            "quota exceeded",
            "billing",
            "too many requests",
            "429",
        )
    )


def looks_like_context_overflow(message: str) -> bool:
    """Return ``True`` when an error message represents context overflow."""
    msg = message.lower()
    return any(
        kw in msg
        for kw in (
            "context length",
            "context_length",
            "context window",
            "token limit",
            "maximum context",
            "max_tokens",
            "input length",
            "request too large",
            "too many tokens",
            "prompt too long",
            "input is too long",
            "maximum token",
        )
    )


# ---------------------------------------------------------------------------
# Retry-state carrier
# ---------------------------------------------------------------------------

class RaptorRetryContext:
    """Mutable state bag threaded through one step's retry loop."""

    def __init__(
        self,
        primary_provider: str,
        primary_model: str,
    ) -> None:
        self.primary_provider = primary_provider
        self.primary_model = primary_model
        self._fallback_chain: Optional[List[Tuple[str, str]]] = None
        self._fallback_index: int = 0
        self.current_provider: str = primary_provider
        self.current_model: str = primary_model

    # ------------------------------------------------------------------
    @property
    def fallback_chain(self) -> List[Tuple[str, str]]:
        if self._fallback_chain is None:
            self._fallback_chain = get_fallback_chain(
                self.primary_provider, self.primary_model
            )
        return self._fallback_chain

    def try_next_fallback(self) -> bool:
        """Advance to the next fallback entry.  Return ``False`` when exhausted."""
        chain = self.fallback_chain
        if self._fallback_index >= len(chain):
            return False
        provider, model = chain[self._fallback_index]
        self._fallback_index += 1
        self.current_provider = provider
        self.current_model = model
        log.warn("raptor.retry.fallback_switch", {
            "from_provider": self.primary_provider,
            "to_provider": provider,
            "to_model": model,
            "fallback_index": self._fallback_index,
        })
        return True

