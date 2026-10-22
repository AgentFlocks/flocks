import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeftRight, CheckSquare, Download, Loader2, Pencil, Search, SlidersHorizontal, X } from 'lucide-react';
import { providerAPI } from '@/api/provider';
import { extractErrorMessage } from '@/utils/error';
import type { DiscoveredModel, ModelDiscoveryResult, ModelImportInput, ModelImportResult } from '@/types';

const LIMIT_FIELDS = ['context_window', 'max_output_tokens'] as const;
const CAPABILITY_FIELDS = ['supports_tools', 'supports_vision', 'supports_reasoning'] as const;
type LimitField = typeof LIMIT_FIELDS[number];
type CapabilityField = typeof CAPABILITY_FIELDS[number];
type ModelMetadata = Pick<ModelImportInput, LimitField | CapabilityField>;
type CapabilityFilter = 'all' | 'supported' | 'unsupported' | 'unknown';
type LimitFilter = { mode: 'all' | 'minimum' | 'unknown'; minimum: string };
type LimitDraft = { mode: 'keep' | 'value' | 'unknown'; value: string };
type MetadataDraft = Record<LimitField, LimitDraft> & Record<CapabilityField, 'keep' | 'unknown' | 'true' | 'false'>;
type ConfigurationEditor = { ids: string[]; batch: boolean; name?: string; draft: MetadataDraft };

function emptyLimitFilters(): Record<LimitField, LimitFilter> {
  return { context_window: { mode: 'all', minimum: '' }, max_output_tokens: { mode: 'all', minimum: '' } };
}

function emptyCapabilityFilters(): Record<CapabilityField, CapabilityFilter> {
  return { supports_tools: 'all', supports_vision: 'all', supports_reasoning: 'all' };
}

/** Empty is unconfirmed, never zero. Null denotes an invalid explicit value. */
function parseTokenLimit(value: string): number | undefined | null {
  const text = value.trim();
  if (!text) return undefined;
  const number = Number(text);
  return /^\d+$/.test(text) && Number.isSafeInteger(number) && number >= 1 && number <= 100_000_000 ? number : null;
}

function makeMetadataDraft(model?: DiscoveredModel): MetadataDraft {
  const limit = (field: LimitField): LimitDraft => ({
    mode: model ? model[field] === undefined ? 'unknown' : 'value' : 'keep',
    value: model?.[field] === undefined ? '' : String(model[field]),
  });
  const capability = (field: CapabilityField): MetadataDraft[CapabilityField] => model
    ? model[field] === undefined ? 'unknown' : model[field] ? 'true' : 'false'
    : 'keep';
  return {
    context_window: limit('context_window'), max_output_tokens: limit('max_output_tokens'),
    supports_tools: capability('supports_tools'), supports_vision: capability('supports_vision'), supports_reasoning: capability('supports_reasoning'),
  };
}

function draftMetadata(draft: MetadataDraft): { metadata: ModelMetadata; invalid: LimitField[] } {
  const metadata: ModelMetadata = {};
  const invalid: LimitField[] = [];
  LIMIT_FIELDS.forEach(field => {
    if (draft[field].mode === 'keep') return;
    if (draft[field].mode === 'unknown') metadata[field] = undefined;
    else {
      const value = parseTokenLimit(draft[field].value);
      if (value === null || value === undefined) invalid.push(field);
      else metadata[field] = value;
    }
  });
  CAPABILITY_FIELDS.forEach(field => {
    if (draft[field] !== 'keep') metadata[field] = draft[field] === 'unknown' ? undefined : draft[field] === 'true';
  });
  return { metadata, invalid };
}

