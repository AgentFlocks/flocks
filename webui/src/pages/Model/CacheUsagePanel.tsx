import { useId } from 'react';
import { AlertCircle, ChevronDown, Database, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UsageStats } from '@/types';
import { formatCacheHitRate, getCacheUsageSummary } from './usageDisplay';

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

export default function CacheUsagePanel({ usageStats, loading, error, onRefresh }: {
  usageStats: UsageStats | null;
  loading: boolean;
  error: boolean;
  onRefresh: () => void;
}) {
  const { t, i18n } = useTranslation('model');
  const titleId = useId();
  const cache = getCacheUsageSummary(usageStats);
  const total = cache.state === 'none' ? 0 : cache.totalRequests;
  const reported = cache.state === 'reported' ? cache.reportedRequests : 0;
  const unknown = Math.max(0, total - reported);
  // Never substitute total cache reads or total input here: historical and
  // unreported records do not have a reliable cache-hit denominator.
  const read = reported > 0 ? count(usageStats?.summary.cache_reported_read_tokens) : null;
  const input = reported > 0 ? count(usageStats?.summary.cache_reported_input_tokens) : null;
  const rate = read !== null && input !== null && input > 0 ? read / input : null;
  const formatCount = (value: number | null) => value === null
    ? t('cacheUsage.unknown') : value.toLocaleString(i18n.language);
  const initialLoading = loading && !usageStats;
  const initialError = error && !usageStats;

  return (
    <section aria-labelledby={titleId} aria-busy={loading} data-testid="model-cache-usage" className="mb-4 shrink-0 rounded-xl border border-slate-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id={titleId} className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-zinc-100">
            <Database className="h-4 w-4 shrink-0" aria-hidden="true" />
            {t('cacheUsage.title')}
          </h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.scope')}</p>
        </div>
        <button type="button" onClick={onRefresh} disabled={loading} className="flex shrink-0 items-center gap-1.5 rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          {t(error ? 'cacheUsage.retry' : 'cacheUsage.refresh')}
        </button>
      </div>

      {loading && <p role="status" className="mt-3 text-xs text-slate-500 dark:text-zinc-400">{t(initialLoading ? 'cacheUsage.loading' : 'cacheUsage.refreshing')}</p>}
      {error && (
        <p role="alert" className="mt-3 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {t(initialError ? 'cacheUsage.loadFailed' : 'cacheUsage.refreshFailed')}
        </p>
      )}

      {!initialLoading && !initialError && (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 lg:grid-cols-4">
            <div>
              <dt className="text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.hitRate')}</dt>
              <dd data-testid="model-cache-rate" className="mt-1 break-words text-2xl font-semibold tabular-nums text-sky-700 dark:text-sky-300">
                {cache.state === 'none' ? '—' : rate === null ? t('cacheUsage.unknown') : formatCacheHitRate(rate)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.cacheRead')}</dt>
              <dd data-testid="model-cache-read" className="mt-1 break-words text-lg font-semibold tabular-nums text-slate-800 dark:text-zinc-100">{cache.state === 'none' ? '—' : formatCount(read)}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.completeInput')}</dt>
              <dd data-testid="model-cache-input" className="mt-1 break-words text-lg font-semibold tabular-nums text-slate-800 dark:text-zinc-100">{cache.state === 'none' ? '—' : formatCount(input)}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.coverage')}</dt>
              <dd data-testid="model-cache-coverage" className="mt-1 text-lg font-semibold tabular-nums text-slate-800 dark:text-zinc-100">{reported.toLocaleString(i18n.language)} / {total.toLocaleString(i18n.language)}</dd>
              <dd data-testid="model-cache-unknown" className="mt-0.5 text-xs text-slate-500 dark:text-zinc-400">{t('cacheUsage.unknownRecords', { count: unknown })}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-slate-500 dark:text-zinc-400">
            {cache.state === 'none' ? t('cacheUsage.empty')
              : cache.state === 'legacy' ? t('cacheUsage.legacy', { tokens: formatCount(cache.cacheRead) })
                : cache.state === 'unreported' ? t('cacheUsage.unreported')
                  : rate === null ? t('cacheUsage.noDenominator')
                    : t('cacheUsage.reportedOnly')}
          </p>
          <details className="group/cache mt-3 border-t border-slate-100 pt-2 text-xs text-slate-500 dark:border-zinc-800 dark:text-zinc-400">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded focus-visible:outline focus-visible:outline-2 [&::-webkit-details-marker]:hidden">
              {t('cacheUsage.explanation')}
              <ChevronDown className="h-3.5 w-3.5 transition-transform group-open/cache:rotate-180" aria-hidden="true" />
            </summary>
            <div className="mt-2 max-h-32 space-y-2 overflow-y-auto pr-2 leading-relaxed">
              <p>{t('cacheUsage.formula')}</p>
              <p>{t('cacheUsage.exclusions')}</p>
              <p>{t('cacheUsage.recordsScope')}</p>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
