import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetToolsResourceForTesting, useToolPage, useTools } from './useTools';

const { listMock, listPageMock, refreshMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  listPageMock: vi.fn(),
  refreshMock: vi.fn(),
}));

vi.mock('@/api/tool', () => ({
  toolAPI: {
    list: listMock,
    listPage: listPageMock,
    refresh: refreshMock,
  },
}));

const emptyFacets = {
  category: {},
  source: {},
  source_groups: {},
  source_name: {},
  enabled: {},
};

describe('useTools', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetToolsResourceForTesting();
  });

  it('renders the tool list without automatically refreshing plugins', async () => {
    listMock.mockResolvedValue({
      data: [
        {
          name: 'tool-alpha',
          description: 'alpha tool',
          category: 'custom',
          source: 'custom',
          enabled: true,
        },
      ],
    });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(result.current.tools[0].name).toBe('tool-alpha');
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it('fetches tools when the window regains focus without refreshing plugins', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
          { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
        ],
      });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(refreshMock).not.toHaveBeenCalled();

    const futureNow = Date.now() + 6000;
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(futureNow);
    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });
    nowSpy.mockRestore();

    expect(refreshMock).not.toHaveBeenCalled();
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('refreshes plugins only when refetch is called explicitly', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
          { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
        ],
      });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('reports a plugin refresh failure after reloading the visible tool list', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      });
    refreshMock.mockRejectedValue(new Error('plugin refresh failed'));

    const { result } = renderHook(() => useTools());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let caught: unknown;
    await act(async () => {
      try {
        await result.current.refetch();
      } catch (error) {
        caught = error;
      }
    });

    expect(caught).toEqual(new Error('plugin refresh failed'));
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledTimes(2);
    expect(result.current.tools).toHaveLength(1);
  });

  it('returns a partial refresh outcome and keeps successfully loaded tools', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
          { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
        ],
      });
    const partialResult = {
      status: 'partial' as const,
      tool_count: 2,
      message: 'plugin: broken manifest',
    };
    refreshMock.mockResolvedValue({ data: partialResult });

    const { result } = renderHook(() => useTools());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let outcome;
    await act(async () => {
      outcome = await result.current.refetch();
    });

    expect(outcome).toEqual(partialResult);
    expect(listMock).toHaveBeenCalledTimes(2);
    expect(result.current.tools.map((tool) => tool.name)).toEqual(['tool-alpha', 'tool-beta']);
  });

  it('returns an HTTP 200 error outcome without rejecting', async () => {
    listMock.mockResolvedValue({ data: [] });
    const errorResult = {
      status: 'error' as const,
      tool_count: 0,
      message: 'all refresh stages failed',
    };
    refreshMock.mockResolvedValue({ data: errorResult });

    const { result } = renderHook(() => useTools());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(result.current.refetch()).resolves.toEqual(errorResult);
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('rejects when the visible list reload fails and preserves the previous tools', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockRejectedValueOnce(new Error('tool list unavailable'));
    refreshMock.mockResolvedValue({
      data: { status: 'success', tool_count: 1, message: 'refreshed' },
    });

    const { result } = renderHook(() => useTools());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(result.current.refetch()).rejects.toThrow('tool list unavailable');
    expect(result.current.tools.map((tool) => tool.name)).toEqual(['tool-alpha']);
    expect(result.current.error).toBe('tool list unavailable');
  });

  it('preserves both backend details when refresh and visible list reload fail', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockRejectedValueOnce({
        response: { data: { detail: 'tool list storage unavailable' } },
        message: 'Request failed with status code 503',
      });
    refreshMock.mockRejectedValue({
      response: { data: { detail: 'plugin registry unavailable' } },
      message: 'Request failed with status code 500',
    });

    const { result } = renderHook(() => useTools());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(result.current.refetch()).rejects.toThrow(
      'Tool refresh failed: plugin registry unavailable; tool list reload failed: tool list storage unavailable',
    );
    expect(result.current.tools.map((tool) => tool.name)).toEqual(['tool-alpha']);
  });

  it('shares the initial tool list request across concurrent hook instances', async () => {
    let resolveList: (value: { data: any[] }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const first = renderHook(() => useTools());
    const second = renderHook(() => useTools());

    expect(listMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveList({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.tools).toHaveLength(1);
    expect(second.result.current.tools).toHaveLength(1);
  });

  it('fetches a server-side page with normalized params', async () => {
    listPageMock.mockResolvedValue({
      data: {
        items: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'mcp', enabled: true }],
        total: 33,
        offset: 20,
        limit: 20,
        facets: {
          ...emptyFacets,
          source: { mcp: 33 },
        },
      },
    });

    const { result } = renderHook(() => useToolPage({
      source: 'mcp',
      q: 'alpha',
      offset: 20,
      limit: 20,
    }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(result.current.total).toBe(33);
    expect(result.current.facets.source.mcp).toBe(33);
    expect(listPageMock).toHaveBeenCalledWith({
      source: 'mcp',
      category: '',
      sourceName: '',
      enabled: '',
      q: 'alpha',
      sortBy: 'source',
      sortDir: 'asc',
      offset: 20,
      limit: 20,
    });
  });

  it('defaults paged requests to 25 items', async () => {
    listPageMock.mockResolvedValue({
      data: {
        items: [],
        total: 0,
        offset: 0,
        limit: 25,
        facets: emptyFacets,
      },
    });

    const { result } = renderHook(() => useToolPage({ source: 'api' }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.limit).toBe(25);
    expect(listPageMock).toHaveBeenCalledWith({
      source: 'api',
      category: '',
      sourceName: '',
      enabled: '',
      q: '',
      sortBy: 'source',
      sortDir: 'asc',
      offset: 0,
      limit: 25,
    });
  });

  it('shares the same paged request across concurrent hook instances', async () => {
    let resolvePage: (value: { data: any }) => void = () => {};
    listPageMock.mockReturnValue(new Promise((resolve) => {
      resolvePage = resolve;
    }));

    const first = renderHook(() => useToolPage({ source: 'api', offset: 0, limit: 20 }));
    const second = renderHook(() => useToolPage({ source: 'api', offset: 0, limit: 20 }));

    expect(listPageMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvePage({
        data: {
          items: [{ name: 'api-tool', description: 'api tool', category: 'custom', source: 'api', enabled: true }],
          total: 1,
          offset: 0,
          limit: 20,
          facets: {
            ...emptyFacets,
            source: { api: 1 },
          },
        },
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.tools).toHaveLength(1);
    expect(second.result.current.tools).toHaveLength(1);
  });

  it('refreshes only the visible paged list when refetch is called', async () => {
    listPageMock
      .mockResolvedValueOnce({
        data: {
          items: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
          total: 1,
          offset: 0,
          limit: 20,
          facets: emptyFacets,
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
            { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
          ],
          total: 2,
          offset: 0,
          limit: 20,
          facets: emptyFacets,
        },
      });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    const { result } = renderHook(() => useToolPage({ offset: 0, limit: 20 }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listMock).not.toHaveBeenCalled();
    expect(listPageMock).toHaveBeenCalledTimes(2);
  });

  it('reports a paged plugin refresh failure after reloading the visible page', async () => {
    listPageMock.mockResolvedValue({
      data: {
        items: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
        total: 1,
        offset: 0,
        limit: 20,
        facets: emptyFacets,
      },
    });
    refreshMock.mockRejectedValue(new Error('paged refresh failed'));

    const { result } = renderHook(() => useToolPage({ offset: 0, limit: 20 }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let caught: unknown;
    await act(async () => {
      try {
        await result.current.refetch();
      } catch (error) {
        caught = error;
      }
    });

    expect(caught).toEqual(new Error('paged refresh failed'));
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listPageMock).toHaveBeenCalledTimes(2);
    expect(result.current.tools).toHaveLength(1);
  });

  it('rejects when the visible page reload fails and preserves the previous page', async () => {
    listPageMock
      .mockResolvedValueOnce({
        data: {
          items: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
          total: 1,
          offset: 0,
          limit: 20,
          facets: emptyFacets,
        },
      })
      .mockRejectedValueOnce(new Error('visible page unavailable'));
    refreshMock.mockResolvedValue({
      data: { status: 'success', tool_count: 1, message: 'refreshed' },
    });

    const { result } = renderHook(() => useToolPage({ offset: 0, limit: 20 }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(result.current.refetch()).rejects.toThrow('visible page unavailable');
    expect(result.current.tools.map((tool) => tool.name)).toEqual(['tool-alpha']);
    expect(result.current.error).toBe('visible page unavailable');
  });

  it('marks other cached queries stale and refreshes them when revisited', async () => {
    const callsBySource = new Map<string, number>();
    listPageMock.mockImplementation(async (params: { source?: string; offset?: number; limit?: number }) => {
      const source = params.source || 'all';
      const call = (callsBySource.get(source) || 0) + 1;
      callsBySource.set(source, call);
      return {
        data: {
          items: [{
            name: `${source}-${call}`,
            description: `${source} tool`,
            category: 'custom',
            source,
            enabled: true,
          }],
          total: 1,
          offset: params.offset ?? 0,
          limit: params.limit ?? 20,
          facets: emptyFacets,
        },
      };
    });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    const { result, rerender } = renderHook(
      ({ source }) => useToolPage({ source, offset: 0, limit: 20 }),
      { initialProps: { source: 'mcp' } },
    );
    await waitFor(() => expect(result.current.tools[0]?.name).toBe('mcp-1'));

    rerender({ source: 'api' });
    await waitFor(() => expect(result.current.tools[0]?.name).toBe('api-1'));

    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.tools[0]?.name).toBe('api-2');
    expect(callsBySource.get('mcp')).toBe(1);

    rerender({ source: 'mcp' });
    await waitFor(() => expect(result.current.tools[0]?.name).toBe('mcp-2'));
  });
});
