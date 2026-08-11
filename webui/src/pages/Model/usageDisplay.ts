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
