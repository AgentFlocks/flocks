import { afterEach, describe, expect, it, vi } from 'vitest';

describe('hubAPI.installStream', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('posts to the configured API base with credentials', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://api.example.test:8000');

    const chunk = new TextEncoder().encode(
      'data: {"event":"complete","id":"soc-workspace","type":"component","name":"SOC Workspace","total":0}\n\n',
    );
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({ done: false, value: chunk })
        .mockResolvedValueOnce({ done: true, value: undefined }),
    };
    const response = {
      ok: true,
      status: 200,
      body: { getReader: () => reader },
    } as unknown as Response;
    const fetchMock = vi.fn(async () => response);
    vi.stubGlobal('fetch', fetchMock);

    const { hubAPI } = await import('./hub');
    const onProgress = vi.fn();

    await hubAPI.installStream('component', 'soc-workspace', onProgress);

    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.example.test:8000/api/hub/plugins/component/soc-workspace/install/stream',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: 'global' }),
      }),
    );
    expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({ event: 'complete' }));
  });

  it('preserves the backend detail when an install is rejected before streaming', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: '该套件需要 Flocks Pro 授权后才能安装' }),
    }));
    const { hubAPI } = await import('./hub');
    await expect(hubAPI.installStream('component', 'pro-scene', vi.fn()))
      .rejects.toThrow('该套件需要 Flocks Pro 授权后才能安装');
  });

  it('falls back to the HTTP status for a non-JSON error response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => { throw new Error('not JSON'); },
    }));
    const { hubAPI } = await import('./hub');
    await expect(hubAPI.installStream('component', 'scene', vi.fn())).rejects.toThrow('HTTP 502');
  });
});
