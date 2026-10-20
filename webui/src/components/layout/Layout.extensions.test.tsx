import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Layout from './Layout';

// A private-build style registry: a sidebar entry per section kind, an
// admin-only entry, a highlighted account-menu entry, two default ids hidden,
// and both layout slots filled.
const { registryMock, useAuth } = vi.hoisted(() => {
  const Icon = () => null;
  const registryMock = {
    routes: [],
    navItems: [
      { id: 'ext-agent-hub', href: '/ext/agent-hub', labelKey: 'extAgentHub', icon: Icon, placement: 'sidebar', order: 5 },
      { id: 'ext-workbench', href: '/ext/workbench', labelKey: 'extWorkbench', icon: Icon, placement: 'sidebar', section: 'aiWorkbench' },
      { id: 'ext-admin', href: '/ext/admin', labelKey: 'extAdminOnly', icon: Icon, placement: 'sidebar', adminOnly: true },
      { id: 'ext-menu', href: '/ext/menu', labelKey: 'extMenu', icon: Icon, placement: 'accountMenu', highlight: true, badge: 'NEW' },
    ],
    settingsSections: [],
    slots: [
      { id: 'v', slot: 'layout.version', component: ({ context }: { context: Record<string, unknown> }) => <span data-testid="ext-version">{`ext-version:${String(context.version)}`}</span> },
      { id: 'n', slot: 'layout.notifications', component: () => <span data-testid="ext-notifications">ext-notifications</span> },
    ],
    i18n: {},
    updateChannel: 'private-channel',
    hideDefault: (id: string) => id === 'channels' || id === 'deviceIntegration',
  };
  return { registryMock, useAuth: vi.fn() };
});

const { checkUpdate } = vi.hoisted(() => ({
  checkUpdate: vi.fn(),
}));

// Installed contract pages; a test may fill these before rendering.
const { contractPages } = vi.hoisted(() => ({
  contractPages: { pages: [] as unknown[], workspaces: [] as unknown[] },
}));

