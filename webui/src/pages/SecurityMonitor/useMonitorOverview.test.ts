import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { MonitorSnapshot } from '@/api/securityMonitoring';
import { isMonitoringOverviewEvent, useMonitorOverview } from './useMonitorOverview';

const mocks = vi.hoisted(() => ({ overview: vi.fn(), subscription: null as any }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: { overview: mocks.overview } }));
vi.mock('@/hooks/useSSE', () => ({ useSSE: (options: any) => { mocks.subscription = options; } }));
const snapshot = { businessDate: '2026-10-13', sessionID: 'monitor-session', runs: [{ session_id: 'monitor-session', execution_id: 'monitor-run' }], currentRun: { session_id: 'live-session' } } as MonitorSnapshot;
const deferred = () => {
  let resolve!: (value: any) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks(); mocks.overview.mockResolvedValue({ data: snapshot }); });
afterEach(() => vi.useRealTimers());
const flush = () => act(async () => { await Promise.resolve(); });
const emit = (type: string, properties = {}) => act(() => mocks.subscription.onEvent({ type, properties }));

it('coalesces repeated refreshes and long-request polls instead of starting parallel overview requests', async () => {
  const first = deferred(); mocks.overview.mockReturnValueOnce(first.promise);
  const { result } = renderHook(() => useMonitorOverview(''));
  await flush();
  act(() => { void result.current.refresh(); void result.current.refresh(); });
  await act(async () => vi.advanceTimersByTime(20000));
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve({ data: snapshot }));
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  expect(result.current.data).toBe(snapshot);
});

it('ignores unrelated tasks and coalesces bursts of monitoring changes into one refresh', async () => {
  renderHook(() => useMonitorOverview('')); await flush();
  for (let i = 0; i < 30; i++) emit('task.updated', { sessionID: `unrelated-${i}`, executionID: 'other' });
  await act(async () => vi.advanceTimersByTime(1000));
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  for (let i = 0; i < 30; i++) emit('task.updated', { sessionID: 'monitor-session' });
  emit('monitor.execution.finished');
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  await act(async () => vi.advanceTimersByTime(500));
  expect(mocks.overview).toHaveBeenCalledTimes(2);
});

it('queues only one follow-up when events arrive during an in-flight request', async () => {
  const slow = deferred(); mocks.overview.mockReturnValueOnce(slow.promise);
  const { result } = renderHook(() => useMonitorOverview('')); await flush();
  emit('monitor.execution.started');
  await act(async () => vi.advanceTimersByTime(500));
  emit('monitor.execution.finished');
  await act(async () => vi.advanceTimersByTime(500));
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  const newest = { ...snapshot, nextRun: 'latest' };
  mocks.overview.mockResolvedValue({ data: newest });
  await act(async () => slow.resolve({ data: snapshot }));
  expect(mocks.overview).toHaveBeenCalledTimes(2);
  expect(result.current.data).toBe(newest);
});

it('does not publish an old date and collapses rapid date changes into the latest date', async () => {
  const slow = deferred(); mocks.overview.mockReturnValueOnce(slow.promise);
  const { result, rerender } = renderHook(({ day }) => useMonitorOverview(day), { initialProps: { day: '' } }); await flush();
  rerender({ day: '2026-10-11' }); rerender({ day: '2026-10-12' });
  const latest = deferred(); mocks.overview.mockReturnValueOnce(latest.promise);
  await act(async () => slow.resolve({ data: snapshot }));
  expect(result.current.data).toBeNull();
  expect(mocks.overview).toHaveBeenCalledTimes(2);
  expect(mocks.overview).toHaveBeenLastCalledWith('2026-10-12');
  const historical = { ...snapshot, businessDate: '2026-10-12' };
  await act(async () => latest.resolve({ data: historical }));
  expect(result.current.data).toBe(historical);
});

