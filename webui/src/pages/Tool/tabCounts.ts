import type { ToolListFacets } from '@/api/tool';

export interface ToolTabCounts {
  all: number;
  mcp: number;
  api: number;
  local: number;
}

export function getToolTabCounts(
  totalTools: number,
  facets: ToolListFacets,
  apiEnabledServicesCount: number,
): ToolTabCounts {
  const allTools = Object.values(facets.source).reduce((sum, count) => sum + count, 0);
  return {
    all: allTools || totalTools,
    mcp: facets.source_groups.mcp ?? 0,
    api: apiEnabledServicesCount,
    local: facets.source.plugin_py ?? 0,
  };
}
