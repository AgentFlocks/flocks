import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, type RouteObject } from 'react-router-dom';
import Layout from './Layout';
import { ThemeProvider } from '@/contexts/ThemeContext';

const {
  checkUpdate, getCurrentRelease, onboardingAPI, sessionApi,
  getActiveNotifications, ackNotification, getNotificationAckStatus,
  productNameContextValue,
  useAuth, useWebUIContractPages, useSceneSuiteUpdates, useTaskDashboard,
  confirmMock, tokenPolicy, changeLanguage, updateModalProps, notificationModal,
} = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
  checkUpdate: vi.fn(),
  getCurrentRelease: vi.fn(),
  onboardingAPI: { getStatus: vi.fn() },
  sessionApi: { create: vi.fn() },
  getActiveNotifications: vi.fn(),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn(),
  productNameContextValue: { productName: 'Flocks', configuredDisplayName: null as string | null, logoUrl: '/favicon.svg' as string | null },
  useAuth: vi.fn(),
  useWebUIContractPages: vi.fn(),
  useSceneSuiteUpdates: vi.fn(() => ({ hasUnseenSuites: false })),
  useTaskDashboard: vi.fn(() => ({ counts: { running: 1, queued: 2 } })),
  confirmMock: vi.fn(() => Promise.resolve(true)),
  tokenPolicy: { ready: true, notice: null, close: vi.fn(), onPresented: vi.fn() },
  updateModalProps: [] as Record<string, unknown>[],
  notificationModal: vi.fn(),
}));

