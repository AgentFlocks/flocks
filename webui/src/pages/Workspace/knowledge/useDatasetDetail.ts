import { useCallback, useEffect, useRef, useState } from 'react';
import { knowledgebaseAPI, type Dataset, type KnowledgeDocument, type Page } from '@/api/knowledgebase';
import { errorCode, PAGE_SIZE } from './state';

export const DOCUMENT_REFRESH_INTERVAL_MS = 2_000;
export const DOCUMENT_REFRESH_LIMIT_MS = 20_000;

type Detail = { dataset: Dataset; documents: Page<KnowledgeDocument> };
type RefreshStatus = 'refreshing' | 'stopped' | 'expired' | 'error';
type State = { key: string; data?: Detail; error?: string; loading: boolean; refreshStatus: RefreshStatus };

// Linking only schedules work upstream. Observe documents for a bounded window,
// without parsing, resubmitting a link, or remounting previews and form drafts.
export function useDatasetDetail(id: string, q: string, page: number, revision: number) {
  const key = JSON.stringify([id, q, page]);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [state, setState] = useState<State>({ key, loading: true, refreshStatus: 'refreshing' });
  const stopRef = useRef<() => void>(() => {});

  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let remaining = Math.ceil(DOCUMENT_REFRESH_LIMIT_MS / DOCUMENT_REFRESH_INTERVAL_MS) - 1;
    const stop = () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(deadline);
      controller.abort();
    };
    const finish = (refreshStatus: RefreshStatus) => {
      stop();
      setState(current => ({ ...current, loading: false, refreshStatus }));
    };
    stopRef.current = () => finish('stopped');
    setState(current => ({ key, data: current.key === key ? current.data : undefined, loading: !current.data || current.key !== key, refreshStatus: 'refreshing' }));
    deadline = setTimeout(() => finish('expired'), DOCUMENT_REFRESH_LIMIT_MS);

    const read = async (initial: boolean) => {
      try {
        const [dataset, documents] = await Promise.all([
          initial || remaining === 0 ? knowledgebaseAPI.dataset(id, controller.signal) : Promise.resolve(undefined),
          knowledgebaseAPI.documents(id, { q, page, page_size: PAGE_SIZE }, controller.signal),
        ]);
        if (stopped) return;
        setState(current => ({ key, data: { dataset: dataset ?? current.data!.dataset, documents }, loading: false, refreshStatus: 'refreshing' }));
        if (remaining > 0) timer = setTimeout(() => { remaining -= 1; void read(false); }, DOCUMENT_REFRESH_INTERVAL_MS);
      } catch (error) {
        if (stopped) return;
        stop();
        setState(current => ({ ...current, error: errorCode(error), loading: false, refreshStatus: 'error' }));
      }
    };
    void read(true);
    return stop;
  }, [id, q, page, key, revision, reloadVersion]);

  return {
    data: state.key === key ? state.data : undefined,
    error: state.key === key ? state.error : undefined,
    loading: state.key !== key || state.loading,
    refreshStatus: state.refreshStatus,
    reload: useCallback(() => setReloadVersion(value => value + 1), []),
    cancel: useCallback(() => stopRef.current(), []),
  };
}
