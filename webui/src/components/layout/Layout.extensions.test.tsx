import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './Layout';

// A private-build style registry: two sidebar entries (one admin-only, one
// without a placement) and a highlighted account-menu entry.
const { registryMock, useAuth } = vi.hoisted(() => {
  const Icon = () => null;
  const registryMock = {
    routes: [],
    navItems: [
      { id: 'ext-agent-hub', href: '/ext/agent-hub', labelKey: 'extAgentHub', icon: Icon, placement: 'sidebar', order: 5 },
      { id: 'ext-default', href: '/ext/default', labelKey: 'extDefault', icon: Icon, order: 8 },
      { id: 'ext-admin', href: '/ext/admin', labelKey: 'extAdminOnly', icon: Icon, placement: 'sidebar', adminOnly: true },
      { id: 'ext-menu', href: '/ext/menu', labelKey: 'extMenu', icon: Icon, placement: 'accountMenu', highlight: true, badge: 'NEW' },
    ],
    settingsSections: [],
    slots: [],
    i18n: {},
    updateChannel: 'private-channel',
  };
  return { registryMock, useAuth: vi.fn() };
});

const { checkUpdate, updateModalProps } = vi.hoisted(() => ({
  checkUpdate: vi.fn(),
  updateModalProps: [] as Record<string, unknown>[],
}));

vi.mock('@ext/registry', () => ({ default: registryMock }));
vi.mock('@/components/common/InstallationResultBanner', () => ({ InstallationResultBanner: () => null }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks', configuredDisplayName: null, faviconUrl: '/favicon.svg', hasCustomFavicon: false, loading: false }),
}));
vi.mock('@/hooks/useWebUIContractPages', () => ({
  useWebUIContractPages: () => ({ pages: [], workspaces: [], loading: false, error: null, refetch: vi.fn() }),
}));
vi.mock('@/hooks/useSceneSuiteUpdates', () => ({ useSceneSuiteUpdates: () => ({ hasUnseenSuites: false }) }));
vi.mock('@/hooks/useTasks', () => ({ useTaskDashboard: () => ({ counts: null }) }));
vi.mock('@/hooks/useTokenPolicyNotice', () => ({
  useTokenPolicyNotice: () => ({ ready: true, notice: null, close: vi.fn(), onPresented: vi.fn() }),
}));
vi.mock('@/hooks/useLayoutOpenTabs', () => ({ useLayoutOpenTabs: () => { const [v, s] = React.useState<any[]>([]); return [v, s]; } }));
vi.mock('@/hooks/useWorkspacePageOrders', () => ({ useWorkspacePageOrders: () => ({ apply: (_id: string, items: any[]) => items }) }));
vi.mock('./SessionHistoryPanel', () => ({ default: () => <div data-testid="history-panel" /> }));
vi.mock('@/components/common/UpdateModal', () => ({
  default: function UpdateModalMock(props: Record<string, unknown>) {
    updateModalProps.push(props);
    return <div role="dialog" aria-label="update-modal" />;
  },
}));
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

function ExtensionPathProbe() {
  return <div data-testid="extension-path">{useLocation().pathname}</div>;
}

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
  return screen.getByRole('menu', { name: `accountAndGlobal:${username}` });
}

/** The Agent workshop's sidebar, where the agent hub's destinations live. */
async function openWorkshop(container: HTMLElement) {
  fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
  await flush();
  return container.querySelector('aside nav') as HTMLElement;
}

function linkTargets(container: HTMLElement) {
  return within(container).queryAllByRole('menuitem').concat(within(container).queryAllByRole('link'))
    .map((element) => [element.textContent?.trim(), element.getAttribute('href')]);
}

