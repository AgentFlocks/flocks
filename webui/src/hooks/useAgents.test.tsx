import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAgents } from './useAgents';

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
});
