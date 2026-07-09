import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './Layout';
import Home from '@/pages/Home';
import { UPDATE_DISMISSED_KEY } from '@/utils/updateDismissal';

const {
  catalogAPI,
  checkUpdate,
  defaultModelAPI,
  mcpAPI,
  onboardingAPI,
  providerAPI,
  sessionApi,
  getActiveNotifications,
  ackNotification,
  getNotificationAckStatus,
  flocksproUsersApi,
  consoleUpgradeApi,
  updateModalMock,
  useAuth,
  useStats,
  useWebUIContractPages,
} = vi.hoisted(() => ({
  catalogAPI: {
    list: vi.fn(),
  },
  checkUpdate: vi.fn(),
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  mcpAPI: {
    getCredentials: vi.fn(),
  },
  onboardingAPI: {
    validate: vi.fn(),
    apply: vi.fn(),
  },
  providerAPI: {
    getServiceCredentials: vi.fn(),
  },
  sessionApi: {
    create: vi.fn(),
  },
  getActiveNotifications: vi.fn(),
  ackNotification: vi.fn(),
  getNotificationAckStatus: vi.fn(),
  flocksproUsersApi: {
    hasCapability: vi.fn(),
    getLicenseStatus: vi.fn(),
  },
  consoleUpgradeApi: {
    getProPackageStatus: vi.fn(),
  },
  updateModalMock: vi.fn(() => null),
  useAuth: vi.fn(),
  useStats: vi.fn(),
  useWebUIContractPages: vi.fn(() => ({
    pages: [
      {
        id: 'dash-1',
        title: '自定义仪表盘',
        route: '/contracts/webui/dash-1',
        icon: 'LayoutDashboard',
        order: 10,
        enabled: true,
        placement: 'home.after',
        buildHash: 'abc',
        buildStatus: 'ready',
      },
    ],
    loading: false,
    error: null,
    refetch: vi.fn(),
  })),
}));

vi.mock('@/api/provider', () => ({
  catalogAPI,
  defaultModelAPI,
  providerAPI,
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI,
}));

vi.mock('@/api/onboarding', () => ({
  onboardingAPI,
}));

vi.mock('@/api/session', () => ({
  sessionApi,
}));

vi.mock('@/api/update', () => ({
  checkUpdate,
}));

vi.mock('@/api/notifications', () => ({
  getActiveNotifications,
  ackNotification,
  getNotificationAckStatus,
}));

vi.mock('@/api/flocksproUsers', () => ({
  flocksproUsersApi,
}));

vi.mock('@/api/consoleUpgrade', () => ({
  consoleUpgradeApi,
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth,
}));

vi.mock('@/hooks/useStats', () => ({
  useStats,
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: vi.fn(),
  }),
}));

vi.mock('@/hooks/useWebUIContractPages', () => ({
  useWebUIContractPages,
}));

vi.mock('@/components/common/LanguageSwitcher', () => ({
  default: () => null,
}));

vi.mock('@/components/common/UpdateModal', () => ({
  UPDATE_DISMISSED_KEY: 'update-dismissed',
  default: (props: Record<string, unknown>) => {
    updateModalMock(props);
    return <div role="dialog" aria-label="update-modal" />;
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) => (
      options?.version ? `${key} ${options.version}` : key
    ),
    i18n: { language: 'zh-CN', changeLanguage: vi.fn() },
  }),
}));

function makeProvider(id: string, name: string, models: Array<{ id: string; name: string }>) {
  return {
    id,
    name,
    description: null,
    credential_schemas: [
      {
        auth_method: 'api_key',
        fields: [
          {
            name: 'api_key',
            label: 'API Key',
            type: 'secret' as const,
            required: true,
            placeholder: '',
          },
        ],
      },
    ],
    env_vars: [],
    default_base_url: null,
    model_count: models.length,
    models: models.map((model) => ({
      ...model,
      model_type: 'llm',
      status: 'active',
      capabilities: {
        supports_tools: true,
        supports_vision: false,
        supports_reasoning: true,
        supports_streaming: true,
      },
    })),
  };
}

function renderHomeWithLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

