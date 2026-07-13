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
  rejectOnError?: boolean;
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
  invalidate: () => void;
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

  type FetchOutcome = {
    data: T;
    failed: boolean;
    error?: unknown;
  };

  let inFlight: Promise<FetchOutcome> | null = null;
  let inFlightGeneration: number | null = null;
  let generation = 0;
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

  const resolveOutcome = (
    request: Promise<FetchOutcome>,
    rejectOnError: boolean,
  ): Promise<T> => request.then((outcome) => {
    if (rejectOnError && outcome.failed) {
      throw outcome.error;
    }
    return outcome.data;
  });

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
      if (force && inFlightGeneration !== generation) {
        return inFlight.then(() => fetch(fetchOptions));
      }
      return resolveOutcome(inFlight, fetchOptions.rejectOnError === true);
    }

    if (!force && recentlyStarted) {
      return Promise.resolve(snapshot.data);
    }

    lastStartedAt = now;
    const requestGeneration = generation;
    updateSnapshot({
      loading: silent ? snapshot.loading : true,
      error: null,
    });

    const request = options.fetcher()
      .then((data) => {
        if (requestGeneration !== generation) {
          return { data, failed: false };
        }
        updateSnapshot({
          data,
          loading: false,
          error: null,
          initialized: true,
          updatedAt: Date.now(),
        });
        return { data, failed: false };
      })
      .catch((error: unknown) => {
        if (requestGeneration !== generation) {
          // An obsolete request must not mutate the current snapshot, but its
          // original strict caller still needs the real failure.
          return { data: snapshot.data, failed: true, error };
        }
        const data = resolveFallbackData(options.fallbackDataOnError, snapshot.data);
        updateSnapshot({
          data,
          loading: false,
          error: getErrorMessage(error),
          initialized: true,
        });
        return { data, failed: true, error };
      })
      .finally(() => {
        if (inFlight === request) {
          inFlight = null;
          inFlightGeneration = null;
        }
      });

    inFlight = request;
    inFlightGeneration = requestGeneration;
    return resolveOutcome(request, fetchOptions.rejectOnError === true);
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
    invalidate: () => {
      generation += 1;
      lastStartedAt = 0;
      updateSnapshot({ updatedAt: 0 });
    },
    resetForTesting: () => {
      inFlight = null;
      inFlightGeneration = null;
      generation = 0;
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
