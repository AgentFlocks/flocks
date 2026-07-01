import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import WebUIContractWorkspaceHost from './index';
import { setupSSEMock } from '@/test/mocks/sse';
import { ThemeContext } from '@/contexts/ThemeContext';

const { listWorkspacesMock } = vi.hoisted(() => ({
  listWorkspacesMock: vi.fn(),
}));

vi.mock('@/api/webuiContractPages', () => ({
  webuiContractPagesAPI: {
    listWorkspaces: listWorkspacesMock,
  },
}));

vi.mock('@/pages/WebUIContractPageHost/PageRuntimeHost', () => ({
  default: ({ pageId }: { pageId?: string }) => <div>page:{pageId}</div>,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

describe('WebUIContractWorkspaceHost', () => {
  setupSSEMock();

  beforeEach(() => {
    vi.clearAllMocks();
    listWorkspacesMock.mockResolvedValue({
      data: [
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

  it('waits for an explicit page selection on the workspace root', async () => {
    render(
      <MemoryRouter initialEntries={['/contracts/webui/workspaces/scene_workspace']}>
        <Routes>
          <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('workspace.selectPage')).toBeInTheDocument();
    });
    expect(screen.queryByText('page:risk-dashboard')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
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
    expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
  });

  it('temporarily uses dark theme for the posture dashboard when the user preference is light', async () => {
    const setTemporaryThemeOverride = vi.fn();
    const { unmount } = render(
      <ThemeContext.Provider
        value={{
          theme: 'light',
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
    expect(screen.getByText('page:risk-dashboard').parentElement).not.toHaveClass('p-6');
    await waitFor(() => {
      expect(setTemporaryThemeOverride).toHaveBeenCalledWith('dark');
    });

    unmount();

    expect(setTemporaryThemeOverride).toHaveBeenLastCalledWith(null);
  });
});
