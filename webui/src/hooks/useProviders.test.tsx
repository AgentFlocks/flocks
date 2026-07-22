import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetProvidersResourceForTesting, useProviders } from './useProviders';

const { listMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
}));

vi.mock('@/api/provider', async () => {
  const actual = await vi.importActual<typeof import('@/api/provider')>('@/api/provider');
  return {
    ...actual,
    providerAPI: {
      ...actual.providerAPI,
      list: listMock,
    },
  };
});

function makeProvider(id: string, models: Record<string, unknown> = {}) {
  return {
    id,
    name: id,
    source: 'builtin',
    env: [],
    key: null,
    options: {},
    models,
  };
}

describe('useProviders', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetProvidersResourceForTesting();
  });

  it('enriches providers with connected state and model counts', async () => {
    listMock.mockResolvedValue({
      data: {
        all: [
          makeProvider('openai', { 'gpt-4o': {} }),
          makeProvider('deepseek'),
        ],
        connected: ['openai'],
      },
    });

    const { result } = renderHook(() => useProviders());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.connectedIds).toEqual(['openai']);
    expect(result.current.providers).toMatchObject([
      { id: 'openai', configured: true, modelCount: 1, category: 'connected' },
      { id: 'deepseek', configured: false, modelCount: 0, category: 'chinese' },
    ]);
  });

  it('shares provider list requests across concurrent hook instances', async () => {
    let resolveList: (value: { data: any }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const first = renderHook(() => useProviders());
    const second = renderHook(() => useProviders());

    expect(listMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveList({
        data: {
          all: [makeProvider('openai')],
          connected: ['openai'],
        },
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.providers).toHaveLength(1);
    expect(second.result.current.providers).toHaveLength(1);
  });

  it('forces a new request when refetch is called', async () => {
    listMock
      .mockResolvedValueOnce({
        data: {
          all: [makeProvider('openai')],
          connected: ['openai'],
        },
      })
      .mockResolvedValueOnce({
        data: {
          all: [makeProvider('openai'), makeProvider('deepseek')],
          connected: ['openai', 'deepseek'],
        },
      });

    const { result } = renderHook(() => useProviders());

    await waitFor(() => {
      expect(result.current.providers).toHaveLength(1);
    });

    await act(async () => {
      await result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.providers).toHaveLength(2);
    });
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});
