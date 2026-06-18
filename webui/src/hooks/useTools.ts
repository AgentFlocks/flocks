import { useState, useEffect, useCallback, useRef } from 'react';
import { toolAPI, Tool } from '@/api/tool';

export function useTools() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initializedRef = useRef(false);

  const fetchTools = useCallback(async (showLoading = false) => {
    try {
      if (showLoading && !initializedRef.current) setLoading(true);
      setError(null);
      const response = await toolAPI.list();
      setTools(Array.isArray(response.data) ? response.data : []);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch tools');
    } finally {
      if (showLoading && !initializedRef.current) setLoading(false);
      initializedRef.current = true;
    }
  }, []);

  const refreshAndFetch = useCallback(async () => {
    try {
      await toolAPI.refresh();
    } catch { /* ignore */ }
    await fetchTools(false);
  }, [fetchTools]);

  useEffect(() => {
    let cancelled = false;

    const init = async () => {
      await fetchTools(true);
      if (cancelled) return;
    };

    void init();

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void fetchTools(false);
      }
    };
    const onFocus = () => {
      void fetchTools(false);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus);
    };
  }, [fetchTools]);

  return {
    tools,
    loading,
    error,
    refetch: refreshAndFetch,
  };
}
