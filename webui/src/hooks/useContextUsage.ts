import { useState, useEffect, useCallback, useRef } from 'react';
import { sessionApi } from '@/api/session';

export interface ContextUsage {
  sessionID: string;
  usedTokens: number;
  contextLimit: number;
  usableLimit: number;
  percentage: number;
  modelID: string;
  providerID: string;
  isOverflow: boolean;
  messageCount: number;
}

interface UseContextUsageOptions {
  refreshInterval?: number;
  showThreshold?: number;
}

const DEFAULT_OPTIONS: UseContextUsageOptions = {
  refreshInterval: 30000,
  showThreshold: 50,
};

export function useContextUsage(
  sessionId: string | null | undefined,
  options: UseContextUsageOptions = {},
) {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchUsage = useCallback(async (signal?: AbortSignal) => {
    if (!sessionId) {
      setUsage(null);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const data = await sessionApi.getContextUsage(sessionId);
      if (!signal?.aborted) {
        setUsage(data);
        setError(null);
      }
    } catch (err) {
      if (!signal?.aborted) {
        setError(err instanceof Error ? err.message : 'Failed to fetch context usage');
        console.warn('[useContextUsage] Fetch error:', err);
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setUsage(null);
      setError(null);
      return;
    }

    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();

    fetchUsage(abortControllerRef.current.signal);

    return () => {
      abortControllerRef.current?.abort();
    };
  }, [sessionId, fetchUsage]);

  useEffect(() => {
    if (!sessionId || !opts.refreshInterval) return;

    intervalRef.current = setInterval(() => {
      abortControllerRef.current?.abort();
      abortControllerRef.current = new AbortController();
      fetchUsage(abortControllerRef.current.signal);
    }, opts.refreshInterval);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [sessionId, opts.refreshInterval, fetchUsage]);

  const refresh = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();
    fetchUsage(abortControllerRef.current.signal);
  }, [fetchUsage]);

  const shouldShow = usage !== null && usage.percentage >= (opts.showThreshold ?? 50);

  const getUsageColor = useCallback((percentage: number): string => {
    if (percentage >= 90) return 'text-red-600';
    if (percentage >= 75) return 'text-orange-500';
    if (percentage >= 50) return 'text-yellow-500';
    return 'text-green-500';
  }, []);

  const getUsageBgColor = useCallback((percentage: number): string => {
    if (percentage >= 90) return 'bg-red-100 border-red-300';
    if (percentage >= 75) return 'bg-orange-100 border-orange-300';
    if (percentage >= 50) return 'bg-yellow-100 border-yellow-300';
    return 'bg-green-100 border-green-300';
  }, []);

  return {
    usage,
    loading,
    error,
    shouldShow,
    refresh,
    getUsageColor,
    getUsageBgColor,
  };
}