function renderHomeWithLayoutAndSessionsRoute() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="sessions" element={<LocationProbe />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

async function flushEffects() {
  await act(async () => {
    if (vi.isFakeTimers()) {
      await vi.advanceTimersByTimeAsync(0);
      return;
    }
    await Promise.resolve();
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('Layout onboarding entry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    localStorage.clear();

    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: null,
      current_version: '0.2.0',
      error: null,
    });
    getActiveNotifications.mockResolvedValue([]);
    getNotificationAckStatus.mockResolvedValue({
      notification_id: 'whats-new-0.2.0',
      user_id: 'user-1',
      acknowledged: false,
    });
    ackNotification.mockResolvedValue({
      notification_id: 'notice-1',
      user_id: 'user-1',
      acknowledged_at: '2026-04-27T00:00:00Z',
    });
    useAuth.mockReturnValue({
      user: {
        id: 'user-1',
        username: 'admin',
        role: 'admin',
        status: 'active',
        must_reset_password: false,
      },
      logout: vi.fn(),
    });

    useStats.mockReturnValue({
      stats: {
        agents: { total: 0 },
        workflows: { total: 0 },
        skills: { total: 0 },
        tools: { total: 0 },
        tasks: { week: 0, scheduledActive: 0 },
        models: { total: 0 },
        system: { status: 'healthy' },
      },
      loading: false,
      error: null,
    });

    defaultModelAPI.getResolved.mockResolvedValue({
      data: {
        provider_id: 'threatbook-cn-llm',
        model_id: 'minimax-m2.7',
      },
    });

    catalogAPI.list.mockResolvedValue({
      data: {
        providers: [
          makeProvider('threatbook-cn-llm', 'ThreatBook CN', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('threatbook-io-llm', 'ThreatBook Global', [
            { id: 'minimax-m2.7', name: 'MiniMax M2.7' },
            { id: 'qwen3.6-plus', name: 'Qwen3.6 Plus' },
            { id: 'qwen3-max', name: 'Qwen 3 Max' },
          ]),
          makeProvider('openai-compatible', 'OpenAI Compatible', []),
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-chat', name: 'DeepSeek V3.2' }]),
        ],
      },
    });

    providerAPI.getServiceCredentials.mockResolvedValue({
      data: { has_credential: false },
    });
    flocksproUsersApi.hasCapability.mockResolvedValue(false);
    flocksproUsersApi.getLicenseStatus.mockRejectedValue(new Error('Flocks Pro unavailable'));
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: false,
      installed_version: null,
      flockspro_component_version: null,
    });

    mcpAPI.getCredentials.mockResolvedValue({
      data: { has_credential: false },
    });

    onboardingAPI.apply.mockResolvedValue({
      data: { success: true },
    });

    sessionApi.create.mockResolvedValue({ id: 'session-1' });
  });

  it('opens onboarding from the home entry and shows configured details for an existing default model', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    renderHomeWithLayout();

    await user.click(screen.getByRole('button', { name: 'getStarted' }));

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');

    await user.click(screen.getByText('onboarding.bootstrap.primaryTitle'));

    expect(screen.getByText('onboarding.bootstrap.configuredDetailsTitle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.tbPlaceholder')).not.toBeInTheDocument();
  });

  it('keeps standard pages out of a flex column content wrapper', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    const { container } = renderHomeWithLayout();

    await flushEffects();

    const contentWrapper = container.querySelector('main .min-h-full.p-6');
    expect(contentWrapper).not.toBeNull();
    expect(contentWrapper).not.toHaveClass('flex');
    expect(contentWrapper).not.toHaveClass('flex-col');
  });

  it('polls update checks hourly', async () => {
    vi.useFakeTimers();

    renderHomeWithLayout();

    await flushEffects();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_599_749);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(2);
  });

  it('checks Flocks Pro bundle updates when Flocks Pro is active', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: true,
      latest_version: '2026.6.22',
      current_version: '2026.6.21',
      current_bundle_version: '2026.6.21',
      latest_bundle_version: '2026.6.22',
      current_core_version: '2026.6.21',
      latest_core_version: '2026.6.21',
      current_pro_component_version: '2026.6.20',
      latest_pro_component_version: '2026.6.22',
      error: null,
    });
    flocksproUsersApi.getLicenseStatus.mockResolvedValue({
      pro_enabled: true,
      active: true,
      status: 'active',
      license_status: 'active',
    });
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      installed_version: '2026.05.13-3',
      flockspro_component_version: '2026.05.13-3',
    });

    renderHomeWithLayout();

    await waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'flockspro'));
    expect(await screen.findByRole('button', { name: 'hasNewVersion v2026.6.22' })).toBeInTheDocument();
  });

  it('shows the Pro update modal when only the Pro component changed after dismissal', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    localStorage.setItem(UPDATE_DISMISSED_KEY, 'flockspro:v2026.6.18:v2026.6.18:v2026.6.1');
    checkUpdate.mockResolvedValue({
      has_update: true,
      latest_version: 'v2026.6.18',
      current_version: 'v2026.6.18',
      current_bundle_version: 'v2026.6.18',
      latest_bundle_version: 'v2026.6.18',
      current_core_version: 'v2026.6.18',
      latest_core_version: 'v2026.6.18',
      current_pro_component_version: 'v2026.6.1',
      latest_pro_component_version: 'v2026.6.2',
      edition: 'flockspro',
      error: null,
    });
    flocksproUsersApi.getLicenseStatus.mockResolvedValue({
      pro_enabled: true,
      active: true,
      status: 'active',
      license_status: 'active',
    });
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      runtime_importable: true,
      installed_version: 'v2026.6.18',
      flockspro_component_version: 'v2026.6.1',
    });

    renderHomeWithLayout();

    await waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'flockspro'));
    await waitFor(() => expect(updateModalMock).toHaveBeenCalled());
  });

  it('shows configured product branding and Pro version for member users', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    useAuth.mockReturnValue({
      user: {
        id: 'user-2',
        username: 'member',
        role: 'member',
        status: 'active',
        must_reset_password: false,
      },
      logout: vi.fn(),
    });
    flocksproUsersApi.getLicenseStatus.mockResolvedValue({
      pro_enabled: true,
      active: true,
      status: 'active',
      license_status: 'active',
    });
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      installed_version: '2026.6.21',
      flockspro_component_version: '2026.6.20',
    });

    const { container } = renderHomeWithLayout();

    expect(await screen.findByText('admin.roleMember')).toBeInTheDocument();
    expect(await screen.findByText('v2026.6.21')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Flocks' })).not.toBeInTheDocument();
    await waitFor(() => expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'flockspro'));

    const sidebarShell = container.querySelector('aside > div');
    const logoRow = sidebarShell?.firstElementChild as HTMLElement | null;
    const accountRow = sidebarShell?.children.item(2) as HTMLElement | null;
    expect(logoRow).not.toBeNull();
    expect(accountRow).not.toBeNull();
    expect(within(logoRow!).getByText('Flocks')).toBeInTheDocument();
    expect(within(logoRow!).queryByText('v2026.6.21')).not.toBeInTheDocument();
    expect(within(accountRow!).getByText('v2026.6.21')).toBeInTheDocument();
  });

  it('keeps Pro account actions and version placement aligned with the standard layout', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: null,
      current_version: '2026.6.20',
      current_bundle_version: '2026.6.22',
      latest_bundle_version: null,
      current_core_version: '2026.6.20',
      latest_core_version: '2026.6.20',
      current_pro_component_version: '2026.6.22',
      latest_pro_component_version: '2026.6.22',
      edition: 'flockspro',
      error: null,
    });
    flocksproUsersApi.getLicenseStatus.mockResolvedValue({
      pro_enabled: true,
      active: true,
      status: 'active',
      license_status: 'active',
    });
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      installed_version: '2026.6.21',
      flockspro_component_version: '2026.6.21',
    });

    const { container } = renderHomeWithLayout();

    expect(await screen.findByText('v2026.6.22')).toBeInTheDocument();

    const sidebarShell = container.querySelector('aside > div');
    const logoRow = sidebarShell?.firstElementChild as HTMLElement | null;
    const accountRow = sidebarShell?.children.item(2) as HTMLElement | null;
    expect(logoRow).not.toBeNull();
    expect(accountRow).not.toBeNull();
    expect(within(logoRow!).getByText('Flocks')).toBeInTheDocument();
    expect(within(logoRow!).queryByText('v2026.6.22')).not.toBeInTheDocument();
    expect(within(accountRow!).getByText('v2026.6.22')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'admin settings' }));

    expect(screen.getByRole('link', { name: 'Flocks' })).toHaveAttribute('href', '/settings/flockspro');
    expect(screen.getByRole('button', { name: 'checkUpdate' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'settings' })).toHaveAttribute('href', '/settings/preferences');

    await user.click(screen.getByRole('button', { name: 'checkUpdate' }));

    expect(screen.getByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
    expect(updateModalMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        edition: 'flockspro',
      }),
    );
  });

  it('keeps new version reminder on the product mark while showing current version in the account area', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: true,
      latest_version: '2026.04.29',
      current_version: '2026.04.28',
      release_notes: 'Release line',
      release_url: 'https://example.com/release',
      error: null,
    });

    const { container } = renderHomeWithLayout();

    const updateButton = await screen.findByRole('button', { name: 'hasNewVersion v2026.04.29' });
    expect(updateButton).toBeInTheDocument();
    expect(screen.getByText('v2026.04.28')).toBeInTheDocument();

    const sidebarShell = container.querySelector('aside > div');
    const logoRow = sidebarShell?.firstElementChild as HTMLElement | null;
    const accountRow = sidebarShell?.children.item(2) as HTMLElement | null;
    expect(logoRow).not.toBeNull();
    expect(accountRow).not.toBeNull();
    expect(within(logoRow!).getByText('Flocks').closest('button')).toBeNull();
    expect(within(logoRow!).queryByText('v2026.04.28')).not.toBeInTheDocument();
    expect(within(logoRow!).getByText('newVersion')).toBeInTheDocument();
    expect(within(accountRow!).getByText('v2026.04.28')).toBeInTheDocument();

    await user.click(screen.getByTitle('collapseNav'));

    const collapsedUpdateButton = screen.getByRole('button', { name: 'hasNewVersion v2026.04.29' });
    expect(collapsedUpdateButton).toHaveClass('h-2.5');
    expect(collapsedUpdateButton).toHaveClass('w-2.5');
    expect(screen.queryByText('newVersion')).not.toBeInTheDocument();
  });

  it('opens the account menu with settings and logout actions', async () => {
    const user = userEvent.setup();
    const logout = vi.fn().mockResolvedValue(undefined);
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    useAuth.mockReturnValue({
      user: {
        id: 'user-1',
        username: 'admin',
        role: 'admin',
        status: 'active',
        must_reset_password: false,
      },
      logout,
    });

    renderHomeWithLayout();

    expect(await screen.findByText('admin.roleAdmin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'admin settings' }));

    expect(screen.getByRole('link', { name: 'Flocks' })).toHaveAttribute('href', '/settings/flockspro');
    expect(screen.getByRole('link', { name: 'settings' })).toHaveAttribute('href', '/settings/preferences');

    await user.click(screen.getByRole('button', { name: 'logout' }));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it('opens the update dialog from the account menu', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    renderHomeWithLayout();

    expect(await screen.findByText('admin.roleAdmin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'admin settings' }));
    await user.click(screen.getByRole('button', { name: 'checkUpdate' }));

    expect(screen.getByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
    expect(updateModalMock).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'logout' })).not.toBeInTheDocument();
  });

  it('keeps desktop layout animation while collapsing the sidebar', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    const { container } = renderHomeWithLayout();

    const aside = container.querySelector('aside');
    const contentShell = container.querySelector('main')?.parentElement;
    expect(aside).toHaveClass('transition-all');
    expect(contentShell).toHaveClass('transition-all');

    await user.click(screen.getByTitle('collapseNav'));

    expect(aside).toHaveClass('w-16');
    expect(aside).toHaveClass('transition-all');
    expect(contentShell).toHaveClass('lg:pl-16');
    expect(contentShell).toHaveClass('transition-all');
  });

  it('only keeps the account divider in the sidebar chrome', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    const { container } = renderHomeWithLayout();

    const sidebarShell = container.querySelector('aside > div');
    const logoRow = sidebarShell?.firstElementChild;
    const accountRow = sidebarShell?.children.item(2);

    expect(logoRow).not.toHaveClass('border-b');
    expect(accountRow).toHaveClass('border-t');
  });

  it('keeps the collapsed account menu selectable outside the sidebar width', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    const { container } = renderHomeWithLayout();

    await user.click(screen.getByTitle('collapseNav'));
    await user.click(screen.getByRole('button', { name: 'admin settings' }));

    expect(container.querySelector('aside > div')).toHaveClass('overflow-visible');
    expect(screen.getByRole('link', { name: 'settings' })).toHaveAttribute('href', '/settings/preferences');
    expect(screen.getByRole('button', { name: 'logout' })).toBeInTheDocument();
  });

  it('enforces a ten-minute minimum gap for focus-triggered update checks', async () => {
    vi.useFakeTimers();

    renderHomeWithLayout();

    await flushEffects();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(599_000);
    });
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await flushEffects();
    expect(checkUpdate).toHaveBeenCalledTimes(2);
  });

  it('reuses update check release notes for the notification modal', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: '2026.04.28',
      current_version: '2026.04.28',
      release_notes: [
        'English update 1',
        '',
        '<details>',
        '<summary>中文</summary>',
        '',
        '中文更新 1',
        '中文更新 2',
        '',
        '</details>',
      ].join('\n'),
      release_url: 'https://example.com/release',
      error: null,
    });
    getNotificationAckStatus.mockResolvedValue({
      notification_id: 'whats-new-2026.04.28',
      user_id: 'user-1',
      acknowledged: false,
    });

    renderHomeWithLayout();

    expect(await screen.findByText('Flocks v2026.04.28 更新内容')).toBeInTheDocument();
    expect(screen.getByText(/中文更新 1/)).toBeInTheDocument();
    expect(screen.queryByText(/English update 1/)).not.toBeInTheDocument();
    expect(getActiveNotifications).toHaveBeenCalledTimes(1);
    expect(getNotificationAckStatus).toHaveBeenCalledWith('whats-new-2026.04.28');
    expect(checkUpdate).toHaveBeenCalledTimes(1);
  });

  it('does not show acknowledged update release notes again', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: '2026.04.28',
      current_version: '2026.04.28',
      release_notes: 'Release line 1\nRelease line 2',
      release_url: 'https://example.com/release',
      error: null,
    });
    getNotificationAckStatus.mockResolvedValue({
      notification_id: 'whats-new-2026.04.28',
      user_id: 'user-1',
      acknowledged: true,
    });

    renderHomeWithLayout();

    await waitFor(() => {
      expect(getNotificationAckStatus).toHaveBeenCalledWith('whats-new-2026.04.28');
    });
    expect(screen.queryByText('Flocks v2026.04.28 更新内容')).not.toBeInTheDocument();
  });

  it('closes the notification modal from the top-right close button', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    getActiveNotifications.mockResolvedValue([
      {
        id: 'notice-1',
        kind: 'announcement',
        title: 'Notice title',
        summary: null,
        body: 'Notice body',
        highlights: [],
        primary_action: null,
        secondary_action: null,
        version: null,
        priority: 10,
      },
    ]);

    renderHomeWithLayout();

    expect(await screen.findByText('Notice title')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'close' }));
    expect(screen.queryByText('Notice title')).not.toBeInTheDocument();
  });

  it('waits for benefit and release notifications before opening the combined modal', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: '2026.04.28',
      current_version: '2026.04.28',
      release_notes: 'Release line 1',
      release_url: 'https://example.com/release',
      error: null,
    });
    const ackStatus = deferred<{
      notification_id: string;
      user_id: string;
      acknowledged: boolean;
    }>();
    getNotificationAckStatus.mockReturnValue(ackStatus.promise);
    getActiveNotifications.mockResolvedValue([
      {
        id: 'token-free-period-extended-2026-04',
        kind: 'benefit',
        title: 'Token 免费期已延长',
        summary: null,
        body: '福利内容',
        highlights: [],
        primary_action: null,
        secondary_action: null,
        version: null,
        priority: 10,
      },
    ]);

    renderHomeWithLayout();

    await waitFor(() => {
      expect(getActiveNotifications).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByText('Token 免费期已延长')).not.toBeInTheDocument();

    await act(async () => {
      ackStatus.resolve({
        notification_id: 'whats-new-2026.04.28',
        user_id: 'user-1',
        acknowledged: false,
      });
    });

    expect(await screen.findByText('Token 免费期已延长')).toBeInTheDocument();
    expect(screen.getByText('Flocks v2026.04.28 更新内容')).toBeInTheDocument();
  });
});

