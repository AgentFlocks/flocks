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
    expect(mockGet).toHaveBeenCalledWith('/permission');
  });

  it('submits the selected reply protocol', async () => {
    mockPost.mockResolvedValue({ data: { success: true } });
    const { permissionApi } = await import('./permission');

    await permissionApi.reply('permission/1', { allow: true, always: true });

    expect(mockPost).toHaveBeenCalledWith('/permission/permission%2F1/reply', {
      allow: true,
      always: true,
    });
  });
});
