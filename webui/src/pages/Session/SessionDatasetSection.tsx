import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useInRouterContext } from 'react-router-dom';
import { ArrowRight, Check, Database, Plus, RefreshCw, X } from 'lucide-react';
import { knowledgebaseAPI, type Dataset, type SessionDatasets } from '@/api/knowledgebase';
import { useOptionalToast } from '@/components/common/Toast';
import { Modal } from '@/pages/Workspace/knowledge/ui';
import './SessionDatasetSection.css';

const buttonClass = 'session-knowledge-button';

class DatasetBoundary extends Component<{ children: ReactNode; resetKey: number }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  componentDidUpdate(previous: { resetKey: number }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: false });
  }
  render() {
    if (this.state.error) return <BoundaryFallback onRetry={() => this.setState({ error: false })} />;
    return this.props.children;
  }
}

function BoundaryFallback({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('session');
  return <div role="alert" className="space-y-2 text-xs text-zinc-500"><p>{t('dataset.renderFailed')}</p><button type="button" className={buttonClass} onClick={onRetry}>{t('dataset.retry')}</button></div>;
}

function rows(binding: SessionDatasets, catalog: Dataset[]): Dataset[] {
  const metadata = new Map((binding.datasets ?? []).map(item => [item.id, item]));
  return binding.dataset_ids.map(id => metadata.get(id) || catalog.find(item => item.id === id) || {
    id, name: id, description: '', document_count: null, chunk_count: null,
  });
}

function pickerRows(binding: SessionDatasets | null, catalog: Dataset[], draft: string[]): Dataset[] {
  const options = new Map(catalog.map(item => [item.id, item]));
  const metadata = new Map((binding?.datasets ?? []).map(item => [item.id, item]));
  for (const id of [...(binding?.dataset_ids ?? []), ...draft]) {
    if (!options.has(id)) options.set(id, metadata.get(id) ?? {
      id, name: id, description: '', document_count: null, chunk_count: null,
    });
  }
  return [...options.values()];
}

function DatasetBody({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation('session');
  const inRouter = useInRouterContext();
  const toast = useOptionalToast();
  const [binding, setBinding] = useState<SessionDatasets | null>(null);
  const [catalog, setCatalog] = useState<Dataset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const request = useRef(0);

  const load = async () => {
    const ticket = ++request.current;
    setLoading(true);
    setError(null);
    try {
      const status = await knowledgebaseAPI.status();
      if (request.current !== ticket) return;
      setConfigured(status.ready);
      if (!status.ready) { setBinding(null); return; }
      const next = await knowledgebaseAPI.sessionDatasets(sessionId);
      if (request.current !== ticket) return;
      const available = new Map<string, Dataset>();
      for (let page = 1; ; page += 1) {
        const result = await knowledgebaseAPI.datasets({ page, page_size: 100 });
        if (request.current !== ticket) return;
        result.items.forEach(item => available.set(item.id, item));
        if (result.items.length === 0 || page * 100 >= result.total) break;
      }
      setBinding(next);
      setCatalog([...available.values()]);
      setDraft(next.dataset_ids);
    } catch (failure) {
      if (request.current !== ticket) return;
      setError((failure as Error).message || t('dataset.loadFailed'));
    } finally {
      if (request.current === ticket) setLoading(false);
    }
  };

  const apply = async () => {
    if (saving) return;
    // A mutation supersedes refreshes that captured an older saved binding.
    const ticket = ++request.current;
    setLoading(false);
    setSaving(true);
    try {
      const next = await knowledgebaseAPI.setSessionDatasets(sessionId, draft);
      if (request.current !== ticket) return;
      setBinding(next);
      setPicking(false);
      toast.success(t('dataset.apply'));
    } catch (failure) {
      if (request.current !== ticket) return;
      toast.error(t('dataset.saveFailed'), (failure as Error).message);
    } finally {
      if (request.current === ticket) setSaving(false);
    }
  };

  useEffect(() => {
    setBinding(null);
    setCatalog([]);
    setDraft([]);
    setPicking(false);
    setSaving(false);
    void load();
    return () => { request.current += 1; };
  }, [sessionId]);
  const selected = binding ? rows(binding, catalog) : [];
  const options = picking ? pickerRows(binding, catalog, draft) : [];
  const fileCount = (item: Dataset) => typeof item.document_count === 'number' && Number.isInteger(item.document_count) && item.document_count >= 0
    ? t('dataset.fileCount', { count: item.document_count }) : t('dataset.fileCountUnknown');
  const manageLabel = <>{t('dataset.manage')}<ArrowRight aria-hidden="true" /></>;
  return <section aria-label={t('dataset.title')} className="session-knowledge">
    <div className="session-knowledge-heading">
      <Database aria-hidden="true" className="h-4 w-4 text-zinc-500 dark:text-zinc-400" />
      <h3 className="truncate text-xs font-semibold text-zinc-700 dark:text-zinc-200">{t('dataset.title')}</h3>
      {binding && <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">{binding.dataset_ids.length}</span>}
      <button type="button" className="session-knowledge-icon-button" aria-label={t('dataset.refresh')} title={t('dataset.refresh')} disabled={loading || saving} onClick={() => void load()}><RefreshCw aria-hidden="true" /></button>
    </div>
    <p className="session-knowledge-hint">{t('dataset.scope')}</p>
    {loading && <p role="status" className="session-knowledge-message">{t('dataset.loading')}</p>}
    {error && <div role="alert" className="session-knowledge-message"><p>{error}</p><button type="button" className={buttonClass} onClick={() => void load()}>{t('dataset.retry')}</button></div>}
    {configured === false && <p className="session-knowledge-empty">{t('dataset.notConfigured')}</p>}
    {binding && <>
      {selected.length === 0 && <p className="session-knowledge-empty">{t('dataset.empty')}</p>}
      <ul className="session-knowledge-cards">{selected.map(item => <li key={item.id} className="session-knowledge-card">
        <span className="session-knowledge-avatar"><Database aria-hidden="true" /></span>
        <div className="session-knowledge-card-body">
          <strong title={item.name}>{item.name}</strong>
          <div className="session-knowledge-card-meta">
            <span>{fileCount(item)}</span>
            <span className="session-knowledge-saved"><Check aria-hidden="true" />{t('dataset.addedToSession')}</span>
          </div>
        </div>
        <button type="button" className="session-knowledge-icon-button" aria-label={t('dataset.remove', { name: item.name })} title={t('dataset.remove', { name: item.name })} onClick={() => { setDraft(binding.dataset_ids.filter(id => id !== item.id)); setPicking(true); }}><X aria-hidden="true" /></button>
      </li>)}</ul>
      <div className="session-knowledge-actions">
        <button type="button" className={buttonClass} onClick={() => { setDraft(binding.dataset_ids); setPicking(true); }}><Plus aria-hidden="true" />{t('dataset.select')}</button>
        {inRouter ? <Link className="session-knowledge-manage" to="/workspace?tab=knowledge">{manageLabel}</Link> : <a className="session-knowledge-manage" href="/workspace?tab=knowledge">{manageLabel}</a>}
      </div>
    </>}
    {picking && <Modal title={t('dataset.picker')} onClose={() => { if (!saving) setPicking(false); }}>
      <div className="session-knowledge-picker">
        <p className="session-knowledge-message">{t('dataset.selectionHint')}</p>
        <div className="session-knowledge-options">
          {options.map(item => <label key={item.id} className={`session-knowledge-choice${draft.includes(item.id) ? ' is-selected' : ''}`}>
            <input type="checkbox" aria-label={item.name} disabled={saving} checked={draft.includes(item.id)} onChange={() => setDraft(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id])} />
            <Database aria-hidden="true" /><span>{item.name}</span><small>{fileCount(item)}</small>
          </label>)}
        </div>
        <div className="session-knowledge-picker-actions">
          <button type="button" className={buttonClass} disabled={saving} onClick={() => setPicking(false)}>{t('dataset.cancel')}</button>
          <button type="button" className={`${buttonClass} is-primary`} disabled={saving} onClick={() => void apply()}>{t(saving ? 'dataset.saving' : 'dataset.apply')}</button>
        </div>
      </div>
    </Modal>}
  </section>;
}

function DatasetLoading() {
  const { t } = useTranslation('session');
  return <p role="status" className="border-t border-zinc-200 px-3 py-3 text-xs text-zinc-500 dark:border-zinc-800">{t('dataset.loading')}</p>;
}

export default function SessionDatasetSection({ sessionId }: { sessionId: string }) {
  return <DatasetBoundary resetKey={0}><Suspense fallback={<DatasetLoading />}><DatasetBody sessionId={sessionId} /></Suspense></DatasetBoundary>;
}
