import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTools } from './useTools';

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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('useTools', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the tool list before the background refresh completes', async () => {
    const refreshDeferred = deferred<{ data: { status: string } }>();

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
    refreshMock.mockReturnValue(refreshDeferred.promise);

    const { result } = renderHook(() => useTools());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tools).toHaveLength(1);
    expect(result.current.tools[0].name).toBe('tool-alpha');
    expect(listMock).toHaveBeenCalledTimes(1);
    expect(refreshMock).toHaveBeenCalledTimes(1);

    refreshDeferred.resolve({ data: { status: 'success' } });

    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(2);
    });
  });

  it('refreshes tools when the window regains focus after the throttle window', async () => {
    const initialNow = Date.now();

    listMock
      .mockResolvedValueOnce({
        data: [{ name: 'tool-alpha', description: 'alpha tool', category: 'custom', source: 'custom', enabled: true }],
      })
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

    expect(result.current.tools).toHaveLength(1);
    expect(refreshMock).toHaveBeenCalledTimes(1);

    const dateNowSpy = vi.spyOn(Date, 'now').mockReturnValue(initialNow + 6000);
    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.tools).toHaveLength(2);
    });

    expect(refreshMock).toHaveBeenCalledTimes(2);
    expect(listMock).toHaveBeenCalledTimes(3);
    dateNowSpy.mockRestore();
  });
});
