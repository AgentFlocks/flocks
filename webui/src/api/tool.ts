import client from './client';

// Re-export shared types from the central types module
export type { ToolParameter, ToolSource, Tool } from '@/types';
import type { Tool, ToolSource } from '@/types';

export interface ToolStatistics {
  toolName: string;
  callCount: number;
  successCount: number;
  errorCount: number;
  totalRuntime: number;
  avgRuntime: number;
  lastUsed?: number;
}

export interface ToolFixture {
  label: string;
  label_cn?: string | null;
  params: Record<string, any>;
  tags: string[];
  has_assertion: boolean;
}

export type ToolListSortField = 'category' | 'source' | 'source_name' | 'enabled' | 'name';
export type ToolListSortDir = 'asc' | 'desc';

export interface ToolListPageParams {
  /** Omitted = All; empty string = Ungrouped; otherwise one exact name. */
  group?: string;
  source?: string;
  category?: string;
  sourceName?: string;
  enabled?: string;
  q?: string;
  sortBy?: ToolListSortField;
  sortDir?: ToolListSortDir;
  offset?: number;
  limit?: number;
}

export interface ToolListFacets {
  /** Counts before group selection and pagination; '' denotes Ungrouped. */
  group?: Record<string, number>;
  category: Record<string, number>;
  source: Record<string, number>;
  source_groups: Record<string, number>;
  source_name: Record<string, number>;
  enabled: Record<string, number>;
}

export interface ToolListPageResponse {
  items: Tool[];
  total: number;
  offset: number;
  limit: number;
  facets: ToolListFacets;
}

export interface ToolRefreshResponse {
  status: 'success' | 'partial' | 'error';
  tool_count: number;
  message: string;
  stages: Record<string, 'success' | 'error'>;
  errors: string[];
}

export interface ToolDeleteResponse {
  status: 'success' | 'partial';
  message: string;
  errors?: string[];
}

export const toolAPI = {
  list: (params?: { source?: ToolSource; category?: string }) =>
    client.get<Tool[]>('/api/tools', { params }),

  listPage: (params?: ToolListPageParams) =>
    client.get<ToolListPageResponse>('/api/tools/page', {
      params: {
        group: params?.group,
        source: params?.source,
        category: params?.category,
        source_name: params?.sourceName,
        enabled: params?.enabled,
        q: params?.q,
        sort_by: params?.sortBy,
        sort_dir: params?.sortDir,
        offset: params?.offset,
        limit: params?.limit,
      },
    }),

  get: (name: string) =>
    client.get<Tool>(`/api/tools/${name}`),

  refresh: () =>
    client.post<ToolRefreshResponse>('/api/tools/refresh'),

  test: (name: string, params: Record<string, any>) =>
    client.post(`/api/tools/${name}/test`, { params }),

  listFixtures: (name: string) =>
    client.get<ToolFixture[]>(`/api/tools/${name}/fixtures`),

  getStatistics: (name: string) =>
    client.get<ToolStatistics>(`/api/tools/${name}/statistics`),

  setEnabled: (name: string, enabled: boolean, options?: { device_id?: string }) =>
    client.patch<Tool>(
      `/api/tools/${name}`,
      { enabled },
      options?.device_id ? { params: { device_id: options.device_id } } : undefined,
    ),

  updateGroup: (name: string, group: string | null) =>
    client.patch<Tool>(`/api/tools/${encodeURIComponent(name)}`, { group }),

  /** Restore the enabled default without removing other native settings. */
  resetSetting: (name: string) =>
    client.post<Tool>(`/api/tools/${name}/reset`),

  delete: (name: string) =>
    client.delete<ToolDeleteResponse>(`/api/tools/${name}`),
};

export async function listAllToolPages(params: ToolListPageParams, options?: { requireComplete?: boolean }): Promise<Tool[]> {
  const pageSize = 200;
  const items: Tool[] = [];
  let offset = 0;

  while (true) {
    const response = await toolAPI.listPage({
      ...params,
      offset,
      limit: pageSize,
    });
    const pageItems = Array.isArray(response.data.items) ? response.data.items : [];
    items.push(...pageItems);
    offset += pageItems.length;
    if (options?.requireComplete && pageItems.length === 0 && offset < response.data.total) {
      throw new Error('The native tool inventory changed before all members could be loaded. Refresh and try again.');
    }
    if (pageItems.length === 0 || offset >= response.data.total) break;
  }

  return items;
}

export const canDirectlyTestTool = (tool: Pick<Tool, 'source'>) =>
  tool.source !== 'builtin';
