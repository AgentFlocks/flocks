import { describe, expect, it } from 'vitest';

import {
  formatTokenMillions,
  getConvertedTotalCost,
  getDefaultDashboardCurrency,
  toggleDashboardCurrency,
} from './usageDisplay';
import type { UsageStats } from '@/types';

const usageStats: UsageStats = {
  summary: {
    total_tokens: 1_234_567,
    total_input_tokens: 1_000_000,
    total_output_tokens: 234_567,
    total_cost: 0,
    total_requests: 2,
    currency: 'MIXED',
    cost_by_currency: [
      { currency: 'USD', total_cost: 1.25 },
      { currency: 'CNY', total_cost: 14 },
    ],
  },
  by_provider: [],
  by_model: [],
  daily: [],
};

describe('usageDisplay helpers', () => {
  it('formats tokens in millions', () => {
    expect(formatTokenMillions(1_234_567)).toBe('1.23M');
  });

  it('uses locale-based default currency', () => {
    expect(getDefaultDashboardCurrency('zh-CN')).toBe('CNY');
    expect(getDefaultDashboardCurrency('en-US')).toBe('USD');
  });

  it('converts grouped costs to the target currency', () => {
    expect(getConvertedTotalCost(usageStats, 'CNY')).toBe('¥22.7500');
    expect(getConvertedTotalCost(usageStats, 'USD')).toBe('$3.2500');
  });

  it('toggles dashboard currency', () => {
    expect(toggleDashboardCurrency('USD')).toBe('CNY');
    expect(toggleDashboardCurrency('CNY')).toBe('USD');
  });
});
