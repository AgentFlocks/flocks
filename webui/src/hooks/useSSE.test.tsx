import { renderHook } from '@testing-library/react';
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
});
