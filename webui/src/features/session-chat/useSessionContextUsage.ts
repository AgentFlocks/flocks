import { useCallback, useEffect, useRef, useState } from 'react';

import { sessionApi, type ContextUsageSnapshot } from '@/api/session';

export interface RefreshContextUsageOptions {
  clear?: boolean;
  skipIfFreshMs?: number;
}

export function useSessionContextUsage(sessionId?: string | null) {
  const [snapshot, setSnapshot] = useState<ContextUsageSnapshot | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [contextWindowTokens, setContextWindowTokens] = useState(0);
  const requestRef = useRef<{ sessionId: string; promise: Promise<void> } | null>(null);
  const requestSeqRef = useRef(0);
  const lastPushAtRef = useRef(0);

  const reset = useCallback((nextRefreshing = false) => {
    setSnapshot(null);
    setRefreshing(nextRefreshing);
    setContextWindowTokens(0);
    requestSeqRef.current += 1;
    requestRef.current = null;
    lastPushAtRef.current = 0;
  }, []);

  const refresh = useCallback((options?: RefreshContextUsageOptions) => {
    if (!sessionId) {
      reset(false);
      return;
    }

    if (options?.clear) {
      reset(true);
    } else if (
      options?.skipIfFreshMs &&
      Date.now() - lastPushAtRef.current < options.skipIfFreshMs
    ) {
      return;
    }

    const existingRequest = requestRef.current;
    if (existingRequest?.sessionId === sessionId) {
      return existingRequest.promise;
    }

    const requestSessionId = sessionId;
    const requestSeq = requestSeqRef.current;
    const request = sessionApi.getContextUsage(requestSessionId).then((nextSnapshot) => {
      if (requestSeq === requestSeqRef.current && nextSnapshot.sessionID === sessionId) {
        setSnapshot(nextSnapshot);
        if (nextSnapshot.contextWindow && nextSnapshot.contextWindow > 0) {
          setContextWindowTokens(nextSnapshot.contextWindow);
        }
        setRefreshing(false);
      }
    }).catch((err) => {
      setRefreshing(false);
      console.warn('[SessionChat] Failed to fetch context usage:', err);
    }).finally(() => {
      if (requestRef.current?.promise === request) {
        requestRef.current = null;
      }
    });

    requestRef.current = { sessionId: requestSessionId, promise: request };
    return request;
  }, [reset, sessionId]);

  const applyPushSnapshot = useCallback((nextSnapshot: ContextUsageSnapshot) => {
    setSnapshot(nextSnapshot);
    if (nextSnapshot.contextWindow && nextSnapshot.contextWindow > 0) {
      setContextWindowTokens(nextSnapshot.contextWindow);
    }
    requestSeqRef.current += 1;
    requestRef.current = null;
    lastPushAtRef.current = Date.now();
    setRefreshing(false);
  }, []);

  const stopRefreshing = useCallback(() => {
    setRefreshing(false);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      reset(false);
      return;
    }

    const requestIdle = (window as any).requestIdleCallback as
      | ((cb: () => void, options?: { timeout?: number }) => number)
      | undefined;
    const cancelIdle = (window as any).cancelIdleCallback as
      | ((id: number) => void)
      | undefined;
    if (requestIdle) {
      const idleId = requestIdle(() => {
        void refresh({ clear: true });
      }, { timeout: 1500 });
      return () => cancelIdle?.(idleId);
    }
    const timer = window.setTimeout(() => {
      void refresh({ clear: true });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [refresh, reset, sessionId]);

  return {
    snapshot,
    refreshing,
    contextWindowTokens,
    refresh,
    applyPushSnapshot,
    reset,
    stopRefreshing,
  };
}
