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
        reasoning_tokens: int = 0,
    ) -> UsageCost:
        """
        Calculate cost from token usage and pricing.

        Args:
            input_tokens: All prompt tokens, cache reads and writes included.
            output_tokens: Number of output tokens (the visible answer).
            pricing: Model price configuration.
            cached_tokens: Cache-read input tokens (part of ``input_tokens``).
            cache_write_tokens: Cache-write input tokens (part of ``input_tokens``).
            reasoning_tokens: Reasoning tokens, billed as output.

        Returns:
            Computed costs.
        """
        unit = pricing.unit if pricing.unit > 0 else 1_000_000

        # Input cost: prompt tokens that neither hit nor fill the cache
        billable_input = max(0, input_tokens - cached_tokens - cache_write_tokens)
        input_cost = (billable_input / unit) * pricing.input

        # Output cost: providers bill reasoning as output
        output_cost = ((output_tokens + reasoning_tokens) / unit) * pricing.output

        # Cache cost.  A model without a cache price is charged the input
        # price: an unknown cache discount is not a free cache.
        read_price = pricing.cache_read if pricing.cache_read is not None else pricing.input
        write_price = pricing.cache_write if pricing.cache_write is not None else pricing.input
        cache_cost = 0.0
        if cached_tokens > 0:
            cache_cost = (cached_tokens / unit) * read_price
        if cache_write_tokens > 0:
            cache_cost += (cache_write_tokens / unit) * write_price

        total_cost = input_cost + output_cost + cache_cost

        return UsageCost(
            input_cost=round(input_cost, 8),
            output_cost=round(output_cost, 8),
            cache_cost=round(cache_cost, 8),
            total_cost=round(total_cost, 8),
            currency=pricing.currency,
        )
