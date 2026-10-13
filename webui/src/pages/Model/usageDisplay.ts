import type { UsageStats } from '@/types';

const USD_TO_CNY = 7;

export type DashboardCurrency = 'USD' | 'CNY';

function formatCurrencyAmount(currency: string, amount: number): string {
  if (currency === 'USD') {
    return `$${amount.toFixed(4)}`;
  }
  if (currency === 'CNY') {
    return `¥${amount.toFixed(4)}`;
  }
  return `${currency} ${amount.toFixed(4)}`;
}

export function formatTokenMillions(totalTokens: number): string {
  return `${(totalTokens / 1_000_000).toFixed(2)}M`;
}

export function formatTokenCount(tokens: number): string {
  if (tokens >= 1_000_000) return formatTokenMillions(tokens);
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

export type CacheUsageSummary =
  | { state: 'none' }
  /** Calls were made but no provider reported cache usage: unknown, not zero. */
  | { state: 'unreported'; totalRequests: number }
  /**
   * Only records from before cache reporting was tracked carry cache reads:
   * the reads are known, the hit rate is not.
   */
  | { state: 'legacy'; cacheRead: number; totalRequests: number }
  | {
    state: 'reported';
    /** Cache reads of the calls that reported cache usage. */
    cacheRead: number;
    /** Share of the reporting calls' input served from the cache. */
    hitRate: number | null;
    reportedRequests: number;
    totalRequests: number;
  };

export function getCacheUsageSummary(usageStats: UsageStats | null): CacheUsageSummary {
  const summary = usageStats?.summary;
  const totalRequests = summary?.total_requests ?? 0;
  if (!summary || totalRequests <= 0) {
    return { state: 'none' };
  }
  const reportedRequests = summary.cache_reported_requests ?? 0;
  if (reportedRequests <= 0) {
    const legacyRead = summary.total_cache_read_tokens ?? 0;
    return legacyRead > 0
      ? { state: 'legacy', cacheRead: legacyRead, totalRequests }
      : { state: 'unreported', totalRequests };
  }
  const cacheRead = summary.cache_reported_read_tokens ?? 0;
  const reportedInput = summary.cache_reported_input_tokens ?? 0;
  return {
    state: 'reported',
    cacheRead,
    hitRate: reportedInput > 0 ? cacheRead / reportedInput : null,
    reportedRequests,
    totalRequests,
  };
}

export function formatCacheHitRate(hitRate: number): string {
  return `${(hitRate * 100).toFixed(1)}%`;
}

export function getDefaultDashboardCurrency(language: string | undefined): DashboardCurrency {
  return language?.toLowerCase().startsWith('zh') ? 'CNY' : 'USD';
}

export function convertCurrencyAmount(
  amount: number,
  sourceCurrency: string,
  targetCurrency: string,
): number | null {
  if (sourceCurrency === targetCurrency) return amount;
  if (sourceCurrency === 'USD' && targetCurrency === 'CNY') return amount * USD_TO_CNY;
  if (sourceCurrency === 'CNY' && targetCurrency === 'USD') return amount / USD_TO_CNY;
  return null;
}

export function getConvertedTotalCost(
  usageStats: UsageStats | null,
  targetCurrency: DashboardCurrency,
): string | null {
  const buckets = usageStats?.summary?.cost_by_currency ?? [];
  if (buckets.length === 0) {
    return null;
  }

  const total = buckets.reduce((sum, bucket) => {
    return sum + (convertCurrencyAmount(
      bucket.total_cost,
      bucket.currency,
      targetCurrency,
    ) ?? 0);
  }, 0);

  if (total <= 0) {
    return null;
  }
  return formatCurrencyAmount(targetCurrency, total);
}

export function toggleDashboardCurrency(currency: DashboardCurrency): DashboardCurrency {
  return currency === 'USD' ? 'CNY' : 'USD';
}
