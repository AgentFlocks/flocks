import { beforeEach, describe, expect, it, vi } from 'vitest';

const getMock = vi.fn();
const patchMock = vi.fn();
const putMock = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => getMock(...args),
    post: vi.fn(),
    patch: (...args: unknown[]) => patchMock(...args),
    put: (...args: unknown[]) => putMock(...args),
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

  it('refuses an incomplete inventory for a whole-group mutation scope', async () => {
    getMock.mockResolvedValue({ data: { items: [], total: 50, offset: 0, limit: 200, facets: {} } });
    const { listAllToolPages } = await import('./tool');
    await expect(listAllToolPages({ group: 'Ops' }, { requireComplete: true })).rejects.toThrow('before all members could be loaded');
    expect(getMock).toHaveBeenCalledExactlyOnceWith('/api/tools/page', { params: expect.objectContaining({ group: 'Ops', offset: 0, limit: 200 }) });
  });
});

describe('native group metadata clients', () => {
  beforeEach(() => {
    getMock.mockReset().mockResolvedValue({ data: {} });
    patchMock.mockReset().mockResolvedValue({ data: {} });
    putMock.mockReset().mockResolvedValue({ data: {} });
  });

  it('writes only group through each original native identity and envelope', async () => {
    const { agentAPI } = await import('./agent');
    const { workflowAPI } = await import('./workflow');
    const { skillAPI } = await import('./skill');
    const { toolAPI } = await import('./tool');
    const { mcpAPI } = await import('./mcp');
    const { providerAPI } = await import('./provider');
    const { deviceAPI } = await import('./device');
    await agentAPI.update('analyst', { group: 'Ops' });
    await workflowAPI.update('draft', { group: null });
    await skillAPI.updateGroup('custom', 'Ops');
    await toolAPI.updateGroup('native_tool', null);
    await mcpAPI.update('configured-server', { group: 'Ops' });
    await providerAPI.updateApiService('service__v2_0', { group: null });
    await deviceAPI.update('device-1', { group: 'Ops' });
    expect(putMock.mock.calls).toEqual([
      ['/api/agent/analyst', { group: 'Ops' }],
      ['/api/workflow/draft', { group: null }],
      ['/api/mcp/configured-server', { config: { group: 'Ops' } }],
      ['/api/devices/device-1', { group: 'Ops' }],
    ]);
    expect(patchMock.mock.calls).toEqual([
      ['/api/skills/custom', { group: 'Ops' }],
      ['/api/tools/native_tool', { group: null }],
      ['/api/provider/api-services/service__v2_0', { group: null }],
    ]);
    expect(getMock).not.toHaveBeenCalled();
  });

  it('omits group for All and sends the empty scalar only for Ungrouped', async () => {
    const { toolAPI } = await import('./tool');
    await toolAPI.listPage({ q: 'needle' });
    await toolAPI.listPage({ q: 'needle', group: '' });
    await toolAPI.listPage({ group: 'One, exact name' });
    expect(getMock.mock.calls.map((call) => call[1].params.group)).toEqual([undefined, '', 'One, exact name']);
  });

  it('keeps ordinary content forms unpolluted by group metadata', async () => {
    const { skillAPI } = await import('./skill');
    const form = { name: 'custom-renamed', description: 'Edited', content: '# Body' };
    await skillAPI.update('custom', form);
    expect(putMock).toHaveBeenCalledExactlyOnceWith('/api/skills/custom', form);
    expect(putMock.mock.calls[0][1]).not.toHaveProperty('group');
  });
});
