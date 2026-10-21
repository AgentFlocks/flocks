import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Loader2, RefreshCw, Search, X } from 'lucide-react';
import { providerAPI } from '@/api/provider';
import { extractErrorMessage } from '@/utils/error';
import type { DiscoveredModel, ModelDiscoveryResult, ModelImportInput, ModelImportResult } from '@/types';

/** Send only importable metadata, never discovery status or credentials. */
export function toModelImport(model: DiscoveredModel): ModelImportInput {
  const { id, name, context_window, max_output_tokens, supports_vision, supports_tools, supports_reasoning } = model;
  return { id, name, context_window, max_output_tokens, supports_vision, supports_tools, supports_reasoning };
}

export function ModelImportSummary({ result }: { result: ModelImportResult }) {
  const { t } = useTranslation('model');
  return (
    <div role="status" className={`rounded-lg border p-3 text-sm ${result.failed.length ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-green-200 bg-green-50 text-green-800'}`}>
      <p>{t('discovery.importSummary', { added: result.added.length, skipped: result.skipped.length, failed: result.failed.length })}</p>
      {result.failed.length > 0 && (
        <ul className="mt-2 max-h-32 overflow-y-auto space-y-1 text-xs">
          {result.failed.map(item => <li key={item.id}><span className="font-mono">{item.id}</span>: {item.error}</li>)}
        </ul>
      )}
    </div>
  );
}

interface ModelDiscoveryProps {
  providerId: string;
  credentials?: { api_key?: string; base_url?: string };
  mode: 'stage' | 'import';
  /** A new custom instance has its own model namespace, unlike its catalog type. */
  newInstance?: boolean;
  disabled?: boolean;
  onUsed?: () => void;
  onSelectionChange?: (models: DiscoveredModel[]) => void;
  onImported?: () => void;
}

