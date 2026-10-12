import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import WebUIContractWorkspaceHost from './index';
import { setupSSEMock } from '@/test/mocks/sse';
import { ThemeContext } from '@/contexts/ThemeContext';
import { PaneActiveContext } from '@/components/layout/PaneActiveContext';

const { useWebUIContractPagesMock } = vi.hoisted(() => ({
  useWebUIContractPagesMock: vi.fn(),
}));

vi.mock('@/hooks/useWebUIContractPages', () => ({
  useWebUIContractPages: useWebUIContractPagesMock,
}));

vi.mock('@/pages/WebUIContractPageHost/PageRuntimeHost', () => ({
  default: ({ pageId, initialBuildHash }: { pageId?: string; initialBuildHash?: string }) => (
    <div data-build-hash={initialBuildHash}>page:{pageId}</div>
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

function renderHostAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('WebUIContractWorkspaceHost', () => {
  it('resets page-local task state when the sidebar links to the same page again', async () => {
    const route = '/contracts/webui/workspaces/scene_workspace/ops-overview';
    render(<MemoryRouter initialEntries={[route]}>
      <Link to={route}>Home</Link>
      <Routes><Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} /></Routes>
    </MemoryRouter>);
    const previous = await screen.findByText('page:ops-overview');
    fireEvent.click(screen.getByRole('link', {name:'Home'}));
    await waitFor(() => expect(screen.getByText('page:ops-overview')).not.toBe(previous));
  });

  setupSSEMock();

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useWebUIContractPagesMock.mockReturnValue({
      pages: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
      workspaces: [
        {
          id: 'scene_workspace',
          title: '场景工作区',
          route: '/contracts/webui/workspaces/scene_workspace',
          icon: 'ShieldCheck',
          order: 10,
          enabled: true,
          placement: 'sceneWorkspace',
          defaultPageId: 'ops-overview',
          sections: [
            {
              id: 'posture',
              label: '态势',
              pageIds: ['risk-dashboard'],
              defaultPageId: 'risk-dashboard',
              contentPadding: 'none',
              themeOverride: 'dark',
            },
            {
              id: 'operations',
              label: '调查列表',
              pageIds: ['ops-overview', 'investigation-list'],
              defaultPageId: 'ops-overview',
              contentPadding: 'comfortable',
            },
          ],
          pages: [
            {
              id: 'risk-dashboard',
              title: '态势看板',
              route: '/contracts/webui/risk-dashboard',
              icon: 'ShieldCheck',
              order: 30,
              enabled: true,
              placement: 'home.after',
              buildHash: 'posture',
              buildStatus: 'ready',
              workspaceId: 'scene_workspace',
              workspaceTitle: '场景工作区',
              workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
            },
            {
              id: 'ops-overview',
              title: '运营总览',
              route: '/contracts/webui/ops-overview',
              icon: 'Shield',
              order: 10,
              enabled: true,
              placement: 'home.after',
              buildHash: 'abc',
              buildStatus: 'ready',
              workspaceId: 'scene_workspace',
              workspaceTitle: '场景工作区',
              workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
            },
            {
              id: 'investigation-list',
              title: '调查列表',
              route: '/contracts/webui/investigation-list',
              icon: 'AlertTriangle',
              order: 20,
              enabled: true,
              placement: 'home.after',
              buildHash: '',
              buildStatus: 'failed',
              workspaceId: 'scene_workspace',
              workspaceTitle: '场景工作区',
              workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
            },
          ],
        },
      ],
    });
  });

  it('redirects the workspace root to its default page', async () => {
    renderHostAt('/contracts/webui/workspaces/scene_workspace');

    await waitFor(() => {
      expect(screen.getByText('page:ops-overview')).toBeInTheDocument();
    });
    expect(screen.queryByText('workspace.selectPage')).not.toBeInTheDocument();
  });

  it('falls back to the first ready page when the default page id is unknown', async () => {
    useWebUIContractPagesMock.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [
        {
          id: 'scene_workspace',
          title: '场景工作区',
          route: '/contracts/webui/workspaces/scene_workspace',
          icon: 'ShieldCheck',
          order: 10,
          enabled: true,
          placement: 'sceneWorkspace',
          defaultPageId: 'missing-page',
          sections: [],
          pages: [
            {
              id: 'investigation-list',
              title: '调查列表',
              route: '/contracts/webui/investigation-list',
              icon: 'AlertTriangle',
              order: 5,
              enabled: true,
              placement: 'home.after',
              buildHash: '',
              buildStatus: 'failed',
              workspaceId: 'scene_workspace',
              workspaceTitle: '场景工作区',
              workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
            },
            {
              id: 'ops-overview',
              title: '运营总览',
              route: '/contracts/webui/ops-overview',
              icon: 'Shield',
              order: 10,
              enabled: true,
              placement: 'home.after',
              buildHash: 'abc',
              buildStatus: 'ready',
              workspaceId: 'scene_workspace',
              workspaceTitle: '场景工作区',
              workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
            },
          ],
        },
      ],
    });
    renderHostAt('/contracts/webui/workspaces/scene_workspace');

    await waitFor(() => {
      expect(screen.getByText('page:ops-overview')).toBeInTheDocument();
    });
  });

  it('renders a selected operation page without a fixed workspace sidebar', async () => {
    render(
      <MemoryRouter initialEntries={['/contracts/webui/workspaces/scene_workspace/investigation-list']}>
        <Routes>
          <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('page:investigation-list')).toBeInTheDocument();
    });
    expect(screen.getByText('page:investigation-list').parentElement).toHaveClass('p-6');
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
  });

  it('temporarily uses dark theme for the posture dashboard when the user preference is light', async () => {
    const setTemporaryThemeOverride = vi.fn();
    const { unmount } = render(
      <ThemeContext.Provider
        value={{
          theme: 'light',
          mode: 'light',
          effectiveTheme: 'light',
          toggleTheme: vi.fn(),
          setTheme: vi.fn(),
          setTemporaryThemeOverride,
        }}
      >
        <MemoryRouter initialEntries={['/contracts/webui/workspaces/scene_workspace/risk-dashboard']}>
          <Routes>
            <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
          </Routes>
        </MemoryRouter>
      </ThemeContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByText('page:risk-dashboard')).toBeInTheDocument();
    });
    expect(screen.getByText('page:risk-dashboard')).toHaveAttribute('data-build-hash', 'posture');
    expect(screen.getByText('page:risk-dashboard').parentElement).not.toHaveClass('p-6');
    await waitFor(() => {
      expect(setTemporaryThemeOverride).toHaveBeenCalledWith('dark');
    });

    unmount();

    expect(setTemporaryThemeOverride).toHaveBeenLastCalledWith(null);
  });
  it('releases the dark override while its keep-alive pane is hidden and takes it back when shown', async () => {
    const setTemporaryThemeOverride = vi.fn();
    const themeValue = {
      theme: 'light' as const,
      mode: 'light' as const,
      effectiveTheme: 'light' as const,
      toggleTheme: vi.fn(),
      setTheme: vi.fn(),
      setTemporaryThemeOverride,
    };
    const tree = (paneActive: boolean) => (
      <ThemeContext.Provider value={themeValue}>
        <PaneActiveContext.Provider value={paneActive}>
          <MemoryRouter initialEntries={['/contracts/webui/workspaces/scene_workspace/risk-dashboard']}>
            <Routes>
              <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
            </Routes>
          </MemoryRouter>
        </PaneActiveContext.Provider>
      </ThemeContext.Provider>
    );
    const { rerender } = render(tree(true));

    await waitFor(() => {
      expect(setTemporaryThemeOverride).toHaveBeenCalledWith('dark');
    });

    // Switching to another tab hides this pane but keeps it mounted: the
    // override must go with the tab, not with the (never happening) unmount.
    setTemporaryThemeOverride.mockClear();
    rerender(tree(false));
    expect(setTemporaryThemeOverride).toHaveBeenCalledTimes(1);
    expect(setTemporaryThemeOverride).toHaveBeenLastCalledWith(null);

    setTemporaryThemeOverride.mockClear();
    rerender(tree(true));
    expect(setTemporaryThemeOverride).toHaveBeenCalledTimes(1);
    expect(setTemporaryThemeOverride).toHaveBeenLastCalledWith('dark');
  });
});
