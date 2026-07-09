import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetToolsResourceForTesting, useTools } from './useTools';

const { listMock, refreshMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  refreshMock: vi.fn(),
}));

vi.mock('@/api/tool', () => ({
  toolAPI: {
    list: listMock,
    refresh: refreshMock,
  },
}));

describe('useTools', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetToolsResourceForTesting();
  });

  it('renders the tool list without automatically refreshing plugins', async () => {
    listMock.mockResolvedValue({
      data: [
        {
          name: 'tool-alpha',
          description: 'alpha tool',
          category: 'custom',
          source: 'custom',
          enabled: true,
        },
      ],
    });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(result.current.tools[0].name).toBe('tool-alpha');
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it('fetches tools when the window regains focus without refreshing plugins', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
          { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
        ],
      });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(refreshMock).not.toHaveBeenCalled();

    const futureNow = Date.now() + 6000;
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(futureNow);
    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });
    nowSpy.mockRestore();

    expect(refreshMock).not.toHaveBeenCalled();
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('refreshes plugins only when refetch is called explicitly', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
      .mockResolvedValueOnce({
        data: [
          { name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true },
          { name: 'tool-beta', description: 'beta tool', category: 'custom', source: 'custom', enabled: true },
        ],
      });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('shares the initial tool list request across concurrent hook instances', async () => {
    let resolveList: (value: { data: any[] }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const first = renderHook(() => useTools());
    const second = renderHook(() => useTools());

    expect(listMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveList({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.tools).toHaveLength(1);
    expect(second.result.current.tools).toHaveLength(1);
  });
});
