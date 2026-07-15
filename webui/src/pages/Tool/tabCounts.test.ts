import { describe, expect, it } from 'vitest';
import type { Tool } from '@/api/tool';
import { getToolTabCounts } from './tabCounts';

describe('getToolTabCounts', () => {
  it('uses enabled API service count for the API tab', () => {
    const tools = [
      { source: 'api', source_name: 'f1' },
      { source: 'api', source_name: 'f2' },
      { source: 'api', source_name: 'f3' },
      { source: 'mcp', source_name: 'mcp-a' },
      { source: 'mcp', source_name: 'mcp-a' },
      { source: 'plugin_py', source_name: 'local' },
      { source: 'plugin_py', source_name: 'local' },
    ] as Tool[];

    expect(getToolTabCounts(tools, 3)).toEqual({
      all: 7,
      mcp: 1,
      api: 3,
      local: 2,
    });
  });
});