describe('Layout WebUI contract pages navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    checkUpdate.mockResolvedValue({
      has_update: false,
      latest_version: null,
      current_version: '0.2.0',
      error: null,
    });
    getActiveNotifications.mockResolvedValue([]);
    useAuth.mockReturnValue({
      user: {
        id: 'user-1',
        username: 'admin',
        role: 'admin',
        status: 'active',
        must_reset_password: false,
      },
      logout: vi.fn(),
    });
    useStats.mockReturnValue({
      stats: {
        agents: { total: 0 },
        workflows: { total: 0 },
        skills: { total: 0 },
        tools: { total: 0 },
        tasks: { week: 0, scheduledActive: 0 },
        models: { total: 0 },
        system: { status: 'healthy' },
      },
      loading: false,
      error: null,
    });
    flocksproUsersApi.hasCapability.mockResolvedValue(false);
    flocksproUsersApi.getLicenseStatus.mockResolvedValue({ pro_enabled: false });
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({ pro_enabled: false });
  });

  it('renders custom WebUI contract page links under the home section', async () => {
    renderHomeWithLayout();
    expect(await screen.findByRole('link', { name: '自定义仪表盘' })).toHaveAttribute(
      'href',
      '/contracts/webui/dash-1',
    );
  });

  it('keeps sidebar workspace groups expanded by default and allows collapsing each group', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    renderHomeWithLayout();

    const aiWorkbenchToggle = await screen.findByRole('button', { name: 'aiWorkbench' });
    const sceneWorkspacesToggle = screen.getByRole('button', { name: 'sceneWorkspaces' });
    const agentHubToggle = screen.getByRole('button', { name: 'agentHub' });

    expect(aiWorkbenchToggle).toHaveAttribute('aria-expanded', 'true');
    expect(sceneWorkspacesToggle).toHaveAttribute('aria-expanded', 'true');
    expect(agentHubToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: 'sessions' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'deviceIntegration' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'agents' })).toBeInTheDocument();

    await user.click(aiWorkbenchToggle);
    expect(aiWorkbenchToggle).toHaveAttribute('aria-expanded', 'false');
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBe(JSON.stringify(['aiWorkbench']));
    expect(screen.queryByRole('link', { name: 'sessions' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'deviceIntegration' })).toBeInTheDocument();
    await user.click(aiWorkbenchToggle);
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBeNull();
    expect(screen.getByRole('link', { name: 'sessions' })).toBeInTheDocument();

    await user.click(sceneWorkspacesToggle);
    expect(sceneWorkspacesToggle).toHaveAttribute('aria-expanded', 'false');
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBe(JSON.stringify(['sceneWorkspaces']));
    expect(screen.queryByRole('link', { name: 'deviceIntegration' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'agents' })).toBeInTheDocument();
    await user.click(sceneWorkspacesToggle);
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBeNull();
    expect(screen.getByRole('link', { name: 'deviceIntegration' })).toBeInTheDocument();

    await user.click(agentHubToggle);
    expect(agentHubToggle).toHaveAttribute('aria-expanded', 'false');
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBe(JSON.stringify(['agentHub']));
    expect(screen.queryByRole('link', { name: 'agents' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'sessions' })).toBeInTheDocument();
    await user.click(agentHubToggle);
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBeNull();
    expect(screen.getByRole('link', { name: 'agents' })).toBeInTheDocument();
  });

  it('restores collapsed sidebar workspace groups after refresh', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    localStorage.setItem('flocks_layout_collapsed_nav_sections', JSON.stringify(['sceneWorkspaces']));

    renderHomeWithLayout();

    expect(await screen.findByRole('button', { name: 'sceneWorkspaces' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('link', { name: 'deviceIntegration' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'aiWorkbench' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: 'sessions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'agentHub' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: 'agents' })).toBeInTheDocument();
  });

  it('does not render WebUI contract page links until their build is ready', async () => {
    useWebUIContractPages.mockReturnValue({
      pages: [
        {
          id: 'ready-page',
          title: '可用页面',
          route: '/contracts/webui/ready-page',
          icon: 'LayoutDashboard',
          order: 10,
          enabled: true,
          placement: 'home.after',
          buildHash: 'ready',
          buildStatus: 'ready',
        },
        {
          id: 'failed-page',
          title: '失败页面',
          route: '/contracts/webui/failed-page',
          icon: 'LayoutDashboard',
          order: 20,
          enabled: true,
          placement: 'home.after',
          buildHash: '',
          buildStatus: 'failed',
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderHomeWithLayout();

    expect(await screen.findByRole('link', { name: '可用页面' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '失败页面' })).not.toBeInTheDocument();
  });

  it('renders WebUI workspaces and device integration in the scene workspace group', async () => {
    const user = userEvent.setup();
    const workspacePages = [
      {
        id: 'risk-dashboard',
        title: '态势看板',
        route: '/contracts/webui/risk-dashboard',
        icon: 'Activity',
        order: 30,
        enabled: true,
        placement: 'home.after',
        buildHash: 'ready',
        buildStatus: 'ready' as const,
        workspaceId: 'scene_workspace',
        workspaceTitle: '场景工作区',
        workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
      },
      {
        id: 'ops-overview',
        title: '运营总览',
        route: '/contracts/webui/ops-overview',
        icon: 'ShieldCheck',
        order: 10,
        enabled: true,
        placement: 'home.after',
        buildHash: 'ready',
        buildStatus: 'ready' as const,
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
        buildHash: 'ready',
        buildStatus: 'ready' as const,
        workspaceId: 'scene_workspace',
        workspaceTitle: '场景工作区',
        workspaceRoute: '/contracts/webui/workspaces/scene_workspace',
      },
    ];
    useWebUIContractPages.mockReturnValue({
      pages: workspacePages,
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
          pages: workspacePages,
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    const { container } = renderHomeWithLayout();

    const workspaceLink = await screen.findByRole('link', { name: '场景工作区' });
    expect(workspaceLink).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/scene_workspace',
    );
    expect(workspaceLink.querySelectorAll('svg')).toHaveLength(2);
    expect(screen.queryByRole('link', { name: '调查列表' })).not.toBeInTheDocument();

    const sectionHeadings = Array.from(container.querySelectorAll('h3')).map((element) => element.textContent);
    expect(sectionHeadings.indexOf('sceneWorkspaces')).toBeGreaterThanOrEqual(0);
    expect(sectionHeadings.indexOf('sceneWorkspaces')).toBeLessThan(sectionHeadings.indexOf('agentHub'));
    expect(sectionHeadings).not.toContain('systemCenter');

    const sceneSection = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === 'sceneWorkspaces')
      ?.parentElement;
    expect(sceneSection?.querySelector('a[href="/contracts/webui/workspaces/scene_workspace"]')).not.toBeNull();
    expect(sceneSection?.querySelector('a[href="/devices"]')).not.toBeNull();

    const agentSection = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === 'agentHub')
      ?.parentElement;
    expect(agentSection?.querySelector('a[href="/devices"]')).toBeNull();
    expect(agentSection?.querySelector('a[href="/models"]')).not.toBeNull();
    expect(agentSection?.querySelector('a[href="/channels"]')).not.toBeNull();

    await user.click(workspaceLink);

    const workspaceMenu = screen.getByRole('navigation', { name: 'workspace.sectionNavigation' });
    expect(workspaceMenu).toBeInTheDocument();
    expect(workspaceMenu).toHaveClass('w-52');
    expect(workspaceMenu).toHaveClass('bg-zinc-100');
    expect(screen.getByRole('link', { name: '态势' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/scene_workspace/risk-dashboard',
    );
    expect(screen.getByRole('link', { name: '运营总览' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/scene_workspace/ops-overview',
    );
    expect(screen.getAllByRole('link', { name: '调查列表' }).find((link) => link.getAttribute('href')?.endsWith('/investigation-list'))).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/scene_workspace/investigation-list',
    );

    const workspaceMenuScope = within(workspaceMenu);
    const collapseButtons = workspaceMenuScope.getAllByTitle('workspace.collapseSidebar');
    await user.click(collapseButtons[collapseButtons.length - 1]);
    expect(screen.queryByRole('link', { name: '运营总览' })).not.toBeInTheDocument();

    await user.click(workspaceMenuScope.getByTitle('workspace.expandSidebar'));
    expect(screen.getByRole('link', { name: '运营总览' })).toBeInTheDocument();

    await user.click(workspaceMenuScope.getByRole('button', { name: '调查列表' }));
    expect(screen.queryByRole('link', { name: '运营总览' })).not.toBeInTheDocument();

    await user.click(workspaceMenuScope.getByRole('button', { name: '调查列表' }));
    expect(screen.getByRole('link', { name: '运营总览' })).toBeInTheDocument();

    await user.unhover(workspaceLink);

    await waitFor(() => {
      expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
    });
  });

  it('starts a SOC-scoped custom page session from the SOC workspace menu', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    sessionApi.create.mockResolvedValueOnce({ id: 'session-soc-custom-page' });

    const socPages = [
      {
        id: 'soc-dashboard',
        title: '告警态势',
        route: '/contracts/webui/soc-dashboard',
        icon: 'Activity',
        order: 10,
        enabled: true,
        placement: 'home.after',
        buildHash: 'ready',
        buildStatus: 'ready' as const,
        workspaceId: 'soc_ui',
        workspaceTitle: 'SOC 工作区',
        workspaceRoute: '/contracts/webui/workspaces/soc_ui',
      },
      {
        id: 'soc-alerts',
        title: '告警调查',
        route: '/contracts/webui/soc-alerts',
        icon: 'AlertTriangle',
        order: 20,
        enabled: true,
        placement: 'home.after',
        buildHash: 'ready',
        buildStatus: 'ready' as const,
        workspaceId: 'soc_ui',
        workspaceTitle: 'SOC 工作区',
        workspaceRoute: '/contracts/webui/workspaces/soc_ui',
      },
    ];
    useWebUIContractPages.mockReturnValue({
      pages: socPages,
      workspaces: [
        {
          id: 'soc_ui',
          title: 'SOC 工作区',
          route: '/contracts/webui/workspaces/soc_ui',
          icon: 'ShieldCheck',
          order: 10,
          enabled: true,
          placement: 'sceneWorkspace',
          defaultPageId: 'soc-alerts',
          sections: [
            {
              id: 'posture',
              label: '态势',
              pageIds: ['soc-dashboard'],
              defaultPageId: 'soc-dashboard',
              contentPadding: 'none',
              themeOverride: 'dark',
            },
            {
              id: 'operations',
              label: '告警运营',
              pageIds: ['soc-alerts'],
              defaultPageId: 'soc-alerts',
              contentPadding: 'none',
            },
          ],
          pages: socPages,
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderHomeWithLayoutAndSessionsRoute();

    await user.click(await screen.findByRole('link', { name: 'SOC 工作区' }));

    const workspaceMenu = screen.getByRole('navigation', { name: 'workspace.sectionNavigation' });
    const workspaceMenuScope = within(workspaceMenu);
    expect(workspaceMenuScope.getByRole('link', { name: '态势' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/soc_ui/soc-dashboard',
    );
    expect(workspaceMenuScope.getByRole('link', { name: '告警运营' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/soc_ui/soc-alerts',
    );

    await user.click(workspaceMenuScope.getByRole('button', { name: 'workspace.customPage' }));

    await waitFor(() => {
      expect(sessionApi.create).toHaveBeenCalledWith({ title: 'workspace.customPageSessionTitle' });
    });
    expect(await screen.findByTestId('location-probe')).toHaveTextContent(
      `/sessions?session=session-soc-custom-page&message=${encodeURIComponent('workspace.socCustomPageInitialMessage')}&display=${encodeURIComponent('workspace.socCustomPageDisplayLabel')}`,
    );
  });
});
