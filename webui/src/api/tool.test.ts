import { beforeEach, describe, expect, it, vi } from 'vitest';

const getMock = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('listAllToolPages', () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it('loads every page for one service without changing the list page size', async () => {
    getMock
      .mockResolvedValueOnce({
        data: {
          items: [{ name: 'first' }],
          total: 2,
          offset: 0,
          limit: 200,
          facets: {},
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{ name: 'second' }],
          total: 2,
          offset: 1,
          limit: 200,
          facets: {},
        },
      });

    const { listAllToolPages } = await import('./tool');
    const result = await listAllToolPages({
      source: 'api',
      sourceName: 'service-a',
      enabled: 'true',
      q: 'indicator',
    });

    expect(result.map((tool) => tool.name)).toEqual(['first', 'second']);
    expect(getMock).toHaveBeenNthCalledWith(1, '/api/tools/page', {
      params: expect.objectContaining({
        source: 'api',
        source_name: 'service-a',
        enabled: 'true',
        q: 'indicator',
        offset: 0,
        limit: 200,
      }),
    });
    expect(getMock).toHaveBeenNthCalledWith(2, '/api/tools/page', {
      params: expect.objectContaining({ source: 'api', source_name: 'service-a', offset: 1, limit: 200 }),
    });
  });

  it('stops on an empty page even when the reported total is stale', async () => {
    getMock.mockResolvedValue({
      data: {
        items: [],
        total: 500,
        offset: 0,
        limit: 200,
        facets: {},
      },
    });

    const { listAllToolPages } = await import('./tool');
    const result = await listAllToolPages({ source: 'mcp', sourceName: 'server-a' });

    expect(result).toEqual([]);
    expect(getMock).toHaveBeenCalledTimes(1);
  });
});
