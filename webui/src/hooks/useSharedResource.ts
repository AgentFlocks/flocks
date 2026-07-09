import { useCallback, useEffect, useSyncExternalStore } from 'react';

export interface SharedResourceSnapshot<T> {
  data: T;
  loading: boolean;
  error: string | null;
  initialized: boolean;
  updatedAt: number;
}

export interface SharedResourceFetchOptions {
  force?: boolean;
  silent?: boolean;
}

export interface CreateSharedResourceOptions<T> {
  initialData: T;
  fetcher: () => Promise<T>;
  staleTimeMs?: number;
  minFetchIntervalMs?: number;
  getErrorMessage?: (error: unknown) => string;
  fallbackDataOnError?: T | ((previous: T) => T);
}

export interface SharedResource<T> {
  getSnapshot: () => SharedResourceSnapshot<T>;
  subscribe: (listener: () => void) => () => void;
  fetch: (options?: SharedResourceFetchOptions) => Promise<T>;
  resetForTesting: () => void;
}

export interface UseSharedResourceOptions {
  enabled?: boolean;
  loadOnMount?: boolean;
  silentInitialLoad?: boolean;
}

export interface UseRefreshOnResumeOptions {
  enabled?: boolean;
}

function defaultGetErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Failed to fetch resource';
}

function resolveFallbackData<T>(
  fallback: CreateSharedResourceOptions<T>['fallbackDataOnError'],
  previous: T,
): T {
  if (typeof fallback === 'function') {
    return (fallback as (previous: T) => T)(previous);
  }
  if (fallback !== undefined) {
    return fallback;
  }
  return previous;
}

export function createSharedResource<T>(options: CreateSharedResourceOptions<T>): SharedResource<T> {
  const staleTimeMs = options.staleTimeMs ?? 0;
  const minFetchIntervalMs = options.minFetchIntervalMs ?? 0;
  const getErrorMessage = options.getErrorMessage ?? defaultGetErrorMessage;
  const listeners = new Set<() => void>();

  let inFlight: Promise<T> | null = null;
  let lastStartedAt = 0;
  let snapshot: SharedResourceSnapshot<T> = {
    data: options.initialData,
    loading: true,
    error: null,
    initialized: false,
    updatedAt: 0,
  };

  const emit = () => {
    listeners.forEach((listener) => listener());
  };

  const updateSnapshot = (next: Partial<SharedResourceSnapshot<T>>) => {
    snapshot = {
      ...snapshot,
      ...next,
    };
    emit();
  };

  const fetch = (fetchOptions: SharedResourceFetchOptions = {}): Promise<T> => {
    const now = Date.now();
    const force = fetchOptions.force === true;
    const silent = fetchOptions.silent === true;
    const freshEnough = snapshot.initialized && now - snapshot.updatedAt < staleTimeMs;
    const recentlyStarted = snapshot.initialized && now - lastStartedAt < minFetchIntervalMs;

    if (!force && freshEnough) {
      return Promise.resolve(snapshot.data);
    }

    if (inFlight) {
      return inFlight;
    }

    if (!force && recentlyStarted) {
      return Promise.resolve(snapshot.data);
    }

    lastStartedAt = now;
    updateSnapshot({
      loading: silent ? snapshot.loading : true,
      error: null,
    });

    const request = options.fetcher()
      .then((data) => {
        updateSnapshot({
          data,
          loading: false,
          error: null,
          initialized: true,
          updatedAt: Date.now(),
        });
        return data;
      })
      .catch((error: unknown) => {
        const data = resolveFallbackData(options.fallbackDataOnError, snapshot.data);
        updateSnapshot({
          data,
          loading: false,
          error: getErrorMessage(error),
          initialized: true,
        });
        return data;
      })
      .finally(() => {
        if (inFlight === request) {
          inFlight = null;
        }
      });

    inFlight = request;
    return request;
  };

  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    fetch,
    resetForTesting: () => {
      inFlight = null;
      lastStartedAt = 0;
      snapshot = {
        data: options.initialData,
        loading: true,
        error: null,
        initialized: false,
        updatedAt: 0,
      };
      emit();
    },
  };
}

export function useSharedResource<T>(
  resource: SharedResource<T>,
  options: UseSharedResourceOptions = {},
) {
  const enabled = options.enabled ?? true;
  const loadOnMount = options.loadOnMount ?? true;
  const snapshot = useSyncExternalStore(
    resource.subscribe,
    resource.getSnapshot,
    resource.getSnapshot,
  );

  useEffect(() => {
    if (!enabled || !loadOnMount) return;
    const current = resource.getSnapshot();
    void resource.fetch({
      silent: current.initialized || options.silentInitialLoad === true,
    });
  }, [enabled, loadOnMount, options.silentInitialLoad, resource]);

  const refetch = useCallback(
    (fetchOptions: SharedResourceFetchOptions = {}) => resource.fetch({
      force: true,
      ...fetchOptions,
    }),
    [resource],
  );

  return {
    ...snapshot,
    refetch,
  };
}

export function useRefreshOnResume(
  refresh: () => void | Promise<unknown>,
  options: UseRefreshOnResumeOptions = {},
) {
  const enabled = options.enabled ?? true;

  useEffect(() => {
    if (!enabled || typeof document === 'undefined' || typeof window === 'undefined') return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    };

    const handleWindowFocus = () => {
      void refresh();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleWindowFocus);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [enabled, refresh]);
}