/** Discovery is read-only. Staged selections are applied only by the owning form. */
export default function ModelDiscovery({ providerId, credentials, mode, newInstance = false, disabled, onUsed, onSelectionChange, onImported }: ModelDiscoveryProps) {
  const { t, i18n } = useTranslation('model');
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ModelDiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importResult, setImportResult] = useState<ModelImportResult | null>(null);
  const requestVersion = useRef(0);
  const busy = useRef(false);
  const callbacks = useRef({ onUsed, onSelectionChange, onImported });
  callbacks.current = { onUsed, onSelectionChange, onImported };
  const apiKey = credentials?.api_key;
  const baseUrl = credentials?.base_url;

  // A response and its selection are valid only for the exact input used to fetch it.
  useEffect(() => {
    requestVersion.current += 1;
    busy.current = false;
    setOpen(false);
    setLoading(false);
    setImporting(false);
    setResult(null);
    setError(null);
    setQuery('');
    setSelected(new Set());
    setImportResult(null);
    callbacks.current.onSelectionChange?.([]);
    return () => { requestVersion.current += 1; };
  }, [providerId, apiKey, baseUrl, newInstance]);

  const publishSelection = (next: Set<string>, models = result?.models ?? []) => {
    setSelected(next);
    callbacks.current.onSelectionChange?.(models.filter(model => next.has(model.id) && model.selectable && !model.already_added));
  };

  const fetchModels = async () => {
    if (busy.current || disabled) return;
    callbacks.current.onUsed?.();
    setOpen(true);
    setLoading(true);
    setError(null);
    setResult(null);
    setImportResult(null);
    publishSelection(new Set());
    busy.current = true;
    const version = ++requestVersion.current;
    try {
      const response = await providerAPI.discoverModels(providerId, credentials);
      if (version !== requestVersion.current) return;
      // Deduplicate the view without changing provider-specific IDs.
      const models = [...new Map(response.data.models.map(model => [model.id, newInstance ? { ...model, already_added: false } : model])).values()];
      setResult({ ...response.data, models });
    } catch (fetchError) {
      if (version === requestVersion.current) setError(extractErrorMessage(fetchError));
    } finally {
      if (version === requestVersion.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };

  const visibleModels = useMemo(() => {
    const search = query.trim().toLowerCase();
    return (result?.models ?? []).filter(model => !search || model.id.toLowerCase().includes(search) || model.name.toLowerCase().includes(search));
  }, [query, result]);
  const eligibleVisible = visibleModels.filter(model => model.selectable && !model.already_added);
  const allVisibleSelected = eligibleVisible.length > 0 && eligibleVisible.every(model => selected.has(model.id));
  const selectedModels = (result?.models ?? []).filter(model => selected.has(model.id) && model.selectable && !model.already_added);
  const allAdded = !!result?.models.length && result.models.every(model => model.already_added);

  const importModels = async () => {
    if (busy.current || disabled || !selectedModels.length) return;
    busy.current = true;
    setImporting(true);
    setError(null);
    const version = requestVersion.current;
    try {
      const response = await providerAPI.importModels(providerId, selectedModels.map(toModelImport));
      if (version !== requestVersion.current) return;
      setImportResult(response.data);
      const added = new Set([...response.data.added, ...response.data.skipped]);
      setResult(previous => previous ? { ...previous, models: previous.models.map(model => added.has(model.id) ? { ...model, already_added: true } : model) } : previous);
      publishSelection(new Set(selectedModels.filter(model => !added.has(model.id)).map(model => model.id)));
      callbacks.current.onImported?.();
    } catch (importError) {
      if (version === requestVersion.current) setError(extractErrorMessage(importError));
    } finally {
      if (version === requestVersion.current) {
        busy.current = false;
        setImporting(false);
      }
    }
  };

  const close = () => {
    requestVersion.current += 1;
    busy.current = false;
    setOpen(false);
    setLoading(false);
    setError(null);
    setImportResult(null);
    // Folding a form section must not discard a model selection pending save.
    if (mode === 'import') {
      setResult(null);
      publishSelection(new Set());
    }
  };

  return (
    <section aria-label={t('discovery.title')} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => !open && mode === 'stage' && result ? setOpen(true) : fetchModels()} disabled={disabled || loading || importing} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : open ? <RefreshCw className="h-4 w-4" /> : <Download className="h-4 w-4" />}
          {loading ? t('discovery.loading') : open ? t('discovery.refresh') : mode === 'stage' && result ? t('discovery.reviewSelection') : t('discovery.title')}
        </button>
        {mode === 'stage' && <span className="text-xs text-gray-500">{t('discovery.stagedHint')}</span>}
        {mode === 'stage' && !open && selectedModels.length > 0 && <span className="text-xs font-medium text-slate-700">{t('discovery.pending', { count: selectedModels.length })}</span>}
        {open && <button type="button" onClick={close} disabled={importing || disabled} aria-label={t('discovery.close')} className="ml-auto p-1 text-gray-400 hover:text-gray-700 disabled:opacity-50"><X className="h-4 w-4" /></button>}
      </div>
      {open && (
        <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3">
          {error && <div role="alert" className="rounded-md bg-red-50 p-2 text-sm text-red-700">{error}</div>}
          {result && (
            <>
              <div className={`space-y-1 text-xs ${result.source === 'catalog' || result.status !== 'success' ? 'text-amber-800' : 'text-gray-600'}`}>
                <p className="font-medium">{t(`discovery.source.${result.source}`)}</p>
                {result.source === 'catalog' && <p>{t('discovery.catalogHint')}</p>}
                {result.message && <p>{t(`discovery.codes.${result.code}`, { defaultValue: result.message })}</p>}
                {result.fetched_at && <p>{t('discovery.fetchedAt', { time: new Date(result.fetched_at).toLocaleString(i18n.language) })}</p>}
                {result.truncated && <p>{t('discovery.truncated')}</p>}
                {result.models.length > 0 && <p>{t('discovery.metadataHint')}</p>}
              </div>
              {result.models.length > 0 ? (
                <>
                  <label className="relative block">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-gray-400" />
                    <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t('discovery.search')} aria-label={t('discovery.search')} className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-8 pr-3 text-sm" />
                  </label>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-600">
                    <span>{t(mode === 'stage' ? 'discovery.pending' : 'discovery.selected', { count: selectedModels.length })}</span>
                    <button type="button" disabled={!eligibleVisible.length || importing || disabled} onClick={() => {
                      const next = new Set(selected);
                      eligibleVisible.forEach(model => allVisibleSelected ? next.delete(model.id) : next.add(model.id));
                      publishSelection(next);
                    }} className="text-slate-700 hover:underline disabled:opacity-40">{t(allVisibleSelected ? 'discovery.deselectVisible' : 'discovery.selectVisible')}</button>
                  </div>
                  {allAdded && <p className="text-sm text-green-700">{t('discovery.allAdded')}</p>}
                  <div className="max-h-64 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200 bg-white">
                    {visibleModels.map(model => (
                      <label key={model.id} className={`flex items-start gap-2.5 px-3 py-2 ${!model.selectable || model.already_added ? 'text-gray-400' : 'cursor-pointer hover:bg-gray-50'}`}>
                        <input type="checkbox" aria-label={model.id} checked={selected.has(model.id)} disabled={disabled || importing || !model.selectable || model.already_added} onChange={() => {
                          const next = new Set(selected);
                          if (next.has(model.id)) next.delete(model.id); else next.add(model.id);
                          publishSelection(next);
                        }} className="mt-1 rounded border-gray-300" />
                        <div className="min-w-0 flex-1">
                          <p className="break-words text-sm font-medium">{model.name || model.id}</p>
                          <p className="break-all font-mono text-xs">{model.id}</p>
                          {model.context_window !== undefined && <p className="mt-0.5 text-xs">{t('discovery.contextWindow', { count: model.context_window })}</p>}
                          {model.already_added ? <p className="mt-0.5 text-xs">{t('discovery.alreadyAdded')}</p> : !model.selectable && <p className="mt-0.5 text-xs">{t(`discovery.unavailable.${model.unavailable_reason}`, { defaultValue: model.unavailable_reason || t('discovery.unavailable.unknown') })}</p>}
                        </div>
                      </label>
                    ))}
                    {visibleModels.length === 0 && <p className="p-4 text-center text-sm text-gray-500">{t('discovery.noMatches')}</p>}
                  </div>
                  {mode === 'import' && <button type="button" onClick={importModels} disabled={disabled || importing || !selectedModels.length} className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-sm text-white hover:bg-slate-900 disabled:opacity-40">{importing && <Loader2 className="h-4 w-4 animate-spin" />}{t(importing ? 'discovery.importing' : 'discovery.import', { count: selectedModels.length })}</button>}
                </>
              ) : <p className="py-3 text-sm text-gray-600">{t(result.status === 'error' ? 'discovery.failed' : 'discovery.empty')}</p>}
            </>
          )}
          {importResult && <ModelImportSummary result={importResult} />}
        </div>
      )}
    </section>
  );
}
