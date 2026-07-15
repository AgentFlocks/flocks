import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetWebUIContractPagesResourceForTesting, useWebUIContractPages } from './useWebUIContractPages';
import { setupSSEMock } from '@/test/mocks/sse';

const { listMock, listWorkspacesMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  listWorkspacesMock: vi.fn(),
}));

vi.mock('@/api/webuiContractPages', () => ({
  webuiContractPagesAPI: {
    list: listMock,
    listWorkspaces: listWorkspacesMock,
  },
}));

describe('useWebUIContractPages', () => {
  const sse = setupSSEMock();

  beforeEach(() => {
    vi.clearAllMocks();
    __resetWebUIContractPagesResourceForTesting();
    listWorkspacesMock.mockResolvedValue({ data: [] });
  });

  it('loads enabled WebUI contract pages for navigation', async () => {
    listMock.mockResolvedValueOnce({
      data: [
        {
          id: 'dash-1',
          title: '仪表盘',
          route: '/contracts/webui/dash-1',
          icon: 'LayoutDashboard',
          order: 10,
          enabled: true,
          placement: 'home.after',
          buildHash: 'abc',
          buildStatus: 'ready',
        },
      ],
    });

    const { result } = renderHook(() => useWebUIContractPages());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.pages).toHaveLength(1);
    expect(result.current.pages[0].title).toBe('仪表盘');
    expect(result.current.workspaces).toHaveLength(0);
    expect(listMock).toHaveBeenCalledWith(true);
    expect(listWorkspacesMock).toHaveBeenCalledWith(true);
  });

  it('shares the initial navigation request across concurrent hook instances', async () => {
    let resolvePages: (value: { data: any[] }) => void = () => {};
    let resolveWorkspaces: (value: { data: any[] }) => void = () => {};
    listMock.mockReturnValue(new Promise((resolve) => {
      resolvePages = resolve;
    }));
    listWorkspacesMock.mockReturnValue(new Promise((resolve) => {
      resolveWorkspaces = resolve;
    }));

    const first = renderHook(() => useWebUIContractPages());
    const second = renderHook(() => useWebUIContractPages());

    expect(listMock).toHaveBeenCalledTimes(1);
    expect(listWorkspacesMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvePages({ data: [{ id: 'dash-1', title: '仪表盘' }] });
      resolveWorkspaces({ data: [{ id: 'workspace-1', title: '工作区', pages: [] }] });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
    });

    expect(first.result.current.pages).toHaveLength(1);
    expect(second.result.current.pages).toHaveLength(1);
    expect(first.result.current.workspaces).toHaveLength(1);
    expect(second.result.current.workspaces).toHaveLength(1);
  });

  it('refetches when contracts.webui.pages.nav_changed SSE event arrives', async () => {
    listMock
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({
        data: [
          {
            id: 'dash-2',
            title: '新页面',
            route: '/contracts/webui/dash-2',
            icon: 'LayoutDashboard',
            order: 20,
            enabled: true,
            placement: 'home.after',
            buildHash: 'def',
            buildStatus: 'ready',
          },
        ],
      });
    listWorkspacesMock.mockResolvedValue({ data: [] });

    const { result } = renderHook(() => useWebUIContractPages());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    sse.open();
    sse.send({
      type: 'contracts.webui.pages.nav_changed',
      properties: { id: 'dash-2' },
    });

    await waitFor(() => {
      expect(result.current.pages).toHaveLength(1);
    });
    expect(listMock).toHaveBeenCalledTimes(2);
    expect(listWorkspacesMock).toHaveBeenCalledTimes(2);
  });
});
