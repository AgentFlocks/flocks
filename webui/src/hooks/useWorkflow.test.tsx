import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useWorkflows } from './useWorkflow';

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

function makeWorkflow() {
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
  };
}

describe('useWorkflows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(result.current.workflows).toEqual([]);
    });

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
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => {
      expect(result.current.workflows).toHaveLength(2);
    });
  });
});
