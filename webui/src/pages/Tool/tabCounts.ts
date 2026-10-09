import type { ToolListFacets } from '@/api/tool';
import type { APIServiceSummary, MCPCatalogEntry, MCPServer } from '@/types';

export function getApiServiceCount(services: readonly Pick<APIServiceSummary, 'integration_type'>[]): number {
  return services.filter((service) => service.integration_type !== 'device').length;
}

/** Match the MCP page's unfiltered native-server/catalog union, once per identity. */
export function getMcpServiceCount(
  servers: Record<string, unknown> | readonly Pick<MCPServer, 'name'>[],
  catalog: readonly Pick<MCPCatalogEntry, 'id'>[],
): number {
  const names = Array.isArray(servers) ? servers.map((server) => server.name) : Object.keys(servers);
  return new Set([...names, ...catalog.map((entry) => entry.id)]).size;
}

export interface ToolTabCounts {
  all: number;
  mcp: number;
  api: number;
  local: number;
}

export function getToolTabCounts(
  totalTools: number,
  facets: ToolListFacets,
  serviceCounts: Pick<ToolTabCounts, 'api' | 'mcp'>,
): ToolTabCounts {
  const allTools = Object.values(facets.source).reduce((sum, count) => sum + count, 0);
  return {
    all: allTools || totalTools,
    mcp: serviceCounts.mcp,
    api: serviceCounts.api,
    local: facets.source.plugin_py ?? 0,
  };
}
