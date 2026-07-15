import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSSE } from './useSSE';

describe('useSSE', () => {
  const eventSourceCtor = vi.fn();

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
});
