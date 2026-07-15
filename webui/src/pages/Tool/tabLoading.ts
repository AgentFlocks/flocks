import type { ToolSource } from '@/api/tool';

export type TabKey = 'all' | 'mcp' | 'api' | 'local';

export const DEFAULT_TOOL_TAB: TabKey = 'all';
export const TOOL_PAGE_SIZE = 25;

export function getTabSourceFilter(tab: TabKey): ToolSource | ToolSource[] | undefined {
  if (tab === 'mcp') return 'mcp';
  if (tab === 'api') return 'api';
  if (tab === 'local') return 'plugin_py';
  return undefined;
}

export function shouldLoadMcpCatalog(tab: TabKey): boolean {
  return tab === 'mcp';
}
