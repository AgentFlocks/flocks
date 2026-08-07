import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ContextUsageSnapshot } from '@/api/session';
import { useSessionContextUsage } from './useSessionContextUsage';

const getContextUsageMock = vi.fn();

vi.mock('@/api/session', () => ({
  sessionApi: {
    getContextUsage: (...args: unknown[]) => getContextUsageMock(...args),
  },
}));

function buildSnapshot(overrides: Partial<ContextUsageSnapshot> = {}): ContextUsageSnapshot {
  return {
    sessionID: 'sess-1',
    usedTokens: 100,
    contextWindow: 1000,
    percent: 10,
    source: 'estimated',
    estimatedTokens: 100,
    compactedTokens: 0,
    segments: [],
    excludedSegments: [],
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('useSessionContextUsage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('deduplicates in-flight refreshes for the same session', async () => {
    const request = deferred<ContextUsageSnapshot>();
    getContextUsageMock.mockReturnValue(request.promise);
    const { result } = renderHook(() => useSessionContextUsage('sess-1'));

    act(() => {
      result.current.refresh();
      result.current.refresh();
    });

    expect(getContextUsageMock).toHaveBeenCalledTimes(1);
    expect(getContextUsageMock).toHaveBeenCalledWith('sess-1');

    await act(async () => {
      request.resolve(buildSnapshot({ usedTokens: 120 }));
      await request.promise;
    });

    expect(result.current.snapshot?.usedTokens).toBe(120);
  });

  it('skips a refresh immediately after a pushed snapshot', () => {
    const { result } = renderHook(() => useSessionContextUsage('sess-1'));

    act(() => {
      result.current.applyPushSnapshot(buildSnapshot({ usedTokens: 420 }));
      result.current.refresh({ skipIfFreshMs: 500 });
    });

    expect(getContextUsageMock).not.toHaveBeenCalled();
    expect(result.current.snapshot?.usedTokens).toBe(420);
  });

  it('ignores stale responses after a newer pushed snapshot', async () => {
    const request = deferred<ContextUsageSnapshot>();
    getContextUsageMock.mockReturnValue(request.promise);
    const { result } = renderHook(() => useSessionContextUsage('sess-1'));

    act(() => {
      result.current.refresh();
      result.current.applyPushSnapshot(buildSnapshot({ usedTokens: 420 }));
    });

    await act(async () => {
      request.resolve(buildSnapshot({ usedTokens: 900 }));
      await request.promise;
    });

    expect(result.current.snapshot?.usedTokens).toBe(420);
  });

  it('forces a new request and reports whether its snapshot was applied', async () => {
    const firstRequest = deferred<ContextUsageSnapshot>();
    const forcedRequest = deferred<ContextUsageSnapshot>();
    getContextUsageMock
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(forcedRequest.promise);
    const { result } = renderHook(() => useSessionContextUsage('sess-1'));

    let appliedPromise: Promise<ContextUsageSnapshot | null> | undefined;
    act(() => {
      result.current.refresh();
      appliedPromise = result.current.refresh({ force: true });
    });

    expect(getContextUsageMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      firstRequest.resolve(buildSnapshot({ usedTokens: 900 }));
      await firstRequest.promise;
    });
    expect(result.current.snapshot).toBeNull();

    let applied: ContextUsageSnapshot | null = null;
    await act(async () => {
      forcedRequest.resolve(buildSnapshot({ usedTokens: 420 }));
      applied = await appliedPromise!;
    });

    expect(applied?.usedTokens).toBe(420);
    expect(result.current.snapshot?.usedTokens).toBe(420);
  });

  it('reports a failed refresh without applying a snapshot', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    getContextUsageMock.mockRejectedValueOnce(new Error('unavailable'));
    const { result } = renderHook(() => useSessionContextUsage('sess-1'));

    try {
      let applied: ContextUsageSnapshot | null = buildSnapshot();
      await act(async () => {
        applied = await result.current.refresh({ force: true })!;
      });

      expect(applied).toBeNull();
      expect(result.current.snapshot).toBeNull();
      expect(warnSpy).toHaveBeenCalled();
    } finally {
      warnSpy.mockRestore();
    }
  });

  it('rejects a response from the previously rendered session immediately', async () => {
    const request = deferred<ContextUsageSnapshot>();
    getContextUsageMock.mockReturnValue(request.promise);
    const { result, rerender } = renderHook(
      ({ sessionId }) => useSessionContextUsage(sessionId),
      { initialProps: { sessionId: 'sess-1' } },
    );

    let appliedPromise: Promise<ContextUsageSnapshot | null> | undefined;
    act(() => {
      appliedPromise = result.current.refresh({ force: true });
    });
    rerender({ sessionId: 'sess-2' });

    let applied: ContextUsageSnapshot | null = buildSnapshot();
    await act(async () => {
      request.resolve(buildSnapshot({ sessionID: 'sess-1', usedTokens: 900 }));
      applied = await appliedPromise!;
    });

    expect(applied).toBeNull();
    expect(result.current.snapshot).toBeNull();
  });
});
