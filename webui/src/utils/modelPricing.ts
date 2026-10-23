import type { PriceTierV2 } from '@/types';

type PricingRates = {
  input: number;
  output: number;
  cache_read?: number | null;
  cache_write?: number | null;
};

type PricingPerMillion = PricingRates & {
  currency: string;
  unit?: number;
  tiers?: PriceTierV2[];
};

export function isPricingFree(pricing: PricingPerMillion): boolean {
  const rates = pricing.tiers?.length ? pricing.tiers : [pricing];
  return rates.every(rate => rate.input === 0
    && rate.output === 0
    && (rate.cache_read ?? rate.input) === 0
    && (rate.cache_write ?? rate.input) === 0);
}

export function formatPricePerMillion(price: number, currency: string, unit = 1_000_000): string {
  const effectiveUnit = Number.isFinite(unit) && unit > 0 ? unit : 1_000_000;
  const symbol = currency === 'CNY'
    ? '¥'
    : currency === 'USD'
      ? '$'
      : `${currency} `;
  return `${symbol}${Number((price * 1_000_000 / effectiveUnit).toPrecision(12))}`;
}

export function formatPricingTierRange(tiers: PriceTierV2[], index: number): string {
  const maximum = tiers[index].max_input_tokens;
  const previous = index > 0 ? tiers[index - 1].max_input_tokens : null;
  const lower = previous == null ? '' : `>${previous.toLocaleString('en-US')}`;
  const upper = maximum == null ? '' : `≤${maximum.toLocaleString('en-US')}`;
  return [lower, upper].filter(Boolean).join(' & ') || '≥0';
}

export function formatPricingPerMillion(pricing: PricingPerMillion): string {
  const tiers = pricing.tiers;
  const hasCacheWrite = tiers?.length
    ? tiers.some(tier => tier.cache_write != null)
    : pricing.cache_write != null;
  const formatRates = (rates: PricingRates, tiered = false): string => {
    const prices = [rates.input, rates.output];
    if (tiered || rates.cache_read != null || hasCacheWrite) prices.push(rates.cache_read ?? rates.input);
    if (hasCacheWrite) prices.push(rates.cache_write ?? rates.input);
    return `${prices.map(price => formatPricePerMillion(price, pricing.currency, pricing.unit)).join('/')}/M`;
  };
  if (tiers?.length) {
    return tiers.map((tier, index) => (
      `${formatPricingTierRange(tiers, index)}: ${formatRates(tier, true)}`
    )).join('; ');
  }
  return formatRates(pricing);
}
