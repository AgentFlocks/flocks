import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import { providerAPI } from '@/api/provider';
import { extractErrorMessage } from '@/utils/error';
import type {
  DiscoveredModel,
  InventoryModelInput,
  ModelInventoryDraft,
  ModelImportInput,
  ModelImportResult,
} from '@/types';

export function toModelImport(model: DiscoveredModel): ModelImportInput {
  const {
    id,
    name,
    context_window,
    max_output_tokens,
    supports_vision,
    supports_tools,
    supports_reasoning,
    supports_streaming,
  } = model;
  return {
    id,
    name,
    context_window,
    max_output_tokens,
    supports_vision,
    supports_tools,
    supports_reasoning,
    supports_streaming,
  };
}

export function ModelImportSummary({ result }: { result: ModelImportResult }) {
  const { t } = useTranslation('model');
  return (
    <div
      role="status"
      className={`text-xs ${result.failed.length ? 'text-amber-800' : 'text-green-700'}`}
    >
      {t('discovery.importSummary', {
        added: result.added.length,
        skipped: result.skipped.length,
        failed: result.failed.length,
      })}
      {result.failed.map((item) => (
        <p key={item.id}>
          {item.id}: {item.error}
        </p>
      ))}
    </div>
  );
}

type Row = InventoryModelInput & { existing: boolean; selectable: boolean };
const AUTO_SELECT_MODEL_LIMIT = 15;
const capabilities = [
  'supports_tools',
  'supports_vision',
  'supports_streaming',
  'supports_reasoning',
] as const;

