import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetStatsResourceForTesting, useStats } from './useStats';

const { getSystemStatsMock } = vi.hoisted(() => ({
  getSystemStatsMock: vi.fn(),
}));

vi.mock('@/api/stats', () => ({
  statsApi: {
    getSystemStats: getSystemStatsMock,
  },
}));

function makeStats(overrides: Record<string, unknown> = {}) {
  return {
    tasks: { week: 1, scheduledActive: 1 },
    agents: { total: 2 },
    workflows: { total: 3 },
    skills: { total: 4 },
    tools: { total: 5 },
    models: { total: 6 },
    system: { status: 'healthy', message: 'ok' },
    ...overrides,
  };
}

describe('useStats', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetStatsResourceForTesting();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetStatsResourceForTesting();
  });

  it('shares the initial stats request across concurrent hook instances', async () => {
    let resolveStats: (value: ReturnType<typeof makeStats>) => void = () => {};
    getSystemStatsMock.mockReturnValue(new Promise((resolve) => {
      resolveStats = resolve;
    }));

    const first = renderHook(() => useStats());
    const second = renderHook(() => useStats());

    expect(getSystemStatsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveStats(makeStats());
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.stats?.agents.total).toBe(2);
    expect(second.result.current.stats?.models.total).toBe(6);
  });

  it('keeps one polling loop for multiple hook instances', async () => {
    vi.useFakeTimers();
    getSystemStatsMock.mockResolvedValue(makeStats());

    const first = renderHook(() => useStats());
    const second = renderHook(() => useStats());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(first.result.current.loading).toBe(false);
    expect(second.result.current.loading).toBe(false);
    expect(getSystemStatsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(getSystemStatsMock).toHaveBeenCalledTimes(2);

    first.unmount();
    second.unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(getSystemStatsMock).toHaveBeenCalledTimes(2);
  });

  it('maps unhealthy system stats to the existing Error return shape', async () => {
    getSystemStatsMock.mockResolvedValue(makeStats({
      system: { status: 'error', message: 'backend down' },
    }));

    const { result } = renderHook(() => useStats());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe('backend down');
  });

  it('maps degraded system stats to the existing Error return shape', async () => {
    getSystemStatsMock.mockResolvedValue(makeStats({
      system: { status: 'warning', message: 'partial' },
    }));

    const { result } = renderHook(() => useStats());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe('partial');
  });
});
