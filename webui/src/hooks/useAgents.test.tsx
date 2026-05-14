import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAgents } from './useAgents';

const { listMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
}));

vi.mock('@/api/agent', () => ({
  agentAPI: {
    list: listMock,
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
});
