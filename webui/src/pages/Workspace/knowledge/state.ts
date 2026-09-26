import { useCallback, useEffect, useRef, useState } from 'react';

export const PAGE_SIZE = 20;

export function useMountedRef() {
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  return mounted;
}

export function errorCode(error: unknown): string {
  return (error as { code?: string; message?: string })?.message || (error as { code?: string })?.code || 'knowledgebase_unavailable';
}

export function useKnowledgeQuery<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const loader = useRef(load);
  loader.current = load;
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string | null; data?: T; error?: string; loading: boolean }>({ key: null, loading: false });
  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let current = true;
    setState({ key, loading: true });
    void Promise.resolve().then(() => loader.current(controller.signal)).then(
      data => { if (current) setState({ key, data, loading: false }); },
      error => { if (current && (error as { name?: string }).name !== 'CanceledError' && (error as { code?: string }).code !== 'ERR_CANCELED') setState({ key, error: errorCode(error), loading: false }); },
    );
    return () => { current = false; controller.abort(); };
  }, [key, revision]);
  return {
    data: key !== null && state.key === key ? state.data : undefined,
    error: key !== null && state.key === key ? state.error : undefined,
    loading: key !== null && (state.key !== key || state.loading),
    reload: useCallback(() => setRevision(value => value + 1), []),
  };
}
