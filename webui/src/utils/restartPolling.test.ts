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

  it('falls back to the loopback backend health endpoint during static handover', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/health') {
        return new Response('', { status: 404 });
      }
      if (url === 'http://127.0.0.1:8000/api/health') {
        return new Response(JSON.stringify({ status: 'healthy' }), { status: 200 });
      }
      return new Response('<html></html>', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(checkRestartReadiness()).resolves.toEqual({ ready: true });
    expect(fetchMock).toHaveBeenCalledWith('/api/health', { cache: 'no-store' });
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8000/api/health', { cache: 'no-store' });
  });
});
