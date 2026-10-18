import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetBackendStatusResourceForTesting, useBackendStatus } from './useBackendStatus';

const { apiGetMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  apiClient: {
    get: apiGetMock,
  },
}));

describe('useBackendStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetBackendStatusResourceForTesting();
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetBackendStatusResourceForTesting();
  });

  it('shares the initial health request across concurrent hook instances', async () => {
    let resolveHealth: (value: { status: number }) => void = () => {};
    apiGetMock.mockReturnValue(new Promise((resolve) => {
      resolveHealth = resolve;
    }));

    const first = renderHook(() => useBackendStatus());
    const second = renderHook(() => useBackendStatus());

    expect(apiGetMock).toHaveBeenCalledTimes(1);
    expect(apiGetMock).toHaveBeenCalledWith('/api/health', { timeout: 5000 });

    await act(async () => {
      resolveHealth({ status: 200 });
    });

    await waitFor(() => {
      expect(first.result.current.status).toBe('connected');
      expect(second.result.current.status).toBe('connected');
    });
  });

  it('keeps one health polling loop for multiple hook instances', async () => {
    vi.useFakeTimers();
    apiGetMock.mockResolvedValue({ status: 200 });

    const first = renderHook(() => useBackendStatus());
    const second = renderHook(() => useBackendStatus());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(first.result.current.status).toBe('connected');
    expect(second.result.current.status).toBe('connected');
    expect(apiGetMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_600_000);
    });

    expect(apiGetMock).toHaveBeenCalledTimes(2);

    first.unmount();
    second.unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_600_000);
    });

    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });

  it('reports checked only after the first health answer arrives', async () => {
    let resolveHealth: (value: { status: number }) => void = () => {};
    apiGetMock.mockReturnValue(new Promise((resolve) => {
      resolveHealth = resolve;
    }));

    const { result } = renderHook(() => useBackendStatus());

    expect(result.current.checked).toBe(false);
    expect(result.current.status).toBe('connecting');

    await act(async () => {
      resolveHealth({ status: 200 });
    });

    await waitFor(() => {
      expect(result.current.checked).toBe(true);
      expect(result.current.status).toBe('connected');
    });
  });

  it('re-checks every 5 seconds after a failed check and stops once the backend is back', async () => {
    vi.useFakeTimers();
    apiGetMock
      .mockRejectedValueOnce({ response: { status: 502 } })
      .mockRejectedValueOnce({ code: 'ECONNABORTED' })
      .mockResolvedValue({ status: 200 });

    const { result } = renderHook(() => useBackendStatus());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.status).toBe('disconnected');
    expect(apiGetMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(apiGetMock).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe('disconnected');
    expect(result.current.message).toBe('连接超时');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(apiGetMock).toHaveBeenCalledTimes(3);
    expect(result.current.status).toBe('connected');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(apiGetMock).toHaveBeenCalledTimes(3);
  });

  it('stops the recovery checks when the last hook instance unmounts', async () => {
    vi.useFakeTimers();
    apiGetMock.mockRejectedValue({ response: { status: 502 } });

    const { result, unmount } = renderHook(() => useBackendStatus());

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.status).toBe('disconnected');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(apiGetMock).toHaveBeenCalledTimes(2);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });

  it('keeps checkHealth compatible with the previous boolean return', async () => {
    apiGetMock
      .mockResolvedValueOnce({ status: 200 })
      .mockRejectedValueOnce({ response: { status: 503 } });

    const { result } = renderHook(() => useBackendStatus());

    await waitFor(() => {
      expect(result.current.status).toBe('connected');
    });

    let ok = true;
    await act(async () => {
      ok = await result.current.checkHealth();
    });

    expect(ok).toBe(false);
    expect(result.current.status).toBe('connecting');
    expect(result.current.message).toBe('后端服务暂时不可用');
  });
});
