import { describe, expect, it } from 'vitest';

import { formatPriceTiers, formatPricingPerMillion, isPricingFree } from './modelPricing';

describe('formatPricingPerMillion', () => {
  it('formats input and output prices', () => {
    expect(formatPricingPerMillion({
      input: 1,
      output: 2,
      currency: 'CNY',
    })).toBe('¥1/¥2/M');
  });

  it('formats all Router input-token price tiers', () => {
    expect(formatPriceTiers({
      input: 4.2,
      output: 16.8,
      currency: 'CNY',
      price_tiers: [
        { max_input_tokens: 512000, input_price: 4.2, output_price: 16.8 },
        { max_input_tokens: null, input_price: 8.4, output_price: 33.6 },
      ],
    })).toBe('≤ 512,000: ¥4.2/¥16.8/M\n> 512,000: ¥8.4/¥33.6/M');
  });

  it('includes the cache-read price when configured', () => {
    expect(formatPricingPerMillion({
      input: 1,
      output: 2,
      cache_read: 0.2,
      currency: 'CNY',
    })).toBe('¥1/¥2/¥0.2/M');
  });

  it('does not mark cache-only pricing as free', () => {
    expect(isPricingFree({
      input: 0,
      output: 0,
      cache_read: 0.2,
      currency: 'CNY',
    })).toBe(false);
  });
});
