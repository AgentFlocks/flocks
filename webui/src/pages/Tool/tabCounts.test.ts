import { describe, expect, it } from 'vitest';
import type { ToolListFacets } from '@/api/tool';
import { getToolTabCounts } from './tabCounts';

describe('getToolTabCounts', () => {
  it('uses enabled API service count for the API tab', () => {
    const facets: ToolListFacets = {
      category: {},
      source: { api: 3, mcp: 2, plugin_py: 2 },
      source_groups: { api: 3, mcp: 1, plugin_py: 1 },
      source_name: {},
      enabled: {},
    };

    expect(getToolTabCounts(2, facets, 3)).toEqual({
      all: 7,
      mcp: 1,
      api: 3,
      local: 2,
    });
  });
});
