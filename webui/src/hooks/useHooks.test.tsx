import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetHookStatusResourceForTesting, useHooks } from './useHooks';

const { getStatusMock } = vi.hoisted(() => ({
  getStatusMock: vi.fn(),
}));

vi.mock('@/api/hooks', () => ({
  hooksApi: {
    getStatus: getStatusMock,
  },
}));

function makeHookStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    session_memory: {
      enabled: true,
      message_count: 3,
      use_llm_slug: false,
      slug_timeout: 10,
    },
    stats: {
      total_event_keys: 2,
      total_handlers: 4,
      event_keys: {},
    },
    ...overrides,
  };
}

describe('useHooks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetHookStatusResourceForTesting();
  });

  it('shares the initial hook status request across concurrent hook instances', async () => {
    let resolveStatus: (value: ReturnType<typeof makeHookStatus>) => void = () => {};
    getStatusMock.mockReturnValue(new Promise((resolve) => {
      resolveStatus = resolve;
    }));

    const first = renderHook(() => useHooks());
    const second = renderHook(() => useHooks());

    expect(getStatusMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveStatus(makeHookStatus());
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.status?.enabled).toBe(true);
    expect(second.result.current.status?.stats.total_handlers).toBe(4);
  });

  it('forces a new request when refetch is called', async () => {
    getStatusMock
      .mockResolvedValueOnce(makeHookStatus({ enabled: true }))
      .mockResolvedValueOnce(makeHookStatus({ enabled: false }));

    const { result } = renderHook(() => useHooks());

    await waitFor(() => {
      expect(result.current.status?.enabled).toBe(true);
    });

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.status?.enabled).toBe(false);
    expect(getStatusMock).toHaveBeenCalledTimes(2);
  });
});