function MetadataEditor({ editor, disabled, onChange, onApply, onCancel }: {
  editor: ConfigurationEditor;
  disabled?: boolean;
  onChange: (draft: MetadataDraft) => void;
  onApply: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation('model');
  const editorId = useId();
  const editorRef = useRef<HTMLElement>(null);
  useEffect(() => {
    editorRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
    editorRef.current?.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled), button:not(:disabled)')?.focus({ preventScroll: true });
  }, []);
  const { metadata, invalid } = draftMetadata(editor.draft);
  return (
    <section ref={editorRef} aria-label={t('discovery.configurationTitle')} className="scroll-mt-4 space-y-3 rounded-lg border-2 border-blue-200 bg-blue-50 p-3">
      <div>
        <p className="text-sm font-medium text-blue-950">{t(editor.batch ? 'discovery.batchConfiguration' : 'discovery.configurationTitle', { count: editor.ids.length })}{!editor.batch && <span className="ml-2 break-all font-mono">{editor.name}</span>}</p>
        <p className="mt-1 text-xs text-blue-900">{t('discovery.editingHint')}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {LIMIT_FIELDS.map(field => (
          <div key={field} className="space-y-1">
            <label className="block text-xs font-medium text-gray-800" htmlFor={`${editorId}-${field}`}>{t(`discovery.fields.${field}`)}</label>
            {editor.batch && (
              <select aria-label={`${t('discovery.batchField')} ${t(`discovery.fields.${field}`)}`} value={editor.draft[field].mode} disabled={disabled} onChange={event => onChange({ ...editor.draft, [field]: { ...editor.draft[field], mode: event.target.value as LimitDraft['mode'] } })} className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm">
                <option value="keep">{t('discovery.keepValue')}</option>
                <option value="value">{t('discovery.setValue')}</option>
                <option value="unknown">{t('discovery.setUnknown')}</option>
              </select>
            )}
            <input id={`${editorId}-${field}`} type="number" min={1} max={100_000_000} step={1} value={editor.draft[field].value} disabled={disabled || editor.batch && editor.draft[field].mode !== 'value'} placeholder={t('discovery.unconfirmed')} aria-invalid={invalid.includes(field)} onChange={event => onChange({ ...editor.draft, [field]: { mode: event.target.value === '' ? 'unknown' : 'value', value: event.target.value } })} className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm disabled:bg-gray-100" />
            {invalid.includes(field) && <p role="alert" className="text-xs text-red-700">{t('discovery.invalidLimit')}</p>}
          </div>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {CAPABILITY_FIELDS.map(field => (
          <label key={field} className="block space-y-1 text-xs font-medium text-gray-800">
            <span>{t(`discovery.fields.${field}`)}</span>
            <select aria-label={t(`discovery.fields.${field}`)} value={editor.draft[field]} disabled={disabled} onChange={event => onChange({ ...editor.draft, [field]: event.target.value as MetadataDraft[CapabilityField] })} className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm font-normal">
              {editor.batch && <option value="keep">{t('discovery.keepValue')}</option>}
              <option value="unknown">{t('discovery.unconfirmed')}</option>
              <option value="true">{t('discovery.supported')}</option>
              <option value="false">{t('discovery.unsupported')}</option>
            </select>
          </label>
        ))}
      </div>
      <p className="text-xs text-gray-600">{t('discovery.configurationHint')}</p>
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">{t('discovery.cancelConfiguration')}</button>
        <button type="button" onClick={onApply} disabled={disabled || invalid.length > 0 || Object.keys(metadata).length === 0} className="rounded-md bg-blue-700 px-3 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-40">{t('discovery.applyConfiguration')}</button>
      </div>
    </section>
  );
}

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
  /** When supplied, this inventory supersedes discovery's older already_added flags. */
  existingModelIds?: string[];
  disabled?: boolean;
  onUsed?: () => void;
  onSelectionChange?: (models: DiscoveredModel[]) => void;
  onReadyChange?: (ready: boolean) => void;
  onImported?: () => void;
}

/** Discovery is read-only. Staged selections are applied only by the owning form. */
export default function ModelDiscovery({ providerId, credentials, mode, newInstance = false, existingModelIds, disabled, onUsed, onSelectionChange, onReadyChange, onImported }: ModelDiscoveryProps) {
  const { t, i18n } = useTranslation('model');
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ModelDiscoveryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [limitFilters, setLimitFilters] = useState(emptyLimitFilters);
  const [capabilityFilters, setCapabilityFilters] = useState(emptyCapabilityFilters);
  const [overrides, setOverrides] = useState<Record<string, ModelMetadata>>({});
  const [editor, setEditor] = useState<ConfigurationEditor | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmedImports, setConfirmedImports] = useState<{ inventoryKey: string | undefined; ids: Set<string> } | null>(null);
  const [importResult, setImportResult] = useState<ModelImportResult | null>(null);
  const requestVersion = useRef(0);
  const busy = useRef(false);
  const callbacks = useRef({ onUsed, onSelectionChange, onReadyChange, onImported });
  callbacks.current = { onUsed, onSelectionChange, onReadyChange, onImported };
  const apiKey = credentials?.api_key;
  const baseUrl = credentials?.base_url;
  // Compare membership rather than array identity: parent renders can allocate
  // the same inventory again before their refresh after an import completes.
  const existingModelKey = existingModelIds === undefined ? undefined : JSON.stringify([...new Set(existingModelIds)].sort());
  const inventoryKeyRef = useRef(existingModelKey);
  inventoryKeyRef.current = existingModelKey;

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
    setLimitFilters(emptyLimitFilters());
    setCapabilityFilters(emptyCapabilityFilters());
    setOverrides({});
    setEditor(null);
    setSelected(new Set());
    setConfirmedImports(null);
    setImportResult(null);
    callbacks.current.onSelectionChange?.([]);
    return () => { requestVersion.current += 1; };
  }, [providerId, apiKey, baseUrl, newInstance]);

  useEffect(() => { callbacks.current.onReadyChange?.(editor === null && !loading && !importing); }, [editor, loading, importing]);

  // An import acknowledgement remains visible immediately while the parent
  // refreshes. The next changed inventory then becomes authoritative again.
  useEffect(() => {
    setConfirmedImports(previous => previous && previous.inventoryKey !== existingModelKey ? null : previous);
  }, [existingModelKey]);

  const models = useMemo(() => {
    const existingIds = existingModelKey === undefined ? null : new Set<string>(JSON.parse(existingModelKey));
    return (result?.models ?? []).map(model => ({
      ...model,
      ...overrides[model.id],
      already_added: (!newInstance && (existingIds ? existingIds.has(model.id) : model.already_added))
        || !!(confirmedImports?.inventoryKey === existingModelKey && confirmedImports?.ids.has(model.id)),
    }));
  }, [result, newInstance, existingModelKey, confirmedImports, overrides]);
  const selectedModels = useMemo(
    () => models.filter(model => selected.has(model.id) && model.selectable && !model.already_added),
    [models, selected],
  );

  useEffect(() => {
    callbacks.current.onSelectionChange?.(selectedModels);
    // Once a model enters the inventory, forget any pending selection so a
    // later external deletion makes it an unchecked, addable candidate.
    setSelected(previous => previous.size === selectedModels.length && selectedModels.every(model => previous.has(model.id))
      ? previous
      : new Set(selectedModels.map(model => model.id)));
  }, [selectedModels]);

  const fetchModels = async () => {
    if (busy.current || disabled || editor) return;
    callbacks.current.onUsed?.();
    setOpen(true);
    setLoading(true);
    setError(null);
    setResult(null);
    setImportResult(null);
    setSelected(new Set());
    setConfirmedImports(null);
    setOverrides({});
    setEditor(null);
    busy.current = true;
    const version = ++requestVersion.current;
    try {
      const response = await providerAPI.discoverModels(providerId, credentials);
      if (version !== requestVersion.current) return;
      // Deduplicate the view without changing provider-specific IDs.
      const models = [...new Map(response.data.models.map(model => [model.id, model])).values()];
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
    return models.filter(model => {
      if (search && !model.id.toLowerCase().includes(search) && !model.name.toLowerCase().includes(search)) return false;
      return LIMIT_FIELDS.every(field => {
        const filter = limitFilters[field];
        if (filter.mode === 'all') return true;
        if (filter.mode === 'unknown') return model[field] === undefined;
        const minimum = parseTokenLimit(filter.minimum);
        return typeof minimum === 'number' && typeof model[field] === 'number' && model[field] >= minimum;
      }) && CAPABILITY_FIELDS.every(field => {
        const filter = capabilityFilters[field];
        if (filter === 'all') return true;
        if (filter === 'unknown') return typeof model[field] !== 'boolean';
        return model[field] === (filter === 'supported');
      });
    });
  }, [query, models, limitFilters, capabilityFilters]);
  const eligibleVisible = visibleModels.filter(model => model.selectable && !model.already_added);
  const checkedCount = models.filter(model => model.already_added || model.selectable && selected.has(model.id)).length;
  const existingCount = models.filter(model => model.already_added).length;
  const allAdded = models.length > 0 && models.every(model => model.already_added);

  const selectVisible = (operation: 'all' | 'invert' | 'none') => {
    if (operation === 'none') {
      setSelected(new Set());
      return;
    }
    const next = new Set(selected);
    eligibleVisible.forEach(model => {
      if (operation === 'invert' && next.has(model.id)) next.delete(model.id);
      else next.add(model.id);
    });
    setSelected(next);
  };

  const applyConfiguration = () => {
    if (!editor || disabled || importing) return;
    const { metadata, invalid } = draftMetadata(editor.draft);
    if (invalid.length) return;
    const editableIds = new Set(models.filter(model => model.selectable && !model.already_added).map(model => model.id));
    setOverrides(previous => {
      const next = { ...previous };
      editor.ids.forEach(id => { if (editableIds.has(id)) next[id] = { ...next[id], ...metadata }; });
      return next;
    });
    setEditor(null);
  };

  const importModels = async () => {
    if (busy.current || disabled || editor || !selectedModels.length) return;
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
      setConfirmedImports(previous => ({
        inventoryKey: inventoryKeyRef.current,
        ids: new Set([...(previous && previous.inventoryKey === inventoryKeyRef.current ? previous.ids : []), ...added]),
      }));
      setSelected(new Set(selectedModels.filter(model => !added.has(model.id)).map(model => model.id)));
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
      setSelected(new Set());
      setOverrides({});
      setEditor(null);
    }
  };

  return (
    <section aria-label={t('discovery.title')} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => !open && mode === 'stage' && result ? setOpen(true) : fetchModels()} disabled={disabled || loading || importing || !!editor} className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-40">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          {loading ? t('discovery.loading') : !open && mode === 'stage' && result ? t('discovery.reviewSelection') : t('discovery.checkAndFetch')}
        </button>
        {mode === 'stage' && <span className="text-xs text-gray-500">{t('discovery.stagedHint')}</span>}
        {mode === 'stage' && !open && checkedCount > 0 && (
          <div className="flex flex-wrap gap-x-3 text-xs font-medium text-slate-700">
            <span>{t('discovery.selected', { count: checkedCount })}</span>
            <span>{t('discovery.pending', { count: selectedModels.length })}</span>
          </div>
        )}
        {open && <button type="button" onClick={close} disabled={importing || disabled || !!editor} aria-label={t('discovery.close')} className="ml-auto p-1 text-gray-400 hover:text-gray-700 disabled:opacity-50"><X className="h-4 w-4" /></button>}
      </div>
      {open && (
        <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3">
          {error && <div role="alert" className="rounded-md bg-red-50 p-2 text-sm text-red-700">{error}</div>}
          {result && (
            <>
              <div role={result.status === 'error' ? 'alert' : 'status'} className={`space-y-1 rounded-lg border p-3 text-xs ${result.status === 'error' ? 'border-red-200 bg-red-50 text-red-800' : result.source === 'api' && result.status === 'success' ? 'border-green-200 bg-green-50 text-green-900' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
                <p className="font-semibold">{t(result.status === 'error' ? 'discovery.fetchFailedHeader' : result.source === 'api' && result.status === 'success' ? 'discovery.listReachable' : 'discovery.catalogOnly')}</p>
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
                  <fieldset aria-label={t('discovery.filtersTitle')} disabled={disabled || importing} className="space-y-3 rounded-lg border border-gray-200 bg-white p-3">
                    <legend className="px-1 text-xs font-medium text-gray-700">{t('discovery.filtersTitle')}</legend>
                    <div className="grid grid-cols-2 gap-3">
                      {LIMIT_FIELDS.map(field => (
                        <label key={field} className="block space-y-1 text-xs text-gray-700">
                          <span>{t(`discovery.fields.${field}`)}</span>
                          <select aria-label={`${t('discovery.filter')} ${t(`discovery.fields.${field}`)}`} value={limitFilters[field].mode} onChange={event => setLimitFilters(previous => ({ ...previous, [field]: { ...previous[field], mode: event.target.value as LimitFilter['mode'] } }))} className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm">
                            <option value="all">{t('discovery.filterAll')}</option>
                            <option value="minimum">{t('discovery.filterMinimum')}</option>
                            <option value="unknown">{t('discovery.unconfirmed')}</option>
                          </select>
                          {limitFilters[field].mode === 'minimum' && <input type="number" min={1} max={100_000_000} step={1} aria-label={`${t('discovery.minimumValue')} ${t(`discovery.fields.${field}`)}`} value={limitFilters[field].minimum} onChange={event => setLimitFilters(previous => ({ ...previous, [field]: { ...previous[field], minimum: event.target.value } }))} placeholder={t('discovery.minimumValue')} className="w-full rounded-md border border-gray-300 p-2 text-sm" />}
                        </label>
                      ))}
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      {CAPABILITY_FIELDS.map(field => (
                        <label key={field} className="block space-y-1 text-xs text-gray-700">
                          <span>{t(`discovery.fields.${field}`)}</span>
                          <select aria-label={`${t('discovery.filter')} ${t(`discovery.fields.${field}`)}`} value={capabilityFilters[field]} onChange={event => setCapabilityFilters(previous => ({ ...previous, [field]: event.target.value as CapabilityFilter }))} className="w-full rounded-md border border-gray-300 bg-white p-2 text-sm">
                            <option value="all">{t('discovery.filterAll')}</option>
                            <option value="supported">{t('discovery.supported')}</option>
                            <option value="unsupported">{t('discovery.unsupported')}</option>
                            <option value="unknown">{t('discovery.unconfirmed')}</option>
                          </select>
                        </label>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <p className="text-gray-500">{t('discovery.filterHint')}</p>
                      <button type="button" onClick={() => { setQuery(''); setLimitFilters(emptyLimitFilters()); setCapabilityFilters(emptyCapabilityFilters()); }} className="rounded-md border border-gray-300 px-2.5 py-1.5 text-gray-700 hover:bg-gray-50">{t('discovery.resetFilters')}</button>
                    </div>
                  </fieldset>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-600">
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      <span>{t('discovery.selected', { count: checkedCount })}</span>
                      <span>{t('discovery.existingCount', { count: existingCount })}</span>
                      <span>{t('discovery.pending', { count: selectedModels.length })}</span>
                      <span>{t('discovery.visibleCount', { count: visibleModels.length, total: models.length })}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 rounded-lg border border-slate-200 bg-white p-2">
                    <button type="button" disabled={!eligibleVisible.length || importing || disabled || !!editor} onClick={() => selectVisible('all')} className="inline-flex items-center gap-1.5 rounded-md border border-slate-400 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-900 hover:bg-slate-100 disabled:opacity-40"><CheckSquare className="h-4 w-4" />{t('discovery.selectVisible')}</button>
                    <button type="button" disabled={!eligibleVisible.length || importing || disabled || !!editor} onClick={() => selectVisible('invert')} className="inline-flex items-center gap-1.5 rounded-md border border-slate-400 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-900 hover:bg-slate-100 disabled:opacity-40"><ArrowLeftRight className="h-4 w-4" />{t('discovery.invertVisible')}</button>
                    <button type="button" disabled={!selectedModels.length || importing || disabled || !!editor} onClick={() => selectVisible('none')} className="inline-flex items-center gap-1.5 rounded-md border border-slate-400 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-40"><X className="h-4 w-4" />{t('discovery.deselectVisible')}</button>
                    <button type="button" disabled={!selectedModels.length || importing || disabled || !!editor} onClick={() => setEditor({ ids: selectedModels.map(model => model.id), batch: true, draft: makeMetadataDraft() })} className="inline-flex items-center gap-1.5 rounded-md border border-blue-300 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-900 hover:bg-blue-100 disabled:opacity-40"><SlidersHorizontal className="h-4 w-4" />{t('discovery.configureSelected', { count: selectedModels.length })}</button>
                    <p className="w-full text-xs text-gray-500">{t('discovery.bulkSelectionHint')}</p>
                  </div>
                  {editor && <MetadataEditor editor={editor} disabled={disabled || importing || !models.some(model => editor.ids.includes(model.id) && model.selectable && !model.already_added)} onChange={draft => setEditor(previous => previous ? { ...previous, draft } : previous)} onApply={applyConfiguration} onCancel={() => setEditor(null)} />}
                  {allAdded && <p className="text-sm text-green-700">{t('discovery.allAdded')}</p>}
                  <div role="list" aria-label={t('discovery.candidateList')} className="max-h-80 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200 bg-white">
                    {visibleModels.map(model => (
                      <div role="listitem" key={model.id} className={`space-y-2 px-3 py-3 ${!model.selectable || model.already_added ? 'text-gray-400' : 'text-gray-800'}`}>
                        <div className="flex items-start justify-between gap-3">
                          <label className="flex min-w-0 flex-1 items-start gap-2.5">
                            <input type="checkbox" aria-label={model.id} checked={model.already_added || model.selectable && selected.has(model.id)} disabled={disabled || importing || !!editor || !model.selectable || model.already_added} onChange={() => {
                              const next = new Set(selected);
                              if (next.has(model.id)) next.delete(model.id); else next.add(model.id);
                              setSelected(next);
                            }} className="mt-1 rounded border-gray-300" />
                            <span className="min-w-0">
                              <span className="block break-words text-sm font-medium">{model.name || model.id}</span>
                              <span className="block break-all font-mono text-xs">{model.id}</span>
                            </span>
                          </label>
                          {model.selectable && !model.already_added && <button type="button" aria-label={`${t('discovery.configureModel')} ${model.id}`} disabled={disabled || importing || !!editor} onClick={() => setEditor({ ids: [model.id], batch: false, name: model.id, draft: makeMetadataDraft(model) })} className="inline-flex shrink-0 items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-40"><Pencil className="h-3.5 w-3.5" />{t('discovery.configureModel')}</button>}
                        </div>
                        <div className="ml-6 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                          {LIMIT_FIELDS.map(field => <span key={field}>{t(`discovery.fields.${field}`)}: {model[field] === undefined ? t('discovery.unconfirmed') : model[field].toLocaleString(i18n.language)}</span>)}
                          {CAPABILITY_FIELDS.map(field => <span key={field}>{t(`discovery.fields.${field}`)}: {t(model[field] === undefined ? 'discovery.unconfirmed' : model[field] ? 'discovery.supported' : 'discovery.unsupported')}</span>)}
                        </div>
                        {!!overrides[model.id] && <p className="ml-6 text-xs text-blue-800">{t('discovery.manualConfiguration')}</p>}
                        {model.already_added ? <p className="ml-6 text-xs">{t('discovery.alreadyAdded')}</p> : !model.selectable && <p className="ml-6 text-xs">{t(`discovery.unavailable.${model.unavailable_reason}`, { defaultValue: model.unavailable_reason || t('discovery.unavailable.unknown') })}</p>}
                      </div>
                    ))}
                    {visibleModels.length === 0 && <p className="p-4 text-center text-sm text-gray-500">{t('discovery.noMatches')}</p>}
                  </div>
                  {mode === 'import' && <button type="button" onClick={importModels} disabled={disabled || importing || !!editor || !selectedModels.length} className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-sm text-white hover:bg-slate-900 disabled:opacity-40">{importing && <Loader2 className="h-4 w-4 animate-spin" />}{t(importing ? 'discovery.importing' : 'discovery.import', { count: selectedModels.length })}</button>}
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