function ModelEditor({
  row,
  onApply,
  onClose,
}: {
  row: Row;
  onApply: (model: Row) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation('model');
  const uid = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(row);
  const [context, setContext] = useState(row.context_window?.toString() ?? '');
  const [output, setOutput] = useState(row.max_output_tokens?.toString() ?? '');
  const validName = !!draft.name.trim()
    && draft.name.trim().length <= 512
    && Array.from(draft.name).every(char => char.charCodeAt(0) >= 32);
  const valid = [context, output].every(
    (value) => !value || (/^\d+$/.test(value) && Number(value) > 0 && Number(value) <= 100_000_000),
  );
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: 'start' });
  }, []);
  return (
    <div
      ref={ref}
      role="group"
      aria-label={t('compact.editModel')}
      className="scroll-mt-4 space-y-4 rounded-lg border border-gray-200 bg-gray-50 p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-sm font-semibold">{t('compact.editModel')}</h4>
        <span className="truncate text-xs text-gray-500">{row.id}</span>
      </div>
      <div className="space-y-1 text-sm">
        <label htmlFor={`${uid}-name`}>{t('form.displayName')}</label>
        <input
          id={`${uid}-name`}
          value={draft.name}
          maxLength={512}
          aria-invalid={!validName}
          aria-describedby={!validName ? `${uid}-name-error` : undefined}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          className="w-full rounded-md border border-gray-300 px-3 py-2"
        />
        {!validName && <span id={`${uid}-name-error`} role="alert" className="block text-xs text-red-600">{t('compact.invalidModelName')}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {(
          [
            ['context', context, setContext, 'form.contextWindow'],
            ['output', output, setOutput, 'form.maxOutput'],
          ] as const
        ).map(([key, value, setter, label]) => (
          <div key={key} className="space-y-1 text-sm">
            <label htmlFor={`${uid}-${key}`}>{t(label)}</label>
            <input
              id={`${uid}-${key}`}
              type="number"
              min={1}
              max={100_000_000}
              step={1}
              value={value}
              placeholder="Auto"
              onChange={(e) => setter(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2"
            />
          </div>
        ))}
      </div>
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">{t('form.capabilities')}</legend>
        <div className="grid grid-cols-2 gap-3">
          {capabilities.map((field) => {
            const enabled = draft[field] ?? field !== 'supports_vision';
            return (
              <label key={field} className="flex items-center gap-2 text-sm">
                <button
                  type="button"
                  role="switch"
                  aria-label={t(`compact.${field}`)}
                  aria-checked={enabled}
                  onClick={() => setDraft({ ...draft, [field]: !enabled })}
                  className={`relative h-5 w-9 shrink-0 rounded-full ${enabled ? 'bg-green-600' : 'bg-gray-400'}`}
                >
                  <span
                    className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${enabled ? 'translate-x-4' : ''}`}
                  />
                </button>
                {t(`compact.${field}`)}
              </label>
            );
          })}
        </div>
      </fieldset>
      {!valid && (
        <p role="alert" className="text-xs text-red-600">
          {t('discovery.invalidLimit')}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        >
          {t('compact.cancel')}
        </button>
        <button
          type="button"
          disabled={!valid || !validName}
          onClick={() =>
            onApply({
              ...draft,
              name: draft.name.trim(),
              context_window: context ? Number(context) : null,
              max_output_tokens: output ? Number(output) : null,
              edited: true,
            })
          }
          className="rounded-md bg-violet-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
        >
          {t('compact.apply')}
        </button>
      </div>
    </div>
  );
}

interface Props {
  providerId: string;
  credentials?: { api_key?: string; base_url?: string };
  mode: 'stage' | 'import';
  newInstance?: boolean;
  existingModelIds?: string[];
  disabled?: boolean;
  onUsed?: () => void;
  onSelectionChange?: (models: DiscoveredModel[]) => void;
  onInventoryChange?: (draft: ModelInventoryDraft) => void;
  onReadyChange?: (ready: boolean) => void;
  onImported?: () => void;
  onAddModel?: () => void;
}

export default function ModelDiscovery({
  providerId,
  credentials,
  mode,
  newInstance = false,
  existingModelIds,
  disabled,
  onUsed,
  onSelectionChange,
  onInventoryChange,
  onReadyChange,
  onImported,
  onAddModel,
}: Props) {
  const { t } = useTranslation('model');
  const [rows, setRows] = useState<Row[]>([]);
  const [deleted, setDeleted] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(!existingModelIds || newInstance);
  const [dirty, setDirty] = useState(false);
  const [editor, setEditor] = useState<Row | null>(null);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [summary, setSummary] = useState<ModelImportResult | null>(null);
  const request = useRef(0);
  const callbacks = useRef({
    onUsed,
    onSelectionChange,
    onInventoryChange,
    onReadyChange,
    onImported,
  });
  callbacks.current = { onUsed, onSelectionChange, onInventoryChange, onReadyChange, onImported };
  const shouldLoad = existingModelIds !== undefined && !newInstance;
  const inventoryKey = JSON.stringify(existingModelIds ?? []);
  const lastInventoryKey = useRef(inventoryKey);
  const deletedRef = useRef(deleted);
  deletedRef.current = deleted;
  const apiKey = credentials?.api_key,
    baseUrl = credentials?.base_url;
  const previousConnection = useRef({ providerId, apiKey, baseUrl });

  useEffect(() => {
    const version = ++request.current;
    setRows([]);
    setDeleted([]);
    setEditor(null);
    setDirty(false);
    setError(null);
    setNotice(null);
    setSummary(null);
    setQuery('');
    setLoaded(!shouldLoad);
    setLoading(shouldLoad);
    if (shouldLoad) {
      providerAPI
        .getModelInventory(providerId)
        .then((response) => {
          if (version !== request.current) return;
          setRows(
            response.data.models.map((model) => ({ ...model, existing: true, selectable: true })),
          );
          setLoaded(true);
        })
        .catch((reason) => {
          if (version === request.current) setError(extractErrorMessage(reason));
        })
        .finally(() => {
          if (version === request.current) setLoading(false);
        });
    }
    return () => {
      request.current += 1;
    };
  }, [providerId, shouldLoad, newInstance]);

  useEffect(() => {
    const previous = previousConnection.current;
    previousConnection.current = { providerId, apiKey, baseUrl };
    if (
      previous.providerId !== providerId ||
      (previous.apiKey === apiKey && previous.baseUrl === baseUrl) ||
      !loaded
    )
      return;
    request.current += 1;
    setLoading(false);
    setError(null);
    setNotice(null);
    setSummary(null);
    setEditor((current) => current?.existing ? current : null);
    setRows((current) => current.filter((row) => row.existing));
    // Saved-model edits, switches, and explicit deletions belong to this
    // provider, even when the credentials for the next discovery change.
  }, [providerId, apiKey, baseUrl, loaded]);

  useEffect(() => {
    if (!shouldLoad || lastInventoryKey.current === inventoryKey) return;
    lastInventoryKey.current = inventoryKey;
    let cancelled = false;
    providerAPI
      .getModelInventory(providerId)
      .then((response) => {
        if (cancelled) return;
        const incoming = new Map(response.data.models.map((model) => [model.id, model]));
        setRows((previous) => {
          const next = previous.map((row) =>
            incoming.has(row.id)
              ? { ...row, existing: true }
              : row.existing
                ? { ...row, existing: false, enabled: false }
                : row,
          );
          const known = new Set(next.map((row) => row.id));
          response.data.models.forEach((model) => {
            if (!known.has(model.id) && !deletedRef.current.includes(model.id))
              next.push({ ...model, existing: true, selectable: true });
          });
          return next;
        });
      })
      .catch((reason) => {
        if (!cancelled) setError(extractErrorMessage(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [inventoryKey, providerId, shouldLoad]);

  const draft = useMemo<ModelInventoryDraft>(
    () => ({
      models: rows
        .filter((row) => row.existing || row.enabled)
        .map(({ existing: _existing, selectable: _selectable, ...model }) => model),
      deleted,
    }),
    [rows, deleted],
  );
  useEffect(() => {
    callbacks.current.onInventoryChange?.(draft);
    callbacks.current.onSelectionChange?.(
      rows
        .filter((row) => row.enabled && !row.existing)
        .map((row) => ({
          ...row,
          context_window: row.context_window ?? undefined,
          max_output_tokens: row.max_output_tokens ?? undefined,
          already_added: false,
          selectable: row.selectable,
        })),
    );
  }, [draft, rows]);
  useEffect(() => {
    callbacks.current.onReadyChange?.(!loading && !saving && !editor && loaded);
  }, [loading, saving, editor, loaded]);

  const fetchModels = async () => {
    if (disabled || loading || saving || editor) return;
    const version = ++request.current;
    setLoading(true);
    setError(null);
    setNotice(null);
    setSummary(null);
    callbacks.current.onUsed?.();
    try {
      let saved = rows;
      if (shouldLoad && !loaded) {
        const response = await providerAPI.getModelInventory(providerId);
        saved = response.data.models.map((model) => ({
          ...model,
          existing: true,
          selectable: true,
        }));
        if (version !== request.current) return;
        setRows(saved);
        setLoaded(true);
      }
      const response = await providerAPI.discoverModels(providerId, credentials);
      if (version !== request.current) return;
      if (response.data.status === 'error') {
        setError(
          t(`discovery.codes.${response.data.code}`, { defaultValue: response.data.message }),
        );
        return;
      }
      const byId = new Map(saved.map((row) => [row.id, row]));
      const candidates = response.data.models.filter((model) => !deleted.includes(model.id));
      const autoSelectNewModels =
        new Set([...byId.keys(), ...candidates.map((model) => model.id)]).size <= AUTO_SELECT_MODEL_LIMIT;
      candidates.forEach((model) => {
        if (!byId.has(model.id))
          byId.set(model.id, {
            id: model.id,
            name: model.name,
            enabled: autoSelectNewModels && model.selectable,
            selectable: model.selectable,
            existing: false,
            supports_tools: model.supports_tools,
            supports_vision: model.supports_vision,
            supports_reasoning: model.supports_reasoning,
            supports_streaming: model.supports_streaming,
          });
      });
      setRows([...byId.values()]);
      setLoaded(true);
      setDirty(true);
      if (response.data.source === 'catalog') setNotice(t('compact.catalogNotice'));
    } catch (reason) {
      if (version === request.current) setError(extractErrorMessage(reason));
    } finally {
      if (version === request.current) setLoading(false);
    }
  };

  const saveModels = async () => {
    if (disabled || loading || saving || editor || !loaded) return;
    setSaving(true);
    setError(null);
    try {
      const result = await providerAPI.saveModelInventory(providerId, draft);
      setSummary(result.data);
      setRows((previous) =>
        previous.map((row) => ({ ...row, existing: row.existing || row.enabled, edited: false })),
      );
      setDeleted([]);
      setDirty(false);
      callbacks.current.onImported?.();
    } catch (reason) {
      setError(extractErrorMessage(reason));
    } finally {
      setSaving(false);
    }
  };
  const busy = disabled || loading || saving || !!editor;
  const visible = rows.filter(
    (row) =>
      !query.trim() || `${row.name} ${row.id}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const enabled = rows.filter((row) => row.enabled).length;
  const selectAll = (value: boolean) => {
    setRows((previous) =>
      previous.map((row) => (row.selectable ? { ...row, enabled: value } : row)),
    );
    setDirty(true);
  };
  return (
    <section aria-label={t('compact.modelList')} className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">
          {t('compact.modelList')}{' '}
          <span className="font-normal text-gray-500">
            {t('compact.count', { count: rows.length, enabled })}
          </span>
        </h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={fetchModels}
            disabled={busy}
            className="inline-flex items-center gap-1 text-xs text-gray-600 hover:text-violet-700 disabled:opacity-40"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {t(rows.length ? 'compact.refresh' : 'discovery.title')}
          </button>
          {onAddModel && (
            <button
              type="button"
              onClick={onAddModel}
              disabled={busy}
              aria-label={t('compact.manualAdd')}
              className="p-1 text-gray-600"
            >
              <Plus className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-xs text-amber-700">
          {notice}
        </p>
      )}
      {rows.length > 0 && (
        <>
          <div className="flex items-center justify-between gap-3">
            <label className="relative min-w-0 flex-1">
              <Search className="absolute left-2 top-2 h-3.5 w-3.5 text-gray-400" />
              <input
                type="search"
                aria-label={t('discovery.search')}
                placeholder={t('discovery.search')}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="w-full rounded-md border border-gray-200 py-1.5 pl-7 pr-2 text-xs"
              />
            </label>
            <div className="flex shrink-0 gap-1">
              <button
                type="button"
                onClick={() => selectAll(true)}
                disabled={busy}
                className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 disabled:opacity-40"
              >
                {t('compact.selectAll')}
              </button>
              <button
                type="button"
                onClick={() => selectAll(false)}
                disabled={busy}
                className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 disabled:opacity-40"
              >
                {t('compact.selectNone')}
              </button>
            </div>
          </div>
          {editor && (
            <ModelEditor
              row={editor}
              onClose={() => setEditor(null)}
              onApply={(model) => {
                setRows((previous) => previous.map((row) => (row.id === model.id ? model : row)));
                setDirty(true);
                setEditor(null);
              }}
            />
          )}
          <ul
            aria-label={t('compact.modelList')}
            className="max-h-96 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200"
          >
            {visible.map((row) => (
              <li key={row.id} className="flex items-center gap-2 px-3 py-3">
                <span className="min-w-0 flex-1 break-all text-sm text-gray-800">
                  {row.name || row.id}
                </span>
                <button
                  type="button"
                  aria-label={`${t('compact.editModel')} ${row.id}`}
                  title={t('compact.editModel')}
                  disabled={busy || !row.selectable}
                  onClick={() => setEditor(row)}
                  className="p-1 text-gray-400 hover:text-gray-700 disabled:opacity-40"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  aria-label={`${t('compact.deleteModel')} ${row.id}`}
                  title={t('compact.deleteModel')}
                  disabled={busy}
                  onClick={() => {
                    setRows((previous) => previous.filter((model) => model.id !== row.id));
                    if (row.existing) setDeleted((previous) => [...previous, row.id]);
                    setDirty(true);
                  }}
                  className="p-1 text-gray-400 hover:text-red-600 disabled:opacity-40"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  role="switch"
                  aria-label={`${t('compact.enableModel')} ${row.id}`}
                  aria-checked={row.enabled}
                  disabled={busy || !row.selectable}
                  onClick={() => {
                    setRows((previous) =>
                      previous.map((model) =>
                        model.id === row.id ? { ...model, enabled: !model.enabled } : model,
                      ),
                    );
                    setDirty(true);
                  }}
                  className={`relative ml-1 h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${row.enabled ? 'bg-violet-600' : 'bg-gray-300'}`}
                >
                  <span
                    className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${row.enabled ? 'translate-x-4' : ''}`}
                  />
                </button>
              </li>
            ))}
            {!visible.length && (
              <li className="p-4 text-center text-xs text-gray-400">{t('discovery.noMatches')}</li>
            )}
          </ul>
        </>
      )}
      {!loading && !rows.length && (
        <p className="py-5 text-center text-xs text-gray-400">{t('compact.empty')}</p>
      )}
      {mode === 'import' && dirty && (
        <div className="flex justify-end">
          <button
            type="button"
            disabled={busy || !loaded}
            onClick={saveModels}
            className="rounded-md bg-violet-600 px-3 py-2 text-sm text-white disabled:opacity-40"
          >
            {saving ? t('compact.saving') : t('compact.saveModels')}
          </button>
        </div>
      )}
      {summary && !dirty && !editor && (
        <p role="status" className="text-xs text-green-700">
          {t('compact.modelsSaved')}
        </p>
      )}
    </section>
  );
}
