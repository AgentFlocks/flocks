"""
Cost calculator for LLM usage

Calculates monetary cost from token usage and model pricing.
"""

from typing import Optional

from flocks.provider.types import PriceConfig, UsageCost


class CostCalculator:
    """
    Stateless cost calculator.

    Given token counts and a PriceConfig, computes the monetary cost.
    """

    @staticmethod
    def calculate(
        input_tokens: int,
        output_tokens: int,
        pricing: PriceConfig,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> UsageCost:
        """
        Calculate cost from token usage and pricing.

        Args:
            input_tokens: Number of input tokens (excluding cached).
            output_tokens: Number of output tokens.
            pricing: Model price configuration.
            cached_tokens: Number of cache-read input tokens.
            cache_write_tokens: Number of cache-write input tokens.

        Returns:
            Computed costs.
        """
        unit = pricing.unit if pricing.unit > 0 else 1_000_000

        # Input cost: non-cached tokens at input price
        billable_input = max(0, input_tokens - cached_tokens)
        input_cost = (billable_input / unit) * pricing.input

        # Output cost
        output_cost = (output_tokens / unit) * pricing.output

        # Cache cost
        cache_cost = 0.0
        if cached_tokens > 0 and pricing.cache_read is not None:
            cache_cost = (cached_tokens / unit) * pricing.cache_read
        if cache_write_tokens > 0 and pricing.cache_write is not None:
            cache_cost += (cache_write_tokens / unit) * pricing.cache_write

        total_cost = input_cost + output_cost + cache_cost

        return UsageCost(
            input_cost=round(input_cost, 8),
            output_cost=round(output_cost, 8),
            cache_cost=round(cache_cost, 8),
            total_cost=round(total_cost, 8),
            currency=pricing.currency,
        )
