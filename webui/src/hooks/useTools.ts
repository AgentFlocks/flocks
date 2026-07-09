import { useCallback } from 'react';
import { toolAPI, Tool } from '@/api/tool';
import { createSharedResource, useRefreshOnResume, useSharedResource } from './useSharedResource';

const TOOL_LIST_STALE_TIME_MS = 5000;
const TOOL_LIST_MIN_FETCH_INTERVAL_MS = 1000;

const toolsResource = createSharedResource<Tool[]>({
  initialData: [],
  staleTimeMs: TOOL_LIST_STALE_TIME_MS,
  minFetchIntervalMs: TOOL_LIST_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const response = await toolAPI.list();
    return Array.isArray(response.data) ? response.data : [];
  },
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch tools'),
});

let toolRefreshInFlight: Promise<void> | null = null;

function refreshToolsResource(): Promise<void> {
  if (toolRefreshInFlight) {
    return toolRefreshInFlight;
  }

  toolRefreshInFlight = toolAPI.refresh()
    .catch(() => {
      // Best-effort refresh; still update the visible list afterwards.
    })
    .then(() => toolsResource.fetch({ force: true, silent: true }))
    .then(() => undefined)
    .finally(() => {
      toolRefreshInFlight = null;
    });

  return toolRefreshInFlight;
}

export function __resetToolsResourceForTesting(): void {
  toolsResource.resetForTesting();
  toolRefreshInFlight = null;
}

export function useTools() {
  const {
    data: tools,
    loading,
    error,
  } = useSharedResource(toolsResource);

  const refreshVisibleList = useCallback(
    () => toolsResource.fetch({ silent: true }),
    [],
  );
  useRefreshOnResume(refreshVisibleList);

  const refetch = useCallback(() => refreshToolsResource(), []);

  return {
    tools,
    loading,
    error,
    refetch,
  };
}
