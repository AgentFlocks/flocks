import { useCallback } from 'react';
import { agentAPI, Agent } from '@/api/agent';
import { createSharedResource, useRefreshOnResume, useSharedResource } from './useSharedResource';

const AGENT_LIST_STALE_TIME_MS = 5000;
const AGENT_REFRESH_MIN_INTERVAL_MS = 1000;

const agentsResource = createSharedResource<Agent[]>({
  initialData: [],
  staleTimeMs: AGENT_LIST_STALE_TIME_MS,
  minFetchIntervalMs: AGENT_REFRESH_MIN_INTERVAL_MS,
  fetcher: async () => {
    const response = await agentAPI.list();
    return Array.isArray(response.data) ? response.data : [];
  },
  fallbackDataOnError: [],
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch agents'),
});

let agentRefreshInFlight: Promise<void> | null = null;
let lastAgentRefreshAt = 0;

function refreshAgentsResource(): Promise<void> {
  const now = Date.now();

  if (agentRefreshInFlight) {
    return agentRefreshInFlight;
  }

  if (now - lastAgentRefreshAt < AGENT_REFRESH_MIN_INTERVAL_MS) {
    return Promise.resolve();
  }

  lastAgentRefreshAt = now;
  agentRefreshInFlight = agentAPI.refresh()
    .catch(() => {
      // Best-effort: if refresh fails, still try to fetch the latest list.
    })
    .then(() => agentsResource.fetch({ force: true, silent: true }))
    .then(() => undefined)
    .finally(() => {
      agentRefreshInFlight = null;
    });

  return agentRefreshInFlight;
}

export function __resetAgentsResourceForTesting(): void {
  agentsResource.resetForTesting();
  agentRefreshInFlight = null;
  lastAgentRefreshAt = 0;
}

export function useAgents() {
  const {
    data: agents,
    loading,
    error,
    refetch: fetchAgents,
  } = useSharedResource(agentsResource);

  const refreshAndFetch = useCallback(() => refreshAgentsResource(), []);
  useRefreshOnResume(refreshAndFetch);

  const refetch = useCallback(
    (showLoading = true) => fetchAgents({ silent: !showLoading }),
    [fetchAgents],
  );

  return {
    agents,
    loading,
    error,
    refetch,
  };
}
