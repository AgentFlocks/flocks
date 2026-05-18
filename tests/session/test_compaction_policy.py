"""
Tests for CompactionPolicy - dynamic context compression thresholds.

Verifies that computed values scale properly across model sizes and that
tier classification, clamping, overrides, and backward-compatible defaults
all work correctly.
"""

from unittest.mock import Mock

import pytest

import flocks.session.lifecycle.compaction.policy as policy_module
from flocks.session.lifecycle.compaction import (
    CompactionPolicy,
    ContextTier,
    _BOUNDS,
    _MIN_OVERFLOW_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _policy(ctx: int, out: int = 4096, **overrides) -> CompactionPolicy:
    """Shortcut to build a policy with optional overrides."""
    return CompactionPolicy.from_model(ctx, out, overrides=overrides or None)


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

class TestTierClassification:
    """Tests for _classify_tier."""

    def test_small_tier(self):
        assert CompactionPolicy._classify_tier(4_000) == ContextTier.SMALL
        assert CompactionPolicy._classify_tier(11_999) == ContextTier.SMALL

    def test_medium_tier(self):
        assert CompactionPolicy._classify_tier(12_000) == ContextTier.MEDIUM
        assert CompactionPolicy._classify_tier(50_000) == ContextTier.MEDIUM
        assert CompactionPolicy._classify_tier(100_000) == ContextTier.MEDIUM

    def test_large_tier(self):
        assert CompactionPolicy._classify_tier(100_001) == ContextTier.LARGE
        assert CompactionPolicy._classify_tier(300_000) == ContextTier.LARGE
        assert CompactionPolicy._classify_tier(500_000) == ContextTier.LARGE

    def test_xlarge_tier(self):
        assert CompactionPolicy._classify_tier(500_001) == ContextTier.XLARGE
        assert CompactionPolicy._classify_tier(1_000_000) == ContextTier.XLARGE


class TestPolicyLogging:
    def test_policy_creation_uses_debug_log(self, monkeypatch: pytest.MonkeyPatch):
        logger = Mock()
        monkeypatch.setattr(policy_module, "_policy_log", logger)

        policy = CompactionPolicy.from_model(128_000, 16_384)

        assert policy.tier == ContextTier.LARGE
        logger.debug.assert_called_once()
        logger.info.assert_not_called()


# ---------------------------------------------------------------------------
# Model-specific policy computation
# ---------------------------------------------------------------------------

class TestGPT4_8K:
    """GPT-4 original: 8192 context, 4096 output -> usable ~4096 (SMALL)."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return _policy(8192, 4096)

    def test_tier(self, policy: CompactionPolicy):
        assert policy.tier == ContextTier.SMALL

    def test_usable(self, policy: CompactionPolicy):
        assert policy.usable_context == 8192 - 4096

    def test_prune_protect_clamped_to_min(self, policy: CompactionPolicy):
        # 4096 * 0.20 = 819 < min 4_000 -> clamped
        assert policy.prune_protect == _BOUNDS["prune_protect"][0]

    def test_prune_minimum_clamped_to_min(self, policy: CompactionPolicy):
        # 4096 * 0.12 = 491 < min 2_000 -> clamped
        assert policy.prune_minimum == _BOUNDS["prune_minimum"][0]

    def test_summary_clamped_to_min(self, policy: CompactionPolicy):
        assert policy.summary_max_tokens == _BOUNDS["summary_max_tokens"][0]

    def test_preserve_last(self, policy: CompactionPolicy):
        assert policy.preserve_last == 2

    def test_overflow_threshold(self, policy: CompactionPolicy):
        # SMALL overflow_ratio = 0.80
        expected = int(4096 * 0.80)
        assert policy.overflow_threshold == expected


class TestGPT4o_128K:
    """GPT-4o: 128K context, 16384 output -> usable 111616 (LARGE, since >100K)."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return _policy(128_000, 16_384)

    def test_tier(self, policy: CompactionPolicy):
        # usable=111616 > 100K -> LARGE
        assert policy.tier == ContextTier.LARGE

    def test_usable(self, policy: CompactionPolicy):
        assert policy.usable_context == 128_000 - 16_384

    def test_prune_protect(self, policy: CompactionPolicy):
        usable = 128_000 - 16_384
        # LARGE tier uses default prune_protect_ratio = 0.25
        expected = int(usable * 0.25)
        lo, hi = _BOUNDS["prune_protect"]
        assert policy.prune_protect == max(lo, min(hi, expected))

    def test_prune_minimum(self, policy: CompactionPolicy):
        usable = 128_000 - 16_384
        # LARGE tier uses default prune_minimum_ratio = 0.15
        expected = int(usable * 0.15)
        lo, hi = _BOUNDS["prune_minimum"]
        assert policy.prune_minimum == max(lo, min(hi, expected))

    def test_summary_max_tokens(self, policy: CompactionPolicy):
        usable = 128_000 - 16_384
        # LARGE tier summary_ratio = 0.05
        expected = int(usable * 0.05)
        lo, hi = _BOUNDS["summary_max_tokens"]
        assert policy.summary_max_tokens == max(lo, min(hi, expected))

    def test_preserve_last(self, policy: CompactionPolicy):
        # LARGE tier -> preserve_last = 6
        assert policy.preserve_last == 6

    def test_overflow_threshold(self, policy: CompactionPolicy):
        usable = 128_000 - 16_384
        # LARGE tier overflow_ratio = 0.87; clamped up to _MIN_OVERFLOW_THRESHOLD if usable >= it
        expected = int(usable * 0.87)
        if usable >= _MIN_OVERFLOW_THRESHOLD:
            expected = max(_MIN_OVERFLOW_THRESHOLD, expected)
        assert policy.overflow_threshold == expected


class TestClaude35_200K:
    """Claude 3.5 Sonnet: 200K context, 8192 output -> usable ~191808 (LARGE)."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return _policy(200_000, 8_192)

    def test_tier(self, policy: CompactionPolicy):
        assert policy.tier == ContextTier.LARGE

    def test_usable(self, policy: CompactionPolicy):
        assert policy.usable_context == 200_000 - 8_192

    def test_prune_protect(self, policy: CompactionPolicy):
        usable = 200_000 - 8_192
        # LARGE uses default prune_protect_ratio = 0.25
        expected = int(usable * 0.25)
        lo, hi = _BOUNDS["prune_protect"]
        assert policy.prune_protect == max(lo, min(hi, expected))

    def test_summary_max_tokens(self, policy: CompactionPolicy):
        usable = 200_000 - 8_192
        # LARGE summary_ratio = 0.05
        expected = int(usable * 0.05)
        lo, hi = _BOUNDS["summary_max_tokens"]
        assert policy.summary_max_tokens == max(lo, min(hi, expected))

    def test_preserve_last(self, policy: CompactionPolicy):
        assert policy.preserve_last == 6

    def test_overflow_threshold(self, policy: CompactionPolicy):
        usable = 200_000 - 8_192
        # LARGE overflow_ratio = 0.87
        expected = int(usable * 0.87)
        assert policy.overflow_threshold == expected


class TestGemini15Pro_1M:
    """Gemini 1.5 Pro: 1M context, 8192 output -> usable ~991808 (XLARGE)."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return _policy(1_000_000, 8_192)

    def test_tier(self, policy: CompactionPolicy):
        assert policy.tier == ContextTier.XLARGE

    def test_usable(self, policy: CompactionPolicy):
        assert policy.usable_context == 1_000_000 - 8_192

    def test_prune_protect_clamped_to_max(self, policy: CompactionPolicy):
        # 991808 * 0.20 = 198361 > max 120_000 -> clamped
        assert policy.prune_protect == _BOUNDS["prune_protect"][1]

    def test_prune_minimum_clamped_to_max(self, policy: CompactionPolicy):
        # 991808 * 0.10 = 99180 > max 60_000 -> clamped
        assert policy.prune_minimum == _BOUNDS["prune_minimum"][1]

    def test_summary_clamped_to_max(self, policy: CompactionPolicy):
        # 991808 * 0.05 = 49590 > max 16_000 -> clamped
        assert policy.summary_max_tokens == _BOUNDS["summary_max_tokens"][1]

    def test_preserve_last(self, policy: CompactionPolicy):
        assert policy.preserve_last == 8

    def test_overflow_threshold(self, policy: CompactionPolicy):
        usable = 1_000_000 - 8_192
        # XLARGE overflow_ratio = 0.90
        expected = int(usable * 0.90)
        assert policy.overflow_threshold == expected


class TestMedium32K:
    """A 32K model: 32768 context, 4096 output -> usable 28672 (MEDIUM)."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return _policy(32_768, 4_096)

    def test_tier(self, policy: CompactionPolicy):
        assert policy.tier == ContextTier.MEDIUM

    def test_values_in_bounds(self, policy: CompactionPolicy):
        for field_name, (lo, hi) in _BOUNDS.items():
            value = getattr(policy, field_name)
            assert lo <= value <= hi, f"{field_name}={value} not in [{lo}, {hi}]"


# ---------------------------------------------------------------------------
# Default (backward-compatible) policy
# ---------------------------------------------------------------------------

class TestDefaultPolicy:
    """CompactionPolicy.default() matches legacy hardcoded constants."""

    @pytest.fixture()
    def policy(self) -> CompactionPolicy:
        return CompactionPolicy.default()

    def test_prune_protect_legacy(self, policy: CompactionPolicy):
        assert policy.prune_protect == 40_000

    def test_prune_minimum_legacy(self, policy: CompactionPolicy):
        assert policy.prune_minimum == 20_000

    def test_summary_max_tokens_legacy(self, policy: CompactionPolicy):
        assert policy.summary_max_tokens == 4_000

    def test_flush_trigger_legacy(self, policy: CompactionPolicy):
        assert policy.flush_trigger == 4_000

    def test_flush_reserve_legacy(self, policy: CompactionPolicy):
        assert policy.flush_reserve == 2_000

    def test_preserve_last_legacy(self, policy: CompactionPolicy):
        assert policy.preserve_last == 4


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

class TestOverrides:
    """User can override individual ratios or absolute values."""

    def test_override_ratio(self):
        policy = _policy(128_000, 16_384, prune_protect_ratio=0.40)
        usable = 128_000 - 16_384
        expected = int(usable * 0.40)
        lo, hi = _BOUNDS["prune_protect"]
        assert policy.prune_protect == max(lo, min(hi, expected))

    def test_override_absolute(self):
        policy = _policy(128_000, 16_384, summary_max_tokens=8000)
        assert policy.summary_max_tokens == 8000

    def test_override_preserve_last(self):
        policy = _policy(128_000, 16_384, preserve_last=10)
        assert policy.preserve_last == 10

    def test_override_overflow_threshold(self):
        policy = _policy(128_000, 16_384, overflow_threshold=100_000)
        assert policy.overflow_threshold == 100_000

    def test_absolute_wins_over_ratio(self):
        # If both ratio and absolute are given, absolute wins
        policy = _policy(
            128_000, 16_384,
            prune_protect_ratio=0.10,
            prune_protect=50_000,
        )
        assert policy.prune_protect == 50_000


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and degenerate inputs."""

    def test_zero_context(self):
        policy = _policy(0, 0)
        assert policy.usable_context == 0
        assert policy.tier == ContextTier.SMALL
        # All values should be at their minimums (clamped)
        for field_name, (lo, _) in _BOUNDS.items():
            assert getattr(policy, field_name) == lo

    def test_output_exceeds_context(self):
        # When max_output_tokens >= context_window, it's capped at 25% of context_window
        # so usable = context_window - (context_window // 4)
        policy = _policy(4096, 8192)
        assert policy.usable_context == 4096 - (4096 // 4)
        assert policy.tier == ContextTier.SMALL

    def test_frozen_dataclass(self):
        policy = _policy(128_000, 16_384)
        with pytest.raises(AttributeError):
            policy.prune_protect = 999  # type: ignore[misc]

    def test_describe_returns_dict(self):
        policy = _policy(128_000, 16_384)
        desc = policy.describe()
        assert isinstance(desc, dict)
        assert "tier" in desc
        assert "prune_protect" in desc
        assert desc["context_window"] == 128_000


# ---------------------------------------------------------------------------
# CompactionConfig.to_overrides
# ---------------------------------------------------------------------------

class TestCompactionConfigOverrides:
    """CompactionConfig.to_overrides() produces correct dict."""

    def test_default_empty(self):
        from flocks.memory.config import CompactionConfig
        cfg = CompactionConfig()
        assert cfg.to_overrides() == {}

    def test_partial_overrides(self):
        from flocks.memory.config import CompactionConfig
        cfg = CompactionConfig(overflow_ratio=0.90, summary_max_tokens=8000)
        overrides = cfg.to_overrides()
        assert overrides == {"overflow_ratio": 0.90, "summary_max_tokens": 8000}

    def test_integration_with_policy(self):
        from flocks.memory.config import CompactionConfig
        cfg = CompactionConfig(prune_protect=50_000)
        policy = CompactionPolicy.from_model(128_000, 16_384, overrides=cfg.to_overrides())
        assert policy.prune_protect == 50_000
