import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useInRouterContext } from 'react-router-dom';
import {
  IconArrowRight,
  IconCheck,
  IconDatabase,
  IconPlus,
  IconRefresh,
  IconX,
} from '@tabler/icons-react';
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
  return <div role="alert" className="space-y-2 text-xs text-fg-muted"><p>{t('dataset.renderFailed')}</p><button type="button" className={buttonClass} onClick={onRetry}>{t('dataset.retry')}</button></div>;
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
  // The picker's shared Modal reads 'workspace', which the Session route does not
  // preload. Loading it here keeps opening the picker from suspending this section,
  // which would re-run the session effect and close the picker again.
  const { t } = useTranslation(['session', 'workspace']);
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
  // Set while the picker holds choices that have not been applied yet.
  const edited = useRef(false);

  const openPicker = (ids: string[], changed: boolean) => {
    edited.current = changed;
    setDraft(ids);
    setPicking(true);
  };
  const closePicker = () => {
    edited.current = false;
    setPicking(false);
  };

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
      // A refresh that returns while the picker is being edited must not undo those choices.
      if (!edited.current) setDraft(next.dataset_ids);
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
      closePicker();
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
    edited.current = false;
    void load();
    return () => { request.current += 1; };
  }, [sessionId]);
  const selected = binding ? rows(binding, catalog) : [];
  const options = picking ? pickerRows(binding, catalog, draft) : [];
  const fileCount = (item: Dataset) => typeof item.document_count === 'number' && Number.isInteger(item.document_count) && item.document_count >= 0
    ? t('dataset.fileCount', { count: item.document_count }) : t('dataset.fileCountUnknown');
  const manageLabel = <>{t('dataset.manage')}<IconArrowRight aria-hidden="true" /></>;
  return <section aria-label={t('dataset.title')} className="session-knowledge">
    <div className="session-knowledge-heading">
      <IconDatabase aria-hidden="true" className="h-4 w-4 text-fg-muted dark:text-fg-muted" />
      <h3 className="truncate text-xs font-semibold text-fg-secondary dark:text-fg-secondary">{t('dataset.title')}</h3>
      {binding && <span className="rounded bg-subtle px-1.5 py-0.5 text-xs text-fg-muted dark:bg-subtle dark:text-fg-muted">{binding.dataset_ids.length}</span>}
      <button type="button" className="session-knowledge-icon-button" aria-label={t('dataset.refresh')} title={t('dataset.refresh')} disabled={loading || saving} onClick={() => void load()}><IconRefresh aria-hidden="true" /></button>
    </div>
    <p className="session-knowledge-hint">{t('dataset.scope')}</p>
    {loading && <p role="status" className="session-knowledge-message">{t('dataset.loading')}</p>}
    {error && <div role="alert" className="session-knowledge-message"><p>{error}</p><button type="button" className={buttonClass} onClick={() => void load()}>{t('dataset.retry')}</button></div>}
    {configured === false && <p className="session-knowledge-empty">{t('dataset.notConfigured')}</p>}
    {binding && <>
      {selected.length === 0 && <p className="session-knowledge-empty">{t('dataset.empty')}</p>}
      <ul className="session-knowledge-cards">{selected.map(item => <li key={item.id} className="session-knowledge-card">
        <span className="session-knowledge-avatar"><IconDatabase aria-hidden="true" /></span>
        <div className="session-knowledge-card-body">
          <strong title={item.name}>{item.name}</strong>
          <div className="session-knowledge-card-meta">
            <span>{fileCount(item)}</span>
            <span className="session-knowledge-saved"><IconCheck aria-hidden="true" />{t('dataset.addedToSession')}</span>
          </div>
        </div>
        <button type="button" className="session-knowledge-icon-button" aria-label={t('dataset.remove', { name: item.name })} title={t('dataset.remove', { name: item.name })} onClick={() => openPicker(binding.dataset_ids.filter(id => id !== item.id), true)}><IconX aria-hidden="true" /></button>
      </li>)}</ul>
      <div className="session-knowledge-actions">
        <button type="button" className={buttonClass} onClick={() => openPicker(binding.dataset_ids, false)}><IconPlus aria-hidden="true" />{t('dataset.select')}</button>
        {inRouter ? <Link className="session-knowledge-manage" to="/workspace?tab=knowledge">{manageLabel}</Link> : <a className="session-knowledge-manage" href="/workspace?tab=knowledge">{manageLabel}</a>}
      </div>
    </>}
    {picking && <Modal title={t('dataset.picker')} onClose={() => { if (!saving) closePicker(); }}>
      <div className="session-knowledge-picker">
        <p className="session-knowledge-message">{t('dataset.selectionHint')}</p>
        <div className="session-knowledge-options">
          {options.map(item => <label key={item.id} className={`session-knowledge-choice${draft.includes(item.id) ? ' is-selected' : ''}`}>
            <input type="checkbox" aria-label={item.name} disabled={saving} checked={draft.includes(item.id)} onChange={() => { edited.current = true; setDraft(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id]); }} />
            <IconDatabase aria-hidden="true" /><span>{item.name}</span><small>{fileCount(item)}</small>
          </label>)}
        </div>
        <div className="session-knowledge-picker-actions">
          <button type="button" className={buttonClass} disabled={saving} onClick={closePicker}>{t('dataset.cancel')}</button>
          <button type="button" className={`${buttonClass} is-primary`} disabled={saving} onClick={() => void apply()}>{t(saving ? 'dataset.saving' : 'dataset.apply')}</button>
        </div>
      </div>
    </Modal>}
  </section>;
}

function DatasetLoading() {
  const { t } = useTranslation('session');
  return <p role="status" className="border-t border-line px-3 py-3 text-xs text-fg-muted dark:border-line">{t('dataset.loading')}</p>;
}

export default function SessionDatasetSection({ sessionId }: { sessionId: string }) {
  return <DatasetBoundary resetKey={0}><Suspense fallback={<DatasetLoading />}><DatasetBody sessionId={sessionId} /></Suspense></DatasetBoundary>;
}
