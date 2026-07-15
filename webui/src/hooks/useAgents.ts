import { useState, useEffect, useCallback, useRef } from 'react';
import { agentAPI, Agent } from '@/api/agent';

export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const lastRefreshRef = useRef(0);

  const fetchAgents = useCallback(async (showLoading = true) => {
    try {
      if (showLoading) setLoading(true);
      setError(null);
      const response = await agentAPI.list();
      setAgents(Array.isArray(response.data) ? response.data : []);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch agents');
      setAgents([]);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const refreshAndFetch = useCallback(async () => {
    const now = Date.now();
    if (now - lastRefreshRef.current < 1000) return;
    lastRefreshRef.current = now;

    try {
      await agentAPI.refresh();
    } catch {
      // Best-effort: if refresh fails, still try to fetch the latest list.
    }

    await fetchAgents(false);
  }, [fetchAgents]);

  useEffect(() => {
    void fetchAgents();

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refreshAndFetch();
      }
    };

    const handleWindowFocus = () => {
      void refreshAndFetch();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleWindowFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [fetchAgents, refreshAndFetch]);

  return {
    agents,
    loading,
    error,
    refetch: (showLoading = true) => fetchAgents(showLoading),
  };
}
