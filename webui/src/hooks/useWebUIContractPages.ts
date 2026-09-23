import { useCallback, useEffect } from 'react';
import i18n from '@/i18n';
import {
  webuiContractPagesAPI,
  type WebUIContractPageListItem,
  type WebUIContractWorkspaceListItem,
} from '@/api/webuiContractPages';
import { useSSE, type SSEEvent } from '@/hooks/useSSE';
import { SCENE_SUITES_CHANGED_EVENT } from '@/utils/sceneSuites';
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
      webuiContractPagesAPI.listWorkspaces(false),
    ]);
    return {
      pages: Array.isArray(pagesResponse.data) ? pagesResponse.data : [],
      workspaces: Array.isArray(workspacesResponse.data) ? workspacesResponse.data : [],
    };
  },
  getErrorMessage: (err) => (
    err instanceof Error ? err.message : i18n.t('nav.fetchFailed', { ns: 'webuiContractPage' })
  ),
});

let localChangeSubscribers = 0;
const handledNavEvents = new WeakSet<SSEEvent>();

function refreshAfterSuiteChange(): void {
  webuiContractNavResource.invalidate();
  void webuiContractNavResource.fetch({ force: true, silent: true });
}

function handleNavigationEvent(event: SSEEvent): void {
  if (event.type !== 'contracts.webui.pages.nav_changed' || handledNavEvents.has(event)) return;
  // A shared SSE message reaches each mounted consumer as the same object.
  // Invalidate it once so every consumer joins the same current request.
  handledNavEvents.add(event);
  refreshAfterSuiteChange();
}

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

  useEffect(() => {
    // Layout and a workspace host share one listener. Later mutations still
    // invalidate an in-flight read so a stale result cannot restore old tabs.
    if (localChangeSubscribers++ === 0) {
      window.addEventListener(SCENE_SUITES_CHANGED_EVENT, refreshAfterSuiteChange);
    }
    return () => {
      if (--localChangeSubscribers === 0) {
        window.removeEventListener(SCENE_SUITES_CHANGED_EVENT, refreshAfterSuiteChange);
      }
    };
  }, []);

  useSSE({
    url: '/api/event',
    onEvent: handleNavigationEvent,
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
