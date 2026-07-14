import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSSE } from './useSSE';

describe('useSSE', () => {
  const eventSourceCtor = vi.fn();
  const eventSources: FakeEventSource[] = [];

  class FakeEventSource {
    url: string;
    withCredentials: boolean;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    onopen: ((event: Event) => void) | null = null;
    readyState = 0;

    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;

    constructor(url: string, init?: EventSourceInit) {
      this.url = url;
      this.withCredentials = Boolean(init?.withCredentials);
      eventSources.push(this);
      eventSourceCtor(url, init);
    }

    close() {
      this.readyState = FakeEventSource.CLOSED;
    }

    addEventListener() {}
    removeEventListener() {}
    dispatchEvent() { return true; }
  }

  beforeEach(() => {
    eventSourceCtor.mockClear();
    eventSources.length = 0;
    vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('defaults to opening authenticated SSE connections', () => {
    const { unmount } = renderHook(() => useSSE({
      url: 'http://127.0.0.1:8000/api/event',
      onEvent: vi.fn(),
    }));

    expect(eventSourceCtor).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/event',
      { withCredentials: true },
    );

    unmount();
  });

  it('allows callers to opt out of credentials explicitly', () => {
    const { unmount } = renderHook(() => useSSE({
      url: '/public/events',
      onEvent: vi.fn(),
      withCredentials: false,
    }));

    expect(eventSourceCtor).toHaveBeenCalledWith(
      '/public/events',
      { withCredentials: false },
    );

    unmount();
  });

  it('shares one EventSource across subscribers with the same URL and credentials', () => {
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const first = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: firstHandler,
    }));
    const second = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: secondHandler,
    }));

    expect(eventSourceCtor).toHaveBeenCalledTimes(1);

    eventSources[0].onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({ type: 'ping', properties: { ok: true } }),
      }),
    );

    expect(firstHandler).toHaveBeenCalledWith({ type: 'ping', properties: { ok: true } });
    expect(secondHandler).toHaveBeenCalledWith({ type: 'ping', properties: { ok: true } });

    first.unmount();
    second.unmount();
  });

  it('keeps the shared EventSource open while another subscriber is still mounted', () => {
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const first = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: firstHandler,
    }));
    const second = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: secondHandler,
    }));

    const shared = eventSources[0];
    first.unmount();

    expect(shared.readyState).not.toBe(FakeEventSource.CLOSED);

    shared.onmessage?.(
      new MessageEvent('message', {
        data: JSON.stringify({ type: 'pong', properties: {} }),
      }),
    );

    expect(firstHandler).not.toHaveBeenCalled();
    expect(secondHandler).toHaveBeenCalledWith({ type: 'pong', properties: {} });

    second.unmount();
    expect(shared.readyState).toBe(FakeEventSource.CLOSED);
  });

  it('isolates event callbacks so one subscriber cannot block the others', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const throwingHandler = vi.fn(() => {
      throw new Error('subscriber failed');
    });
    const healthyHandler = vi.fn();

    const first = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: throwingHandler,
    }));
    const second = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: healthyHandler,
    }));

    act(() => {
      eventSources[0].onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({ type: 'ping', properties: { ok: true } }),
        }),
      );
    });

    expect(throwingHandler).toHaveBeenCalledTimes(1);
    expect(healthyHandler).toHaveBeenCalledWith({ type: 'ping', properties: { ok: true } });

    first.unmount();
    second.unmount();
  });

  it('waits for a successful fast reconnect before running isolated recovery callbacks', async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const throwingErrorHandler = vi.fn(() => {
      throw new Error('error callback failed');
    });
    const healthyErrorHandler = vi.fn();
    const throwingRecovery = vi.fn(() => {
      throw new Error('recovery callback failed');
    });
    const healthyRecovery = vi.fn();

    const first = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: vi.fn(),
      onError: throwingErrorHandler,
      onReconnect: throwingRecovery,
      reconnect: { initialDelay: 1000, maxDelay: 1000 },
    }));
    const second = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: vi.fn(),
      onError: healthyErrorHandler,
      onReconnect: healthyRecovery,
      reconnect: { initialDelay: 1000, maxDelay: 1000 },
    }));

    const failedSource = eventSources[0];
    act(() => {
      failedSource.onerror?.(new Event('error'));
    });

    expect(failedSource.readyState).toBe(FakeEventSource.CLOSED);
    expect(throwingErrorHandler).toHaveBeenCalledTimes(1);
    expect(healthyErrorHandler).toHaveBeenCalledTimes(1);
    expect(first.result.current.status).toBe('reconnecting');
    expect(throwingRecovery).not.toHaveBeenCalled();
    expect(healthyRecovery).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(eventSourceCtor).toHaveBeenCalledTimes(2);
    expect(throwingRecovery).not.toHaveBeenCalled();
    expect(healthyRecovery).not.toHaveBeenCalled();

    act(() => {
      eventSources[1].onopen?.(new Event('open'));
    });

    expect(first.result.current.status).toBe('connected');
    expect(throwingRecovery).toHaveBeenCalledTimes(1);
    expect(healthyRecovery).toHaveBeenCalledTimes(1);

    first.unmount();
    second.unmount();
  });

  it('runs recovery after a successful slow reconnect too', async () => {
    vi.useFakeTimers();
    const onReconnect = vi.fn();
    const hook = renderHook(() => useSSE({
      url: '/api/event',
      onEvent: vi.fn(),
      onReconnect,
      reconnect: { maxRetries: 0 },
    }));

    act(() => {
      eventSources[0].onerror?.(new Event('error'));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(eventSourceCtor).toHaveBeenCalledTimes(2);
    expect(onReconnect).not.toHaveBeenCalled();

    act(() => {
      eventSources[1].onopen?.(new Event('open'));
    });

    expect(onReconnect).toHaveBeenCalledTimes(1);
    hook.unmount();
  });

  it('reconnects and recovers after the server reports dropped events', () => {
    const onEvent = vi.fn();
    const onReconnect = vi.fn();
    const hook = renderHook(() => useSSE({
      url: '/api/event',
      onEvent,
      onReconnect,
    }));

    const staleSource = eventSources[0];
    act(() => {
      staleSource.onopen?.(new Event('open'));
      staleSource.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'server.events_dropped',
            properties: { dropped: 4 },
          }),
        }),
      );
    });

    expect(onEvent).toHaveBeenCalledWith({
      type: 'server.events_dropped',
      properties: { dropped: 4 },
    });
    expect(staleSource.readyState).toBe(FakeEventSource.CLOSED);
    expect(eventSourceCtor).toHaveBeenCalledTimes(2);
    expect(onReconnect).not.toHaveBeenCalled();

    act(() => {
      eventSources[1].onopen?.(new Event('open'));
    });

    expect(onReconnect).toHaveBeenCalledTimes(1);
    hook.unmount();
  });
});
