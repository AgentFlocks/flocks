"""
Cost calculator for LLM usage

Calculates monetary cost from token usage and model pricing.
"""

from decimal import ROUND_HALF_UP, Decimal
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
            input_tokens: Total number of input tokens reported by the provider.
            output_tokens: Number of output tokens.
            pricing: Model price configuration.
            cached_tokens: Number of cache-read input tokens.
            cache_write_tokens: Number of cache-write input tokens.
            reasoning_tokens: Reasoning tokens stored separately from output.

        Returns:
            Computed costs.
        """
        unit = pricing.unit if pricing.unit > 0 else 1_000_000

        input_price = pricing.input
        output_price = pricing.output
        if pricing.price_tiers:
            # Router chooses one tier from the complete prompt token count and
            # applies that tier to both input and output. ``None`` is the
            # unbounded fallback tier.
            selected_tier = pricing.price_tiers[-1]
            for tier in pricing.price_tiers:
                if (
                    tier.max_input_tokens is None
                    or input_tokens <= tier.max_input_tokens
                ):
                    selected_tier = tier
                    break
            input_price = selected_tier.input
            output_price = selected_tier.output

        # Most providers bill cached input separately. Router publishes no
        # cache discount and bills the complete prompt at the selected input
        # rate, which is represented explicitly on its pricing profile.
        billable_input = (
            max(0, input_tokens)
            if pricing.cache_read_uses_input
            else max(0, input_tokens - cached_tokens)
        )
        input_cost = (billable_input / unit) * input_price

        # Some provider payloads report reasoning as a subset of completion
        # tokens. Flocks stores that subset separately; opt-in profiles add it
        # back when the upstream bill uses the complete completion count.
        billable_output = output_tokens + (
            reasoning_tokens if pricing.reasoning_uses_output else 0
        )
        output_cost = (billable_output / unit) * output_price

        # Cache cost
        cache_cost = 0.0
        if (
            not pricing.cache_read_uses_input
            and cached_tokens > 0
            and pricing.cache_read is not None
        ):
            cache_cost = (cached_tokens / unit) * pricing.cache_read
        if cache_write_tokens > 0 and pricing.cache_write is not None:
            cache_cost += (cache_write_tokens / unit) * pricing.cache_write

        total_cost = input_cost + output_cost + cache_cost

        if pricing.cost_rounding_places is not None:
            quantum = Decimal(1).scaleb(-pricing.cost_rounding_places)

            def normalize(value: Decimal) -> float:
                return float(value.quantize(quantum, ROUND_HALF_UP))

            # Router normalizes each component before adding the total.
            decimal_unit = Decimal(unit)
            input_cost = normalize(
                Decimal(billable_input) * Decimal(str(input_price)) / decimal_unit
            )
            output_cost = normalize(
                Decimal(billable_output) * Decimal(str(output_price)) / decimal_unit
            )
            decimal_cache_cost = Decimal(0)
            if (
                not pricing.cache_read_uses_input
                and cached_tokens > 0
                and pricing.cache_read is not None
            ):
                decimal_cache_cost += (
                    Decimal(cached_tokens)
                    * Decimal(str(pricing.cache_read))
                    / decimal_unit
                )
            if cache_write_tokens > 0 and pricing.cache_write is not None:
                decimal_cache_cost += (
                    Decimal(cache_write_tokens)
                    * Decimal(str(pricing.cache_write))
                    / decimal_unit
                )
            cache_cost = normalize(decimal_cache_cost)
            total_cost = normalize(
                Decimal(str(input_cost))
                + Decimal(str(output_cost))
                + Decimal(str(cache_cost))
            )

        return UsageCost(
            input_cost=round(input_cost, 8),
            output_cost=round(output_cost, 8),
            cache_cost=round(cache_cost, 8),
            total_cost=round(total_cost, 8),
            currency=pricing.currency,
        )
