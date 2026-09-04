import { describe, expect, it } from 'vitest';

import { formatPricingPerMillion, isPricingFree } from './modelPricing';

describe('formatPricingPerMillion', () => {
  it('formats input and output prices', () => {
    expect(formatPricingPerMillion({
      input: 1,
      output: 2,
      currency: 'CNY',
    })).toBe('¥1/¥2/M');
  });

  it('includes the cache-read price when configured', () => {
    expect(formatPricingPerMillion({
      input: 1,
      output: 2,
      cache_read: 0.2,
      currency: 'CNY',
    })).toBe('¥1/¥2/¥0.2/M');
  });

  it('formats CNY tier boundaries including an open-ended fallback tier', () => {
    expect(formatPricingPerMillion({
      input: 4.2,
      output: 16.8,
      currency: 'CNY',
      price_tiers: [
        { max_input_tokens: 512_000, input: 4.2, output: 16.8 },
        { max_input_tokens: null, input: 8.4, output: 33.6 },
      ],
      price_version: '2026061601',
    })).toBe('≤512K: ¥4.2/¥16.8/M · >512K: ¥8.4/¥33.6/M');
  });

  it('uses the ordinary price when the tier list is empty', () => {
    expect(formatPricingPerMillion({
      input: 4.2,
      output: 16.8,
      currency: 'CNY',
      price_tiers: [],
    })).toBe('¥4.2/¥16.8/M');
  });
});

describe('isPricingFree', () => {
  it('does not mark cache-only pricing as free', () => {
    expect(isPricingFree({
      input: 0,
      output: 0,
      cache_read: 0.2,
      currency: 'CNY',
    })).toBe(false);
  });

  it('does not mark pricing with a paid tier as free', () => {
    expect(isPricingFree({
      input: 0,
      output: 0,
      currency: 'CNY',
      price_tiers: [
        { max_input_tokens: null, input: 1, output: 2 },
      ],
    })).toBe(false);
  });

  it('marks zero base and tier prices as free', () => {
    expect(isPricingFree({
      input: 0,
      output: 0,
      cache_read: 0,
      cache_write: 0,
      currency: 'CNY',
      price_tiers: [
        { max_input_tokens: 512_000, input: 0, output: 0 },
        { max_input_tokens: null, input: 0, output: 0 },
      ],
    })).toBe(true);
  });
});