vi.mock('@/api/update', () => ({ checkUpdate, getCurrentRelease, DEFAULT_UPDATE_CHANNEL: 'default' }));
vi.mock('@/api/onboarding', () => ({ onboardingAPI }));
vi.mock('@/api/session', () => ({ sessionApi }));
vi.mock('@/api/notifications', () => ({ getActiveNotifications, ackNotification, getNotificationAckStatus }));
vi.mock('@/components/common/InstallationResultBanner', () => ({ InstallationResultBanner: () => null }));
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
    updateModalProps.push(props);
    React.useEffect(() => { (props.onPresented as (() => void) | undefined)?.(); }, [props.onPresented]);
    return <div role="dialog" aria-label="update-modal" />;
  }
  return { default: UpdateModalMock };
});
vi.mock('@/components/common/OnboardingModal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div role="dialog" aria-label="onboarding"><button onClick={onClose}>close-onboarding</button></div>
  ),
}));
vi.mock('@/components/common/NotificationModal', () => ({
  default: (props: Record<string, unknown>) => {
    notificationModal(props);
    return null;
  },
}));
vi.mock('@/components/common/TokenPolicyNotice', () => ({ default: () => null }));
vi.mock('@/components/common/BackendStatusBanner', () => ({
  BackendStatusBanner: ({ inline }: { inline?: boolean }) => <div data-testid="backend-status-banner" data-inline={String(Boolean(inline))} />,
}));
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
  { path: 'settings/system-logs', element: <div>system logs page</div> },
  { path: 'settings/account', element: <div>account page</div> },
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
  updateModalProps.length = 0;
  localStorage.clear();
  Object.assign(productNameContextValue, { productName: 'Flocks', configuredDisplayName: null, logoUrl: '/favicon.svg' });
  useAuth.mockReturnValue({ user: { id: 'u1', username: 'admin', role: 'admin' }, logout: vi.fn() });
  onboardingAPI.getStatus.mockResolvedValue({ data: { completed: true } });
  checkUpdate.mockResolvedValue({ has_update: false, current_version: '2026.9.1' });
  getCurrentRelease.mockResolvedValue(null);
  getActiveNotifications.mockResolvedValue([]);
  getNotificationAckStatus.mockResolvedValue({ acknowledged: true });
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
    expect(within(header).getByRole('link', { name: 'sceneManagement' })).toHaveAttribute('href', '/scenes/suites');
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

  it('puts the backend status banner in the content column, right above the page', () => {
    renderAt('/');
    const banner = screen.getByTestId('backend-status-banner');
    const main = document.querySelector('main') as HTMLElement;
    expect(banner).toHaveAttribute('data-inline', 'true');
    expect(banner.parentElement).toBe(main.parentElement);
    expect(banner.nextElementSibling).toBe(main);
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

  it('opens scene management from the module bar without replacing the remembered SOC page', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区')],
      loading: false, error: null, refetch: vi.fn(),
    });
    useSceneSuiteUpdates.mockReturnValue({ hasUnseenSuites: true });
    renderAt('/');
    const menu = screen.getByRole('navigation', { name: 'modules' });
    expect(within(menu).getByText('SOC 工作区')).toBeInTheDocument();
    expect(within(menu).getByText('代码漏洞审计工作区')).toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).queryByRole('heading', { name: 'moreScenes' })).not.toBeInTheDocument();
    expect(within(sidebar).queryByRole('link', { name: 'sceneSuiteManager' })).not.toBeInTheDocument();
    const sceneMgmt = within(menu).getByRole('link', { name: 'sceneManagement' });
    expect(within(sceneMgmt).getByText('newBadge')).toBeInTheDocument();
    fireEvent.click(sceneMgmt);
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    expect(sceneMgmt).toHaveAttribute('aria-current', 'page');
    expect(within(menu).getByRole('button', { name: 'SOC 工作区' })).not.toHaveAttribute('aria-current');
    expect(within(sidebar).queryByRole('link', { name: 'settingsPreferences' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
    expect(sceneMgmt).not.toHaveAttribute('aria-current');
  });

  it.each(['missing', 'disabled', 'locked', 'loading'])('hides SOC but keeps scene management when the SOC suite is %s', async (state) => {
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: state === 'loading', error: null, refetch: vi.fn(),
      workspaces: state === 'missing' || state === 'loading' ? [] : [sceneWorkspace('soc_ui', 'SOC 工作区', {
        enabled: state !== 'disabled', locked: state === 'locked',
      })],
    });
    renderAt('/');
    const menu = screen.getByRole('navigation', { name: 'modules' });
    expect(within(menu).queryByRole('button', { name: 'socWorkspace' })).not.toBeInTheDocument();
    expect(within(menu).queryByRole('button', { name: 'SOC 工作区' })).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('link', { name: 'sceneManagement' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    expect(screen.queryByRole('heading', { name: 'moreScenes' })).not.toBeInTheDocument();
    expect(within(menu).getByRole('link', { name: 'sceneManagement' })).toHaveAttribute('aria-current', 'page');
  });

  it('drops SOC from the header but keeps scene management after all installed scenes are removed', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区'), sceneWorkspace('code_audit_ui', '代码漏洞审计工作区'), sceneWorkspace('ai_redteam_ui', 'AI 渗透')],
    });
    const view = renderAt('/scenes/suites');
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toBeInTheDocument();
    useWebUIContractPages.mockReturnValue({ pages: [], workspaces: [], loading: false, error: null, refetch: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    expect(screen.queryByRole('button', { name: 'SOC 工作区' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'socWorkspace' })).not.toBeInTheDocument();
    view.unmount();
    renderAt('/scenes/suites');
    expect(screen.queryByRole('button', { name: 'socWorkspace' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sceneManagement' })).toHaveAttribute('aria-current', 'page');
  });

  it('switches to a scene directly from the module bar', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [], workspaces: [sceneWorkspace('soc_ui', 'SOC 工作区')], loading: false, error: null, refetch: vi.fn(),
    });
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui'));
  });

  it('separates code audit from SOC alert operations and restores its original page routes', async () => {
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
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('button', { name: '代码漏洞审计工作区' })).toHaveAttribute('aria-current', 'page');
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    expect(within(sidebar).getAllByRole('heading').map((heading) => heading.textContent)).toEqual(['报告']);
    expect(within(sidebar).getByRole('link', { name: 'codeAuditProjects' })).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-home');
    expect(within(sidebar).getByRole('link', { name: '任务列表' })).toHaveAttribute('href', path);
    expect(within(sidebar).getByRole('link', { name: '任务列表' })).toHaveAttribute('aria-current', 'page');
    expect(within(sidebar).getByRole('link', { name: '报告中心' })).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/audit-report');
    fireEvent.click(within(sidebar).getByRole('button', { name: '报告' }));
    expect(within(sidebar).queryByRole('link', { name: '报告中心' })).not.toBeInTheDocument();
    expect(within(sidebar).getByRole('link', { name: '任务列表' })).toBeInTheDocument();
    expect(within(sidebar).queryByRole('link', { name: '告警列表' })).not.toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('button', { name: '报告' }));
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    expect(within(sidebar).getAllByRole('heading').map((heading) => heading.textContent)).toEqual(['态势', '告警运营', '其他']);
    expect(within(sidebar).getByRole('link', { name: '告警列表' })).toBeInTheDocument();
    expect(within(sidebar).queryByRole('link', { name: '任务列表' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    view.unmount();
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    fireEvent.click(screen.getByRole('link', { name: 'SOC 工作区 概览' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/soc_ui/soc_ui-overview'));
    expect(screen.queryByRole('button', { name: 'codeAudit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '代码漏洞审计工作区' })).not.toBeInTheDocument();
  });

  it('shows code audit as its own module without the alert suite', async () => {
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
    expect(screen.getByRole('button', { name: '代码漏洞审计工作区' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('button', { name: 'socWorkspace' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'codeAudit' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '报告' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: '报告中心' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sceneManagement' })).toBeInTheDocument();
    await waitFor(() => expect(activeProbe().textContent).toBe('/contracts/webui/workspaces/code_audit_ui/audit-home?view=audits'));
  });

  it.each(['uninstalled', 'disabled', 'locked'])('keeps the active audit page as its own module when the alert suite becomes %s', async (state) => {
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
    await waitFor(() => expect(screen.getByRole('button', { name: '代码漏洞审计工作区' })).toHaveAttribute('aria-current', 'page'));
    expect(activeProbe().textContent).toBe(path);
    expect(screen.getByRole('link', { name: '代码漏洞审计工作区 概览' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'sceneManagement' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'SOC 工作区' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'socWorkspace' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
  });

  it.each([
    { from: 'both suites', to: 'code audit alone', before: true, after: false },
    { from: 'code audit alone', to: 'both suites', before: false, after: true },
  ])('returns to the independent audit page when available suites change from $from to $to', async ({ before, after }) => {
    const audit = sceneWorkspace('code_audit_ui', '代码漏洞审计工作区');
    const soc = sceneWorkspace('soc_ui', 'SOC 工作区');
    const nav = { pages: [], loading: false, error: null, refetch: vi.fn(), workspaces: before ? [soc, audit] : [audit] };
    useWebUIContractPages.mockReturnValue(nav);
    const path = `${audit.route}/code_audit_ui-overview?view=audits`;
    const view = renderAt(path);
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));
    nav.workspaces = after ? [soc, audit] : [{ ...soc, enabled: false }, audit];
    view.rerender(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
  });

  it('keeps the latest audit page independent while SOC is disabled and re-enabled', async () => {
    const page = (id: string, title: string) => ({
      id, title, route: `/contracts/webui/${id}`, icon: 'ShieldCheck', order: 10, enabled: true,
      placement: 'home.after', buildHash: 'r', buildStatus: 'ready', workspaceId: 'code_audit_ui',
    });
    const audit = sceneWorkspace('code_audit_ui', '代码漏洞审计工作区', {
      defaultPageId: 'audit-home',
      pages: [page('audit-home', '代码审计首页')],
      sections: [
        { id: 'home', label: '首页', flat: true, pageIds: ['audit-home'], defaultPageId: 'audit-home' },
        { id: 'audits', label: '任务列表', flat: true, pageIds: ['audit-home'], defaultPageId: 'audit-home', query: { view: 'audits' } },
      ],
    });
    const soc = sceneWorkspace('soc_ui', 'SOC 工作区');
    const nav = { pages: [], loading: false, error: null, refetch: vi.fn(), workspaces: [soc, audit] };
    useWebUIContractPages.mockReturnValue(nav);
    const home = `${audit.route}/audit-home`;
    const tasks = `${audit.route}/audit-home?view=audits`;
    const view = renderAt(home);
    const rerenderNav = (workspaces: any[]) => {
      nav.workspaces = workspaces;
      view.rerender(
        <MemoryRouter initialEntries={[home]}>
          <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
        </MemoryRouter>,
      );
    };
    await waitFor(() => expect(activeProbe().textContent).toBe(home));
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));

    // SOC off: the audit module keeps its own last page, then moves on.
    rerenderNav([{ ...soc, enabled: false }, audit]);
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(home));
    fireEvent.click(screen.getByRole('link', { name: '任务列表' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(tasks));
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));

    // SOC back: its entry stays separate while audit keeps the newer task page.
    rerenderNav([soc, audit]);
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(soc.route));
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(tasks));
    fireEvent.click(screen.getByRole('link', { name: 'codeAuditProjects' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(home));
    fireEvent.click(screen.getByRole('button', { name: 'workbench' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/'));

    // SOC off again: audit still returns to its own most recently visited page.
    rerenderNav([{ ...soc, enabled: false }, audit]);
    fireEvent.click(screen.getByRole('button', { name: '代码漏洞审计工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(home));
  });

  it.each(['disabled', 'locked', 'uninstalled'])('keeps the SOC page memory when an old link opens a %s audit suite', async (state) => {
    const socPage = '/contracts/webui/workspaces/soc_ui/soc_ui-overview?severity=high';
    localStorage.setItem('flocks_layout_workspace_paths', JSON.stringify({ 'scene:soc_ui': socPage }));
    const audit = sceneWorkspace('code_audit_ui', '代码漏洞审计工作区', {
      enabled: state !== 'disabled', locked: state === 'locked',
    });
    useWebUIContractPages.mockReturnValue({
      pages: [], loading: false, error: null, refetch: vi.fn(),
      workspaces: state === 'uninstalled' ? [sceneWorkspace('soc_ui', 'SOC 工作区')] : [sceneWorkspace('soc_ui', 'SOC 工作区'), audit],
    });
    renderAt('/contracts/webui/workspaces/code_audit_ui/code_audit_ui-overview?view=audits');
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    fireEvent.click(screen.getByRole('button', { name: 'SOC 工作区' }));
    await waitFor(() => expect(activeProbe().textContent).toBe(socPage));
  });

  it('keeps scene management on the audit module after SOC is disabled', async () => {
    const audit = sceneWorkspace('code_audit_ui', '代码漏洞审计工作区');
    const soc = sceneWorkspace('soc_ui', 'SOC 工作区');
    const nav = { pages: [], loading: false, error: null, refetch: vi.fn(), workspaces: [soc, audit] };
    useWebUIContractPages.mockReturnValue(nav);
    const path = `${audit.route}/code_audit_ui-overview`;
    const view = renderAt(path);
    await waitFor(() => expect(activeProbe().textContent).toBe(path));
    fireEvent.click(screen.getByRole('link', { name: 'sceneManagement' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/scenes/suites'));
    nav.workspaces = [{ ...soc, enabled: false }, audit];
    view.rerender(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
      </MemoryRouter>,
    );
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    await waitFor(() => expect(within(sidebar).getByRole('link', { name: '代码漏洞审计工作区 概览' })).toBeInTheDocument());
    expect(within(sidebar).queryByRole('link', { name: 'newChat' })).not.toBeInTheDocument();
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

  it('opens settings from the footer menu with the settings sections in the sidebar', async () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'systemConfiguration' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/preferences'));
    const sidebar = screen.getByRole('navigation', { name: 'moduleNavigation' });
    // A stock build has no edition sections; those an extension registers sort in by order.
    expect(within(sidebar).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'settingsPreferences', 'accountManagement', 'systemLog', 'archivedData',
    ]);
    expect(screen.queryByTestId('history-panel')).not.toBeInTheDocument();
    fireEvent.click(within(sidebar).getByRole('link', { name: 'archivedData' }));
    await waitFor(() => expect(activeProbe().textContent).toBe('/settings/archived-data'));
    expect(within(sidebar).getByRole('link', { name: 'archivedData' })).toHaveAttribute('aria-current', 'page');
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
    // The open build registers no extension entries: no upgrade link and no tag.
    expect(within(menu).queryByRole('menuitem', { name: /upgrade/i })).not.toBeInTheDocument();
    expect(menu.querySelector('.border')).toBeNull();
    for (const link of Array.from(menu.querySelectorAll('a'))) {
      expect(['/settings/preferences', 'https://portal.agentflocks.com']).toContain(link.getAttribute('href'));
    }
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

  it('asks the default update channel and forces a fresh check when opened by hand', async () => {
    renderAt('/');
    // A stock build leaves the channel unset, so the server picks its default.
    await waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', undefined));
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    fireEvent.click(within(screen.getByRole('menu', { name: 'accountAndGlobal:admin' })).getByRole('menuitem', { name: 'checkUpdate' }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'update-modal' })).toBeInTheDocument());
    expect(updateModalProps.at(-1)).toEqual(expect.objectContaining({ forceInitialCheck: true }));
    expect(updateModalProps.at(-1)).not.toHaveProperty('channel');
  });

  const bundleRelease = (component: string, notes: string) => ({
    channel: 'bundle',
    current_version: 'v2026.10.16',
    latest_version: 'v2026.10.16',
    current_bundle_version: 'v2026.10.16',
    current_core_version: 'v2026.10.15',
    current_component_version: component,
    has_update: false,
    release_notes: notes,
    release_url: null,
    error: null,
  });

  it('announces an installed bundle with its own release notes, under the name the install shows', async () => {
    Object.assign(productNameContextValue, { productName: 'Acme Suite', configuredDisplayName: null, logoUrl: '/favicon.svg' });
    getCurrentRelease.mockResolvedValue(bundleRelease('2026.10.16', '报告中心不再列出空报告'));
    getNotificationAckStatus.mockResolvedValue({ acknowledged: false });
    renderAt('/');
    await waitFor(() => expect(notificationModal).toHaveBeenCalled());
    const id = 'whats-new-bundle-v2026.10.16-core-v2026.10.15-component-v2026.10.16';
    expect(getNotificationAckStatus).toHaveBeenCalledWith(id);
    expect(notificationModal.mock.lastCall?.[0].notifications).toEqual([
      expect.objectContaining({
        id,
        kind: 'whats_new',
        title: 'Acme Suite v2026.10.16 更新内容',
        body: '报告中心不再列出空报告',
        version: 'v2026.10.16',
      }),
    ]);
  });

  it('reads a bundle version an installer recorded under its edition prefix', async () => {
    Object.assign(productNameContextValue, { productName: 'Acme Suite', configuredDisplayName: null, logoUrl: '/favicon.svg' });
    getCurrentRelease.mockResolvedValue({ ...bundleRelease('acme-v2026.10.16', '说明'), current_bundle_version: 'acme-v2026.10.16' });
    getNotificationAckStatus.mockResolvedValue({ acknowledged: false });
    renderAt('/');
    await waitFor(() => expect(notificationModal).toHaveBeenCalled());
    expect(getNotificationAckStatus).toHaveBeenCalledWith('whats-new-bundle-v2026.10.16-core-v2026.10.15-component-v2026.10.16');
    expect(notificationModal.mock.lastCall?.[0].notifications).toEqual([
      expect.objectContaining({ title: 'Acme Suite v2026.10.16 更新内容', version: 'v2026.10.16' }),
    ]);
  });

  it('does not let a dismissed bundle component hide a same-number component update', async () => {
    Object.assign(productNameContextValue, { productName: 'Acme Suite', configuredDisplayName: null, logoUrl: '/favicon.svg' });
    getCurrentRelease.mockResolvedValue(bundleRelease('2026.10.16', '组件 2026.10.16 的说明'));
    getNotificationAckStatus.mockResolvedValue({ acknowledged: false });
    const first = renderAt('/');
    await waitFor(() => expect(getNotificationAckStatus).toHaveBeenCalled());
    const dismissedId = getNotificationAckStatus.mock.calls[0][0];
    first.unmount();
    vi.clearAllMocks();

    getCurrentRelease.mockResolvedValue(bundleRelease('2026.10.17', '组件 2026.10.17 的说明'));
    getNotificationAckStatus.mockImplementation(async (id: string) => ({ acknowledged: id === dismissedId }));
    renderAt('/');
    await waitFor(() => expect(notificationModal).toHaveBeenCalled());
    expect(getNotificationAckStatus).not.toHaveBeenCalledWith(dismissedId);
    expect(notificationModal.mock.lastCall?.[0].notifications).toEqual([
      expect.objectContaining({ body: '组件 2026.10.17 的说明', title: 'Acme Suite v2026.10.16 更新内容' }),
    ]);
  });

  it('keeps the Core release notice for a Core install', async () => {
    getCurrentRelease.mockResolvedValue({
      // The server names its own releases' channel.
      channel: 'flocks',
      current_version: '2026.10.15',
      latest_version: '2026.10.15',
      has_update: false,
      release_notes: 'OSS 2026.10.15 notes',
      release_url: null,
      error: null,
    });
    getNotificationAckStatus.mockResolvedValue({ acknowledged: false });
    renderAt('/');
    await waitFor(() => expect(notificationModal).toHaveBeenCalled());
    expect(getNotificationAckStatus).toHaveBeenCalledWith('whats-new-2026.10.15');
    expect(notificationModal.mock.lastCall?.[0].notifications).toEqual([
      expect.objectContaining({
        id: 'whats-new-2026.10.15',
        title: 'Flocks v2026.10.15 更新内容',
        body: 'OSS 2026.10.15 notes',
      }),
    ]);
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

describe('Layout onboarding', () => {
  it('opens onboarding for an admin when the first-run setup is not complete', async () => {
    onboardingAPI.getStatus.mockResolvedValue({ data: { completed: false } });
    renderAt('/');
    expect(await screen.findByRole('dialog', { name: 'onboarding' })).toBeInTheDocument();
  });

  it('does not automatically reopen dismissed onboarding, including after a fresh mount', async () => {
    onboardingAPI.getStatus.mockResolvedValue({ data: { completed: false, dismissed: true } });
    const firstView = renderAt('/');
    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();
    firstView.unmount();

    renderAt('/');
    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();
  });

  it('lets admins manually reopen dismissed onboarding', async () => {
    onboardingAPI.getStatus.mockResolvedValue({ data: { completed: false, dismissed: true } });
    renderAt('/');
    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledOnce());
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(new Event('flocks:open-onboarding'));
    });
    expect(await screen.findByRole('dialog', { name: 'onboarding' })).toBeInTheDocument();
  });

  it('does not bypass a dismissal when the status request fails, while manual opening still works', async () => {
    onboardingAPI.getStatus.mockRejectedValue(new Error('temporary network failure'));
    renderAt('/');
    await act(async () => {});
    expect(onboardingAPI.getStatus).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();

    await act(async () => { window.dispatchEvent(new Event('flocks:open-onboarding')); });
    expect(await screen.findByRole('dialog', { name: 'onboarding' })).toBeInTheDocument();
  });

  it('does not reopen a manually closed guide when an earlier automatic status response arrives', async () => {
    let resolveStatus!: (value: { data: { completed: boolean } }) => void;
    onboardingAPI.getStatus.mockReturnValue(new Promise((resolve) => { resolveStatus = resolve; }));
    renderAt('/');
    await waitFor(() => expect(onboardingAPI.getStatus).toHaveBeenCalledOnce());
    await act(async () => { window.dispatchEvent(new Event('flocks:open-onboarding')); });
    expect(await screen.findByRole('dialog', { name: 'onboarding' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'close-onboarding' }));
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();

    await act(async () => { resolveStatus({ data: { completed: false } }); });
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();
  });

  it('keeps onboarding away from members: no status check, no prompt, no event opener', async () => {
    useAuth.mockReturnValue({ user: { id: 'u2', username: 'member', role: 'member' }, logout: vi.fn() });
    onboardingAPI.getStatus.mockResolvedValue({ data: { completed: false } });
    renderAt('/');
    await act(async () => {
      window.dispatchEvent(new Event('flocks:open-onboarding'));
    });
    expect(onboardingAPI.getStatus).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();
  });

  it('closes onboarding when the signed-in user is no longer an admin', async () => {
    onboardingAPI.getStatus.mockResolvedValue({ data: { completed: false } });
    const { rerender } = renderAt('/');
    expect(await screen.findByRole('dialog', { name: 'onboarding' })).toBeInTheDocument();

    useAuth.mockReturnValue({ user: { id: 'u2', username: 'member', role: 'member' }, logout: vi.fn() });
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <Routes><Route path="/*" element={<Layout contentRoutes={routes} />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByRole('dialog', { name: 'onboarding' })).not.toBeInTheDocument();
  });
});

describe('Layout content panes', () => {
  // Settings sections scroll with the page; the system log viewer sizes its own
  // card from the viewport and scrolls inside it, so it needs no special pane.
  it.each(['/settings/system-logs', '/settings/account'])('wraps %s in the scrolling min-h-full wrapper', async (path) => {
    renderAt(path);

    const text = path.endsWith('system-logs') ? 'system logs page' : 'account page';
    const wrapper = (await screen.findByText(text)).parentElement as HTMLElement;
    expect(wrapper.className).toContain('min-h-full');
    expect(wrapper.className).not.toContain('min-h-0');
    expect(wrapper.parentElement?.className).toContain('overflow-y-auto');
  });
});
