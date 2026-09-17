import { useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { Search, SlidersHorizontal, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ScopeConfig } from './types';
import { attributeLabel, localized } from './presentation';

export default function FilterToolbar({
  config,
  query,
  facets,
  searchRef,
  onQuery,
  onFacet,
  onClear,
}: {
  config: ScopeConfig;
  query: string;
  facets: Record<string, string[]>;
  searchRef: RefObject<HTMLInputElement | null>;
  onQuery: (value: string) => void;
  onFacet: (key: string, value: string) => void;
  onClear: () => void;
}) {
  const { t, i18n } = useTranslation('pluginLibrary');
  const [open, setOpen] = useState(false);
  const controlRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const popoverId = useId();
  const summaryId = `${popoverId}-summary`;
  const count = config.facets.reduce(
    (total, facet) => total + (facets[facet.key]?.length || 0),
    0,
  );
  const summary = config.facets
    .flatMap((facet) => {
      const values = facets[facet.key] || [];
      return values.length
        ? [
            `${localized(facet.label, i18n.language)}: ${values
              .map((value) =>
                attributeLabel(config, facet.key, value, i18n.language),
              )
              .join(', ')}`,
          ]
        : [];
    })
    .join(' · ');
  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    popoverRef.current?.focus();

    const onPointerDown = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !controlRef.current?.contains(event.target)
      ) {
        // An outside click should retain focus on its own target.
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
      <div className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border border-slate-200 bg-white px-2 focus-within:border-slate-400 focus-within:ring-1 focus-within:ring-slate-200 sm:w-[220px] sm:flex-none">
        <Search
          className="h-3.5 w-3.5 shrink-0 text-slate-400"
          aria-hidden="true"
        />
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          aria-label={t('searchLabel')}
          aria-keyshortcuts="/"
          placeholder={t('compactSearchPlaceholder')}
          className="h-full min-w-0 flex-1 border-0 bg-transparent p-0 text-xs text-slate-900 outline-none placeholder:text-slate-400 focus:ring-0"
        />
        {query ? (
          <button
            type="button"
            onClick={() => {
              onQuery('');
              searchRef.current?.focus();
            }}
            aria-label={t('clearSearch')}
            title={t('clearSearch')}
            className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        ) : (
          <kbd
            aria-hidden="true"
            className="hidden rounded border border-slate-200 px-1 text-[10px] leading-4 text-slate-400 sm:block"
          >
            /
          </kbd>
        )}
      </div>
      <div
        ref={controlRef}
        className="relative shrink-0"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget))
            setOpen(false);
        }}
      >
        <button
          ref={triggerRef}
          type="button"
          aria-label={t('filters')}
          aria-expanded={open}
          aria-controls={popoverId}
          aria-haspopup="dialog"
          aria-describedby={count ? summaryId : undefined}
          title={
            count ? `${t('filterCount', { count })} · ${summary}` : t('filters')
          }
          onClick={() => setOpen((current) => !current)}
          className={`inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500 ${count || open ? 'border-slate-300 bg-slate-100 text-slate-900' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
          {t('filters')}
          {count > 0 && (
            <span
              aria-hidden="true"
              className="rounded bg-slate-200 px-1 text-[10px] font-medium tabular-nums leading-4 text-slate-700"
            >
              {count}
            </span>
          )}
        </button>
        {count > 0 && (
          <span id={summaryId} className="sr-only">
            {t('filterCount', { count })} · {summary}
          </span>
        )}
        {open && (
          <div
            ref={popoverRef}
            id={popoverId}
            role="dialog"
            aria-label={t('filters')}
            tabIndex={-1}
            className="absolute right-0 top-full z-40 mt-2 max-h-[min(70vh,440px)] w-[280px] max-w-[calc(100vw-32px)] overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg outline-none"
          >
            <div className="space-y-3 p-3">
              {config.facets.map((facet) => {
                const values = facets[facet.key] || [];
                return (
                  <fieldset key={facet.key} className="min-w-0">
                    <legend className="mb-1 px-1 text-xs font-medium text-slate-700">
                      {localized(facet.label, i18n.language)}
                    </legend>
                    <div className="space-y-0.5">
                      {facet.options.map((option) => (
                        <label
                          key={option.value}
                          className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-50 ${values.includes(option.value) ? 'bg-slate-50' : ''}`}
                        >
                          <input
                            type="checkbox"
                            className="h-3.5 w-3.5 shrink-0 accent-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500"
                            checked={values.includes(option.value)}
                            onChange={() => onFacet(facet.key, option.value)}
                          />
                          <span className="min-w-0 break-words">
                            {localized(option.label, i18n.language)}
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                );
              })}
            </div>
            <div className="sticky bottom-0 flex items-center justify-between gap-2 border-t border-slate-100 bg-white px-3 py-2">
              <button
                type="button"
                onClick={onClear}
                disabled={!count && !query.trim()}
                className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t('clearFilters')}
              </button>
              <button
                type="button"
                onClick={closeAndRestoreFocus}
                className="rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500"
              >
                {t('doneFiltering')}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
