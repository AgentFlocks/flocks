"""
Cost calculator for LLM usage

Calculates monetary cost from token usage and model pricing.
"""

from flocks.provider.types import PriceConfig, PriceTier, UsageCost


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
        rates: PriceConfig | PriceTier = pricing
        if pricing.tiers:
            # Select one complete price tier using the whole prompt, before
            # subtracting cache tokens. Tiers are not progressive tax bands.
            for tier in pricing.tiers:
                if tier.max_input_tokens is None or input_tokens <= tier.max_input_tokens:
                    rates = tier
                    break
            else:
                raise ValueError("No price tier covers the request input tokens")

        # Input cost: prompt tokens that neither hit nor fill the cache
        billable_input = max(0, input_tokens - cached_tokens - cache_write_tokens)
        input_cost = (billable_input / unit) * rates.input

        # Output cost: providers bill reasoning as output
        output_cost = ((output_tokens + reasoning_tokens) / unit) * rates.output

        # Cache cost.  A model without a cache price is charged the input
        # price: an unknown cache discount is not a free cache.
        read_price = rates.cache_read if rates.cache_read is not None else rates.input
        write_price = rates.cache_write if rates.cache_write is not None else rates.input
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
