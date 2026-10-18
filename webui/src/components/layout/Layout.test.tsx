import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, type RouteObject } from 'react-router-dom';
import Layout from './Layout';
import { ThemeProvider } from '@/contexts/ThemeContext';

const {
  checkUpdate, getCurrentRelease, onboardingAPI, sessionApi,
  getActiveNotifications, ackNotification, getNotificationAckStatus,
  flocksproUsersApi, consoleUpgradeApi, productNameContextValue,
  useAuth, useWebUIContractPages, useSceneSuiteUpdates, useTaskDashboard,
  confirmMock, tokenPolicy, changeLanguage,
} = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
  checkUpdate: vi.fn(),
  getCurrentRelease: vi.fn(),
  onboardingAPI: { getStatus: vi.fn() },
  sessionApi: { create: vi.fn() },
  getActiveNotifications: vi.fn(),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn(),
  flocksproUsersApi: { hasCapability: vi.fn(), getLicenseStatus: vi.fn() },
  consoleUpgradeApi: { getProPackageStatus: vi.fn() },
  productNameContextValue: { productName: 'Flocks', proProductName: 'Flocks Pro', configuredDisplayName: null as string | null, logoUrl: '/favicon.svg' as string | null },
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
vi.mock('@/components/common/UpdateModal', () => {
  function UpdateModalMock(props: Record<string, unknown>) {
    React.useEffect(() => { (props.onPresented as (() => void) | undefined)?.(); }, [props.onPresented]);
    return <div role="dialog" aria-label="update-modal" />;
  }
  return { default: UpdateModalMock };
});
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
    i18n: { language: 'zh-CN', changeLanguage },
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
  Object.assign(productNameContextValue, { productName: 'Flocks', configuredDisplayName: null, logoUrl: '/favicon.svg' });
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
  it('shows the product name and the workbench and workshop modules by default', async () => {
    renderAt('/');
    expect(screen.getByText('Flocks')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'workbench' })).toBeInTheDocument();
    expect(screen.getByTestId('history-panel')).toBeInTheDocument();
    const header = document.querySelector('[data-workspace-header]') as HTMLElement;
    const controls = ['language', 'theme', 'notifications', 'help'];
    expect(Array.from(header.querySelectorAll('button[aria-label], a[aria-label]'))
      .map((element) => element.getAttribute('aria-label'))
      .filter((label) => controls.includes(label!))).toEqual(controls);
    expect(header.querySelector('a[href^="/settings"]')).not.toBeInTheDocument();
    expect(header.querySelector('a[href^="/scenes"]')).not.toBeInTheDocument();
  });

  it('offers the history resize handle only in the expanded workbench', async () => {
    renderAt('/');
    expect(screen.getByRole('separator', { name: 'history.resizeHeight' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'collapseNav' }));
    expect(screen.queryByRole('separator', { name: 'history.resizeHeight' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'expandNav' }));
    expect(screen.getByRole('separator', { name: 'history.resizeHeight' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/agents'));
    expect(screen.queryByRole('separator', { name: 'history.resizeHeight' })).not.toBeInTheDocument();
  });

  it('shares the resized width with the content area and preserves it when collapsed', () => {
    renderAt('/');
    const sidebar = document.querySelector('aside')!;
    const layout = sidebar.parentElement!;
    const resize = screen.getByRole('separator', { name: 'resizeNav' });
    expect(layout.style.getPropertyValue('--sidebar-width')).toBe('192px');
    fireEvent.keyDown(resize, { key: 'ArrowRight' });
    expect(layout.style.getPropertyValue('--sidebar-width')).toBe('208px');
    expect(sidebar).toHaveClass('w-[var(--sidebar-width)]');
    expect(screen.getByRole('main').parentElement).toHaveClass('lg:pl-[var(--sidebar-width)]');
    fireEvent.click(screen.getByRole('button', { name: 'collapseNav' }));
    expect(layout.style.getPropertyValue('--sidebar-width')).toBe('64px');
    expect(screen.queryByRole('separator', { name: 'resizeNav' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'expandNav' }));
    expect(layout.style.getPropertyValue('--sidebar-width')).toBe('208px');
  });

  it('shows the Flocks logo beside the product name', () => {
    renderAt('/');
    const header = document.querySelector('[data-workspace-header]') as HTMLElement;
    expect(header.querySelector('img[data-brand-logo]')).toHaveAttribute('src', '/favicon.svg');
  });

  it('keeps the initial mark for a custom product name', () => {
    Object.assign(productNameContextValue, { productName: 'Acme SOC', configuredDisplayName: 'Acme SOC', logoUrl: null });
    renderAt('/');
    const header = document.querySelector('[data-workspace-header]') as HTMLElement;
    expect(header.querySelector('img[data-brand-logo]')).toBeNull();
    expect(within(within(header).getByRole('link', { name: 'Acme SOC' })).getByText('A')).toBeInTheDocument();
    expect(within(header).getByText('Acme SOC')).toBeInTheDocument();
  });

  it('opens the scene catalog from More Scenes in SOC and keeps SOC selected', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区')],
      loading: false, error: null, refetch: vi.fn(),
    });
    useSceneSuiteUpdates.mockReturnValue({ hasUnseenSuites: true });
    renderAt('/');
    const menu = screen.getByRole('navigation', { name: 'modules' });
    expect(within(menu).getByText('SOC 工作区')).toBeInTheDocument();
    expect(within(menu).queryByText('代码漏洞审计工作区')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'sceneManagement' })).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).getByRole('heading', { name: 'moreScenes' })).toBeInTheDocument();
    const sceneMgmt = within(sidebar).getByRole('link', { name: 'sceneSuiteManager' });
    expect(within(sceneMgmt).getByText('newBadge')).toBeInTheDocument();
    fireEvent.click(sceneMgmt);
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    expect(within(sidebar).getByRole('link', { name: 'sceneSuiteManager' })).toHaveAttribute('aria-current', 'page');
    expect(within(menu).getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-current', 'page');
    expect(within(sidebar).queryByRole('link', { name: 'settingsPreferences' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
  });

  it.each(['missing', 'disabled', 'locked', 'loading'])('keeps Add Scene reachable in SOC when the SOC suite is %s', async (state) => {
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: state === 'loading', error: null, refetch: vi.fn(),
      workspaces: state === 'missing' || state === 'loading' ? [] : [sceneWorkspace('soc_ui', 'SOC 工作区', {
        enabled: state !== 'disabled', locked: state === 'locked',
      })],
    });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'socWorkspace' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    expect(screen.getByRole('button', { name: 'socWorkspace' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('heading', { name: 'moreScenes' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sceneSuiteManager' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
  });

  it('keeps SOC and Add Scene after all installed scenes are removed', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区'), sceneWorkspace('ai_redteam_ui', 'AI 渗透')],
    });
    const view = renderAt('/scenes/suites');
    useWebUIContractPages.mockReturnValue({ pages: [], workspaces: [], loading: false, error: null, refetch: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    fireEvent.click(screen.getByRole('button', { name: 'socWorkspace' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    expect(screen.getByRole('link', { name: 'sceneSuiteManager' })).toHaveAttribute('aria-current', 'page');
    expect(localStorage.getItem('flocks_layout_current_workspace')).toBe('scene:soc_ui');
    view.unmount();
    renderAt('/scenes/suites');
    expect(screen.getByRole('button', { name: 'socWorkspace' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'sceneSuiteManager' })).toBeInTheDocument();
  });

  it('switches to a scene directly from the module bar', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区')], loading: false, error: null, refetch: vi.fn(),
    });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
  });

  it('groups code audit below SOC alert operations and restores its original page routes', async () => {
    const page = (workspaceId: string, id: string, title: string) => ({
      id, title, route: `/contracts/webui/${id}`, icon: 'ShieldCheck', order: 10, enabled: true,
      placement: 'home.after', buildHash: 'r', buildStatus: 'ready', workspaceId,
    });
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [
        sceneWorkspace('soc_ui', 'SOC 工作区', {
          pages: [page('soc_ui', 'soc-dashboard', '态势总览'), page('soc_ui', 'soc-alerts', '告警列表'), page('soc_ui', 'soc-extra', '其他页面')],
          sections: [
            { id: 'posture', label: '态势', pageIds: ['soc-dashboard'] },
            { id: 'operations', label: '告警运营', pageIds: ['soc-alerts'] },
            { id: 'other', label: '其他', pageIds: ['soc-extra'] },
          ],
        }),
        sceneWorkspace('code_audit_ui', '代码漏洞审计工作区', {
          pages: [page('code_audit_ui', 'audit-home', '代码审计首页'), page('code_audit_ui', 'audit-report', '报告中心')],
          sections: [
            { id: 'home', label: '首页', flat: true, pageIds: ['audit-home'] },
            { id: 'audits', label: '任务列表', flat: true, pageIds: ['audit-home'], query: { view: 'audits' } },
            { id: 'reports', label: '报告', pageIds: ['audit-report'] },
          ],
        }),
      ],
    });
    const path = '/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits';
    const view = renderAt(path);
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: '代码漏洞审计工作区' })).not.toBeInTheDocument();
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).getAllByRole('heading').map((heading) => heading.textContent)).toEqual(['态势', '告警运营', 'codeAudit', '其他', 'moreScenes']);
    expect(within(sidebar).getByRole('link', { name: 'codeAuditProjects' })).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-home');
    expect(within(sidebar).getByRole('link', { name: '任务列表' })).toHaveAttribute('href', path);
    expect(within(sidebar).getByRole('link', { name: '任务列表' })).toHaveAttribute('aria-current', 'page');
    expect(within(sidebar).getByRole('link', { name: '报告中心' })).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-report');
    fireEvent.click(within(sidebar).getByRole('button', { name: 'codeAudit' }));
    expect(within(sidebar).queryByRole('link', { name: '任务列表' })).not.toBeInTheDocument();
    expect(within(sidebar).getByRole('link', { name: '告警列表' })).toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('button', { name: 'codeAudit' }));
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    view.unmount();
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
    expect(screen.getByRole('link', { name: '任务列表' })).toHaveAttribute('aria-current', 'page');
  });

  it.each([{ enabled: false }, { locked: true }])('keeps unavailable code-audit pages out of SOC: %s', async (state) => {
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区', state)],
    });
    renderAt('/contracts/webui/workspaces/code_audit_ui/code_audit_ui-overview');
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    fireEvent.click(screen.getByRole('link', { name: 'SOC 工作区 概览' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui/soc_ui-overview'));
    expect(screen.queryByRole('button', { name: 'codeAudit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '代码漏洞审计工作区' })).not.toBeInTheDocument();
  });

  it('keeps code-audit sections under SOC without the alert suite', async () => {
    const page = (id: string, title: string) => ({
      id, title, route: `/contracts/webui/${id}`, icon: 'ShieldCheck', order: 10, enabled: true,
      placement: 'home.after', buildHash: 'r', buildStatus: 'ready', workspaceId: 'code_audit_ui',
    });
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [sceneWorkspace('code_audit_ui', '代码漏洞审计工作区', {
        defaultPageId: 'audit-home',
        pages: [page('audit-home', '代码审计首页'), page('audit-report', '报告中心')],
        sections: [
          { id: 'home', label: '首页', flat: true, icon: 'Home', pageIds: ['audit-home'], defaultPageId: 'audit-home' },
          { id: 'audits', label: '任务列表', flat: true, icon: 'ListTodo', pageIds: ['audit-home'], defaultPageId: 'audit-home', query: { view: 'audits' } },
          { id: 'reports', label: '报告', pageIds: ['audit-report'], defaultPageId: 'audit-report' },
        ],
      })],
    });
    renderAt('/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits');
    const home = await screen.findByRole('link', { name: 'codeAuditProjects' });
    const tasks = screen.getByRole('link', { name: '任务列表' });
    expect(home).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-home');
    expect(tasks).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits');
    expect(tasks).toHaveAttribute('aria-current', 'page');
    expect(home).not.toHaveAttribute('aria-current');
    // Flat sections have no group header and do not repeat the page title.
    expect(screen.queryByRole('button', { name: '首页' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '任务列表' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '代码审计首页' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'socWorkspace' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: '代码漏洞审计工作区' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'codeAudit' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: '报告中心' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sceneSuiteManager' })).toBeInTheDocument();
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits'));
  });

  it.each(['uninstalled', 'disabled', 'locked'])('keeps the active audit page and SOC links when the alert suite becomes %s', async (state) => {
    const audit = sceneWorkspace('code_audit_ui', '代码漏洞审计工作区');
    const nav = {
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), audit],
    };
    useWebUIContractPages.mockReturnValue(nav);
    const path = `${audit.route}/code_audit_ui-overview`;
    const view = renderAt(path);
    nav.workspaces = state === 'uninstalled' ? [audit] : [sceneWorkspace('soc_ui', 'SOC 工作区', {
      enabled: state !== 'disabled', locked: state === 'locked',
    }), audit];
    // A navigation refresh while the code-audit page is open must not redirect it.
    view.rerender(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'socWorkspace' })).toHaveAttribute('aria-current', 'page'));
    expect(activeProbe().textContent).toBe(path);
    expect(screen.getByRole('link', { name: '代码漏洞审计工作区 概览' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'sceneSuiteManager' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '代码漏洞审计工作区' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    fireEvent.click(screen.getByRole('button', { name: 'socWorkspace' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
  });

  it('hides the history panel outside the Agent workspace', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区')], loading: false, error: null, refetch: vi.fn(),
    });
    renderAt('/contracts/webui/workspaces/soc_ui/soc_ui-overview');
    await waitFor(() => expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument());
  });

  it('puts channels in the workshop and selects the right module for direct links', async () => {
    renderAt('/channels');
    expect(screen.getByRole('button', { name: 'agentWorkshop' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'channels' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    expect(screen.queryByRole('link', { name: 'channels' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/channels'));
  });

  it('opens settings from the footer menu with Flocks Pro in the sidebar', async () => {
    flocksproUsersApi.hasCapability.mockResolvedValue(true);
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'systemConfiguration' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/preferences'));
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'settingsPreferences', 'accountManagement', 'securityConfig', 'systemLog', 'auditLogs', 'Flocks Pro', 'archivedData',
    ]);
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('link', { name: 'auditLogs' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/audit-logs'));
    expect(within(sidebar).getByRole('link', { name: 'auditLogs' })).toHaveAttribute('aria-current', 'page');
    fireEvent.click(within(sidebar).getByRole('link', { name: 'Flocks Pro' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/flockspro'));
    expect(within(sidebar).getByRole('link', { name: 'Flocks Pro' })).toHaveAttribute('aria-current', 'page');
    expect(within(sidebar).queryByRole('link', { name: 'sceneSuiteManager' })).not.toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('link', { name: 'settingsPreferences' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/preferences'));
  });

  it('preserves settings permissions for members', () => {
    useAuth.mockReturnValue({ user: { username: 'member', role: 'member' }, logout: vi.fn() });
    renderAt('/settings/preferences');
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'settingsPreferences', 'accountManagement', 'systemLog', 'archivedData',
    ]);
  });

  it('combines account and version information in a single sidebar footer menu', () => {
    renderAt('/');
    const header = document.querySelector('[data-workspace-header]') as HTMLElement;
    const sidebar = document.querySelector('aside') as HTMLElement;
    expect(within(header).queryByText('admin')).not.toBeInTheDocument();
    expect(within(sidebar).getByText('admin')).toBeInTheDocument();
    expect(within(sidebar).queryByRole('button', { name: 'versionInfo' })).not.toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('button', { name: 'accountAndGlobal:admin' }));
    const menu = screen.getByRole('menu', { name: 'accountAndGlobal:admin' });
    expect(within(menu).getByRole('menuitem', { name: 'checkUpdate' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: /upgradePro/ })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: 'logout' })).toBeInTheDocument();
    expect(within(menu).queryByRole('menuitem', { name: 'channels' })).not.toBeInTheDocument();
  });

  it('switches language and persists all theme modes from header shortcuts', () => {
    render(<ThemeProvider><MemoryRouter><Layout contentRoutes={routes} /></MemoryRouter></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'language' }));
    expect(screen.getByRole('menuitemradio', { name: '简体中文' })).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'English' }));
    expect(changeLanguage).toHaveBeenCalledWith('en-US');
    for (const mode of ['dark', 'light', 'system']) {
      fireEvent.click(screen.getByRole('button', { name: 'theme' }));
      fireEvent.click(screen.getByRole('menuitemradio', { name: `${mode}Theme` }));
      expect(localStorage.getItem('flocks_theme_mode')).toBe(mode);
      fireEvent.click(screen.getByRole('button', { name: 'theme' }));
      expect(screen.getByRole('menuitemradio', { name: `${mode}Theme` })).toHaveAttribute('aria-checked', 'true');
      fireEvent.keyDown(document, { key: 'Escape' });
    }
  });

  it('moves capability navigation into the workshop and remembers both modules', async () => {
    renderAt('/tasks');
    expect(screen.getByRole('button', { name: 'workbench' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'workspace' })).toHaveAttribute('href', '/workspace?tab=files');
    expect(screen.queryByRole('link', { name: 'workflows' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/agents'));
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^tasks/ })).not.toBeInTheDocument();
    for (const name of ['capabilityOrchestration', 'knowledgeAndMemory', 'toolsAndModels']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('link', { name: 'memory' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/workspace?tab=memory'));
    expect(screen.getByRole('link', { name: 'memory' })).toHaveAttribute('aria-current', 'page');
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/tasks'));
    fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/workspace?tab=memory'));
  });

  it.each(['knowledge', 'memory'])('opens a direct %s link in the workshop', (tab) => {
    renderAt(`/workspace?tab=${tab}`);
    expect(screen.getByRole('button', { name: 'agentWorkshop' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: tab === 'knowledge' ? 'knowledgeBase' : 'memory' })).toHaveAttribute('aria-current', 'page');
  });

  it('returns from settings when the remembered module is selected', async () => {
    localStorage.setItem('flocks_layout_current_workspace', 'agent-workshop');
    localStorage.setItem('flocks_layout_workspace_paths', JSON.stringify({ 'agent-workshop': '/tools' }));
    renderAt('/settings/preferences');
    const module = screen.getByRole('button', { name: 'agentWorkshop' });
    expect(module).not.toHaveAttribute('aria-current');
    fireEvent.click(module);
    await waitFor(() => expect(activeProbe().textContent).toBe('/tools'));
  });

  it('ignores old workbench paths that now belong to the workshop', async () => {
    localStorage.setItem('flocks_layout_workspace_paths', JSON.stringify({ agent: '/workflows/old' }));
    renderAt('/agents');
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
  });

  it('confirms before logging out', async () => {
    const logout = vi.fn();
    useAuth.mockReturnValue({ user: { id: 'u1', username: 'admin', role: 'admin' }, logout });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'accountAndGlobal:admin' })).getByRole('menuitem', { name: 'logout' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it('shows the update reminder for admins and opens the update dialog on check', async () => {
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '2026.9.1', latest_version: '2026.9.2' });
    renderAt('/');
    await waitFor(() => expect(screen.getByText('newVersion')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'accountAndGlobal:admin' })).getByRole('menuitem', { name: 'checkUpdate' }));
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
