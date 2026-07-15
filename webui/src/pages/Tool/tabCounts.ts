import type { Tool } from '@/api/tool';

export interface ToolTabCounts {
  all: number;
  mcp: number;
  api: number;
  local: number;
}

export function getToolTabCounts(tools: Tool[], apiEnabledServicesCount: number): ToolTabCounts {
  return {
    all: tools.length,
    mcp: new Set(tools.filter((tool) => tool.source === 'mcp').map((tool) => tool.source_name)).size,
    api: apiEnabledServicesCount,
    local: tools.filter((tool) => tool.source === 'plugin_py').length,
  };
}
