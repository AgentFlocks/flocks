import { describe, expect, it } from 'vitest';
import type { ToolListFacets } from '@/api/tool';
import { getApiServiceCount, getMcpServiceCount, getToolTabCounts } from './tabCounts';

describe('getToolTabCounts', () => {
  it('uses total native services instead of enabled services or tool-bearing source groups', () => {
    const facets: ToolListFacets = {
      category: {},
      source: { api: 64, mcp: 49, plugin_py: 5 },
      source_groups: { api: 7, mcp: 6, plugin_py: 1 },
      source_name: {},
      enabled: { true: 46, false: 3 },
    };
    expect(getToolTabCounts(49, facets, { api: 10, mcp: 11 })).toEqual({
      all: 118,
      mcp: 11,
      api: 10,
      local: 5,
    });
    expect(getToolTabCounts(0, { ...facets, source: {}, source_groups: {} }, { api: 10, mcp: 11 })).toEqual({
      all: 0, mcp: 11, api: 10, local: 0,
    });
  });

  it('counts disabled API services and excludes every device integration', () => {
    const services = Array.from({ length: 26 }, (_, index) => ({
      id: `service-${index}`, enabled: index < 7 || index >= 10,
      integration_type: index >= 10 ? 'device' : undefined,
    }));
    expect(getApiServiceCount(services)).toBe(10);
  });

  it('counts the MCP native/catalog union once, regardless of state or tool count', () => {
    const servers = [
      { name: 'connected', status: 'connected', tools_count: 49 },
      { name: 'gridinsoft', status: 'connected', tools_count: 0 },
      { name: 'disabled', status: 'disabled', tools_count: 0 },
      { name: 'disconnected', status: 'disconnected', tools_count: 0 },
      { name: 'error', status: 'error', tools_count: 0 },
    ];
    const catalog = [{ id: 'gridinsoft' }, { id: 'disabled' }, { id: 'catalog-only' }];
    expect(getMcpServiceCount(servers, catalog)).toBe(6);
    expect(getMcpServiceCount(Object.fromEntries(servers.map((server) => [server.name, server])), catalog)).toBe(6);
  });
});
