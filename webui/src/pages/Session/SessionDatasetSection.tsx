import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type Dataset, type SessionDatasets } from '@/api/knowledgebase';
import { useOptionalToast } from '@/components/common/Toast';

const buttonClass = 'rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200';

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
  if (binding.datasets?.length) return binding.datasets;
  return binding.dataset_ids.map(id => catalog.find(item => item.id === id) || {
    id, name: id, description: '', document_count: null, chunk_count: null,
  });
}

function DatasetBody({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation('session');
  const toast = useOptionalToast();
  const [binding, setBinding] = useState<SessionDatasets | null>(null);
  const [catalog, setCatalog] = useState<Dataset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [picking, setPicking] = useState(false);
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
      const page = next.datasets ? null : await knowledgebaseAPI.datasets({ page_size: 100 });
      if (request.current !== ticket) return;
      setBinding(next);
      setCatalog(page?.items ?? []);
      setDraft(next.dataset_ids);
    } catch (failure) {
      if (request.current !== ticket) return;
      setError((failure as Error).message || t('dataset.loadFailed'));
    } finally {
      if (request.current === ticket) setLoading(false);
    }
  };

  const apply = async () => {
    try {
      const next = await knowledgebaseAPI.setSessionDatasets(sessionId, draft);
      setBinding(next);
      setPicking(false);
      toast.success(t('dataset.apply'));
    } catch (failure) {
      toast.error(t('dataset.saveFailed'), (failure as Error).message);
    }
  };

  useEffect(() => {
    setBinding(null);
    setCatalog([]);
    setDraft([]);
    setPicking(false);
    void load();
  }, [sessionId]);
  const selected = binding ? rows(binding, catalog) : [];
  return <section aria-label={t('dataset.title')} className="space-y-2 border-t border-zinc-200 px-3 py-3 text-sm dark:border-zinc-800">
    <div className="flex items-center gap-2">
      <h3 className="font-medium">{t('dataset.title')}</h3>
      <button type="button" className={`${buttonClass} ml-auto`} onClick={() => void load()}>{t('dataset.refresh')}</button>
    </div>
    <p className="text-xs text-zinc-500">{t('dataset.scope')}</p>
    {loading && <p role="status">{t('dataset.loading')}</p>}
    {error && <div role="alert" className="space-y-2 text-xs"><p>{error}</p><button type="button" className={buttonClass} onClick={() => void load()}>{t('dataset.retry')}</button></div>}
    {configured === false && <p className="text-xs text-zinc-500">{t('dataset.notConfigured')}</p>}
    {binding && !picking && <>
      {selected.length === 0 && <p className="text-xs text-zinc-500">{t('dataset.empty')}</p>}
      <ul>{selected.map(item => <li key={item.id}>{item.name}</li>)}</ul>
      <button type="button" className={buttonClass} onClick={() => { setDraft(binding.dataset_ids); setPicking(true); }}>{t('dataset.select')}</button>
    </>}
    {picking && <div className="space-y-2">
      {catalog.map(item => <label key={item.id} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={draft.includes(item.id)} onChange={() => setDraft(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id])} />{item.name}</label>)}
      <div className="flex gap-2"><button type="button" className={buttonClass} onClick={() => void apply()}>{t('dataset.apply')}</button><button type="button" className={buttonClass} onClick={() => setPicking(false)}>{t('dataset.cancel')}</button></div>
    </div>}
  </section>;
}

function DatasetLoading() {
  const { t } = useTranslation('session');
  return <p role="status" className="border-t border-zinc-200 px-3 py-3 text-xs text-zinc-500 dark:border-zinc-800">{t('dataset.loading')}</p>;
}

export default function SessionDatasetSection({ sessionId }: { sessionId: string }) {
  return <DatasetBoundary resetKey={0}><Suspense fallback={<DatasetLoading />}><DatasetBody sessionId={sessionId} /></Suspense></DatasetBoundary>;
}
