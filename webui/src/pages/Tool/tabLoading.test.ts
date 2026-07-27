import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TOOL_TAB,
  TOOL_PAGE_SIZE,
  getTabSourceFilter,
  shouldLoadMcpCatalog,
} from './tabLoading';

describe('tool tab loading strategy', () => {
  it('starts from all tools with 25 visible rows', () => {
    expect(DEFAULT_TOOL_TAB).toBe('all');
    expect(TOOL_PAGE_SIZE).toBe(25);
    expect(getTabSourceFilter(DEFAULT_TOOL_TAB)).toBeUndefined();
  });

  it('loads each source group only for its tab', () => {
    expect(getTabSourceFilter('all')).toBeUndefined();
    expect(getTabSourceFilter('mcp')).toBe('mcp');
    expect(getTabSourceFilter('api')).toBe('api');
    expect(getTabSourceFilter('local')).toBe('plugin_py');
  });

  it('defers MCP catalog requests until the MCP tab is opened', () => {
    expect(shouldLoadMcpCatalog('all')).toBe(false);
    expect(shouldLoadMcpCatalog('api')).toBe(false);
    expect(shouldLoadMcpCatalog('local')).toBe(false);
    expect(shouldLoadMcpCatalog('mcp')).toBe(true);
  });
});
