import { useState, useEffect, useCallback, useRef } from 'react';
import { workflowAPI, Workflow } from '@/api/workflow';

const POLL_INTERVAL_MS = 10_000;

export function useWorkflows(category?: string, status?: string) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadingRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  const resetPollTimer = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => fetchWorkflows(true), POLL_INTERVAL_MS);
  }, [fetchWorkflows]);

  // Initial fetch
  useEffect(() => {
    fetchWorkflows();
  }, [fetchWorkflows]);

  // Polling with resetable timer
  useEffect(() => {
    resetPollTimer();
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [resetPollTimer]);

  // Refetch on focus and reset poll timer to avoid immediate double-fetch
  useEffect(() => {
    const onFocus = () => {
      fetchWorkflows(true);
      resetPollTimer();
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [fetchWorkflows, resetPollTimer]);

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
