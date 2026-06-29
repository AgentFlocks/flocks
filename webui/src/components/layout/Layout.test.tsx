import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Layout from './Layout';
import Home from '@/pages/Home';

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
  default: () => null,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
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
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_599_999);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(checkUpdate).toHaveBeenCalledTimes(2);
  });

  it('skips GitHub-backed update checks when Flocks Pro is active', async () => {
    vi.useFakeTimers();
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

    await flushEffects();
    expect(checkUpdate).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_600_000);
    });
    expect(checkUpdate).not.toHaveBeenCalled();
  });

  it('shows Flocks Pro branding and version for member users', async () => {
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
      installed_version: '2026.05.22',
      flockspro_component_version: '2026.05.22',
    });

    renderHomeWithLayout();

    expect(await screen.findByText('Flocks Pro')).toBeInTheDocument();
    expect(await screen.findByText('pro-v2026.05.22')).toBeInTheDocument();
    expect(await screen.findByText('admin.roleMember')).toBeInTheDocument();
    expect(screen.queryByText('flocksproUpgrade')).not.toBeInTheDocument();
    expect(checkUpdate).not.toHaveBeenCalled();
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

    expect(screen.getByRole('link', { name: 'settings' })).toHaveAttribute('href', '/settings/preferences');

    await user.click(screen.getByRole('button', { name: 'logout' }));
    expect(logout).toHaveBeenCalledTimes(1);
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
    useWebUIContractPages.mockReturnValue({
      pages: [
        {
          id: 'soc-alerts',
          title: '告警运营',
          route: '/contracts/webui/soc-alerts',
          icon: 'AlertTriangle',
          order: 20,
          enabled: true,
          placement: 'home.after',
          buildHash: 'ready',
          buildStatus: 'ready',
          workspaceId: 'soc_ui',
          workspaceTitle: 'SOC 工作区',
          workspaceRoute: '/contracts/webui/workspaces/soc_ui',
        },
      ],
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
          pages: [],
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    const { container } = renderHomeWithLayout();

    expect(await screen.findByRole('link', { name: 'SOC 工作区' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/soc_ui',
    );
    expect(screen.queryByRole('link', { name: '告警运营' })).not.toBeInTheDocument();

    const sectionHeadings = Array.from(container.querySelectorAll('h3')).map((element) => element.textContent);
    expect(sectionHeadings.indexOf('sceneWorkspaces')).toBeGreaterThanOrEqual(0);
    expect(sectionHeadings.indexOf('sceneWorkspaces')).toBeGreaterThan(sectionHeadings.indexOf('agentHub'));
    expect(sectionHeadings).not.toContain('systemCenter');

    const sceneSection = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === 'sceneWorkspaces')
      ?.parentElement;
    expect(sceneSection?.querySelector('a[href="/contracts/webui/workspaces/soc_ui"]')).not.toBeNull();
    expect(sceneSection?.querySelector('a[href="/devices"]')).not.toBeNull();

    const agentSection = Array.from(container.querySelectorAll('h3'))
      .find((heading) => heading.textContent === 'agentHub')
      ?.parentElement;
    expect(agentSection?.querySelector('a[href="/devices"]')).toBeNull();
    expect(agentSection?.querySelector('a[href="/models"]')).toBeNull();
    expect(agentSection?.querySelector('a[href="/channels"]')).toBeNull();
  });
});
