import { ChevronDown, Database } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { summarizeCacheUsage, type CacheUsageCall } from '@/features/session-chat/cacheUsage';

/** Cache visibility for the assistant records represented by one reply. */
export default function SessionCacheUsage({ calls, isActive = false }: {
  calls: readonly CacheUsageCall[];
  isActive?: boolean;
}) {
  const { t, i18n } = useTranslation('session');
  if (calls.length === 0) return null;
  const summary = summarizeCacheUsage(calls);
  const formatCount = (value: number | null) => value === null
    ? t('chat.cacheUsage.unknownValue')
    : value.toLocaleString(i18n.language);
  const formatRate = (value: number | null) => value === null
    ? t('chat.cacheUsage.unknownValue')
    : `${(value * 100).toFixed(1)}%`;

  return (
    <details data-testid="session-cache-usage" className="group/cache mt-3 max-w-full text-xs text-slate-500 dark:text-zinc-400">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-2 gap-y-1 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 [&::-webkit-details-marker]:hidden">
        <Database className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>{t('chat.cacheUsage.summary', { rate: formatRate(summary.hitRate) })}</span>
        <span>{t('chat.cacheUsage.coverage', { reported: summary.reportedCalls, total: summary.totalCalls })}</span>
        {isActive && <span>{t('chat.cacheUsage.updating')}</span>}
        <ChevronDown className="h-3 w-3 shrink-0 transition-transform group-open/cache:rotate-180" aria-hidden="true" />
      </summary>
      <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3 dark:border-zinc-700 dark:bg-zinc-900/50">
        <p>{t('chat.cacheUsage.formula')}</p>
        {summary.reportedCalls > 0 && (
          <p data-testid="session-cache-totals">
            {t('chat.cacheUsage.totals', {
              read: formatCount(summary.cacheRead), input: formatCount(summary.promptTokens),
            })}
          </p>
        )}
        {summary.reportedCalls < summary.totalCalls && <p>{t('chat.cacheUsage.partial')}</p>}
        <ol className="space-y-2">
          {calls.map((call, index) => (
            <li key={call.messageID} data-message-id={call.messageID} className="rounded-md border border-slate-200 bg-white p-2.5 dark:border-zinc-700 dark:bg-zinc-900">
              <div className="flex flex-wrap justify-between gap-1 text-slate-600 dark:text-zinc-300">
                <span className="break-all font-medium">{t('chat.cacheUsage.call', { index: index + 1 })} · {call.providerID || t('chat.cacheUsage.unknownValue')} / {call.modelID || t('chat.cacheUsage.unknownValue')}</span>
                <span>{call.reported === true
                  ? t('chat.cacheUsage.callRate', { rate: formatRate(call.hitRate) })
                  : t(call.reported === false ? 'chat.cacheUsage.unreported' : 'chat.cacheUsage.legacy')}</span>
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
                {([
                  [call.reported === true ? 'uncachedInput' : 'recordedInput', call.input],
                  ['cacheRead', call.reported === true || call.reported === null && (call.cacheRead ?? 0) > 0 ? call.cacheRead : null],
                  ['cacheWrite', call.reported === true || call.reported === null && (call.cacheWrite ?? 0) > 0 ? call.cacheWrite : null],
                  ['promptTokens', call.promptTokens],
                  ['output', call.output],
                  ['reasoning', call.reasoning],
                ] as const).map(([label, value]) => (
                  <div key={label} className="flex flex-wrap justify-between gap-x-2">
                    <dt>{t(`chat.cacheUsage.${label}`)}</dt>
                    <dd className="tabular-nums">{formatCount(value)}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 break-all text-[10px] text-slate-400 dark:text-zinc-500">{t('chat.cacheUsage.messageID')}: {call.messageID}</p>
            </li>
          ))}
        </ol>
        <p>{t('chat.cacheUsage.scope')}</p>
      </div>
    </details>
  );
}
