type PricingTierPerMillion = {
  max_input_tokens?: number | null;
  input: number;
  output: number;
};

type PricingPerMillion = {
  input: number;
  output: number;
  cache_read?: number | null;
  cache_write?: number | null;
  cache_read_uses_input?: boolean;
  currency: string;
  price_tiers?: PricingTierPerMillion[] | null;
  price_version?: string | null;
};

export function isPricingFree(pricing: PricingPerMillion): boolean {
  return pricing.input === 0
    && pricing.output === 0
    && (pricing.cache_read ?? 0) === 0
    && (pricing.cache_write ?? 0) === 0
    && (pricing.price_tiers ?? []).every(tier => tier.input === 0 && tier.output === 0);
}

function currencySymbol(currency: string): string {
  return currency === 'CNY'
    ? '¥'
    : currency === 'USD'
      ? '$'
      : `${currency} `;
}

function formatTokenLimit(tokens: number): string {
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(3))}M`;
  if (tokens >= 1_000) return `${Number((tokens / 1_000).toFixed(3))}K`;
  return String(tokens);
}

function formatTierBoundary(maxInputTokens: number | null | undefined, previousMax?: number): string {
  if (maxInputTokens == null) {
    return previousMax == null ? 'all' : `>${formatTokenLimit(previousMax)}`;
  }
  if (previousMax == null) return `≤${formatTokenLimit(maxInputTokens)}`;
  return `>${formatTokenLimit(previousMax)}–≤${formatTokenLimit(maxInputTokens)}`;
}

function formatPricePair(input: number, output: number, symbol: string): string {
  return `${symbol}${input}/${symbol}${output}/M`;
}

export function formatPricingPerMillion(pricing: PricingPerMillion): string {
  const symbol = currencySymbol(pricing.currency);
  if (pricing.price_tiers?.length) {
    let previousMax: number | undefined;
    return pricing.price_tiers.map((tier) => {
      const boundary = formatTierBoundary(tier.max_input_tokens, previousMax);
      if (tier.max_input_tokens != null) previousMax = tier.max_input_tokens;
      return `${boundary}: ${formatPricePair(tier.input, tier.output, symbol)}`;
    }).join(' · ');
  }

  const prices = [pricing.input, pricing.output];
  if (pricing.cache_read != null) prices.push(pricing.cache_read);
  return `${prices.map(price => `${symbol}${price}`).join('/')}/M`;
}
