import { useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Link2, Loader2, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { CatalogProvider } from '@/types';

interface Props {
  providers: CatalogProvider[];
  selectedId: string;
  loading: boolean;
  failed: boolean;
  onSelect: (id: string) => void;
  onRetry: () => void;
  onBack?: () => void;
}

export default function ProviderCatalogPicker({
  providers,
  selectedId,
  loading,
  failed,
  onSelect,
  onRetry,
  onBack,
}: Props) {
  const { t } = useTranslation('model');
  const [query, setQuery] = useState('');
  const visible = useMemo(() => {
    const search = query.trim().toLowerCase();
    return providers.filter(
      (provider) =>
        !search ||
        `${provider.name} ${provider.id} ${provider.description || ''}`
          .toLowerCase()
          .includes(search),
    );
  }, [providers, query]);

  return (
    <section aria-label={t('providerPicker.title')} className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900">{t('providerPicker.title')}</h3>
          <p className="mt-1 text-sm text-gray-500">{t('providerPicker.hint')}</p>
        </div>
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            className="inline-flex shrink-0 items-center gap-1 py-1 text-xs text-gray-600 hover:text-violet-700"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t('providerPicker.back')}
          </button>
        )}
      </div>
      <label className="relative block">
        <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-gray-400" />
        <input
          type="search"
          aria-label={t('form.searchProvider')}
          placeholder={t('form.searchProvider')}
          value={query}
          disabled={loading || failed}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-lg border border-gray-300 py-2.5 pl-9 pr-3 text-sm focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-100 disabled:bg-gray-50"
        />
      </label>
      {loading ? (
        <div
          role="status"
          className="flex items-center justify-center gap-2 py-12 text-sm text-gray-500"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('providerPicker.loading')}
        </div>
      ) : failed ? (
        <div
          role="alert"
          className="space-y-3 rounded-lg border border-red-100 bg-red-50 p-5 text-center text-sm text-red-700"
        >
          <p>{t('providerPicker.loadFailed')}</p>
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs"
          >
            {t('providerList.retry')}
          </button>
        </div>
      ) : visible.length === 0 ? (
        <div className="space-y-3 rounded-lg border border-dashed border-gray-200 p-8 text-center text-sm text-gray-500">
          <p>{t(query ? 'form.noResults' : 'providerPicker.noneAvailable')}</p>
          {query && (
            <button type="button" onClick={() => setQuery('')} className="text-xs text-violet-700">
              {t('providerPicker.clearSearch')}
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
          {visible.map((provider) => {
            const compatible = provider.id === 'openai-compatible';
            const selected = provider.id === selectedId;
            return (
              <button
                key={provider.id}
                type="button"
                aria-label={provider.name}
                aria-pressed={selected}
                onClick={() => onSelect(provider.id)}
                className={`group flex min-h-24 items-start gap-3 rounded-xl border p-4 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-violet-400 ${compatible ? 'border-violet-200 bg-violet-50/60 hover:border-violet-400 hover:bg-violet-50' : 'border-gray-200 bg-white hover:border-violet-300 hover:bg-gray-50'} ${selected ? 'ring-2 ring-violet-400' : ''}`}
              >
                <span
                  aria-hidden="true"
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${compatible ? 'bg-violet-100 text-violet-700' : 'bg-gray-100 text-gray-600'}`}
                >
                  {compatible ? <Link2 className="h-5 w-5" /> : provider.name.slice(0, 2)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-gray-900">{provider.name}</span>
                  <span className="mt-1 block line-clamp-2 text-xs leading-5 text-gray-500">
                    {compatible
                      ? t('providerPicker.compatibleHint')
                      : provider.description || provider.id}
                  </span>
                </span>
                {selected ? (
                  <Check className="mt-1 h-4 w-4 shrink-0 text-violet-700" />
                ) : (
                  <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-gray-300 group-hover:text-violet-600" />
                )}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
