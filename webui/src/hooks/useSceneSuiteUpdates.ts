import { useCallback, useEffect, useMemo, useState } from 'react';
import { hubAPI, type HubSceneSuite } from '@/api/hub';
import { SCENE_SUITES_CHANGED_EVENT, sceneSuiteErrorMessage } from '@/utils/sceneSuites';
import { useSSE, type SSEEvent } from './useSSE';
import { createSharedResource, useSharedResource } from './useSharedResource';

const SEEN_CHANGED_EVENT = 'flocks:scene-suites-seen';
const SEEN_STORAGE_PREFIX = 'flocks.sceneSuites.seen';
const BACKGROUND_REFRESH_INTERVAL_MS = 1000;

const sceneSuitesResource = createSharedResource<HubSceneSuite[]>({
  initialData: [],
  staleTimeMs: 60_000,
  minFetchIntervalMs: 1000,
  fetcher: async () => {
    // This dedicated endpoint owns the same component filtering as the manager.
    const response = await hubAPI.sceneSuites();
    return Array.isArray(response.data) ? response.data : [];
  },
  getErrorMessage: (error) => sceneSuiteErrorMessage(error, 'Failed to load scene suites'),
});

let resumeSubscribers = 0;
let refreshTimer: number | null = null;
let lastBackgroundRefreshAt = 0;
const handledEvents = new WeakSet<SSEEvent>();

function cancelScheduledRefresh(): void {
  if (refreshTimer !== null) window.clearTimeout(refreshTimer);
  refreshTimer = null;
}

function scheduleFreshCheck(): void {
  // Invalidate immediately, including during a read, so an old response cannot
  // restore the pre-mutation state. One trailing timer combines event bursts.
  sceneSuitesResource.invalidate();
  if (refreshTimer !== null) return;
  const delay = Math.max(0, lastBackgroundRefreshAt + BACKGROUND_REFRESH_INTERVAL_MS - Date.now());
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null;
    lastBackgroundRefreshAt = Date.now();
    void sceneSuitesResource.fetch({ force: true, silent: true });
  }, delay);
}

function handleSceneEvent(event: SSEEvent): void {
  if (event.type !== 'hub.scene_suites.changed' && event.type !== 'contracts.webui.pages.nav_changed') return;
  // useSSE shares one connection and passes this same event to each consumer.
  if (handledEvents.has(event)) return;
  handledEvents.add(event);
  scheduleFreshCheck();
}

function handleVisibleResume(): void {
  if (document.visibilityState === 'visible') scheduleFreshCheck();
}

function versionKey(suite: HubSceneSuite): string {
  return JSON.stringify([suite.id, suite.version]);
}

function readSeen(storageKey: string): Set<string> {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) || '[]');
    return new Set(Array.isArray(saved) ? saved.filter((item): item is string => typeof item === 'string') : []);
  } catch {
    return new Set();
  }
}

export async function notifySceneSuitesChanged(): Promise<HubSceneSuite[]> {
  cancelScheduledRefresh();
  lastBackgroundRefreshAt = Date.now();
  sceneSuitesResource.invalidate();
  window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT));
  return sceneSuitesResource.fetch({ force: true, silent: true });
}

export function __resetSceneSuiteUpdatesForTesting(): void {
  cancelScheduledRefresh();
  lastBackgroundRefreshAt = 0;
  sceneSuitesResource.resetForTesting();
}

export function useSceneSuiteUpdates({ viewingScenes = false, userId }: {
  viewingScenes?: boolean;
  userId?: string;
} = {}) {
  const { data: suites, loading, error, initialized, refetch } = useSharedResource(sceneSuitesResource);
  const storageKey = `${SEEN_STORAGE_PREFIX}:${userId || 'local'}`;
  const [seen, setSeen] = useState(() => readSeen(storageKey));
  const available = useMemo(() => suites.filter((suite) => suite.state === 'available' && !suite.installedVersion), [suites]);

  useSSE({
    url: '/api/event',
    onEvent: handleSceneEvent,
    onReconnect: scheduleFreshCheck,
    enabled: typeof EventSource !== 'undefined',
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  useEffect(() => {
    if (resumeSubscribers++ === 0) {
      window.addEventListener('focus', scheduleFreshCheck);
      document.addEventListener('visibilitychange', handleVisibleResume);
      // Changes may have happened while no suite consumer was subscribed.
      if (sceneSuitesResource.getSnapshot().initialized) scheduleFreshCheck();
    }
    return () => {
      if (--resumeSubscribers === 0) {
        window.removeEventListener('focus', scheduleFreshCheck);
        document.removeEventListener('visibilitychange', handleVisibleResume);
        cancelScheduledRefresh();
      }
    };
  }, []);

  useEffect(() => {
    // An initial uncached mount already starts a current read. Returning to the
    // manager must check again even when the ordinary 60-second cache is fresh.
    if (viewingScenes && sceneSuitesResource.getSnapshot().initialized) scheduleFreshCheck();
  }, [viewingScenes]);

  useEffect(() => {
    const refreshSeen = () => setSeen(readSeen(storageKey));
    refreshSeen();
    window.addEventListener('storage', refreshSeen);
    window.addEventListener(SEEN_CHANGED_EVENT, refreshSeen);
    return () => {
      window.removeEventListener('storage', refreshSeen);
      window.removeEventListener(SEEN_CHANGED_EVENT, refreshSeen);
    };
  }, [storageKey]);

  const markSeen = useCallback(() => {
    if (!initialized || loading || error) return;
    const next = readSeen(storageKey);
    let changed = false;
    for (const suite of suites) {
      const key = versionKey(suite);
      if (!next.has(key)) {
        next.add(key);
        changed = true;
      }
    }
    if (!changed) return;
    setSeen(next);
    try {
      window.localStorage.setItem(storageKey, JSON.stringify([...next]));
      window.dispatchEvent(new Event(SEEN_CHANGED_EVENT));
    } catch {
      // Private browsing and storage quotas must not prevent browsing scenes.
    }
  }, [suites, error, initialized, loading, storageKey]);

  useEffect(() => {
    if (viewingScenes) markSeen();
  }, [markSeen, viewingScenes]);

  return {
    suites,
    loading,
    error,
    hasUnseenSuites: !error && !viewingScenes && available.some((suite) => !seen.has(versionKey(suite))),
    markSeen,
    refetch,
  };
}