vi.mock('@ext/registry', () => ({ default: registryMock }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks', configuredDisplayName: null, faviconUrl: '/favicon.svg', hasCustomFavicon: false, loading: false }),
}));
vi.mock('@/hooks/useWebUIContractPages', () => ({
  useWebUIContractPages: () => ({ pages: contractPages.pages, workspaces: contractPages.workspaces, loading: false, error: null, refetch: vi.fn() }),
}));
vi.mock('@/hooks/useSceneSuiteUpdates', () => ({ useSceneSuiteUpdates: () => ({ hasUnseenSuites: false }) }));
vi.mock('@/hooks/useTasks', () => ({ useTaskDashboard: () => ({ counts: null }) }));
vi.mock('@/hooks/useTokenPolicyNotice', () => ({
  useTokenPolicyNotice: () => ({ ready: true, notice: null, close: vi.fn(), onPresented: vi.fn() }),
}));
vi.mock('@/hooks/useLayoutOpenTabs', () => ({ useLayoutOpenTabs: () => { const [v, s] = React.useState<any[]>([]); return [v, s]; } }));
vi.mock('@/hooks/useWorkspacePageOrders', () => ({ useWorkspacePageOrders: () => ({ apply: (_id: string, items: any[]) => items }) }));
vi.mock('./SessionHistoryPanel', () => ({ default: () => <div data-testid="history-panel" /> }));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }));
vi.mock('@/api/onboarding', () => ({ onboardingAPI: { getStatus: vi.fn().mockResolvedValue({ data: { completed: true } }) } }));
vi.mock('@/api/update', () => ({
  checkUpdate,
  getCurrentRelease: vi.fn().mockResolvedValue({ current_version: '0.2.0', latest_version: '0.2.0', release_notes: null, error: null }),
  DEFAULT_UPDATE_CHANNEL: 'default',
}));
vi.mock('@/api/notifications', () => ({
  getActiveNotifications: vi.fn().mockResolvedValue([]),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn().mockResolvedValue({ acknowledged: true }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => (options?.name ? `${key}:${String(options.name)}` : key),
    i18n: { language: 'zh-CN', changeLanguage: vi.fn(), exists: () => true },
  }),
}));

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/*" element={<Layout contentRoutes={[{ index: true, element: <div>page body</div> }]} />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
}

function openAccountMenu(username = 'admin') {
  fireEvent.click(screen.getByRole('button', { name: `accountAndGlobal:${username}` }));
  return screen.getByRole('menu', { name: 'accountMenu' });
}

function linkTargets(container: HTMLElement) {
  return within(container).queryAllByRole('menuitem').concat(within(container).queryAllByRole('link'))
    .map((element) => [element.textContent?.trim(), element.getAttribute('href')]);
}

describe('Layout extension registry integration', () => {
  beforeEach(() => {
    localStorage.clear();
    checkUpdate.mockResolvedValue({ has_update: false, latest_version: null, current_version: '0.2.0', error: null });
    useAuth.mockReturnValue({
      user: { id: 'u1', username: 'admin', role: 'admin', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
  });

  it('puts sidebar entries in the Agent menu or the integrations group and honours hideDefault', async () => {
    const { container } = renderLayout();
    await flush();

    // Workbench-section entries extend the Agent workspace's sidebar menu.
    const sidebar = container.querySelector('aside nav') as HTMLElement;
    expect(linkTargets(sidebar)).toContainEqual(['extWorkbench', '/ext/workbench']);
    // The agent hub lives in the account menu now, so its entries join the integrations group.
    const menu = openAccountMenu();
    const targets = linkTargets(menu);
    expect(targets).toContainEqual(['extAgentHub', '/ext/agent-hub']);
    expect(targets).toContainEqual(['extAdminOnly', '/ext/admin']);
    // Hidden defaults are gone, untouched ones remain.
    expect(targets.find(([, href]) => href === '/channels')).toBeUndefined();
    expect(targets.find(([, href]) => href === '/devices')).toBeUndefined();
    expect(targets.find(([, href]) => href === '/agents')).toBeDefined();
    expect(targets.find(([, href]) => href === '/models')).toBeDefined();
    expect(within(sidebar).getByRole('button', { name: 'newChat' })).toBeInTheDocument();
  });

  it('hides a whole group when hideDefault names a sidebar group id of earlier releases', async () => {
    const { hideDefault, navItems } = registryMock;
    registryMock.navItems = [
      ...navItems,
      { id: 'ext-scene', href: '/ext/scene', labelKey: 'extScene', icon: navItems[0].icon, placement: 'sidebar', section: 'sceneWorkspaces' },
    ];
    // A workbench workspace page and a standalone custom page, both ready.
    contractPages.workspaces = [{
      id: 'bench', title: 'Bench', route: '/bench', placement: 'aiWorkbench', enabled: true,
      pages: [{ id: 'bench-page', title: 'Bench page', order: 0, enabled: true, buildStatus: 'ready', icon: null }],
    }];
    contractPages.pages = [
      { id: 'custom-page', title: 'Custom page', route: '/custom/page', placement: 'home.after', enabled: true, buildStatus: 'ready', icon: null },
    ];
    try {
      const shown = renderLayout();
      await flush();
      const shownHrefs = linkTargets(shown.container.querySelector('aside nav') as HTMLElement).map(([, href]) => href);
      expect(shownHrefs).toEqual(expect.arrayContaining(['/bench/bench-page', '/custom/page', '/ext/scene']));
      shown.unmount();

      registryMock.hideDefault = (id: string) => ['aiWorkbench', 'agentHub', 'customPages'].includes(id);
      const { container } = renderLayout();
      await flush();

      const sidebar = container.querySelector('aside nav') as HTMLElement;
      const sidebarHrefs = linkTargets(sidebar).map(([, href]) => href);
      for (const href of ['/', '/tasks', '/workflows', '/bench/bench-page', '/custom/page', '/ext/workbench', '/ext/scene']) {
        expect(sidebarHrefs).not.toContain(href);
      }
      expect(within(sidebar).queryByRole('button', { name: 'newChat' })).toBeNull();

      const menuHrefs = linkTargets(openAccountMenu()).map(([, href]) => href);
      for (const href of ['/agents', '/skills', '/tools', '/workspace', '/devices', '/hub', '/models', '/channels', '/ext/agent-hub', '/ext/admin']) {
        expect(menuHrefs).not.toContain(href);
      }
      // Account-menu entries never belonged to those groups.
      expect(menuHrefs).toContain('/ext/menu');
    } finally {
      registryMock.hideDefault = hideDefault;
      registryMock.navItems = navItems;
      contractPages.workspaces = [];
      contractPages.pages = [];
    }
  });

  it('renders account-menu entries with their highlight and badge', async () => {
    renderLayout();
    await flush();

    const entry = within(openAccountMenu()).getByRole('menuitem', { name: /extMenu/ });
    expect(entry).toHaveAttribute('href', '/ext/menu');
    expect(entry.className).toContain('bg-gradient-to-r');
    expect(within(entry).getByText('NEW')).toBeInTheDocument();
  });

  it('drops admin-only entries for members', async () => {
    useAuth.mockReturnValue({
      user: { id: 'u2', username: 'member1', role: 'member', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
    renderLayout();
    await flush();

    const hrefs = linkTargets(openAccountMenu('member1')).map(([, href]) => href);
    expect(hrefs).toContain('/ext/agent-hub');
    expect(hrefs).not.toContain('/ext/admin');
  });

  it('asks the registry update channel', async () => {
    renderLayout();
    await flush();
    await vi.waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'private-channel'));
  });

  it('mounts the layout.version and layout.notifications slots', async () => {
    renderLayout();
    await flush();

    // The slot replaces the default version badge and receives the version once the update check resolved.
    expect(screen.getByTestId('ext-version')).toBeInTheDocument();
    expect(await screen.findByText('ext-version:v0.2.0')).toBeInTheDocument();
    expect(screen.getByTestId('ext-notifications')).toBeInTheDocument();
  });
});
