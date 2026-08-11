/**
 * React hook for managing hook system
 */

import { hooksApi, type HookStatus } from '@/api/hooks';
import { createSharedResource, useSharedResource } from './useSharedResource';

const hookStatusResource = createSharedResource<HookStatus | null>({
  initialData: null,
  staleTimeMs: 5_000,
  minFetchIntervalMs: 1_000,
  fetcher: hooksApi.getStatus,
  fallbackDataOnError: null,
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch hook status'),
});

export function __resetHookStatusResourceForTesting(): void {
  hookStatusResource.resetForTesting();
}

export function useHooks() {
  const { data: status, loading, error, refetch } = useSharedResource(hookStatusResource);

  return {
    status,
    loading,
    error,
    refetch,
  };
}
