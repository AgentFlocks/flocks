import { useCallback, useEffect } from 'react';
import { reaApi, type ReaState, type ReaStatus } from '@/api/rea';
import { useOptionalAuth } from '@/contexts/authState';
import { __resetReaCardStateForTesting, setReaCardIdentity } from './cardState';
import { useSSE, type SSEEvent } from '@/hooks/useSSE';
import {
  createSharedResource,
  useRefreshOnResume,
  useSharedResource,
  type SharedResource,
} from '@/hooks/useSharedResource';

export const REA_INSTALL_EVENT = 'rea.install.updated';
export const REA_UNINSTALL_EVENT = 'rea.uninstall.updated';
const STATUS_POLL_MS = 1500;
const STATUS_WATCH_POLL_MS = 20_000;
const ANONYMOUS = 'anonymous';

/** States in which a job runs and the status moves on its own. */
export const REA_BUSY_STATES: ReadonlySet<ReaState> = new Set(['installing', 'validating', 'uninstalling']);

const errorMessage = (error: unknown) => (error instanceof Error ? error.message : String(error));

/*
 * Status is cached per signed-in account and role. Admins see installer
 * details members must not, and the badge is per account, so after a
 * re-login in the same tab, or a role change of the same account, the
 * previous cache is dropped instead of being shown.
 */
let cacheIdentity: string | null = null;
let statusCache: SharedResource<ReaStatus | null> | null = null;

// One poller per resource no matter how many views watch it.
const pollers = new Map<string, { count: number; timer: number }>();

function useCacheIdentity(): string {
  const user = useOptionalAuth()?.user;
  return user ? `${user.id}:${user.role}` : ANONYMOUS;
}

function switchIdentity(identity: string): void {
  setReaCardIdentity(identity);
  if (cacheIdentity === identity) return;
  cacheIdentity = identity;
  statusCache = null;
  pollers.forEach((poller) => window.clearInterval(poller.timer));
  pollers.clear();
}

/** The signed-in account (and role) REA state belongs to; switches the caches when it changes. */
export function useReaIdentity(): string {
  const identity = useCacheIdentity();
  switchIdentity(identity);
  return identity;
}

/** The identity the caches currently belong to (for async work that must not cross accounts). */
export function currentReaIdentity(): string | null {
  return cacheIdentity;
}

function statusResource(identity: string): SharedResource<ReaStatus | null> {
  switchIdentity(identity);
  if (!statusCache) {
    statusCache = createSharedResource<ReaStatus | null>({
      initialData: null,
      staleTimeMs: 10_000,
      minFetchIntervalMs: 300,
      fetcher: () => reaApi.status(),
      getErrorMessage: errorMessage,
    });
  }
  return statusCache;
}

function retainPoller(key: string, intervalMs: number, tick: () => void): () => void {
  const existing = pollers.get(key);
  if (existing) {
    existing.count += 1;
  } else {
    pollers.set(key, { count: 1, timer: window.setInterval(tick, intervalMs) });
  }
  return () => {
    const current = pollers.get(key);
    if (!current) return;
    current.count -= 1;
    if (current.count <= 0) {
      window.clearInterval(current.timer);
      pollers.delete(key);
    }
  };
}

/** Force a fresh read: a request started before the change must not answer it. */
function refetch<T>(resource: SharedResource<T>, rejectOnError = false): Promise<T> {
  resource.invalidate();
  return resource.fetch({ force: true, silent: true, rejectOnError });
}

// useSSE shares one connection and hands the same event to every subscriber.
const handledEvents = new WeakSet<SSEEvent>();

function handleReaEvent(event: SSEEvent): void {
  if (handledEvents.has(event)) return;
  handledEvents.add(event);
  if ((event.type === REA_INSTALL_EVENT || event.type === REA_UNINSTALL_EVENT) && statusCache) {
    void refetch(statusCache);
  }
}

/**
 * The unified REA status. While a job runs it is re-read every 1.5 s (job
 * events only reach the connection that started it). ``watch`` also re-reads
 * it now and then and when the tab regains focus, so the shortcut, the
 * upload rules and the plugin square follow an install or uninstall done
 * elsewhere.
 */
export function useReaStatus({ enabled = true, watch = false }: { enabled?: boolean; watch?: boolean } = {}) {
  const identity = useCacheIdentity();
  const resource = statusResource(identity);
  const snapshot = useSharedResource(resource, { enabled });
  const state = snapshot.data?.state;
  const busy = Boolean(state && REA_BUSY_STATES.has(state));
  const watching = Boolean(enabled && watch && snapshot.data && !busy);

  useSSE({
    url: '/api/event',
    onEvent: handleReaEvent,
    enabled: enabled && typeof EventSource !== 'undefined',
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  useEffect(() => {
    if (!enabled || !busy) return undefined;
    return retainPoller(`status:${identity}`, STATUS_POLL_MS, () => {
      void resource.fetch({ force: true, silent: true });
    });
  }, [busy, enabled, identity, resource]);

  useEffect(() => {
    if (!watching) return undefined;
    return retainPoller(`status-watch:${identity}`, STATUS_WATCH_POLL_MS, () => {
      void resource.fetch({ force: true, silent: true });
    });
  }, [identity, resource, watching]);

  const refreshOnResume = useCallback(() => refetch(resource), [resource]);
  useRefreshOnResume(refreshOnResume, { enabled: Boolean(enabled && watch) });

  const refresh = useCallback(() => refetch(resource, true), [resource]);

  const recordInstallSession = useCallback(async (sessionId: string) => {
    await reaApi.recordInstallSession(sessionId);
    return refetch(resource);
  }, [resource]);

  return {
    status: snapshot.data,
    loading: snapshot.loading && !snapshot.initialized,
    error: snapshot.error,
    identity,
    refresh,
    recordInstallSession,
  };
}

/** Samples of any file type can be attached once REA is usable. */
export function reaAcceptsSamples(status: ReaStatus | null | undefined): boolean {
  return status?.state === 'ready';
}

export function __resetReaStateForTesting(): void {
  __resetReaCardStateForTesting();
  pollers.forEach((poller) => window.clearInterval(poller.timer));
  pollers.clear();
  cacheIdentity = null;
  statusCache = null;
}
