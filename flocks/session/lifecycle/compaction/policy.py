"""Compaction Policy — dynamic threshold computation from model parameters.

Determines context overflow thresholds, pruning limits, and summary budgets
based on model context window size and tier classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any

from flocks.utils.log import Log

_policy_log = Log.create(service="session.compaction_policy")


# ============================================================================
# Context Tier
# ============================================================================

class ContextTier(str, Enum):
    """Model context size tier, used to apply tier-specific ratio adjustments."""
    SMALL = "small"      # usable < 12K
    MEDIUM = "medium"    # 12K ~ 100K
    LARGE = "large"      # 100K ~ 500K
    XLARGE = "xlarge"    # > 500K


# ============================================================================
# Policy ratio tables
# ============================================================================

_DEFAULT_RATIOS: Dict[str, float] = {
    "prune_protect_ratio": 0.25,
    "prune_minimum_ratio": 0.15,
    "flush_trigger_ratio": 0.03,
    "flush_reserve_ratio": 0.015,
    "summary_ratio": 0.04,
    "overflow_ratio": 0.85,
    "overflow_buffer_ratio": 0.05,
}

_TIER_OVERRIDES: Dict[ContextTier, Dict[str, float]] = {
    ContextTier.SMALL: {
        "prune_protect_ratio": 0.20,
        "prune_minimum_ratio": 0.12,
        "flush_trigger_ratio": 0.05,
        "flush_reserve_ratio": 0.025,
        "summary_ratio": 0.06,
        "overflow_ratio": 0.80,
        "overflow_buffer_ratio": 0.08,
    },
    ContextTier.MEDIUM: {},
    ContextTier.LARGE: {
        "flush_trigger_ratio": 0.02,
        "flush_reserve_ratio": 0.01,
        "summary_ratio": 0.05,
        "overflow_ratio": 0.87,
        "overflow_buffer_ratio": 0.04,
    },
    ContextTier.XLARGE: {
        "prune_protect_ratio": 0.20,
        "prune_minimum_ratio": 0.10,
        "flush_trigger_ratio": 0.015,
        "flush_reserve_ratio": 0.008,
        "summary_ratio": 0.05,
        "overflow_ratio": 0.90,
        "overflow_buffer_ratio": 0.03,
    },
}

_TIER_PRESERVE_LAST: Dict[ContextTier, int] = {
    ContextTier.SMALL: 2,
    ContextTier.MEDIUM: 4,
    ContextTier.LARGE: 6,
    ContextTier.XLARGE: 8,
}

_BOUNDS: Dict[str, tuple[int, int]] = {
    "prune_protect":      (4_000,  120_000),
    "prune_minimum":      (2_000,   60_000),
    "flush_trigger":      (1_000,   20_000),
    "flush_reserve":      (  500,   10_000),
    "summary_max_tokens": (1_000,   16_000),
    "overflow_buffer":    (2_000,   32_000),
}

# Minimum overflow threshold for models with sufficient context window.
_MIN_OVERFLOW_THRESHOLD = 100_000


# ============================================================================
# CompactionPolicy
# ============================================================================

@dataclass(frozen=True)
class CompactionPolicy:
    """
    Immutable set of compaction thresholds derived from a model's context
    window and output token limit.

    All values are integers (token counts) except ``overflow_ratio`` which is
    kept as a float so callers can apply it to varying token counts.
    """

    context_window: int
    max_output_tokens: int
    usable_context: int
    tier: ContextTier

    prune_protect: int
    """Tokens worth of recent tool-call outputs to keep intact."""

    prune_minimum: int
    """Minimum prunable tokens required before pruning is worthwhile."""

    flush_trigger: int
    """Token buffer before compaction at which memory flush triggers."""

    flush_reserve: int
    """Tokens reserved for the flush operation itself."""

    summary_max_tokens: int
    """Max tokens for the LLM-generated summary during compaction."""

    overflow_threshold: int
    """Absolute token count that triggers context overflow."""

    overflow_buffer: int
    """Reserved headroom used for preemptive cleanup before hard overflow."""

    preemptive_threshold: int
    """Soft threshold that triggers cheap cleanup before full compaction."""

    preserve_last: int
    """Number of recent messages to always preserve during truncation."""

    # --- Factory methods -----------------------------------------------------

    @classmethod
    def from_model(
        cls,
        context_window: int,
        max_output_tokens: int = 4096,
        max_input_tokens: Optional[int] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> CompactionPolicy:
        """Build a policy from model parameters."""
        overrides = overrides or {}

        if max_output_tokens >= context_window:
            _policy_log.warn("compaction_policy.capping_output_tokens", {
                "context_window": context_window,
                "max_output_tokens_original": max_output_tokens,
                "max_output_tokens_capped": context_window // 4,
            })
            max_output_tokens = context_window // 4

        if max_input_tokens and max_input_tokens > 0:
            usable = max_input_tokens
        else:
            usable = max(0, context_window - max_output_tokens)
        if usable == 0:
            _policy_log.warn("compaction_policy.zero_usable", {
                "context_window": context_window,
                "max_output_tokens": max_output_tokens,
            })

        tier = cls._classify_tier(usable)

        ratios = {**_DEFAULT_RATIOS, **_TIER_OVERRIDES.get(tier, {})}
        for key in list(ratios.keys()):
            if key in overrides:
                ratios[key] = float(overrides[key])

        raw = {
            "prune_protect":      usable * ratios["prune_protect_ratio"],
            "prune_minimum":      usable * ratios["prune_minimum_ratio"],
            "flush_trigger":      usable * ratios["flush_trigger_ratio"],
            "flush_reserve":      usable * ratios["flush_reserve_ratio"],
            "summary_max_tokens": usable * ratios["summary_ratio"],
            "overflow_buffer":    usable * ratios["overflow_buffer_ratio"],
        }

        clamped: Dict[str, int] = {}
        for key, value in raw.items():
            lo, hi = _BOUNDS[key]
            effective_lo = min(lo, int(value)) if value > 0 else lo
            clamped[key] = int(max(effective_lo, min(hi, value)))

        overflow_ratio = ratios["overflow_ratio"]
        overflow_threshold = int(usable * overflow_ratio)
        if usable >= _MIN_OVERFLOW_THRESHOLD:
            overflow_threshold = max(_MIN_OVERFLOW_THRESHOLD, overflow_threshold)
        overflow_buffer = clamped["overflow_buffer"]
        preemptive_threshold = max(0, overflow_threshold - overflow_buffer)

        preserve_last = _TIER_PRESERVE_LAST.get(tier, 4)

        for key in clamped:
            if key in overrides and not key.endswith("_ratio"):
                clamped[key] = int(overrides[key])
        if "overflow_threshold" in overrides:
            overflow_threshold = int(overrides["overflow_threshold"])
        if "overflow_buffer" in overrides:
            overflow_buffer = int(overrides["overflow_buffer"])
        if "preemptive_threshold" in overrides:
            preemptive_threshold = int(overrides["preemptive_threshold"])
        else:
            preemptive_threshold = max(0, overflow_threshold - overflow_buffer)
        if "preserve_last" in overrides:
            preserve_last = int(overrides["preserve_last"])

        policy = cls(
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            usable_context=usable,
            tier=tier,
            prune_protect=clamped["prune_protect"],
            prune_minimum=clamped["prune_minimum"],
            flush_trigger=clamped["flush_trigger"],
            flush_reserve=clamped["flush_reserve"],
            summary_max_tokens=clamped["summary_max_tokens"],
            overflow_threshold=overflow_threshold,
            overflow_buffer=overflow_buffer,
            preemptive_threshold=preemptive_threshold,
            preserve_last=preserve_last,
        )

        _policy_log.info("compaction_policy.created", {
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "usable_context": usable,
            "tier": tier.value,
            "prune_protect": policy.prune_protect,
            "prune_minimum": policy.prune_minimum,
            "flush_trigger": policy.flush_trigger,
            "flush_reserve": policy.flush_reserve,
            "summary_max_tokens": policy.summary_max_tokens,
            "overflow_threshold": policy.overflow_threshold,
            "overflow_buffer": policy.overflow_buffer,
            "preemptive_threshold": policy.preemptive_threshold,
            "preserve_last": policy.preserve_last,
        })

        return policy

    @classmethod
    def from_model_definition(
        cls,
        model_def: Any,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> CompactionPolicy:
        """Build a policy from a ``ModelDefinition`` instance (duck-typed)."""
        limits = getattr(model_def, "limits", None)
        if limits is None:
            return cls.from_model(128000, 4096, overrides=overrides)

        context_window = getattr(limits, "context_window", 128000)
        max_output_tokens = getattr(limits, "max_output_tokens", 4096)
        return cls.from_model(context_window, max_output_tokens, overrides=overrides)

    @classmethod
    def default(cls) -> CompactionPolicy:
        """Return a policy with the legacy hardcoded values."""
        return cls(
            context_window=128000,
            max_output_tokens=4096,
            usable_context=123904,
            tier=ContextTier.MEDIUM,
            prune_protect=40_000,
            prune_minimum=20_000,
            flush_trigger=4_000,
            flush_reserve=2_000,
            summary_max_tokens=4_000,
            overflow_threshold=int(123904 * 0.85),
            overflow_buffer=8_192,
            preemptive_threshold=max(0, int(123904 * 0.85) - 8_192),
            preserve_last=4,
        )

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _classify_tier(usable_context: int) -> ContextTier:
        if usable_context < 12_000:
            return ContextTier.SMALL
        if usable_context <= 100_000:
            return ContextTier.MEDIUM
        if usable_context <= 500_000:
            return ContextTier.LARGE
        return ContextTier.XLARGE

    def describe(self) -> Dict[str, Any]:
        """Return a human-readable dict of the policy for logging / debug."""
        return {
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "usable_context": self.usable_context,
            "tier": self.tier.value,
            "prune_protect": self.prune_protect,
            "prune_minimum": self.prune_minimum,
            "flush_trigger": self.flush_trigger,
            "flush_reserve": self.flush_reserve,
            "summary_max_tokens": self.summary_max_tokens,
            "overflow_threshold": self.overflow_threshold,
            "preserve_last": self.preserve_last,
        }
