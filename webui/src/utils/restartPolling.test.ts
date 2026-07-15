import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { checkRestartReadiness } from './restartPolling';

describe('checkRestartReadiness', () => {
  const originalLocation = window.location;

  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: new URL('http://127.0.0.1:5173/'),
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it('checks same-origin health without probing the legacy backend port', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/health') {
        return new Response('', { status: 404 });
      }
      return new Response('<html></html>', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(checkRestartReadiness()).resolves.toEqual({
      ready: false,
      reason: 'health check returned HTTP 404',
    });
    expect(fetchMock).toHaveBeenCalledWith('/api/health', { cache: 'no-store' });
    expect(fetchMock).toHaveBeenCalledWith('/', { cache: 'no-store' });
    expect(fetchMock).not.toHaveBeenCalledWith('http://127.0.0.1:8000/api/health', { cache: 'no-store' });
  });
});
