import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Layout from './Layout';

// A private-build style registry: one sidebar entry per section kind, an
// admin-only entry, two default ids hidden, and both layout slots filled.
const { registryMock, useAuth } = vi.hoisted(() => {
  const Icon = () => null;
  const registryMock = {
    routes: [],
    navItems: [
      { id: 'ext-agent-hub', href: '/ext/agent-hub', labelKey: 'extAgentHub', icon: Icon, placement: 'sidebar', order: 5 },
      { id: 'ext-workbench', href: '/ext/workbench', labelKey: 'extWorkbench', icon: Icon, placement: 'sidebar', section: 'aiWorkbench' },
      { id: 'ext-admin', href: '/ext/admin', labelKey: 'extAdminOnly', icon: Icon, placement: 'sidebar', adminOnly: true },
      { id: 'ext-menu', href: '/ext/menu', labelKey: 'extMenu', icon: Icon, placement: 'accountMenu' },
    ],
    settingsSections: [],
    slots: [
      { id: 'v', slot: 'layout.version', component: ({ context }: { context: Record<string, unknown> }) => <span data-testid="ext-version">{`ext-version:${String(context.version)}`}</span> },
      { id: 'n', slot: 'layout.notifications', component: () => <span data-testid="ext-notifications">ext-notifications</span> },
    ],
    i18n: {},
    hideDefault: (id: string) => id === 'channels' || id === 'deviceIntegration',
  };
  return { registryMock, useAuth: vi.fn() };
});

vi.mock('@ext/registry', () => ({ default: registryMock }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks', configuredDisplayName: null, faviconUrl: '/favicon.svg', hasCustomFavicon: false, loading: false }),
}));
vi.mock('@/hooks/useWebUIContractPages', () => ({
  useWebUIContractPages: () => ({ pages: [], workspaces: [], loading: false, error: null, refetch: vi.fn() }),
}));
vi.mock('@/api/update', () => ({ checkUpdate: vi.fn().mockResolvedValue({ has_update: false, latest_version: null, current_version: '0.2.0', error: null }) }));
vi.mock('@/api/notifications', () => ({
  getActiveNotifications: vi.fn().mockResolvedValue([]),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn().mockResolvedValue({ acknowledged: true }),
}));
vi.mock('@/api/session', () => ({ sessionApi: { create: vi.fn() } }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => ({ error: vi.fn() }) }));
vi.mock('@/components/common/LanguageSwitcher', () => ({ default: () => null }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN', changeLanguage: vi.fn() },
  }),
}));

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<div>page body</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
}

describe('Layout extension registry integration', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    useAuth.mockReturnValue({
      user: { id: 'u1', username: 'admin', role: 'admin', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
  });

  it('renders sidebar navItems in their section and honours hideDefault for default ids', async () => {
    renderLayout();
    await flush();

    const links = screen.getAllByRole('link').map((a) => [a.textContent?.trim(), a.getAttribute('href')]);
    // extension entries appear (default section agentHub, explicit aiWorkbench, admin-only for an admin)
    expect(links).toContainEqual(['extAgentHub', '/ext/agent-hub']);
    expect(links).toContainEqual(['extWorkbench', '/ext/workbench']);
    expect(links).toContainEqual(['extAdminOnly', '/ext/admin']);
    // hidden defaults are gone: the channels and device-integration entries
    // (both live in the agent hub group now)
    expect(links.find(([, href]) => href === '/channels')).toBeUndefined();
    expect(links.find(([, href]) => href === '/devices')).toBeUndefined();
    // untouched defaults remain
    expect(links.find(([, href]) => href === '/agents')).toBeDefined();
    expect(links.find(([, href]) => href === '/sessions')).toBeDefined();
  });

  it('drops admin-only sidebar navItems for members', async () => {
    useAuth.mockReturnValue({
      user: { id: 'u2', username: 'member1', role: 'member', status: 'active', must_reset_password: false },
      logout: vi.fn(),
    });
    renderLayout();
    await flush();

    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('/ext/agent-hub');
    expect(hrefs).not.toContain('/ext/admin');
  });

  it('mounts the layout.version and layout.notifications slots', async () => {
    renderLayout();
    await flush();

    // the slot replaces the default version badge and receives the version once the update check resolved
    expect(screen.getByTestId('ext-version')).toBeInTheDocument();
    expect(await screen.findByText('ext-version:v0.2.0')).toBeInTheDocument();
    expect(screen.getByTestId('ext-notifications')).toBeInTheDocument();
  });

  it('keeps the username readable and truncates the role label instead', async () => {
    renderLayout();
    await flush();

    const username = screen.getByTitle('admin');
    expect(username).toHaveClass('shrink-0');
    expect(username).toHaveClass('min-w-[4ch]');
    const role = screen.getByTitle('admin.roleAdmin');
    expect(role).toHaveClass('truncate');
    expect(role).toHaveClass('min-w-0');
    expect(role).not.toHaveClass('shrink-0');
  });
});
