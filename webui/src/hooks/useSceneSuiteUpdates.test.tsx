import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { __resetSceneSuiteUpdatesForTesting, notifySceneSuitesChanged, useSceneSuiteUpdates } from './useSceneSuiteUpdates';
import { setupSSEMock } from '@/test/mocks/sse';

const { sceneSuites } = vi.hoisted(() => ({ sceneSuites: vi.fn() }));
vi.mock('@/api/hub', () => ({ hubAPI: { sceneSuites } }));

const suite = { id: 'soc-suite', name: 'SOC', version: '1.0.0', state: 'available', edition: 'oss', installedVersion: null };

describe('useSceneSuiteUpdates', () => {
  const sse = setupSSEMock();

  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    __resetSceneSuiteUpdatesForTesting();
    sceneSuites.mockResolvedValue({ data: [suite] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('shares one request and shows NEW only for available uninstalled suites', async () => {
    const first = renderHook(() => useSceneSuiteUpdates());
    const second = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(first.result.current.hasUnseenSuites).toBe(true));
    expect(second.result.current.hasUnseenSuites).toBe(true);
    expect(sceneSuites).toHaveBeenCalledTimes(1);

    sceneSuites.mockResolvedValue({ data: [
      { ...suite, state: 'installed', installedVersion: '1.0.0' },
      { ...suite, id: 'incompatible', state: 'incompatible' },
      { ...suite, id: 'partial', state: 'partial' },
    ] });
    await act(() => notifySceneSuitesChanged());
    expect(first.result.current.hasUnseenSuites).toBe(false);
  });

  it('marks fetched suites seen when visiting the manager and keeps the badge clear on return', async () => {
    const hook = renderHook(({ viewingScenes }) => useSceneSuiteUpdates({ viewingScenes, userId: 'alice' }), {
      initialProps: { viewingScenes: false },
    });
    await waitFor(() => expect(hook.result.current.hasUnseenSuites).toBe(true));
    hook.rerender({ viewingScenes: true });
    await waitFor(() => expect(localStorage.getItem('flocks.sceneSuites.seen:alice')).toContain('soc-suite'));
    hook.rerender({ viewingScenes: false });
    expect(hook.result.current.hasUnseenSuites).toBe(false);
    hook.unmount();

    const again = renderHook(() => useSceneSuiteUpdates({ userId: 'alice' }));
    expect(again.result.current.hasUnseenSuites).toBe(false);
    const anotherUser = renderHook(() => useSceneSuiteUpdates({ userId: 'bob' }));
    expect(anotherUser.result.current.hasUnseenSuites).toBe(true);
  });

  it('marks suites seen when the request finishes after entering the manager', async () => {
    let resolve: (value: unknown) => void = () => {};
    sceneSuites.mockReturnValue(new Promise((done) => { resolve = done; }));
    const hook = renderHook(({ viewingScenes }) => useSceneSuiteUpdates({ viewingScenes }), {
      initialProps: { viewingScenes: true },
    });
    await act(async () => resolve({ data: [suite] }));
    hook.rerender({ viewingScenes: false });
    expect(hook.result.current.hasUnseenSuites).toBe(false);
  });

  it('shows NEW again when a new suite or an unseen version arrives', async () => {
    const { result } = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => result.current.markSeen());
    expect(result.current.hasUnseenSuites).toBe(false);
    sceneSuites.mockResolvedValue({ data: [{ ...suite, version: '2.0.0' }] });
    await act(() => notifySceneSuitesChanged());
    expect(result.current.hasUnseenSuites).toBe(true);
    act(() => result.current.markSeen());
    sceneSuites.mockResolvedValue({ data: [{ ...suite, version: '2.0.0' }, { ...suite, id: 'new-scene' }] });
    await act(() => notifySceneSuitesChanged());
    expect(result.current.hasUnseenSuites).toBe(true);
  });

  it('fails without a badge and recovers after a successful refresh', async () => {
    sceneSuites.mockRejectedValueOnce({ response: { data: { detail: 'catalog unavailable' } } });
    const { result } = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('catalog unavailable');
    expect(result.current.hasUnseenSuites).toBe(false);
    await act(() => result.current.refetch());
    expect(result.current.hasUnseenSuites).toBe(true);
  });

  it('tolerates corrupt persisted state and denied storage writes', async () => {
    localStorage.setItem('flocks.sceneSuites.seen:local', 'not-json');
    const { result } = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(result.current.hasUnseenSuites).toBe(true));
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    act(() => result.current.markSeen());
    expect(result.current.hasUnseenSuites).toBe(false);
  });

  it('does not announce a previously browsed installed scene as new after uninstall', async () => {
    sceneSuites.mockResolvedValue({ data: [{ ...suite, state: 'installed', installedVersion: '1.0.0' }] });
    const hook = renderHook(({ viewingScenes }) => useSceneSuiteUpdates({ viewingScenes }), {
      initialProps: { viewingScenes: true },
    });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    hook.rerender({ viewingScenes: false });
    sceneSuites.mockResolvedValue({ data: [suite] });
    await act(() => notifySceneSuitesChanged());
    expect(hook.result.current.hasUnseenSuites).toBe(false);
  });

  it.each(['hub.scene_suites.changed', 'contracts.webui.pages.nav_changed'])(
    'refreshes cached suite state after an external %s event', async (type) => {
      const { result } = renderHook(() => useSceneSuiteUpdates());
      await waitFor(() => expect(result.current.loading).toBe(false));
      vi.useFakeTimers();
      sceneSuites.mockResolvedValue({ data: [{ ...suite, state: 'installed', installedVersion: '1.0.0' }] });
      act(() => sse.send({ type, properties: { suiteId: suite.id, action: 'install' } }));
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(sceneSuites).toHaveBeenCalledTimes(2);
      expect(result.current.suites[0].state).toBe('installed');
      expect(result.current.hasUnseenSuites).toBe(false);
    },
  );

  it('coalesces consumers and throttles bursts while retaining the latest external change', async () => {
    const first = renderHook(() => useSceneSuiteUpdates());
    const second = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    vi.useFakeTimers();
    sceneSuites.mockResolvedValue({ data: [{ ...suite, version: '2.0.0' }] });
    act(() => {
      for (let index = 0; index < 20; index += 1) {
        sse.send({ type: 'hub.scene_suites.changed', properties: {} });
      }
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    sceneSuites.mockResolvedValue({ data: [{ ...suite, version: '3.0.0' }] });
    act(() => {
      sse.send({ type: 'hub.scene_suites.changed', properties: {} });
      sse.send({ type: 'contracts.webui.pages.nav_changed', properties: {} });
    });
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(sceneSuites).toHaveBeenCalledTimes(3);
    expect(first.result.current.suites[0].version).toBe('3.0.0');
    expect(second.result.current.suites[0].version).toBe('3.0.0');
  });

  it('invalidates a request in flight before accepting the post-event result', async () => {
    let resolveOld: (value: unknown) => void = () => {};
    sceneSuites
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ data: [{ ...suite, state: 'installed', installedVersion: '1.0.0' }] });
    vi.useFakeTimers();
    const { result } = renderHook(() => useSceneSuiteUpdates());
    act(() => sse.send({ type: 'hub.scene_suites.changed', properties: {} }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(async () => resolveOld({ data: [suite] }));
    expect(result.current.suites[0].state).toBe('installed');
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    expect(result.current.hasUnseenSuites).toBe(false);
  });

  it('forces a shared fresh check on focus and visible resume within the cache window', async () => {
    const first = renderHook(() => useSceneSuiteUpdates());
    renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    vi.useFakeTimers();
    sceneSuites.mockResolvedValue({ data: [] });
    act(() => {
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    expect(first.result.current.suites).toHaveLength(0);
    sceneSuites.mockResolvedValue({ data: [suite] });
    await act(() => vi.advanceTimersByTimeAsync(1000));
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(3);
    expect(first.result.current.suites).toHaveLength(1);
  });

  it('checks fresh state when entering the scene manager without waiting for cache expiry', async () => {
    const hook = renderHook(({ viewingScenes }) => useSceneSuiteUpdates({ viewingScenes }), {
      initialProps: { viewingScenes: false },
    });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    vi.useFakeTimers();
    sceneSuites.mockResolvedValue({ data: [{ ...suite, id: 'external-scene' }] });
    hook.rerender({ viewingScenes: true });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    expect(hook.result.current.suites[0].id).toBe('external-scene');
    hook.rerender({ viewingScenes: false });
    expect(hook.result.current.hasUnseenSuites).toBe(false);
  });

  it('refreshes once when the shared SSE connection recovers', async () => {
    const first = renderHook(() => useSceneSuiteUpdates());
    renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    vi.useFakeTimers();
    act(() => sse.open());
    expect(sceneSuites).toHaveBeenCalledTimes(1);
    sceneSuites.mockResolvedValue({ data: [] });
    act(() => sse.error());
    await act(() => vi.advanceTimersByTimeAsync(3000));
    act(() => sse.open());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    expect(first.result.current.suites).toHaveLength(0);
  });

  it('ignores unrelated events and hidden visibility changes', async () => {
    const { result } = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(result.current.loading).toBe(false));
    vi.useFakeTimers();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    act(() => {
      sse.send({ type: 'session.updated', properties: {} });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(sceneSuites).toHaveBeenCalledTimes(1);
  });

  it('does not accept a stale background response during the trailing throttle interval', async () => {
    const { result } = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(result.current.loading).toBe(false));
    vi.useFakeTimers();
    let resolveOld: (value: unknown) => void = () => {};
    sceneSuites
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ data: [{ ...suite, version: '3.0.0' }] });
    act(() => sse.send({ type: 'hub.scene_suites.changed', properties: {} }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => sse.send({ type: 'hub.scene_suites.changed', properties: {} }));
    await act(async () => resolveOld({ data: [{ ...suite, version: '2.0.0' }] }));
    expect(result.current.suites[0].version).toBe('1.0.0');
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(result.current.suites[0].version).toBe('3.0.0');
    expect(sceneSuites).toHaveBeenCalledTimes(3);
  });

  it('cancels queued work on last unmount and checks fresh state on resubscription', async () => {
    const first = renderHook(() => useSceneSuiteUpdates());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    first.unmount();
    vi.useFakeTimers();
    sceneSuites.mockResolvedValue({ data: [] });
    const resumed = renderHook(() => useSceneSuiteUpdates());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
    expect(resumed.result.current.suites).toHaveLength(0);
    act(() => sse.send({ type: 'hub.scene_suites.changed', properties: {} }));
    resumed.unmount();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(sceneSuites).toHaveBeenCalledTimes(2);
  });
});