describe('Layout extension registry integration', () => {
  beforeEach(() => {
    localStorage.clear();
    updateModalProps.length = 0;
    checkUpdate.mockResolvedValue({ has_update: false, latest_version: null, current_version: '0.2.0', error: null });
    useAuth.mockReturnValue({
      user: { id: 'u1', username: 'admin', role: 'admin', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
  });

  it('puts every sidebar entry in the workshop tools and models group, after the defaults', async () => {
    const { container } = renderLayout();
    await flush();

    // The workbench menu keeps only its own entries.
    const sidebar = container.querySelector('aside nav') as HTMLElement;
    expect(within(sidebar).getByRole('button', { name: 'newChat' })).toBeInTheDocument();
    expect(linkTargets(sidebar).map(([, href]) => href).filter((href) => href?.startsWith('/ext/'))).toEqual([]);
    // Sidebar entries, with or without an explicit placement, join the workshop's tools and models group in order.
    const workshop = await openWorkshop(container);
    const toolsAndModels = within(workshop).getByRole('heading', { name: 'toolsAndModels' }).parentElement as HTMLElement;
    expect(linkTargets(toolsAndModels).map(([, href]) => href)).toEqual([
      '/tools', '/devices', '/models', '/channels', '/hub', '/ext/admin', '/ext/agent-hub', '/ext/default',
    ]);
    expect(linkTargets(workshop)).toContainEqual(['extAgentHub', '/ext/agent-hub']);
    // The account menu keeps only its own entries.
    const accountHrefs = linkTargets(openAccountMenu()).map(([, href]) => href);
    expect(accountHrefs).toContain('/ext/menu');
    expect(accountHrefs).not.toContain('/ext/agent-hub');
    expect(accountHrefs).not.toContain('/ext/default');
  });

  it('keeps the workshop on a page an extension lists in its menu', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/ext/agent-hub']}>
        <Routes>
          <Route path="/*" element={<Layout contentRoutes={[{ path: '*', element: <div>extension page</div> }]} />} />
        </Routes>
      </MemoryRouter>,
    );
    await flush();

    // Not the workbench: the module tab and the sidebar are the workshop's.
    expect(screen.getByRole('button', { name: 'agentWorkshop' })).toHaveAttribute('aria-current', 'page');
    const sidebar = container.querySelector('aside nav') as HTMLElement;
    expect(within(sidebar).getByRole('heading', { name: 'toolsAndModels' })).toBeInTheDocument();
  });

  it('returns to the workshop page an extension lists after a reload', async () => {
    // Left on the extension page in the workshop, then the workbench, then reloaded.
    localStorage.setItem('flocks_layout_workspace_paths', JSON.stringify({ 'agent-workshop': '/ext/agent-hub' }));
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/*" element={<Layout contentRoutes={[{ path: '*', element: <ExtensionPathProbe /> }]} />} />
        </Routes>
      </MemoryRouter>,
    );
    await flush();

    fireEvent.click(screen.getByRole('button', { name: 'agentWorkshop' }));
    await flush();
    expect(screen.getAllByTestId('extension-path').some((probe) => probe.textContent === '/ext/agent-hub')).toBe(true);
  });

  it('renders account-menu entries with their highlight and badge', async () => {
    renderLayout();
    await flush();

    const entry = within(openAccountMenu()).getByRole('menuitem', { name: /extMenu/ });
    expect(entry).toHaveAttribute('href', '/ext/menu');
    // Framed like dev's own upgrade entry.
    expect(entry).toHaveClass('border', 'bg-ui-subtle');
    expect(within(entry).getByText('NEW')).toBeInTheDocument();
  });

  it('drops admin-only entries for members', async () => {
    useAuth.mockReturnValue({
      user: { id: 'u2', username: 'member1', role: 'member', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
    const { container } = renderLayout();
    await flush();

    const hrefs = linkTargets(await openWorkshop(container)).map(([, href]) => href);
    expect(hrefs).toContain('/ext/agent-hub');
    expect(hrefs).not.toContain('/ext/admin');
  });

  it('asks the registry update channel', async () => {
    renderLayout();
    await flush();
    await vi.waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'private-channel'));
  });

  it('gives the update dialog opened by hand the registry update channel', async () => {
    renderLayout();
    await flush();
    fireEvent.click(within(openAccountMenu()).getByRole('menuitem', { name: 'checkUpdate' }));
    expect(await screen.findByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
    // The dialog checks and applies on the channel the reminder checked, not the default one.
    expect(updateModalProps.at(-1)).toEqual(expect.objectContaining({ channel: 'private-channel', forceInitialCheck: true }));
  });
});
