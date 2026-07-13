import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Workflow } from '@/api/workflow';
import {
  __getWorkflowResourceCacheSizeForTesting,
  __resetWorkflowResourcesForTesting,
  useWorkflows,
} from './useWorkflow';

const { listMock, getMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  getMock: vi.fn(),
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    list: listMock,
    get: getMock,
  },
}));

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'wf-1',
    name: 'Workflow One',
    category: 'default',
    workflowJson: {
      start: 'node-1',
      nodes: [{ id: 'node-1', type: 'python' }],
      edges: [],
    },
    status: 'active' as const,
    source: 'global' as const,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    stats: {
      callCount: 1,
      successCount: 1,
      errorCount: 0,
      totalRuntime: 1,
      avgRuntime: 1,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    ...overrides,
  };
}

describe('useWorkflows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetWorkflowResourcesForTesting();
  });

  it('clears workflows when a silent refetch fails', async () => {
    listMock.mockResolvedValueOnce({
      data: [makeWorkflow()],
    });
    listMock.mockRejectedValueOnce(new Error('Session expired'));

    const { result } = renderHook(() => useWorkflows());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.workflows).toHaveLength(1);

    const futureNow = Date.now() + 6000;
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(futureNow);
    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.workflows).toEqual([]);
    });
    nowSpy.mockRestore();

    expect(result.current.error).toBe('Session expired');
  });

  it('refetches workflows when the page becomes visible', async () => {
    listMock
      .mockResolvedValueOnce({
        data: [makeWorkflow()],
      })
      .mockResolvedValueOnce({
        data: [makeWorkflow(), makeWorkflow({ id: 'wf-2', name: 'Workflow Two' })],
      });

    const { result } = renderHook(() => useWorkflows());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.workflows).toHaveLength(1);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
    const futureNow = Date.now() + 6000;
    const nowSpy = vi.spyOn(Date, 'now').mockReturnValue(futureNow);
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => {
      expect(result.current.workflows).toHaveLength(2);
    });
    nowSpy.mockRestore();
  });

  it('shares workflow list requests across concurrent hook instances with the same filters', async () => {
    let resolveList: (value: { data: any[] }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolveList = resolve;
    }));

    const first = renderHook(() => useWorkflows('default', 'active'));
    const second = renderHook(() => useWorkflows('default', 'active'));

    expect(listMock).toHaveBeenCalledTimes(1);
    expect(listMock).toHaveBeenCalledWith({ category: 'default', status: 'active' });

    await act(async () => {
      resolveList({
        data: [makeWorkflow()],
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.workflows).toHaveLength(1);
    expect(second.result.current.workflows).toHaveLength(1);
  });

  it('bounds parameterized workflow resources for long-lived sessions', () => {
    listMock.mockResolvedValue({ data: [] });
    for (let index = 0; index < 100; index += 1) {
      const hook = renderHook(() => useWorkflows(`category-${index}`, 'active'));
      hook.unmount();
    }

    expect(__getWorkflowResourceCacheSizeForTesting()).toBe(80);
  });
});
