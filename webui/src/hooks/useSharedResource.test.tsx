import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createSharedResource, useSharedResource } from './useSharedResource';

describe('useSharedResource', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('deduplicates concurrent initial fetches for the same resource', async () => {
    let resolveFetch: (value: string[]) => void = () => {};
    const fetcher = vi.fn(() => new Promise<string[]>((resolve) => {
      resolveFetch = resolve;
    }));
    const resource = createSharedResource<string[]>({
      initialData: [],
      fetcher,
    });

    const first = renderHook(() => useSharedResource(resource));
    const second = renderHook(() => useSharedResource(resource));

    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFetch(['ready']);
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.data).toEqual(['ready']);
    expect(second.result.current.data).toEqual(['ready']);
  });

  it('reuses fresh data until the stale window expires', async () => {
    let now = 1000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);

    const fetcher = vi.fn()
      .mockResolvedValueOnce('first')
      .mockResolvedValueOnce('second');
    const resource = createSharedResource<string>({
      initialData: '',
      staleTimeMs: 5000,
      fetcher,
    });

    const { result } = renderHook(() => useSharedResource(resource));

    await waitFor(() => {
      expect(result.current.data).toBe('first');
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      await resource.fetch({ silent: true });
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    now += 6000;
    await act(async () => {
      await resource.fetch({ silent: true });
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe('second');
  });

  it('invalidates fresh data without allowing an older in-flight request to overwrite it', async () => {
    const resolvers: Array<(value: string) => void> = [];
    const fetcher = vi.fn(() => new Promise<string>((resolve) => {
      resolvers.push(resolve);
    }));
    const resource = createSharedResource<string>({
      initialData: 'initial',
      staleTimeMs: 60_000,
      fetcher,
    });

    const { result } = renderHook(() => useSharedResource(resource));
    expect(fetcher).toHaveBeenCalledTimes(1);

    resource.invalidate();
    const revalidation = resource.fetch({ force: true, silent: true });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvers[0]('stale');
      await Promise.resolve();
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe('initial');

    await act(async () => {
      resolvers[1]('fresh');
      await revalidation;
    });

    expect(result.current.data).toBe('fresh');
  });

  it('refetches invalidated data even inside the stale window', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce('first')
      .mockResolvedValueOnce('second');
    const resource = createSharedResource<string>({
      initialData: '',
      staleTimeMs: 60_000,
      fetcher,
    });

    await resource.fetch();
    resource.invalidate();
    await resource.fetch();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(resource.getSnapshot().data).toBe('second');
  });
});
