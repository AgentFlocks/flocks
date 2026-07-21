import { useCallback } from 'react';
import i18n from '@/i18n';
import {
  webuiContractPagesAPI,
  type WebUIContractPageListItem,
  type WebUIContractWorkspaceListItem,
} from '@/api/webuiContractPages';
import { useSSE } from '@/hooks/useSSE';
import { createSharedResource, useRefreshOnResume, useSharedResource } from './useSharedResource';

interface WebUIContractNavResourceData {
  pages: WebUIContractPageListItem[];
  workspaces: WebUIContractWorkspaceListItem[];
}

const WEBUI_CONTRACT_NAV_STALE_TIME_MS = 1000;
const WEBUI_CONTRACT_NAV_MIN_FETCH_INTERVAL_MS = 1000;

const webuiContractNavResource = createSharedResource<WebUIContractNavResourceData>({
  initialData: {
    pages: [],
    workspaces: [],
  },
  staleTimeMs: WEBUI_CONTRACT_NAV_STALE_TIME_MS,
  minFetchIntervalMs: WEBUI_CONTRACT_NAV_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const [pagesResponse, workspacesResponse] = await Promise.all([
      webuiContractPagesAPI.list(true),
      webuiContractPagesAPI.listWorkspaces(true),
    ]);
    return {
      pages: Array.isArray(pagesResponse.data) ? pagesResponse.data : [],
      workspaces: Array.isArray(workspacesResponse.data) ? workspacesResponse.data : [],
    };
  },
  fallbackDataOnError: {
    pages: [],
    workspaces: [],
  },
  getErrorMessage: (err) => (
    err instanceof Error ? err.message : i18n.t('nav.fetchFailed', { ns: 'webuiContractPage' })
  ),
});

export function __resetWebUIContractPagesResourceForTesting(): void {
  webuiContractNavResource.resetForTesting();
}

export function useWebUIContractPages() {
  const {
    data,
    loading,
    error,
    refetch,
  } = useSharedResource(webuiContractNavResource);

  const refreshOnResume = useCallback(
    () => webuiContractNavResource.fetch({ silent: true }),
    [],
  );
  useRefreshOnResume(refreshOnResume);

  useSSE({
    url: '/api/event',
    onEvent: useCallback((evt) => {
      if (evt.type === 'contracts.webui.pages.nav_changed') {
        void webuiContractNavResource.fetch({ force: true, silent: true });
      }
    }, []),
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  return {
    pages: data.pages,
    workspaces: data.workspaces,
    loading,
    error,
    refetch,
  };
}
