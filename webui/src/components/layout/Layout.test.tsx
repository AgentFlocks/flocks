import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, type RouteObject } from 'react-router-dom';
import Layout from './Layout';

const {
  checkUpdate, getCurrentRelease, onboardingAPI, sessionApi,
  getActiveNotifications, ackNotification, getNotificationAckStatus,
  flocksproUsersApi, consoleUpgradeApi, productNameContextValue,
  useAuth, useWebUIContractPages, useSceneSuiteUpdates, useTaskDashboard,
  confirmMock, tokenPolicy,
} = vi.hoisted(() => ({
  checkUpdate: vi.fn(),
  getCurrentRelease: vi.fn(),
  onboardingAPI: { getStatus: vi.fn() },
  sessionApi: { create: vi.fn() },
  getActiveNotifications: vi.fn(),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn(),
  flocksproUsersApi: { hasCapability: vi.fn(), getLicenseStatus: vi.fn() },
  consoleUpgradeApi: { getProPackageStatus: vi.fn() },
  productNameContextValue: { productName: 'Flocks', proProductName: 'Flocks Pro', configuredDisplayName: null as string | null },
  useAuth: vi.fn(),
  useWebUIContractPages: vi.fn(),
  useSceneSuiteUpdates: vi.fn(() => ({ hasUnseenSuites: false })),
  useTaskDashboard: vi.fn(() => ({ counts: { running: 1, queued: 2 } })),
  confirmMock: vi.fn(() => Promise.resolve(true)),
  tokenPolicy: { ready: true, notice: null, close: vi.fn(), onPresented: vi.fn() },
}));

vi.mock('@/api/update', () => ({ checkUpdate, getCurrentRelease }));
vi.mock('@/api/onboarding', () => ({ onboardingAPI }));
vi.mock('@/api/session', () => ({ sessionApi }));
vi.mock('@/api/notifications', () => ({ getActiveNotifications, ackNotification, getNotificationAckStatus }));
vi.mock('@/api/flocksproUsers', () => ({ flocksproUsersApi }));
vi.mock('@/api/consoleUpgrade', () => ({ consoleUpgradeApi }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/contexts/ProductNameContext', () => ({ useProductName: () => productNameContextValue }));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => confirmMock }));
vi.mock('@/hooks/useSceneSuiteUpdates', () => ({ useSceneSuiteUpdates }));
vi.mock('@/hooks/useWebUIContractPages', () => ({ useWebUIContractPages }));
vi.mock('@/hooks/useTasks', () => ({ useTaskDashboard }));
vi.mock('@/hooks/useTokenPolicyNotice', () => ({ useTokenPolicyNotice: () => tokenPolicy }));
vi.mock('@/hooks/useLayoutOpenTabs', () => ({ useLayoutOpenTabs: () => { const [v, s] = React.useState<any[]>([]); return [v, s]; } }));
vi.mock('@/hooks/useWorkspacePageOrders', () => ({ useWorkspacePageOrders: () => ({ apply: (_id: string, items: any[]) => items, applyNav: (_id: string, items: any[]) => items }) }));
// The history panel is heavy (sessions, SSE); it has its own test.
vi.mock('./SessionHistoryPanel', () => ({ default: () => <div data-testid="history-panel" /> }));
vi.mock('@/components/common/UpdateModal', () => ({
  default: (props: Record<string, unknown>) => {
    React.useEffect(() => { (props.onPresented as (() => void) | undefined)?.(); }, [props.onPresented]);
    return <div role="dialog" aria-label="update-modal" />;
  },
}));
vi.mock('@/components/common/OnboardingModal', () => ({ default: () => <div role="dialog" aria-label="onboarding" /> }));
vi.mock('@/components/common/NotificationModal', () => ({ default: () => null }));
vi.mock('@/components/common/TokenPolicyNotice', () => ({ default: () => null }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) => {
      if (options?.version) return `${key} ${options.version}`;
      if (options?.name) return `${key}:${options.name}`;
      if (options?.count !== undefined) return `${key}:${options.count}`;
      return key;
    },
    i18n: { language: 'zh-CN', changeLanguage: vi.fn() },
  }),
}));

function RouteProbe() {
  const location = useLocation();
  return <div data-testid="probe">{`${location.pathname}${location.search}`}</div>;
}

const routes: RouteObject[] = [
  { index: true, element: <RouteProbe /> },
  { path: '*', element: <RouteProbe /> },
];

function activeProbe(): HTMLElement {
  const probes = screen.getAllByTestId('probe').filter((el) => el.closest('[data-keep-alive-pane="active"]'));
  return probes[0] ?? screen.getAllByTestId('probe')[0];
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
    </MemoryRouter>,
  );
}

