import { describe, expect, it } from 'vitest';

import { formatPricingPerMillion } from './modelPricing';

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
});
