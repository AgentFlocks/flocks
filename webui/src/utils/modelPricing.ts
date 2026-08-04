type PricingPerMillion = {
  input: number;
  output: number;
  cache_read?: number | null;
  cache_write?: number | null;
  currency: string;
};

export function isPricingFree(pricing: PricingPerMillion): boolean {
  return pricing.input === 0
    && pricing.output === 0
    && (pricing.cache_read ?? 0) === 0
    && (pricing.cache_write ?? 0) === 0;
}

export function formatPricingPerMillion(pricing: PricingPerMillion): string {
  const symbol = pricing.currency === 'CNY'
    ? '¥'
    : pricing.currency === 'USD'
      ? '$'
      : `${pricing.currency} `;
  const prices = [pricing.input, pricing.output];
  if (pricing.cache_read != null) prices.push(pricing.cache_read);
  return `${prices.map(price => `${symbol}${price}`).join('/')}/M`;
}
