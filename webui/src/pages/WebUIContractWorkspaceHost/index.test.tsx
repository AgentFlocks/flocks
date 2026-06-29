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
          id: 'soc_ui',
          title: 'SOC 工作区',
          route: '/contracts/webui/workspaces/soc_ui',
          icon: 'ShieldCheck',
          order: 10,
          enabled: true,
          placement: 'sceneWorkspace',
          defaultPageId: 'soc-overview',
          sections: [
            {
              id: 'posture',
              label: '态势',
              pageIds: ['alert-denoise-triage-dashboard'],
              defaultPageId: 'alert-denoise-triage-dashboard',
              contentPadding: 'none',
              themeOverride: 'dark',
            },
            {
              id: 'operations',
              label: '告警运营',
              pageIds: ['soc-overview', 'soc-alerts'],
              defaultPageId: 'soc-overview',
              contentPadding: 'comfortable',
            },
          ],
          pages: [
            {
              id: 'alert-denoise-triage-dashboard',
              title: '告警态势',
              route: '/contracts/webui/alert-denoise-triage-dashboard',
              icon: 'ShieldCheck',
              order: 30,
              enabled: true,
              placement: 'home.after',
              buildHash: 'posture',
              buildStatus: 'ready',
              workspaceId: 'soc_ui',
              workspaceTitle: 'SOC 工作区',
              workspaceRoute: '/contracts/webui/workspaces/soc_ui',
            },
            {
              id: 'soc-overview',
              title: 'SOC 总览',
              route: '/contracts/webui/soc-overview',
              icon: 'Shield',
              order: 10,
              enabled: true,
              placement: 'home.after',
              buildHash: 'abc',
              buildStatus: 'ready',
              workspaceId: 'soc_ui',
              workspaceTitle: 'SOC 工作区',
              workspaceRoute: '/contracts/webui/workspaces/soc_ui',
            },
            {
              id: 'soc-alerts',
              title: '告警运营',
              route: '/contracts/webui/soc-alerts',
              icon: 'AlertTriangle',
              order: 20,
              enabled: true,
              placement: 'home.after',
              buildHash: '',
              buildStatus: 'failed',
              workspaceId: 'soc_ui',
              workspaceTitle: 'SOC 工作区',
              workspaceRoute: '/contracts/webui/workspaces/soc_ui',
            },
          ],
        },
      ],
    });
  });

  it('waits for an explicit page selection on the SOC workspace root', async () => {
    render(
      <MemoryRouter initialEntries={['/contracts/webui/workspaces/soc_ui']}>
        <Routes>
          <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('workspace.selectPage')).toBeInTheDocument();
    });
    expect(screen.queryByText('page:alert-denoise-triage-dashboard')).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
  });

  it('renders a selected operation page without a fixed workspace sidebar', async () => {
    render(
      <MemoryRouter initialEntries={['/contracts/webui/workspaces/soc_ui/soc-alerts']}>
        <Routes>
          <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('page:soc-alerts')).toBeInTheDocument();
    });
    expect(screen.getByText('page:soc-alerts').parentElement).toHaveClass('p-6');
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
        <MemoryRouter initialEntries={['/contracts/webui/workspaces/soc_ui/alert-denoise-triage-dashboard']}>
          <Routes>
            <Route path="/contracts/webui/workspaces/:workspaceId/:pageId?" element={<WebUIContractWorkspaceHost />} />
          </Routes>
        </MemoryRouter>
      </ThemeContext.Provider>,
    );

    await waitFor(() => {
      expect(screen.getByText('page:alert-denoise-triage-dashboard')).toBeInTheDocument();
    });
    expect(screen.getByText('page:alert-denoise-triage-dashboard').parentElement).not.toHaveClass('p-6');
    await waitFor(() => {
      expect(setTemporaryThemeOverride).toHaveBeenCalledWith('dark');
    });

    unmount();

    expect(setTemporaryThemeOverride).toHaveBeenLastCalledWith(null);
  });
});
