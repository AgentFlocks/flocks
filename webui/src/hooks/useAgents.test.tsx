import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetAgentsResourceForTesting, useAgents } from './useAgents';

const { listMock, refreshMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  refreshMock: vi.fn(),
}));

vi.mock('@/api/agent', () => ({
  agentAPI: {
    list: listMock,
    refresh: refreshMock,
  },
}));

describe('useAgents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetAgentsResourceForTesting();
  });

  it('returns an empty array when the API payload is not an array', async () => {
    listMock.mockResolvedValue({
      data: { items: [] },
    });

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.agents).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it('refreshes agents when the window regains focus', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'rex', mode: 'primary' }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'rex', mode: 'primary' },
          { name: 'pr-creator', mode: 'subagent' },
        ],
      });
    refreshMock.mockResolvedValue({ data: { count: 2 } });

    const { result } = renderHook(() => useAgents());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.agents).toHaveLength(1);

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.agents).toHaveLength(2);
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('shares the initial agent list request across concurrent hook instances', async () => {
    let resolveList: (value: { data: any[] }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const first = renderHook(() => useAgents());
    const second = renderHook(() => useAgents());

    expect(listMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveList({
        data: [{ name: 'rex', mode: 'primary' }],
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.agents).toHaveLength(1);
    expect(second.result.current.agents).toHaveLength(1);
  });
});
