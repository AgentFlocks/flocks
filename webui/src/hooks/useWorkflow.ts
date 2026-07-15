import { useState, useEffect, useCallback, useRef } from 'react';
import { workflowAPI, Workflow } from '@/api/workflow';

export function useWorkflows(category?: string, status?: string) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadingRef = useRef(false);
  const lastRefreshRef = useRef(0);

  const fetchWorkflows = useCallback(async (silent = false) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const response = await workflowAPI.list({ category, status });
      if (Array.isArray(response.data)) {
        setWorkflows(response.data);
      } else {
        setWorkflows([]);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to fetch workflows');
      setWorkflows([]);
    } finally {
      loadingRef.current = false;
      if (!silent) setLoading(false);
    }
  }, [category, status]);

  // Initial fetch
  useEffect(() => {
    void fetchWorkflows();
  }, [fetchWorkflows]);

  const refreshOnResume = useCallback((force = false) => {
    const now = Date.now();
    if (!force && now - lastRefreshRef.current < 1000) return;
    lastRefreshRef.current = now;
    void fetchWorkflows(true);
  }, [fetchWorkflows]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        refreshOnResume(false);
      }
    };
    const onFocus = () => {
      refreshOnResume(false);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus);
    };
  }, [refreshOnResume]);

  return {
    workflows,
    loading,
    error,
    refetch: () => fetchWorkflows(),
  };
}

export function useWorkflow(id?: string) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkflow = async () => {
    if (!id) return;
    
    try {
      setLoading(true);
      setError(null);
      const response = await workflowAPI.get(id);
      setWorkflow(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch workflow');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkflow();
  }, [id]);

  return {
    workflow,
    loading,
    error,
    refetch: fetchWorkflow,
  };
}
