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

export const toolAPI = {
  list: (params?: { source?: ToolSource; category?: string }) =>
    client.get<Tool[]>('/api/tools', { params }),

  get: (name: string) =>
    client.get<Tool>(`/api/tools/${name}`),

  refresh: () =>
    client.post('/api/tools/refresh'),

  test: (name: string, params: Record<string, any>) =>
    client.post(`/api/tools/${name}/test`, { params }),

  listFixtures: (name: string) =>
    client.get<ToolFixture[]>(`/api/tools/${name}/fixtures`),

  getStatistics: (name: string) =>
    client.get<ToolStatistics>(`/api/tools/${name}/statistics`),

  setEnabled: (name: string, enabled: boolean) =>
    client.patch<Tool>(`/api/tools/${name}`, { enabled }),

  /**
   * Remove the user-level setting and restore the YAML/registration default
   * for this tool (currently only the `enabled` flag is overlaid).
   */
  resetSetting: (name: string) =>
    client.post<Tool>(`/api/tools/${name}/reset`),

  delete: (name: string) =>
    client.delete<{ status: string; message: string }>(`/api/tools/${name}`),
};

export const canDirectlyTestTool = (tool: Pick<Tool, 'source'>) =>
  tool.source !== 'builtin';
