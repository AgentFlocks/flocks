import { describe, expect, it } from 'vitest';

import {
  convertCurrencyAmount,
  formatCacheHitRate,
  formatTokenCount,
  formatTokenMillions,
  getCacheUsageSummary,
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

  it('converts individual amounts between USD and CNY', () => {
    expect(convertCurrencyAmount(1, 'USD', 'CNY')).toBe(7);
    expect(convertCurrencyAmount(7, 'CNY', 'USD')).toBe(1);
    expect(convertCurrencyAmount(1, 'EUR', 'CNY')).toBeNull();
  });

  it('toggles dashboard currency', () => {
    expect(toggleDashboardCurrency('USD')).toBe('CNY');
    expect(toggleDashboardCurrency('CNY')).toBe('USD');
  });

  it('formats token counts compactly', () => {
    expect(formatTokenCount(812)).toBe('812');
    expect(formatTokenCount(8_192)).toBe('8.2K');
    expect(formatTokenCount(3_240_000)).toBe('3.24M');
  });

  it('reports cache usage as unknown when no call reported it', () => {
    expect(getCacheUsageSummary(null)).toEqual({ state: 'none' });
    expect(getCacheUsageSummary(usageStats)).toEqual({ state: 'unreported', totalRequests: 2 });
    expect(getCacheUsageSummary({
      ...usageStats,
      summary: { ...usageStats.summary, cache_reported_requests: 0, total_cache_read_tokens: 0 },
    })).toEqual({ state: 'unreported', totalRequests: 2 });
  });

  it('shows pre-upgrade cache reads without a hit rate', () => {
    expect(getCacheUsageSummary({
      ...usageStats,
      summary: {
        ...usageStats.summary,
        total_cache_read_tokens: 800,
        cache_reported_requests: 0,
        cache_reported_read_tokens: 0,
        cache_reported_input_tokens: 0,
      },
    })).toEqual({ state: 'legacy', cacheRead: 800, totalRequests: 2 });
  });

  it('computes the hit rate over the calls that reported cache usage', () => {
    const summary = getCacheUsageSummary({
      ...usageStats,
      summary: {
        ...usageStats.summary,
        // 900 more cache reads come from a record made before the upgrade.
        total_cache_read_tokens: 9_092,
        cache_reported_requests: 1,
        cache_reported_read_tokens: 8_192,
        cache_reported_input_tokens: 10_000,
      },
    });

    expect(summary).toEqual({
      state: 'reported',
      cacheRead: 8_192,
      hitRate: 0.8192,
      reportedRequests: 1,
      totalRequests: 2,
    });
    expect(formatCacheHitRate(0.8192)).toBe('81.9%');
  });
});
