import { useEffect, useMemo } from 'react';
import { statsApi, SystemStats } from '@/api/stats';
import { createSharedResource, useSharedResource } from './useSharedResource';

const STATS_POLL_INTERVAL_MS = 30_000;
const STATS_STALE_TIME_MS = 5_000;
const STATS_MIN_FETCH_INTERVAL_MS = 1_000;

const statsResource = createSharedResource<SystemStats | null>({
  initialData: null,
  staleTimeMs: STATS_STALE_TIME_MS,
  minFetchIntervalMs: STATS_MIN_FETCH_INTERVAL_MS,
  fetcher: statsApi.getSystemStats,
  fallbackDataOnError: (previous) => previous,
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch system stats'),
});

let statsPollSubscriberCount = 0;
let statsPollIntervalId: number | null = null;

function subscribeStatsPolling(): () => void {
  statsPollSubscriberCount += 1;

  if (statsPollSubscriberCount === 1) {
    statsPollIntervalId = window.setInterval(() => {
      void statsResource.fetch({ silent: true });
    }, STATS_POLL_INTERVAL_MS);
  }

  return () => {
    statsPollSubscriberCount = Math.max(0, statsPollSubscriberCount - 1);
    if (statsPollSubscriberCount === 0 && statsPollIntervalId !== null) {
      window.clearInterval(statsPollIntervalId);
      statsPollIntervalId = null;
    }
  };
}

export function __resetStatsResourceForTesting(): void {
  statsResource.resetForTesting();
  statsPollSubscriberCount = 0;
  if (statsPollIntervalId !== null) {
    window.clearInterval(statsPollIntervalId);
    statsPollIntervalId = null;
  }
}

export function useStats() {
  const { data: stats, loading, error: fetchError, refetch } = useSharedResource(statsResource);

  useEffect(() => {
    return subscribeStatsPolling();
  }, []);

  const error = useMemo(() => {
    if (fetchError) return new Error(fetchError);
    if (stats?.system.status === 'error' || stats?.system.status === 'warning') return new Error(stats.system.message);
    return null;
  }, [fetchError, stats?.system.message, stats?.system.status]);

  return { stats, loading, error, refetch };
}
