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
# v2 anomaly detection thresholds (B1) — see docs/design/context-compaction-v2.md
# ============================================================================

# When ``max_output_tokens`` claims more than this fraction of the context
# window, treat the provider metadata as suspicious (e.g. GLM-5.1 reports
# ``max_output_tokens=168000`` against ``context_window=198000``, which
# would leave only 30K of usable input — the model can in fact accept far
# more) and cap the value to a sane default.  The threshold sits below
# the legacy ``>=`` comparison so anomalies are caught without disturbing
# normal models like Claude / GPT-4 whose output ratios stay well under 0.5.
MAX_OUTPUT_RATIO_THRESHOLD = 0.7

# After the anomaly is detected we cap ``max_output_tokens`` to this share
# of the context window.  Picked to mirror typical generation budgets
# (~25%) so the resulting ``usable_context`` covers the entire prompt
# half of the window.
SAFE_OUTPUT_RATIO = 0.25


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

# Note: ``overflow_ratio`` used to live here for tier-aware overflow
# thresholds.  We now compute ``overflow_threshold`` directly from the
# raw context window (``int(context_window * 0.85)``, mirroring
# hermes-agent's gateway), so the per-tier overrides only carry the
# fields that still drive computation.
_DEFAULT_RATIOS: Dict[str, float] = {
    "prune_protect_ratio": 0.25,
    "prune_minimum_ratio": 0.15,
    "flush_trigger_ratio": 0.03,
    "flush_reserve_ratio": 0.015,
    "summary_ratio": 0.04,
    "overflow_buffer_ratio": 0.05,
}

_TIER_OVERRIDES: Dict[ContextTier, Dict[str, float]] = {
    ContextTier.SMALL: {
        "prune_protect_ratio": 0.20,
        "prune_minimum_ratio": 0.12,
        "flush_trigger_ratio": 0.05,
        "flush_reserve_ratio": 0.025,
        "summary_ratio": 0.06,
        "overflow_buffer_ratio": 0.08,
    },
    ContextTier.MEDIUM: {},
    ContextTier.LARGE: {
        "flush_trigger_ratio": 0.02,
        "flush_reserve_ratio": 0.01,
        "summary_ratio": 0.05,
        "overflow_buffer_ratio": 0.04,
    },
    ContextTier.XLARGE: {
        "prune_protect_ratio": 0.20,
        "prune_minimum_ratio": 0.10,
        "flush_trigger_ratio": 0.015,
        "flush_reserve_ratio": 0.008,
        "summary_ratio": 0.05,
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


# ============================================================================
# CompactionPolicy
# ============================================================================

@dataclass(frozen=True)
class CompactionPolicy:
    """
    Immutable set of compaction thresholds derived from a model's context
    window and output token limit.

    All values are integer token counts.  Tier-aware ratios live in the
    ``_DEFAULT_RATIOS`` / ``_TIER_OVERRIDES`` tables and are resolved to
    absolute token counts during ``from_model``.
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

        # B1: anomaly-aware ``max_output_tokens`` capping
        #
        # Many providers ship ``max_output_tokens`` metadata that's wildly
        # disproportionate to the actual window (e.g. GLM-5.1 reports
        # ``max_output_tokens=168000`` against ``context_window=198000`` —
        # that would leave only 30K usable input).  We always cap any
        # value claiming more than ``MAX_OUTPUT_RATIO_THRESHOLD`` of the
        # window down to ``SAFE_OUTPUT_RATIO`` to keep ``usable_context``
        # sane.  The cap is purely for *estimation*; the real generation
        # budget passed to the provider is unaffected.
        if context_window > 0:
            anomaly_floor = int(context_window * MAX_OUTPUT_RATIO_THRESHOLD)
            if max_output_tokens >= anomaly_floor:
                capped = int(context_window * SAFE_OUTPUT_RATIO)
                _policy_log.warn("compaction_policy.capping_output_tokens", {
                    "context_window": context_window,
                    "max_output_tokens_original": max_output_tokens,
                    "max_output_tokens_capped": capped,
                    "reason": (
                        "metadata_anomaly_exceeds_window"
                        if max_output_tokens >= context_window
                        else "metadata_anomaly_output_too_large"
                    ),
                    "threshold_ratio": MAX_OUTPUT_RATIO_THRESHOLD,
                })
                max_output_tokens = capped

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
            clamped[key] = int(max(lo, min(hi, int(value))))

        # Fixed 85 % of the full context window — matches hermes-agent gateway
        # behaviour and is simpler than a tier-adjusted ``usable × ratio``.
        # Users can override the ratio via the ``overflow_ratio`` Memory
        # config field (forwarded as an override here); the default keeps
        # the hermes-style fixed threshold.
        overflow_ratio_override = overrides.get("overflow_ratio")
        if overflow_ratio_override is not None:
            try:
                ratio_val = float(overflow_ratio_override)
                if 0 < ratio_val < 1:
                    overflow_threshold = int(context_window * ratio_val)
                else:
                    _policy_log.warn("compaction_policy.overflow_ratio_out_of_range", {
                        "value": ratio_val, "fallback": 0.85,
                    })
                    overflow_threshold = int(context_window * 0.85)
            except (TypeError, ValueError):
                _policy_log.warn("compaction_policy.overflow_ratio_parse_error", {
                    "raw": overflow_ratio_override, "fallback": 0.85,
                })
                overflow_threshold = int(context_window * 0.85)
        else:
            overflow_threshold = int(context_window * 0.85)
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

        _policy_log.debug("compaction_policy.created", {
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
        """Return a policy with sensible medium-tier defaults.

        Equivalent to ``CompactionPolicy.from_model(128_000, 4096)`` but
        constructed without going through the factory so unit tests can
        hold a stable baseline policy.
        """
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
            overflow_threshold=int(128000 * 0.85),
            overflow_buffer=8_192,
            preemptive_threshold=max(0, int(128000 * 0.85) - 8_192),
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
