import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

describe('permissionApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists pending approval requests', async () => {
    const permissions = [{ id: 'permission-1', sessionID: 'session-1' }];
    mockGet.mockResolvedValue({ data: permissions });
    const { permissionApi } = await import('./permission');

    await expect(permissionApi.list()).resolves.toEqual(permissions);
    expect(mockGet).toHaveBeenCalledWith('/api/permission');
  });

  it('submits the selected reply protocol', async () => {
    mockPost.mockResolvedValue({ data: { success: true } });
    const { permissionApi } = await import('./permission');

    await permissionApi.reply('permission/1', { allow: true, always: true });

    expect(mockPost).toHaveBeenCalledWith('/api/permission/permission%2F1/reply', {
      allow: true,
      always: true,
    });
  });

  it('supports explicit response payloads', async () => {
    mockPost.mockResolvedValue({ data: { success: true } });
    const { permissionApi } = await import('./permission');

    await permissionApi.reply('permission/2', { allow: true, response: 'trust_tool_network' });

    expect(mockPost).toHaveBeenCalledWith('/api/permission/permission%2F2/reply', {
      allow: true,
      response: 'trust_tool_network',
    });
  });

  it('supports explicit trust network target response', async () => {
    mockPost.mockResolvedValue({ data: { success: true } });
    const { permissionApi } = await import('./permission');

    await permissionApi.reply('permission/3', { allow: true, response: 'trust_network_target' });

    expect(mockPost).toHaveBeenCalledWith('/api/permission/permission%2F3/reply', {
      allow: true,
      response: 'trust_network_target',
    });
  });
});
