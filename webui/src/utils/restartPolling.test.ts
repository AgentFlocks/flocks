import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { checkRestartReadiness, UPGRADE_PROGRESS_TOKEN_HEADER } from './restartPolling';

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
    vi.useRealTimers();
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
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/health',
      '/',
    ]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/health',
      expect.objectContaining({
        cache: 'no-store',
        signal: expect.anything(),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/',
      expect.objectContaining({
        cache: 'no-store',
        signal: expect.anything(),
      }),
    );
  });

  it('aborts stalled readiness requests so polling can continue', async () => {
    vi.useFakeTimers();
    const requestSignals: AbortSignal[] = [];
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal;
      if (!signal) {
        throw new Error('expected an abort signal');
      }
      requestSignals.push(signal);
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'));
        }, { once: true });
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const readinessPromise = checkRestartReadiness();
    await vi.advanceTimersByTimeAsync(3_000);
    await vi.advanceTimersByTimeAsync(3_000);

    const readiness = await readinessPromise;
    expect(readiness.ready).toBe(false);
    expect(readiness.reason).toContain('health check failed');
    expect(readiness.reason).toContain('root page check failed');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(requestSignals).toHaveLength(2);
    expect(requestSignals.every((signal) => signal.aborted)).toBe(true);
  });

  it('gives up on a response whose body stalls after the headers arrived', async () => {
    vi.useFakeTimers();
    const stalledBody = () => new Promise<never>(() => {});
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: String(input) === '/',
      status: String(input) === '/' ? 200 : 503,
      headers: new Headers({ 'Content-Type': 'application/json' }),
      json: stalledBody,
      text: stalledBody,
    }) as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    let settled = false;
    const readinessPromise = checkRestartReadiness('tok-123').finally(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(3_000);
    await vi.advanceTimersByTimeAsync(3_000);

    expect(settled).toBe(true);
    const readiness = await readinessPromise;
    expect(readiness.ready).toBe(false);
    expect(readiness.reason).toContain('health check failed');
    expect(readiness.reason).toContain('root page check failed');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('reads the upgrade handoff progress from a 503 health response without probing the root page', async () => {
    const upgrade = {
      phase: 'running',
      step: 'build_webui',
      steps: [
        { id: 'stop', status: 'done' },
        { id: 'build_webui', status: 'running' },
        { id: 'start', status: 'pending' },
      ],
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'upgrading', upgrade }), {
      status: 503,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const readiness = await checkRestartReadiness('tok-123');

    expect(readiness.ready).toBe(false);
    expect(readiness.upgrade).toEqual(upgrade);
    expect(readiness.reason).toContain('build_webui');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.objectContaining({
      headers: { [UPGRADE_PROGRESS_TOKEN_HEADER]: 'tok-123' },
    }));
  });

  it('ignores 503 bodies that are not handoff progress', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/health') {
        return new Response(JSON.stringify({ status: 'starting' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('<html></html>', { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const readiness = await checkRestartReadiness();

    expect(readiness).toEqual({ ready: false, reason: 'health check returned HTTP 503' });
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty('headers');
  });
});