function sceneWorkspace(id: string, title: string, over: Record<string, any> = {}) {
  return {
    id, title, titleEn: title, route: `/contracts/webui/workspaces/${id}`,
    icon: 'ShieldCheck', order: 20, enabled: true, placement: 'sceneWorkspace',
    defaultPageId: `${id}-overview`, sections: [],
    pages: [{ id: `${id}-overview`, title: `${title} 概览`, route: `/contracts/webui/${id}-overview`, icon: 'LayoutDashboard', order: 10, enabled: true, placement: 'home.after', buildHash: 'r', buildStatus: 'ready', workspaceId: id }],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  useAuth.mockReturnValue({ user: { id: 'u1', username: 'admin', role: 'admin' }, logout: vi.fn() });
  onboardingAPI.getStatus.mockResolvedValue({ data: { completed: true } });
  checkUpdate.mockResolvedValue({ has_update: false, current_version: '2026.9.1' });
  getCurrentRelease.mockResolvedValue(null);
  getActiveNotifications.mockResolvedValue([]);
  getNotificationAckStatus.mockResolvedValue({ acknowledged: true });
  flocksproUsersApi.getLicenseStatus.mockResolvedValue({ pro_enabled: false });
  flocksproUsersApi.hasCapability.mockResolvedValue(false);
  consoleUpgradeApi.getProPackageStatus.mockResolvedValue({ pro_enabled: false });
  useSceneSuiteUpdates.mockReturnValue({ hasUnseenSuites: false });
  useTaskDashboard.mockReturnValue({ counts: { running: 1, queued: 2 } });
  confirmMock.mockResolvedValue(true);
  useWebUIContractPages.mockReturnValue({ pages: [], workspaces: [], loading: false, error: null, refetch: vi.fn() });
});

describe('Layout chrome', () => {
  it('shows the product name and the Agent workspace by default', async () => {
    renderAt('/');
    expect(screen.getByText('Flocks')).toBeInTheDocument();
    expect(screen.getByText('agentWorkspace')).toBeInTheDocument();
    expect(screen.getByTestId('history-panel')).toBeInTheDocument();
  });

  it('lists enabled scenes and the scene manager in the switcher', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区')],
      loading: false, error: null, refetch: vi.fn(),
    });
    useSceneSuiteUpdates.mockReturnValue({ hasUnseenSuites: true });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'switchWorkspace' }));
    const menu = screen.getByRole('menu', { name: 'switchWorkspace' });
    expect(within(menu).getByText('SOC 工作区')).toBeInTheDocument();
    expect(within(menu).getByText('代码漏洞审计工作区')).toBeInTheDocument();
    const sceneMgmt = within(menu).getByRole('menuitem', { name: /sceneManagement/ });
    expect(within(sceneMgmt).getByText('newBadge')).toBeInTheDocument();
  });

  it('switches to a scene workspace from the switcher', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区')], loading: false, error: null, refetch: vi.fn(),
    });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'switchWorkspace' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'switchWorkspace' })).getByRole('menuitemradio', { name: /SOC 工作区/ }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
  });

  it('hides the history panel outside the Agent workspace', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区')], loading: false, error: null, refetch: vi.fn(),
    });
    renderAt('/contracts/webui/workspaces/soc_ui/soc_ui-overview');
    await waitFor(() => expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument());
  });

  it('opens the account menu with capability groups and system management', async () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    const menu = screen.getByRole('menu', { name: 'accountMenu' });
    expect(within(menu).getByText('aiCapabilities')).toBeInTheDocument();
    expect(within(menu).getByText('integrationsAndExtensions')).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: /systemManagement/ })).toHaveAttribute('href', '/settings/preferences');
    expect(within(menu).getByRole('menuitem', { name: 'agents' })).toHaveAttribute('href', '/agents');
  });

  it('confirms before logging out', async () => {
    const logout = vi.fn();
    useAuth.mockReturnValue({ user: { id: 'u1', username: 'admin', role: 'admin' }, logout });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'accountMenu' })).getByRole('menuitem', { name: 'logout' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it('shows the update reminder for admins and opens the update dialog on check', async () => {
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '2026.9.1', latest_version: '2026.9.2' });
    renderAt('/');
    await waitFor(() => expect(screen.getByText('newVersion')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'accountMenu' })).getByRole('menuitem', { name: 'checkUpdate' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'update-modal' })).toBeInTheDocument());
  });

  it('surfaces active tasks and backend notifications in the bell menu', async () => {
    getActiveNotifications.mockResolvedValue([{ id: 'n1', kind: 'announcement', title: '系统公告', summary: '内容', highlights: [], priority: 10 }]);
    const { container } = renderAt('/');
    await waitFor(() => expect(container.querySelector('[data-notification-dot]')).not.toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'notifications' }));
    const menu = screen.getByRole('menu', { name: 'notifications' });
    expect(within(menu).getByText('tasksInProgress:3')).toBeInTheDocument();
    await waitFor(() => expect(within(menu).getByText('系统公告')).toBeInTheDocument());
  });

  it('starts a new chat from the Agent sidebar', async () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'newChat' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/sessions'));
  });
});
