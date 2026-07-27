import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __resetTaskResourcesForTesting,
  __getTaskResourceCacheSizesForTesting,
  useQueueStatus,
  useTaskDashboard,
  useTaskExecutions,
  useTaskExecutionsByScheduler,
} from './useTasks';

const {
  dashboardMock,
  getSystemNoticeMock,
  listExecutionsMock,
  listSchedulerExecutionsMock,
  listSchedulersMock,
  queueStatusMock,
} = vi.hoisted(() => ({
  dashboardMock: vi.fn(),
  getSystemNoticeMock: vi.fn(),
  listExecutionsMock: vi.fn(),
  listSchedulerExecutionsMock: vi.fn(),
  listSchedulersMock: vi.fn(),
  queueStatusMock: vi.fn(),
}));

vi.mock('@/api/task', () => ({
  taskAPI: {
    dashboard: dashboardMock,
    getSystemNotice: getSystemNoticeMock,
    listExecutions: listExecutionsMock,
    listSchedulerExecutions: listSchedulerExecutionsMock,
    listSchedulers: listSchedulersMock,
    queueStatus: queueStatusMock,
  },
}));

function makeExecution(overrides: Record<string, unknown> = {}) {
  return {
    id: 'exec-1',
    schedulerID: 'scheduler-1',
    title: 'Execution One',
    description: '',
    priority: 'normal',
    source: { sourceType: 'user' },
    triggerType: 'run_once',
    status: 'running',
    deliveryStatus: 'unread',
    executionInputSnapshot: {},
    retry: { maxRetries: 0, retryCount: 0, retryDelaySeconds: 0 },
    executionMode: 'workflow',
    agentName: 'rex',
    createdAt: '2026-07-09T00:00:00Z',
    updatedAt: '2026-07-09T00:00:00Z',
    ...overrides,
  };
}

function makeExecutionPage(items = [makeExecution()]) {
  return {
    data: {
      items,
      total: items.length,
      offset: 0,
      limit: 20,
    },
  };
}

describe('useTasks shared resources', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetTaskResourcesForTesting();
    dashboardMock.mockResolvedValue({
      data: {
        running: 0,
        queued: 0,
        completed_week: 0,
        completed_unviewed: 0,
        failed_week: 0,
        scheduled_active: 0,
        queue_paused: false,
      },
    });
    getSystemNoticeMock.mockResolvedValue({ data: null });
    listExecutionsMock.mockResolvedValue(makeExecutionPage());
    listSchedulerExecutionsMock.mockResolvedValue(makeExecutionPage());
    listSchedulersMock.mockResolvedValue({
      data: { items: [], total: 0, offset: 0, limit: 20 },
    });
    queueStatusMock.mockResolvedValue({
      data: {
        paused: false,
        max_concurrent: 2,
        running: 0,
        queued: 0,
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shares task execution list requests across concurrent hook instances with the same filters', async () => {
    let resolveList: (value: ReturnType<typeof makeExecutionPage>) => void = () => {};
    listExecutionsMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const filters = { status: 'running' as const, offset: 0, limit: 20 };
    const first = renderHook(() => useTaskExecutions(filters));
    const second = renderHook(() => useTaskExecutions(filters));

    expect(listExecutionsMock).toHaveBeenCalledTimes(1);
    expect(listExecutionsMock).toHaveBeenCalledWith(filters);

    await act(async () => {
      resolveList(makeExecutionPage([makeExecution()]));
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.tasks).toHaveLength(1);
    expect(second.result.current.tasks).toHaveLength(1);
  });

  it('deduplicates active execution polling across hook instances', async () => {
    vi.useFakeTimers();
    listExecutionsMock.mockResolvedValue(makeExecutionPage([makeExecution({ status: 'running' })]));

    renderHook(() => useTaskExecutions({ offset: 0, limit: 20 }, { pollInterval: 5000 }));
    renderHook(() => useTaskExecutions({ offset: 0, limit: 20 }, { pollInterval: 5000 }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(listExecutionsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(listExecutionsMock).toHaveBeenCalledTimes(2);
  });

  it('shares dashboard and queue status requests across concurrent hook instances', async () => {
    const firstDashboard = renderHook(() => useTaskDashboard());
    const secondDashboard = renderHook(() => useTaskDashboard());
    const firstQueue = renderHook(() => useQueueStatus());
    const secondQueue = renderHook(() => useQueueStatus());

    await waitFor(() => {
      expect(firstDashboard.result.current.loading).toBe(false);
      expect(secondDashboard.result.current.loading).toBe(false);
      expect(firstQueue.result.current.loading).toBe(false);
      expect(secondQueue.result.current.loading).toBe(false);
    });

    expect(dashboardMock).toHaveBeenCalledTimes(1);
    expect(queueStatusMock).toHaveBeenCalledTimes(1);
  });

  it('does not fetch scheduler execution records when scheduler id is absent', async () => {
    const { result } = renderHook(() => useTaskExecutionsByScheduler(undefined));

    expect(result.current.loading).toBe(false);
    expect(result.current.records).toEqual([]);
    expect(listSchedulerExecutionsMock).not.toHaveBeenCalled();
  });

  it('bounds parameterized execution resources for long-lived sessions', () => {
    for (let offset = 0; offset < 100; offset += 1) {
      const hook = renderHook(() => useTaskExecutions({ offset, limit: 20 }));
      hook.unmount();
    }

    expect(__getTaskResourceCacheSizesForTesting().executions).toBe(80);
  });
});
