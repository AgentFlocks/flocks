import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  claimTokenPolicy,
  confirmTokenPolicyDisplay,
  getTokenPolicyStatus,
  type TokenPolicyStatus,
} from '@/api/tokenPolicy';
import { useTokenPolicyNotice } from './useTokenPolicyNotice';

vi.mock('@/api/tokenPolicy', () => ({
  getTokenPolicyStatus: vi.fn(),
  claimTokenPolicy: vi.fn(),
  confirmTokenPolicyDisplay: vi.fn(),
}));
const now = '2026-09-14T01:59:59.000Z';
const notice = { id: 'policy', occurrence_id: 'a'.repeat(64), expires_at: '2026-10-09T01:00:00.000Z' };
const status = (candidate = false, overrides: Partial<TokenPolicyStatus> = {}): TokenPolicyStatus => ({
  state: 'active',
  notice: candidate ? notice : null,
  server_now: new Date().toISOString(),
  next_check_at: new Date(Date.now() + 60000).toISOString(),
  waiting_for_display: false,
  lease_expires_at: null,
  ...overrides,
});
const reserved = () => status(true, { lease_expires_at: new Date(Date.now() + 15000).toISOString() });
async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('useTokenPolicyNotice', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(now);
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    vi.mocked(getTokenPolicyStatus).mockImplementation(async () => status());
    vi.mocked(claimTokenPolicy).mockImplementation(async () => reserved());
    vi.mocked(confirmTokenPolicyDisplay).mockResolvedValue(true);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('confirms only after presentation; refresh and focus do not repeat a shown notice', async () => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true));
    const { result, unmount } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    expect(result.current.notice).toEqual(notice);
    expect(confirmTokenPolicyDisplay).not.toHaveBeenCalled();
    act(() => result.current.onPresented());
    await flush();
    expect(confirmTokenPolicyDisplay).toHaveBeenCalledTimes(1);
    act(() => result.current.close());
    act(() => window.dispatchEvent(new Event('focus')));
    await flush();
    expect(result.current.notice).toBeNull();
    unmount();
    renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    expect(claimTokenPolicy).toHaveBeenCalledTimes(1);
  });

  it('checks at the server supplied Monday deadline', async () => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(
      status(false, { next_check_at: '2026-09-14T02:00:00.000Z' }),
    );
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    vi.mocked(getTokenPolicyStatus).mockImplementation(async () => status(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.notice).toEqual(notice);
  });

  it('defers during upgrade then reserves as soon as it closes', async () => {
    vi.mocked(getTokenPolicyStatus).mockImplementation(async () => status(true));
    const { result, rerender } = renderHook(({ blocked }) => useTokenPolicyNotice('alice', blocked), {
      initialProps: { blocked: true },
    });
    await flush();
    expect(claimTokenPolicy).not.toHaveBeenCalled();
    rerender({ blocked: false });
    await flush();
    expect(result.current.notice).toEqual(notice);
  });

  it('keeps upgrade gate closed in a background tab, then checks policy first', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    vi.mocked(getTokenPolicyStatus).mockResolvedValue(status(true));
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    expect(result.current.ready).toBe(false);
    expect(getTokenPolicyStatus).not.toHaveBeenCalled();
    visibility.mockReturnValue('visible');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await flush();
    expect(result.current.notice).toEqual(notice);
  });

  it.each(['hidden', 'blocked'])(
    'does not display or confirm if it becomes %s during claim',
    async (reason) => {
      let resolveClaim!: (value: TokenPolicyStatus) => void;
      vi.mocked(getTokenPolicyStatus).mockResolvedValue(status(true));
      vi.mocked(claimTokenPolicy).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveClaim = resolve;
          }),
      );
      const { result, rerender } = renderHook(({ blocked }) => useTokenPolicyNotice('alice', blocked), {
        initialProps: { blocked: false },
      });
      await flush();
      if (reason === 'hidden') {
        vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
        act(() => document.dispatchEvent(new Event('visibilitychange')));
      } else rerender({ blocked: true });
      await act(async () => {
        resolveClaim(reserved());
      });
      expect(result.current.notice).toBeNull();
      expect(confirmTokenPolicyDisplay).not.toHaveBeenCalled();
    },
  );

  it('recovers a reservation after refresh before the claim response arrives', async () => {
    let leaseUntil = 0;
    let resolveFirst!: (value: TokenPolicyStatus) => void;
    vi.mocked(getTokenPolicyStatus).mockImplementation(async () =>
      leaseUntil > Date.now()
        ? status(false, { waiting_for_display: true, next_check_at: new Date(leaseUntil).toISOString() })
        : status(true),
    );
    vi.mocked(claimTokenPolicy).mockImplementationOnce(() => {
      leaseUntil = Date.now() + 15000;
      return new Promise((resolve) => {
        resolveFirst = resolve;
      });
    });
    const first = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    first.unmount();
    const second = renderHook(() => useTokenPolicyNotice('alice', false));
    await act(async () => {
      resolveFirst(reserved());
    });
    await flush();
    expect(second.result.current.notice).toBeNull();
    expect(second.result.current.ready).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(second.result.current.notice).toEqual(notice);
    expect(claimTokenPolicy).toHaveBeenCalledTimes(2);
  });

  it('times out an unrendered lazy component without confirming it', async () => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true));
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(result.current.notice).toBeNull();
    expect(confirmTokenPolicyDisplay).not.toHaveBeenCalled();
  });

  it('never renders a lease whose response arrived after its deadline', async () => {
    const delayed = reserved();
    let resolveClaim!: (value: TokenPolicyStatus) => void;
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true));
    vi.mocked(claimTokenPolicy).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveClaim = resolve;
        }),
    );
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
      resolveClaim(delayed);
    });
    expect(result.current.notice).toBeNull();
    expect(confirmTokenPolicyDisplay).not.toHaveBeenCalled();
  });

  it('retries a lost display confirmation with the same reservation', async () => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true));
    vi.mocked(confirmTokenPolicyDisplay).mockRejectedValueOnce(new Error('timeout'));
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    act(() => result.current.onPresented());
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const calls = vi.mocked(confirmTokenPolicyDisplay).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0].slice(0, 2)).toEqual(calls[1].slice(0, 2));
    expect(result.current.notice).toEqual(notice);
  });

  it.each(['finished', 'disabled'] as const)('stops periodic requests for a %s campaign', async (state) => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValue(status(false, { state, next_check_at: null }));
    renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(90000);
    });
    expect(getTokenPolicyStatus).toHaveBeenCalledTimes(1);
    act(() => window.dispatchEvent(new Event('focus')));
    await flush();
    expect(getTokenPolicyStatus).toHaveBeenCalledTimes(2);
  });

  it('releases the gate on API failure and retries on reconnect', async () => {
    vi.mocked(getTokenPolicyStatus).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    expect(result.current.ready).toBe(true);
    vi.mocked(getTokenPolicyStatus).mockResolvedValue(status(true));
    act(() => window.dispatchEvent(new Event('online')));
    await flush();
    expect(result.current.notice).toEqual(notice);
  });

  it('expires a displayed card offline using the server-relative deadline', async () => {
    const candidate = { ...notice, expires_at: '2026-09-14T02:00:01.000Z' };
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true, { notice: candidate }));
    vi.mocked(claimTokenPolicy).mockResolvedValueOnce({ ...reserved(), notice: candidate });
    const { result } = renderHook(() => useTokenPolicyNotice('alice', false));
    await flush();
    act(() => result.current.onPresented());
    await flush();
    vi.mocked(getTokenPolicyStatus).mockRejectedValue(new Error('offline'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.notice).toBeNull();
  });

  it('clears the notice on logout and aborts pending requests', async () => {
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce(status(true));
    const { result, rerender } = renderHook(({ userId }) => useTokenPolicyNotice(userId, false), {
      initialProps: { userId: 'alice' as string | undefined },
    });
    await flush();
    const signal = vi.mocked(getTokenPolicyStatus).mock.calls[0][0];
    rerender({ userId: undefined });
    await flush();
    expect(result.current.notice).toBeNull();
    expect(signal?.aborted).toBe(true);
  });
});
