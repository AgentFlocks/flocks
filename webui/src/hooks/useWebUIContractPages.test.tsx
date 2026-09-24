import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __resetWebUIContractPagesResourceForTesting, useWebUIContractPages } from './useWebUIContractPages';
import { setupSSEMock } from '@/test/mocks/sse';
import { SCENE_SUITES_CHANGED_EVENT } from '@/utils/sceneSuites';

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
    expect(listWorkspacesMock).toHaveBeenCalledWith(false);
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

  it('keeps disabled workspaces and refreshes after a local suite change', async () => {
    listMock.mockResolvedValue({ data: [] });
    listWorkspacesMock
      .mockResolvedValueOnce({ data: [{ id: 'scene_ui', enabled: false, pages: [] }] })
      .mockResolvedValueOnce({ data: [{ id: 'scene_ui', enabled: true, pages: [] }] });
    const { result } = renderHook(() => useWebUIContractPages());
    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));
    expect(result.current.workspaces[0].enabled).toBe(false);

    act(() => window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT)));

    await waitFor(() => expect(result.current.workspaces[0].enabled).toBe(true));
    expect(listWorkspacesMock).toHaveBeenCalledWith(false);
  });

  it('keeps the last navigation data when a refresh fails', async () => {
    listMock.mockResolvedValueOnce({ data: [{ id: 'page' }] }).mockRejectedValueOnce(new Error('offline'));
    listWorkspacesMock.mockResolvedValue({ data: [{ id: 'scene_ui', enabled: true, pages: [] }] });
    const { result } = renderHook(() => useWebUIContractPages());
    await waitFor(() => expect(result.current.workspaces).toHaveLength(1));
    act(() => window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT)));
    await waitFor(() => expect(result.current.error).toBe('offline'));
    expect(result.current.pages).toHaveLength(1);
    expect(result.current.workspaces).toHaveLength(1);
  });

  it('shares a local mutation refresh across mounted consumers', async () => {
    listMock.mockResolvedValue({ data: [] });
    const first = renderHook(() => useWebUIContractPages());
    const second = renderHook(() => useWebUIContractPages());
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    await act(async () => window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT)));
    expect(listMock).toHaveBeenCalledTimes(2);
    first.unmount();
    await act(async () => window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT)));
    expect(listMock).toHaveBeenCalledTimes(3);
    expect(second.result.current.loading).toBe(false);
  });

  it('discards an old in-flight response when a suite changes', async () => {
    let resolveOld: (value: { data: any[] }) => void = () => {};
    listMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ data: [{ id: 'current-page' }] });
    listWorkspacesMock.mockResolvedValue({ data: [] });
    const { result } = renderHook(() => useWebUIContractPages());
    act(() => window.dispatchEvent(new Event(SCENE_SUITES_CHANGED_EVENT)));
    await act(async () => resolveOld({ data: [{ id: 'old-page' }] }));
    await waitFor(() => expect(result.current.pages[0]?.id).toBe('current-page'));
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('invalidates an old in-flight navigation response when a remote SSE arrives', async () => {
    let resolveOld: (value: { data: any[] }) => void = () => {};
    listMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ data: [{ id: 'current-page' }] });
    const { result } = renderHook(() => useWebUIContractPages());
    act(() => sse.send({ type: 'contracts.webui.pages.nav_changed', properties: {} }));
    await act(async () => resolveOld({ data: [{ id: 'old-page' }] }));
    await waitFor(() => expect(result.current.pages[0]?.id).toBe('current-page'));
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it('handles each remote event once across consumers while retaining later changes', async () => {
    listMock.mockResolvedValue({ data: [] });
    const first = renderHook(() => useWebUIContractPages());
    const second = renderHook(() => useWebUIContractPages());
    await waitFor(() => expect(first.result.current.loading).toBe(false));

    await act(async () => sse.send({ type: 'contracts.webui.pages.nav_changed', properties: {} }));
    expect(listMock).toHaveBeenCalledTimes(2);
    expect(listWorkspacesMock).toHaveBeenCalledTimes(2);
    first.unmount();
    listMock.mockResolvedValue({ data: [{ id: 'next-page' }] });
    await act(async () => sse.send({ type: 'contracts.webui.pages.nav_changed', properties: {} }));
    expect(listMock).toHaveBeenCalledTimes(3);
    expect(second.result.current.pages[0].id).toBe('next-page');
  });
});
