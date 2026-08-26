type PricingPerMillion = {
  input: number;
  output: number;
  cache_read?: number | null;
  cache_write?: number | null;
  currency: string;
  price_tiers?: Array<{
    max_input_tokens?: number | null;
    input_price: number;
    output_price: number;
  }> | null;
};

function currencySymbol(currency: string): string {
  return currency === 'CNY'
    ? '¥'
    : currency === 'USD'
      ? '$'
      : `${currency} `;
}

export function isPricingFree(pricing: PricingPerMillion): boolean {
  return pricing.input === 0
    && pricing.output === 0
    && (pricing.cache_read ?? 0) === 0
    && (pricing.cache_write ?? 0) === 0;
}

export function formatPricingPerMillion(pricing: PricingPerMillion): string {
  const symbol = currencySymbol(pricing.currency);
  const prices = [pricing.input, pricing.output];
  if (pricing.cache_read != null) prices.push(pricing.cache_read);
  return `${prices.map(price => `${symbol}${price}`).join('/')}/M`;
}

export function formatPriceTiers(pricing: PricingPerMillion): string {
  const tiers = pricing.price_tiers ?? [];
  const symbol = currencySymbol(pricing.currency);
  let previousMax = 0;
  return tiers.map(tier => {
    const range = tier.max_input_tokens == null
      ? `> ${previousMax.toLocaleString('en-US')}`
      : previousMax === 0
        ? `≤ ${tier.max_input_tokens.toLocaleString('en-US')}`
        : `${previousMax.toLocaleString('en-US')} < Token ≤ ${tier.max_input_tokens.toLocaleString('en-US')}`;
    if (tier.max_input_tokens != null) previousMax = tier.max_input_tokens;
    return `${range}: ${symbol}${tier.input_price}/${symbol}${tier.output_price}/M`;
  }).join('\n');
}