it('does not reuse stale facts while start/pause is in progress and refreshes after it completes', async () => {
  const old = deferred(); mocks.overview.mockReturnValueOnce(old.promise);
  const { result } = renderHook(() => useMonitorOverview('')); await flush();
  act(() => result.current.suspendRefresh());
  emit('monitor.control.changed');
  await act(async () => vi.advanceTimersByTime(10000));
  await act(async () => old.resolve({ data: snapshot }));
  expect(result.current.data).toBeNull();
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  const paused = { ...snapshot, installation: { status: 'disabled' } };
  mocks.overview.mockResolvedValue({ data: paused });
  await act(async () => result.current.resumeRefresh());
  expect(mocks.overview).toHaveBeenCalledTimes(2);
  expect(result.current.data).toBe(paused);
});

it('waits for a post-mutation refresh even if the previous request is still in flight', async () => {
  const old = deferred(); mocks.overview.mockReturnValueOnce(old.promise);
  const { result } = renderHook(() => useMonitorOverview('')); await flush();
  let done = false;
  act(() => { result.current.suspendRefresh(); void result.current.resumeRefresh().then(() => { done = true; }); });
  expect(done).toBe(false);
  const updated = { ...snapshot, nextRun: 'post-mutation' };
  mocks.overview.mockResolvedValue({ data: updated });
  await act(async () => old.resolve({ data: snapshot }));
  expect(done).toBe(true);
  expect(result.current.data).toBe(updated);
});

it('can recover on retry and stops queued work after unmount', async () => {
  const old = deferred(); mocks.overview.mockReturnValueOnce(old.promise);
  const { result, rerender, unmount } = renderHook(({ day }) => useMonitorOverview(day), { initialProps: { day: '' } }); await flush();
  rerender({ day: '2026-10-12' });
  mocks.overview.mockRejectedValueOnce(new Error('current error'));
  await act(async () => old.reject(new Error('obsolete error')));
  expect(result.current.error).toContain('数据更新失败');
  await act(async () => result.current.refresh());
  expect(result.current.error).toBe('');
  emit('monitor.control.changed');
  const count = mocks.overview.mock.calls.length;
  unmount();
  await act(async () => vi.advanceTimersByTime(30000));
  expect(mocks.overview).toHaveBeenCalledTimes(count);
});

it('recognizes the current global monitoring session while viewing a historical day', () => {
  expect(isMonitoringOverviewEvent({ type: 'task.updated', properties: { sessionID: 'live-session' } }, snapshot)).toBe(true);
  expect(isMonitoringOverviewEvent({ type: 'task.updated', properties: { executionID: 'monitor-run' } }, snapshot)).toBe(true);
  expect(isMonitoringOverviewEvent({ type: 'task.updated', properties: {} }, snapshot)).toBe(false);
  expect(isMonitoringOverviewEvent({ type: 'session.updated', properties: { sessionID: 'monitor-session' } }, snapshot)).toBe(false);
});


it('keeps a previous historical date stale when switching back to today', async () => {
  const history = { ...snapshot, businessDate: '2026-10-12' };
  mocks.overview.mockResolvedValueOnce({ data: history });
  const { result, rerender } = renderHook(({ day }) => useMonitorOverview(day), { initialProps: { day: '2026-10-12' } });
  await flush();
  expect(result.current.isCurrentSnapshot).toBe(true);
  const latest = deferred(); mocks.overview.mockReturnValueOnce(latest.promise);
  rerender({ day: '' });
  await flush();
  expect(result.current.data).toBe(history);
  expect(result.current.isCurrentSnapshot).toBe(false);
  await act(async () => latest.resolve({ data: snapshot }));
  expect(result.current.isCurrentSnapshot).toBe(true);
});

it('does not lose an event update when polling flushes its timer during an earlier request', async () => {
  const old = deferred(); mocks.overview.mockReturnValueOnce(old.promise);
  const { result } = renderHook(() => useMonitorOverview('')); await flush();
  emit('monitor.execution.finished');
  act(() => { void result.current.refresh(); });
  expect(mocks.overview).toHaveBeenCalledTimes(1);
  await act(async () => old.resolve({ data: snapshot }));
  expect(mocks.overview).toHaveBeenCalledTimes(2);
});

it('never starts a deferred fetch after the component unmounts', async () => {
  const { unmount } = renderHook(() => useMonitorOverview(''));
  unmount();
  await flush();
  expect(mocks.overview).not.toHaveBeenCalled();
});
