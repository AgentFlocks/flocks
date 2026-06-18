import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useUserDefinedPages } from './useUserDefinedPages';
import { setupSSEMock } from '@/test/mocks/sse';

const { listMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
}));

vi.mock('@/api/userDefinedPages', () => ({
  userDefinedPagesAPI: {
    list: listMock,
  },
}));

describe('useUserDefinedPages', () => {
  const sse = setupSSEMock();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads enabled user defined pages for navigation', async () => {
    listMock.mockResolvedValueOnce({
      data: [
        {
          id: 'dash-1',
          title: '仪表盘',
          route: '/user-defined-pages/dash-1',
          icon: 'LayoutDashboard',
          order: 10,
          enabled: true,
          placement: 'home.after',
          buildHash: 'abc',
          buildStatus: 'ready',
        },
      ],
    });

    const { result } = renderHook(() => useUserDefinedPages());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.pages).toHaveLength(1);
    expect(result.current.pages[0].title).toBe('仪表盘');
    expect(listMock).toHaveBeenCalledWith(true);
  });

  it('refetches when user_defined_pages.nav_changed SSE event arrives', async () => {
    listMock
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({
        data: [
          {
            id: 'dash-2',
            title: '新页面',
            route: '/user-defined-pages/dash-2',
            icon: 'LayoutDashboard',
            order: 20,
            enabled: true,
            placement: 'home.after',
            buildHash: 'def',
            buildStatus: 'ready',
          },
        ],
      });

    const { result } = renderHook(() => useUserDefinedPages());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    sse.open();
    sse.send({
      type: 'user_defined_pages.nav_changed',
      properties: { id: 'dash-2' },
    });

    await waitFor(() => {
      expect(result.current.pages).toHaveLength(1);
    });
    expect(listMock).toHaveBeenCalledTimes(2);
  });
});
