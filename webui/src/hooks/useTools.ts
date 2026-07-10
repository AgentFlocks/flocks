import { useCallback, useMemo } from 'react';
import {
  toolAPI,
  Tool,
  type ToolListFacets,
  type ToolListPageParams,
  type ToolListPageResponse,
} from '@/api/tool';
import {
  createSharedResource,
  useRefreshOnResume,
  useSharedResource,
  type SharedResource,
} from './useSharedResource';

const TOOL_LIST_STALE_TIME_MS = 5000;
const TOOL_LIST_MIN_FETCH_INTERVAL_MS = 1000;
const MAX_TOOL_PAGE_RESOURCES = 80;
const DEFAULT_TOOL_PAGE_LIMIT = 25;

const EMPTY_TOOL_FACETS: ToolListFacets = {
  category: {},
  source: {},
  source_groups: {},
  source_name: {},
  enabled: {},
};

const EMPTY_TOOL_PAGE: ToolListPageResponse = {
  items: [],
  total: 0,
  offset: 0,
  limit: DEFAULT_TOOL_PAGE_LIMIT,
  facets: EMPTY_TOOL_FACETS,
};

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
const toolPageResources = new Map<string, SharedResource<ToolListPageResponse>>();

function normalizeToolPageParams(params: ToolListPageParams): Required<ToolListPageParams> {
  return {
    source: params.source ?? '',
    category: params.category ?? '',
    sourceName: params.sourceName ?? '',
    enabled: params.enabled ?? '',
    q: params.q ?? '',
    sortBy: params.sortBy ?? 'source',
    sortDir: params.sortDir ?? 'asc',
    offset: params.offset ?? 0,
    limit: params.limit ?? DEFAULT_TOOL_PAGE_LIMIT,
  };
}

function makeToolPageResourceKey(params: ToolListPageParams): string {
  return JSON.stringify(normalizeToolPageParams(params));
}

function getToolPageResource(params: ToolListPageParams): SharedResource<ToolListPageResponse> {
  const normalized = normalizeToolPageParams(params);
  const key = makeToolPageResourceKey(normalized);
  const existing = toolPageResources.get(key);
  if (existing) return existing;

  const resource = createSharedResource<ToolListPageResponse>({
    initialData: {
      ...EMPTY_TOOL_PAGE,
      offset: normalized.offset,
      limit: normalized.limit,
    },
    staleTimeMs: TOOL_LIST_STALE_TIME_MS,
    minFetchIntervalMs: TOOL_LIST_MIN_FETCH_INTERVAL_MS,
    fetcher: async () => {
      const response = await toolAPI.listPage(normalized);
      return response.data;
    },
    fallbackDataOnError: (previous) => previous,
    getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch tools'),
  });
  toolPageResources.set(key, resource);
  if (toolPageResources.size > MAX_TOOL_PAGE_RESOURCES) {
    const oldestKey = toolPageResources.keys().next().value;
    if (oldestKey) toolPageResources.delete(oldestKey);
  }
  return resource;
}

function refreshToolsResource(): Promise<void> {
  if (toolRefreshInFlight) {
    return toolRefreshInFlight;
  }

  toolRefreshInFlight = toolAPI.refresh()
    .catch(() => {
      // Best-effort refresh; still update the visible list afterwards.
    })
    .then(() => {
      toolsResource.invalidate();
      toolPageResources.forEach((resource) => resource.invalidate());
      return toolsResource.fetch({ force: true, silent: true });
    })
    .then(() => undefined)
    .finally(() => {
      toolRefreshInFlight = null;
    });

  return toolRefreshInFlight;
}

export function __resetToolsResourceForTesting(): void {
  toolsResource.resetForTesting();
  toolPageResources.forEach((resource) => resource.resetForTesting());
  toolPageResources.clear();
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

export function useToolPage(params: ToolListPageParams) {
  const resource = useMemo(
    () => getToolPageResource(params),
    [
      params.source,
      params.category,
      params.sourceName,
      params.enabled,
      params.q,
      params.sortBy,
      params.sortDir,
      params.offset,
      params.limit,
    ],
  );
  const {
    data,
    loading,
    error,
    initialized,
  } = useSharedResource(resource);

  const refreshVisiblePage = useCallback(
    () => resource.fetch({ silent: true }),
    [resource],
  );
  useRefreshOnResume(refreshVisiblePage);

  const refetch = useCallback(async () => {
    if (toolRefreshInFlight) {
      await toolRefreshInFlight;
    } else {
      toolRefreshInFlight = toolAPI.refresh()
        .catch(() => {
          // Best-effort refresh; still update the visible page afterwards.
        })
        .then(() => undefined)
        .finally(() => {
          toolRefreshInFlight = null;
        });
      await toolRefreshInFlight;
    }
    toolPageResources.forEach((pageResource) => pageResource.invalidate());
    toolsResource.invalidate();
    await resource.fetch({ force: true, silent: true });
  }, [resource]);

  return {
    tools: data.items,
    total: data.total,
    facets: data.facets,
    offset: data.offset,
    limit: data.limit,
    loading,
    error,
    initialized,
    refetch,
  };
}
