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
});
