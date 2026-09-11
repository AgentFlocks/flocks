import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes, useLocation, type RouteObject } from 'react-router-dom';
import Layout from './Layout';
import Home from '@/pages/Home';
import { UPDATE_DISMISSED_KEY } from '@/utils/updateDismissal';
import { getTokenPolicyStatus, claimTokenPolicy, confirmTokenPolicyDisplay } from '@/api/tokenPolicy';

vi.mock('@/api/tokenPolicy', () => ({
  getTokenPolicyStatus: vi.fn(),
  claimTokenPolicy: vi.fn(),
  confirmTokenPolicyDisplay: vi.fn(),
}));

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
  productNameContextValue,
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
    getStatus: vi.fn(),
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
  productNameContextValue: {
    productName: 'Flocks',
    proProductName: 'Flocks Pro',
    configuredDisplayName: null as string | null,
    faviconUrl: '/favicon.svg',
    hasCustomFavicon: false,
    loading: false,
    refreshProductName: vi.fn(),
    updateProductName: vi.fn(),
    uploadProductFavicon: vi.fn(),
    resetProductFavicon: vi.fn(),
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

vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => productNameContextValue,
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
  default: function MockUpdateModal(props: Record<string, unknown>) {
    React.useEffect(() => { (props.onPresented as (() => void) | undefined)?.(); }, [props.onPresented]);
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

function makeOnboardingStatus(overrides: Record<string, any> = {}) {
  return {
    completed: true,
    has_default_model: true,
    default_model: {
      provider_id: 'threatbook-cn-llm',
      model_id: 'minimax-m2.7',
    },
    threatbook_intel: {
      configured: false,
      region: null,
      api_configured: false,
      api_service_id: 'threatbook-cn',
      mcp_configured: false,
      mcp_connected: false,
      mcp_status: 'not_configured',
      mcp_name: 'threatbook_mcp',
      service_matrix: {
        cn: ['api', 'mcp'],
        global: ['api'],
      },
    },
    ...overrides,
  };
}

// Pages rendered inside the layout under test: Home plus a probe for every
// other path. The layout keeps one pane per open tab mounted, so the probe
// also carries local state to prove a hidden tab survives untouched.
const defaultCustomPage = {
  id: 'dash-1',
  title: '自定义仪表盘',
  route: '/contracts/webui/dash-1',
  icon: 'LayoutDashboard',
  order: 10,
  enabled: true,
  placement: 'home.after',
  buildHash: 'abc',
  buildStatus: 'ready' as const,
};

const testContentRoutes: RouteObject[] = [
  { index: true, element: <Home /> },
  { path: 'sessions', element: <RouteProbe /> },
  { path: 'workflows/*', element: <RouteProbe /> },
  { path: 'contracts/webui/workspaces/:workspaceId/:pageId?', element: <RouteProbe /> },
  { path: '*', element: <RouteProbe /> },
];

function renderHomeWithLayout() {
  return renderLayoutAt('/');
}

function renderHomeWithLayoutAndSessionsRoute() {
  return renderLayoutAt('/');
}

/** The location probe of the visible (active) pane; hidden panes keep their own. */
function activeProbe(): HTMLElement {
  const probe = screen.getAllByTestId('location-probe')
    .find((element) => element.closest('[data-keep-alive-pane="active"]'));
  if (!probe) throw new Error('no active location probe');
  return probe;
}

function RouteProbe() {
  const location = useLocation();
  const [count, setCount] = React.useState(0);
  return (
    <div>
      <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>
      <button type="button" onClick={() => setCount((value) => value + 1)}>count-up</button>
      <span data-testid="probe-count">{count}</span>
      <Link to="/contracts/webui/workspaces/soc_ui/soc-alerts">go-soc-alerts</Link>
      <Link to="/sessions">go-sessions</Link>
      <Link to="/workflows/wf-1">go-workflow-detail</Link>
    </div>
  );
}

function renderLayoutAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/*" element={<Layout contentRoutes={testContentRoutes} />} />
      </Routes>
    </MemoryRouter>,
  );
}

function makeSocPage(id: string, title: string, icon: string, order: number) {
  return {
    id,
    title,
    route: `/contracts/webui/${id}`,
    icon,
    order,
    enabled: true,
    placement: 'home.after',
    buildHash: 'ready',
    buildStatus: 'ready' as const,
    workspaceId: 'soc_ui',
    workspaceTitle: 'SOC 工作区',
    workspaceRoute: '/contracts/webui/workspaces/soc_ui',
  };
}

function makeSceneWorkspace(id: string, title: string, pages: Array<{ id: string; title: string }>) {
  return {
    id,
    title,
    titleEn: title,
    route: `/contracts/webui/workspaces/${id}`,
    icon: 'ShieldCheck',
    order: 20,
    enabled: true,
    placement: 'sceneWorkspace',
    defaultPageId: pages[0].id,
    sections: [],
    pages: pages.map((page, index) => ({
      id: page.id,
      title: page.title,
      route: `/contracts/webui/${page.id}`,
      icon: 'LayoutDashboard',
      order: 10 + index,
      enabled: true,
      placement: 'home.after',
      buildHash: 'ready',
      buildStatus: 'ready' as const,
      workspaceId: id,
      workspaceTitle: title,
      workspaceRoute: `/contracts/webui/workspaces/${id}`,
    })),
  };
}

function mockSocWorkspaceNav() {
  const socPages = [
    makeSocPage('soc-dashboard', '态势', 'Activity', 10),
    makeSocPage('soc-overview', 'SOC 总览', 'ShieldCheck', 15),
    makeSocPage('soc-alerts', '告警调查', 'AlertTriangle', 20),
  ];
  useWebUIContractPages.mockReturnValue({
    pages: socPages,
    workspaces: [
      {
        id: 'soc_ui',
        title: 'SOC 工作区',
        titleEn: 'SOC Workspace',
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
            pageIds: ['soc-dashboard'],
            defaultPageId: 'soc-dashboard',
            contentPadding: 'none',
            themeOverride: 'dark',
          },
          {
            id: 'operations',
            label: '告警运营',
            pageIds: ['soc-overview', 'soc-alerts'],
            defaultPageId: 'soc-overview',
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
  return socPages;
}

function partitionNames(): string[] {
  return within(screen.getByRole('tablist', { name: 'partitions' }))
    .getAllByRole('tab')
    .map((tab) => tab.textContent ?? '');
}

function activePartitionName(): string {
  return within(screen.getByRole('tablist', { name: 'partitions' }))
    .getAllByRole('tab')
    .find((tab) => tab.getAttribute('aria-selected') === 'true')?.textContent ?? '';
}

/** The first scene group of the scene partition; these read its entries. */
function sceneMenuSection(): HTMLElement {
  const nav = document.querySelector('aside nav') as HTMLElement;
  const section = nav.querySelector('div.mb-6');
  if (!section) throw new Error('scene menu not rendered');
  return section as HTMLElement;
}

function sceneMenuLinks(): string[] {
  return Array.from(sceneMenuSection().querySelectorAll('a')).map((link) => link.textContent ?? '');
}

function sceneMenuHrefs(): (string | null)[] {
  return Array.from(sceneMenuSection().querySelectorAll('a')).map((link) => link.getAttribute('href'));
}

function sectionHeadings(container: HTMLElement): string[] {
  const nav = container.querySelector('aside nav') as HTMLElement;
  return Array.from(nav.querySelectorAll('h3')).map((element) => element.textContent ?? '');
}

function navPageOrder(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-nav-page-id]')).map((element) => element.getAttribute('data-nav-page-id') ?? '');
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
    vi.mocked(confirmTokenPolicyDisplay).mockResolvedValue(true);
    vi.mocked(getTokenPolicyStatus).mockResolvedValue({ state: 'active', waiting_for_display: false, lease_expires_at: null, notice: null, server_now: new Date().toISOString(), next_check_at: null });
    vi.mocked(claimTokenPolicy).mockResolvedValue({ state: 'active', waiting_for_display: false, lease_expires_at: null, notice: null, server_now: new Date().toISOString(), next_check_at: null });
    localStorage.clear();
    productNameContextValue.productName = 'Flocks';
    productNameContextValue.proProductName = 'Flocks Pro';
    productNameContextValue.configuredDisplayName = null;

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
    onboardingAPI.getStatus.mockResolvedValue({
      data: makeOnboardingStatus(),
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
          makeProvider('deepseek', 'DeepSeek', [{ id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' }]),
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

  it.each(['gotIt', 'close'])('shows the policy before a pending update, then resumes the update on %s', async (action) => {
    const policyStatus = {
      state: 'active' as const, waiting_for_display: false, lease_expires_at: new Date(Date.now() + 15000).toISOString(),
      notice: { id: 'policy', occurrence_id: 'a'.repeat(64), expires_at: new Date(Date.now() + 86400000).toISOString() },
      server_now: new Date().toISOString(),
      next_check_at: null,
    };
    const pending = deferred<typeof policyStatus>();
    vi.mocked(getTokenPolicyStatus).mockReturnValueOnce(pending.promise);
    vi.mocked(claimTokenPolicy).mockResolvedValueOnce(policyStatus);
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '0.2.0', latest_version: '0.3.0', error: null });
    renderHomeWithLayout();
    await waitFor(() => expect(checkUpdate).toHaveBeenCalled());
    expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
    await act(async () => pending.resolve(policyStatus));
    expect(await screen.findByRole('dialog', { name: 'tokenPolicyTitle' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: action }));
    expect(await screen.findByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
    expect(ackNotification).not.toHaveBeenCalled();
    expect(localStorage.getItem(UPDATE_DISMISSED_KEY)).toBeNull();
  });

  it('does not replace an already presented upgrade when a policy becomes due', async () => {
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '0.2.0', latest_version: '0.3.0', error: null });
    renderHomeWithLayout();
    const update = await screen.findByRole('dialog', { name: 'update-modal' });
    const policyStatus = {
      state: 'active' as const, waiting_for_display: false, lease_expires_at: new Date(Date.now() + 15000).toISOString(),
      notice: { id: 'policy', occurrence_id: 'a'.repeat(64), expires_at: new Date(Date.now() + 86400000).toISOString() },
      server_now: new Date().toISOString(), next_check_at: null,
    };
    vi.mocked(getTokenPolicyStatus).mockResolvedValue(policyStatus);
    vi.mocked(claimTokenPolicy).mockResolvedValue(policyStatus);
    fireEvent.focus(window);
    await flushEffects();
    expect(claimTokenPolicy).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'update-modal' })).toBe(update);
    const props = updateModalMock.mock.calls.at(-1)![0] as unknown as { onClose: () => void };
    act(() => props.onClose());
    expect(await screen.findByRole('dialog', { name: 'tokenPolicyTitle' })).toBeInTheDocument();
  });

  it('keeps background upgrades pending and shows policy first on return', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const policyStatus = {
      state: 'active' as const, waiting_for_display: false, lease_expires_at: new Date(Date.now() + 15000).toISOString(),
      notice: { id: 'policy', occurrence_id: 'a'.repeat(64), expires_at: new Date(Date.now() + 86400000).toISOString() },
      server_now: new Date().toISOString(), next_check_at: null,
    };
    vi.mocked(getTokenPolicyStatus).mockResolvedValue(policyStatus);
    vi.mocked(claimTokenPolicy).mockResolvedValue(policyStatus);
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '0.2.0', latest_version: '0.3.0', error: null });
    try {
      renderHomeWithLayout();
      await waitFor(() => expect(checkUpdate).toHaveBeenCalled());
      expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
      expect(getTokenPolicyStatus).not.toHaveBeenCalled();
      visibility.mockReturnValue('visible');
      fireEvent(document, new Event('visibilitychange'));
      expect(await screen.findByRole('dialog', { name: 'tokenPolicyTitle' })).toBeInTheDocument();
      expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole('button', { name: 'gotIt' }));
      expect(await screen.findByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
    } finally {
      visibility.mockRestore();
    }
  });

  it('waits for an abandoned reservation to expire before allowing an upgrade', async () => {
    const policyStatus = {
      state: 'active' as const, waiting_for_display: false, lease_expires_at: new Date(Date.now() + 15000).toISOString(),
      notice: { id: 'policy', occurrence_id: 'a'.repeat(64), expires_at: new Date(Date.now() + 86400000).toISOString() },
      server_now: new Date().toISOString(), next_check_at: null,
    };
    vi.mocked(getTokenPolicyStatus).mockResolvedValueOnce({
      ...policyStatus, notice: null, lease_expires_at: null, waiting_for_display: true,
      next_check_at: new Date(Date.now() + 600).toISOString(),
    }).mockResolvedValue(policyStatus);
    vi.mocked(claimTokenPolicy).mockResolvedValue(policyStatus);
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '0.2.0', latest_version: '0.3.0', error: null });
    renderHomeWithLayout();
    await waitFor(() => expect(checkUpdate).toHaveBeenCalled());
    expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
    expect(await screen.findByRole('dialog', { name: 'tokenPolicyTitle' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'update-modal' })).not.toBeInTheDocument();
  });

  it('still displays an upgrade when the policy status request fails', async () => {
    vi.mocked(getTokenPolicyStatus).mockRejectedValue(new Error('timeout'));
    checkUpdate.mockResolvedValue({ has_update: true, current_version: '0.2.0', latest_version: '0.3.0', error: null });
    renderHomeWithLayout();
    expect(await screen.findByRole('dialog', { name: 'update-modal' })).toBeInTheDocument();
  });

  it('opens onboarding from the home entry and shows configured details for an existing default model', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    renderHomeWithLayout();

    await user.click(screen.getByRole('button', { name: 'getStarted' }));

    await screen.findByText('onboarding.bootstrap.primaryConfiguredSummary');

    expect(screen.getByText('onboarding.bootstrap.configuredDetailsTitle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.editPrimary' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('onboarding.bootstrap.modelKeyPlaceholder')).not.toBeInTheDocument();
  });

  it('allows member users to open onboarding from the home entry', async () => {
    const user = userEvent.setup();
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

    renderHomeWithLayout();
    await user.click(screen.getByRole('button', { name: 'getStarted' }));

    expect(await screen.findByText('onboarding.bootstrap.modelPageTitle')).toBeInTheDocument();
  });

  it('auto-opens onboarding from backend status even when the old dismissed flag exists', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    onboardingAPI.getStatus.mockResolvedValue({
      data: makeOnboardingStatus({
        completed: false,
        has_default_model: false,
        default_model: null,
      }),
    });

    renderHomeWithLayout();

    expect(await screen.findByText('onboarding.bootstrap.modelPageTitle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'onboarding.bootstrap.savePrimary' })).toBeInTheDocument();
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
    expect(screen.queryByRole('link', { name: 'Flocks Pro' })).not.toBeInTheDocument();
    expect(checkUpdate).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(getActiveNotifications).toHaveBeenCalledWith('zh-CN');
    });

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

    expect(screen.getByRole('link', { name: 'Flocks Pro' })).toHaveAttribute('href', '/settings/flockspro');
    expect(screen.getByRole('button', { name: 'checkUpdate' })).toBeInTheDocument();
    // 系统设置是顶栏分区，账号菜单里不再重复给一个入口。
    expect(screen.queryByRole('link', { name: 'settings' })).not.toBeInTheDocument();

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

  it('adapts the sidebar for a custom long workbench name and hides the product update mark', async () => {
    const customName = '超长的威胁研判自动化工作台名称用于验证左侧菜单栏不会溢出';
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    productNameContextValue.productName = customName;
    productNameContextValue.proProductName = customName;
    productNameContextValue.configuredDisplayName = customName;
    checkUpdate.mockResolvedValue({
      has_update: true,
      latest_version: '2026.04.29',
      current_version: '2026.04.28',
      release_notes: 'Release line',
      release_url: 'https://example.com/release',
      error: null,
    });

    const { container } = renderHomeWithLayout();

    const aside = container.querySelector('aside') as HTMLElement | null;
    expect(aside).not.toBeNull();
    await waitFor(() => {
      expect(Number.parseInt(aside!.style.width, 10)).toBeGreaterThan(208);
    });

    const sidebarShell = container.querySelector('aside > div');
    const logoRow = sidebarShell?.firstElementChild as HTMLElement | null;
    expect(logoRow).not.toBeNull();
    const productLabel = within(logoRow!).getByText(customName);
    expect(productLabel.className).toContain('[overflow-wrap:anywhere]');
    expect(screen.queryByText('newVersion')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'hasNewVersion v2026.04.29' })).not.toBeInTheDocument();
  });

  it('resizes and persists the expanded desktop sidebar width', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');

    const { container } = renderHomeWithLayout();

    const aside = container.querySelector('aside') as HTMLElement | null;
    expect(aside).not.toBeNull();
    const resizeHandle = await screen.findByRole('separator', { name: 'resizeNav' });

    fireEvent.pointerDown(resizeHandle, { pointerId: 1, clientX: 208 });
    await act(async () => {
      const pointerMove = new Event('pointermove') as PointerEvent;
      Object.defineProperty(pointerMove, 'clientX', { value: 320 });
      window.dispatchEvent(pointerMove);
    });
    await act(async () => {
      window.dispatchEvent(new Event('pointerup'));
    });

    expect(aside).toHaveStyle({ width: '320px' });
    expect(localStorage.getItem('flocks_layout_sidebar_width')).toBe('320');
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

    expect(screen.getByRole('link', { name: 'Flocks Pro' })).toHaveAttribute('href', '/settings/flockspro');
    const updateEntry = screen.getByRole('button', { name: 'checkUpdate' });
    // 系统设置是顶栏分区，账号菜单里不再重复给一个入口。
    expect(screen.queryByRole('link', { name: 'settings' })).not.toBeInTheDocument();
    // D02: 检查更新与退出登录都留在左下角，检查更新在前。
    const logoutEntry = screen.getByRole('button', { name: 'logout' });
    expect(updateEntry.compareDocumentPosition(logoutEntry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // D02: the usage portal moved into the system settings menu.
    expect(screen.queryByRole('link', { name: 'flocksLlmUsageQuota' })).not.toBeInTheDocument();

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
    expect(updateModalMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        forceInitialCheck: true,
      }),
    );
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
    expect(screen.queryByRole('link', { name: 'settings' })).not.toBeInTheDocument();
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

  it('loads backend notifications without waiting for the update check', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    const updateCheck = deferred<{
      has_update: boolean;
      latest_version: null;
      current_version: string;
      error: null;
    }>();
    checkUpdate.mockReturnValue(updateCheck.promise);

    renderHomeWithLayout();

    await waitFor(() => {
      expect(getActiveNotifications).toHaveBeenCalledWith('zh-CN');
    });
    expect(getNotificationAckStatus).not.toHaveBeenCalled();

    await act(async () => {
      updateCheck.resolve({
        has_update: false,
        latest_version: null,
        current_version: '0.2.0',
        error: null,
      });
    });
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
    onboardingAPI.getStatus.mockResolvedValue({ data: makeOnboardingStatus() });
    useWebUIContractPages.mockReturnValue({
      pages: [defaultCustomPage],
      workspaces: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it('shows the three fixed partitions and swaps the sidebar menu when switching', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const { container } = renderHomeWithLayout();

    await screen.findByRole('button', { name: 'aiWorkbench' });
    expect(partitionNames()).toEqual(['partitionAgent', 'partitionScene', 'partitionSettings']);
    expect(activePartitionName()).toBe('partitionAgent');
    expect(sectionHeadings(container)).toEqual(['aiWorkbench', 'agentHub']);
    expect(screen.getByRole('link', { name: 'flocksHome' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('link', { name: 'sessions' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'SOC 工作区' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'settingsPreferences' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    // The scene suite is the first level, its pages the second.
    await waitFor(() => expect(sceneMenuLinks()).toEqual(['态势', 'SOC 总览', '告警调查']));
    expect(sectionHeadings(container)).toEqual(['SOC 工作区']);
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-expanded', 'true');
    expect(activePartitionName()).toBe('partitionScene');
    expect(screen.queryByRole('link', { name: 'sessions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'deviceIntegration' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '告警调查' })).toBeInTheDocument();
    // The suite manager is a top-bar entry, not a sidebar menu item.
    expect(within(screen.getByRole('tablist', { name: 'partitions' }).parentElement as HTMLElement)
      .getByRole('link', { name: 'sceneSuiteManager' })).toHaveAttribute('href', '/scenes/suites');
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-dashboard'));

    await user.click(screen.getByRole('tab', { name: 'partitionSettings' }));
    await waitFor(() => expect(sectionHeadings(container)).toEqual([
      'settingsGroupPreferences',
      'settingsGroupData',
      'settingsGroupSystem',
    ]));
    // D02: the LLM usage portal moved into the system settings menu.
    expect(screen.getByRole('link', { name: 'flocksLlmUsageQuota' }))
      .toHaveAttribute('href', 'https://portal.agentflocks.com');
    expect(screen.getByRole('link', { name: 'settingsPreferences' })).toHaveAttribute('href', '/settings/preferences');
    expect(screen.getByRole('link', { name: 'accountManagement' })).toHaveAttribute('href', '/settings/account');
    expect(screen.queryByRole('link', { name: '告警调查' })).not.toBeInTheDocument();
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/settings/preferences'));
  });

  it('returns each partition to the page it was left on', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    renderLayoutAt('/sessions');
    await screen.findByRole('button', { name: 'aiWorkbench' });

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-dashboard'));

    await user.click(screen.getByRole('link', { name: '告警调查' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-alerts'));

    await user.click(screen.getByRole('tab', { name: 'partitionAgent' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/sessions'));
    expect(activePartitionName()).toBe('partitionAgent');

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-alerts'));
    expect(JSON.parse(localStorage.getItem('flocks_layout_partition_paths') ?? '{}')).toMatchObject({
      agent: '/sessions',
      scene: '/contracts/webui/workspaces/soc_ui/soc-alerts',
    });
  });

  it('follows the route into the partition that owns it', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    renderLayoutAt('/sessions');
    await screen.findByRole('button', { name: 'aiWorkbench' });
    expect(activePartitionName()).toBe('partitionAgent');

    await user.click(screen.getByRole('link', { name: 'go-soc-alerts' }));
    await waitFor(() => expect(activePartitionName()).toBe('partitionScene'));
    expect(screen.getByRole('link', { name: '告警调查' })).toHaveClass('bg-white');

    await user.click(screen.getByRole('link', { name: 'go-sessions' }));
    await waitFor(() => expect(activePartitionName()).toBe('partitionAgent'));
    expect(screen.getByRole('button', { name: 'aiWorkbench' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('keeps the suite manager reachable when no scene workspace is installed', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    useWebUIContractPages.mockReturnValue({
      pages: [],
      workspaces: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderHomeWithLayout();
    await screen.findByRole('button', { name: 'aiWorkbench' });

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    expect(await screen.findByRole('link', { name: 'sceneSuiteManager' }))
      .toHaveAttribute('href', '/scenes/suites');
    expect(screen.queryByRole('button', { name: 'SOC 工作区' })).not.toBeInTheDocument();
    // Agent and settings stay usable while no scene workspace exists.
    await user.click(screen.getByRole('tab', { name: 'partitionAgent' }));
    expect(await screen.findByRole('link', { name: 'sessions' })).toBeInTheDocument();
  });

  it('renders custom WebUI contract page links in the scene partition', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    renderHomeWithLayout();

    await screen.findByRole('button', { name: 'aiWorkbench' });
    expect(screen.queryByRole('link', { name: '自定义仪表盘' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    expect(await screen.findByRole('link', { name: '自定义仪表盘' })).toHaveAttribute(
      'href',
      '/contracts/webui/dash-1',
    );
  });

  it('does not render WebUI contract page links until their build is ready', async () => {
    const user = userEvent.setup();
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
    await screen.findByRole('button', { name: 'aiWorkbench' });
    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));

    expect(await screen.findByRole('link', { name: '可用页面' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '失败页面' })).not.toBeInTheDocument();
  });

  it('keeps the accordion of each partition independent', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    renderHomeWithLayout();

    const aiWorkbenchToggle = await screen.findByRole('button', { name: 'aiWorkbench' });
    const agentHubToggle = screen.getByRole('button', { name: 'agentHub' });
    expect(screen.queryByRole('button', { name: 'sceneWorkspaces' })).not.toBeInTheDocument();
    expect(aiWorkbenchToggle).toHaveAttribute('aria-expanded', 'true');
    expect(agentHubToggle).toHaveAttribute('aria-expanded', 'true');

    await user.click(agentHubToggle);
    expect(agentHubToggle).toHaveAttribute('aria-expanded', 'false');
    expect(localStorage.getItem('flocks_layout_collapsed_nav_sections')).toBe(JSON.stringify(['agentHub']));
    expect(screen.queryByRole('link', { name: 'agents' })).not.toBeInTheDocument();

    // The scene partition has its own accordion: the SOC group opens on its own.
    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    expect(await screen.findByRole('link', { name: '告警调查' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-expanded', 'true');
    expect(sceneMenuLinks()).toEqual(['态势', 'SOC 总览', '告警调查']);
  });

  it('restores the expanded primary group and collapsed agent studio after refresh', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    localStorage.setItem('flocks_layout_expanded_primary_nav_section', 'workspace:soc_ui');
    localStorage.setItem('flocks_layout_collapsed_nav_sections', JSON.stringify(['agentHub']));
    mockSocWorkspaceNav();

    renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');

    expect(await screen.findByRole('link', { name: '告警调查' })).toBeInTheDocument();
    expect(activePartitionName()).toBe('partitionScene');
  });

  it('renders the SOC workspace in the scene partition and keeps device integration in the agent studio', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const { container } = renderHomeWithLayout();

    await screen.findByRole('button', { name: 'aiWorkbench' });
    const agentSection = screen.getByRole('button', { name: 'agentHub' }).closest('div.mb-6') as HTMLElement;
    expect(Array.from(agentSection.querySelectorAll('a')).map((link) => link.getAttribute('href'))).toEqual([
      '/agents',
      '/skills',
      '/tools',
      '/devices',
      '/hub',
      '/models',
      '/channels',
    ]);
    expect(screen.getByRole('link', { name: 'deviceIntegration' })).toHaveAttribute('href', '/devices');

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    await screen.findByRole('link', { name: 'SOC 总览' });
    // The workspace title is the first-level group; its sections (态势 /
    // 告警运营) do not become headings, the pages sit flat under the scene.
    expect(sectionHeadings(container)).toEqual(['SOC 工作区']);
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByRole('button', { name: '告警运营' })).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'workspace.sectionNavigation' })).not.toBeInTheDocument();
    expect(sceneMenuHrefs()).toEqual([
      '/contracts/webui/workspaces/soc_ui/soc-dashboard',
      '/contracts/webui/workspaces/soc_ui/soc-overview',
      '/contracts/webui/workspaces/soc_ui/soc-alerts',
    ]);
    // Device integration is not duplicated into the scene partition.
    expect(screen.queryByRole('link', { name: 'deviceIntegration' })).not.toBeInTheDocument();
  });

  it('reorders SOC workspace pages by dragging and keeps the order after refresh', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const first = renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');
    await screen.findByRole('link', { name: '告警调查' });
    expect(navPageOrder(first.container)).toEqual(['soc-dashboard', 'soc-overview', 'soc-alerts']);

    const itemFor = (pageId: string) => first.container.querySelector(`[data-nav-page-id="${pageId}"]`) as HTMLElement;
    // The menu is one flat list, so a page can move anywhere in it.
    fireEvent.dragStart(itemFor('soc-alerts'));
    fireEvent.dragOver(itemFor('soc-dashboard'));
    expect(itemFor('soc-dashboard').className).toContain('ring-2');
    fireEvent.drop(itemFor('soc-dashboard'));
    fireEvent.dragEnd(itemFor('soc-alerts'));

    expect(JSON.parse(localStorage.getItem('flocks_layout_workspace_page_order:soc_ui') ?? '[]')).toEqual([
      'soc-alerts',
      'soc-dashboard',
      'soc-overview',
    ]);
    expect(navPageOrder(first.container)).toEqual(['soc-alerts', 'soc-dashboard', 'soc-overview']);
    expect(itemFor('soc-dashboard').className).not.toContain('ring-2');

    first.unmount();
    const second = renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');
    await screen.findByRole('link', { name: '告警调查' });
    expect(navPageOrder(second.container)).toEqual(['soc-alerts', 'soc-dashboard', 'soc-overview']);
    expect(user).toBeDefined();
  });

  it('moves a SOC workspace page with Alt+Arrow keys', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const { container } = renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');
    const alertsLink = await screen.findByRole('link', { name: '告警调查' });

    fireEvent.keyDown(alertsLink, { key: 'ArrowUp', altKey: true });
    expect(navPageOrder(container)).toEqual(['soc-dashboard', 'soc-alerts', 'soc-overview']);

    fireEvent.keyDown(screen.getByRole('link', { name: '告警调查' }), { key: 'ArrowUp', altKey: true });
    expect(navPageOrder(container)).toEqual(['soc-alerts', 'soc-dashboard', 'soc-overview']);

    // Without Alt the arrow keys are left alone and nothing moves.
    fireEvent.keyDown(screen.getByRole('link', { name: '告警调查' }), { key: 'ArrowDown' });
    expect(navPageOrder(container)).toEqual(['soc-alerts', 'soc-dashboard', 'soc-overview']);
  });

  it('starts a SOC-scoped custom page session from the SOC workspace menu', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    sessionApi.create.mockResolvedValueOnce({ id: 'session-soc-custom-page' });
    mockSocWorkspaceNav();

    renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');

    await screen.findByRole('link', { name: '态势' });
    const socSection = sceneMenuSection();
    expect(within(socSection).getByRole('link', { name: '态势' })).toHaveAttribute(
      'href',
      '/contracts/webui/workspaces/soc_ui/soc-dashboard',
    );

    await user.click(within(socSection).getByRole('button', { name: 'workspace.customPage' }));

    await waitFor(() => {
      expect(sessionApi.create).toHaveBeenCalledWith({ title: 'workspace.customPageSessionTitle' });
    });
    await waitFor(() => expect(activeProbe()).toHaveTextContent(
      `/sessions?session=session-soc-custom-page&message=${encodeURIComponent('workspace.socCustomPageInitialMessage')}&display=${encodeURIComponent('workspace.socCustomPageDisplayLabel')}`,
    ));
  });

  it('hides the SOC custom page action from members', async () => {
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
    mockSocWorkspaceNav();

    renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');

    await screen.findByRole('link', { name: '态势' });
    const socSection = sceneMenuSection();
    expect(within(socSection).queryByRole('button', { name: 'workspace.customPage' })).not.toBeInTheDocument();
    expect(within(socSection).getByRole('button', { name: 'workspace.customTitle' })).toBeInTheDocument();
  });

  it('customizes the SOC dashboard title from the SOC workspace menu', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    const titleChanged = vi.fn();
    window.addEventListener('soc-dashboard:title-changed', titleChanged);
    mockSocWorkspaceNav();

    try {
      renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');

      await screen.findByRole('link', { name: '态势' });
      const socSection = sceneMenuSection();
      await user.click(within(socSection).getByRole('button', { name: 'workspace.customTitle' }));

      const input = screen.getByLabelText('workspace.customTitle');
      await user.clear(input);
      await user.type(input, '自定义 SOC 态势中心');
      await user.click(screen.getByRole('button', { name: 'workspace.customTitleSave' }));

      expect(localStorage.getItem('soc-dashboard-custom-title-v1')).toBe('自定义 SOC 态势中心');
      expect(titleChanged).toHaveBeenCalledTimes(1);
      expect(titleChanged.mock.calls[0][0]).toMatchObject({
        detail: { title: '自定义 SOC 态势中心' },
      });
    } finally {
      window.removeEventListener('soc-dashboard:title-changed', titleChanged);
    }
  });

  it('returns a menu entry to the page it was last left on', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    renderLayoutAt('/workflows/wf-1');

    await screen.findByRole('button', { name: 'aiWorkbench' });
    expect(screen.getByRole('link', { name: 'workflows' })).toHaveAttribute('href', '/workflows/wf-1');

    await user.click(screen.getByRole('link', { name: 'go-sessions' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/sessions'));

    await user.click(screen.getByRole('link', { name: 'workflows' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/workflows/wf-1'));
    expect(JSON.parse(localStorage.getItem('flocks_layout_open_tabs') ?? '[]')).toEqual([
      { href: '/workflows', path: '/workflows/wf-1' },
      { href: '/sessions', path: '/sessions' },
    ]);
  });

  it('restores visited panes after refresh and drops entries that no longer exist', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    localStorage.setItem('flocks_layout_open_tabs', JSON.stringify([
      { href: '/workflows', path: '/workflows/wf-9' },
      { href: '/contracts/webui/workspaces/soc_ui/soc-alerts', path: '/contracts/webui/workspaces/soc_ui/soc-alerts' },
      { href: '/contracts/webui/workspaces/old_ws/gone', path: '/contracts/webui/workspaces/old_ws/gone' },
    ]));
    mockSocWorkspaceNav();

    renderLayoutAt('/sessions');

    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'aiWorkbench' });
    // A stored entry keeps the path it was left on.
    expect(screen.getByRole('link', { name: 'workflows' })).toHaveAttribute('href', '/workflows/wf-9');

    await user.click(screen.getByRole('tab', { name: 'partitionScene' }));
    await screen.findByRole('link', { name: '态势' });
    // The entry of a workspace that no longer exists is simply not there.
    expect(screen.queryByRole('link', { name: /old_ws/ })).not.toBeInTheDocument();
  });

  it('reorders AI workbench entries by dragging and keeps the order after refresh', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const first = renderHomeWithLayout();
    await screen.findByRole('button', { name: 'aiWorkbench' });
    const keysIn = (container: HTMLElement, sectionName: string) => {
      const section = within(container).getByRole('button', { name: sectionName }).closest('div.mb-6') as HTMLElement;
      return Array.from(section.querySelectorAll('[data-nav-item-key]')).map((element) => element.getAttribute('data-nav-item-key'));
    };
    expect(keysIn(first.container, 'aiWorkbench')).toEqual(['/', '/sessions', '/workspace', '/tasks', '/workflows']);
    expect(keysIn(first.container, 'agentHub')).toEqual(['/agents', '/skills', '/tools', '/devices', '/hub', '/models', '/channels']);

    const itemFor = (key: string) => first.container.querySelector(`[data-nav-item-key="${key}"]`) as HTMLElement;
    fireEvent.dragStart(itemFor('/workflows'));
    fireEvent.dragOver(itemFor('/sessions'));
    fireEvent.drop(itemFor('/sessions'));
    fireEvent.dragEnd(itemFor('/workflows'));

    expect(keysIn(first.container, 'aiWorkbench')).toEqual(['/', '/workflows', '/sessions', '/workspace', '/tasks']);
    expect(JSON.parse(localStorage.getItem('flocks_layout_nav_item_order:aiWorkbench') ?? '[]'))
      .toEqual(['/', '/workflows', '/sessions', '/workspace', '/tasks']);
    // Other groups are untouched.
    expect(keysIn(first.container, 'agentHub')).toEqual(['/agents', '/skills', '/tools', '/devices', '/hub', '/models', '/channels']);

    // Keyboard works for built-in entries as well.
    fireEvent.keyDown(within(first.container).getByRole('link', { name: 'tasks' }), { key: 'ArrowUp', altKey: true });
    expect(keysIn(first.container, 'aiWorkbench')).toEqual(['/', '/workflows', '/sessions', '/tasks', '/workspace']);

    first.unmount();
    const second = renderHomeWithLayout();
    await screen.findByRole('button', { name: 'aiWorkbench' });
    expect(keysIn(second.container, 'aiWorkbench')).toEqual(['/', '/workflows', '/sessions', '/tasks', '/workspace']);
  });

  it('gives every scene suite its own first-level group and opens one scene at a time', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    const socPages = mockSocWorkspaceNav();
    const previous = useWebUIContractPages();
    const codeAudit = makeSceneWorkspace('code_audit_ui', '代码审计', [
      { id: 'code-audit-overview', title: '审计总览' },
      { id: 'code-audit-findings', title: '缺陷清单' },
    ]);
    useWebUIContractPages.mockReturnValue({
      pages: [...socPages, ...codeAudit.pages],
      workspaces: [...previous.workspaces, codeAudit],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    const { container } = renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');
    await screen.findByRole('link', { name: '告警调查' });

    // One heading per scene; the scene owning the route is open, the other closed.
    expect(sectionHeadings(container)).toEqual(['SOC 工作区', '代码审计']);
    const socToggle = screen.getByRole('button', { name: 'SOC 工作区' });
    const auditToggle = screen.getByRole('button', { name: '代码审计' });
    expect(socToggle).toHaveAttribute('aria-expanded', 'true');
    expect(auditToggle).toHaveAttribute('aria-expanded', 'false');
    expect(sceneMenuLinks()).toEqual(['态势', 'SOC 总览', '告警调查']);
    expect(screen.queryByRole('link', { name: '审计总览' })).not.toBeInTheDocument();
    // Workspace actions belong to the SOC group only.
    expect(within(sceneMenuSection()).getByRole('button', { name: 'workspace.customPage' })).toBeInTheDocument();
    // No switcher in the top bar any more: the groups are the switcher.
    expect(screen.queryByRole('tablist', { name: 'scenes' })).not.toBeInTheDocument();

    // Opening the other scene closes SOC (accordion) and shows only that scene's pages.
    await user.click(auditToggle);
    expect(auditToggle).toHaveAttribute('aria-expanded', 'true');
    expect(socToggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('link', { name: '审计总览' })).toHaveAttribute('href', '/contracts/webui/workspaces/code_audit_ui/code-audit-overview');
    expect(screen.queryByRole('link', { name: '告警调查' })).not.toBeInTheDocument();
    const auditSection = auditToggle.closest('div.mb-6') as HTMLElement;
    expect(within(auditSection).queryByRole('button', { name: 'workspace.customPage' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('link', { name: '缺陷清单' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/code_audit_ui/code-audit-findings'));
    expect(auditToggle).toHaveAttribute('aria-expanded', 'true');
    expect(localStorage.getItem('flocks_layout_expanded_primary_nav_section')).toBe('workspace:code_audit_ui');

    // Coming back through the SOC group works the same way.
    await user.click(socToggle);
    expect(socToggle).toHaveAttribute('aria-expanded', 'true');
    expect(auditToggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(screen.getByRole('link', { name: 'SOC 总览' }));
    await waitFor(() => expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-overview'));
  });

  it('opens the scene group that owns a directly opened page', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    const socPages = mockSocWorkspaceNav();
    const previous = useWebUIContractPages();
    const redteam = makeSceneWorkspace('ai_redteam_ui', 'AI 红队', [
      { id: 'ai-redteam-overview', title: '演练总览' },
      { id: 'ai-redteam-surface', title: '攻击面' },
    ]);
    useWebUIContractPages.mockReturnValue({
      pages: [...socPages, ...redteam.pages],
      workspaces: [...previous.workspaces, redteam],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderLayoutAt('/contracts/webui/workspaces/ai_redteam_ui/ai-redteam-surface');
    await screen.findByRole('link', { name: '攻击面' });

    expect(screen.getByRole('button', { name: 'AI 红队' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'SOC 工作区' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('link', { name: '告警调查' })).not.toBeInTheDocument();
    expect(activePartitionName()).toBe('partitionScene');
  });

  it('keeps the single installed scene under its own first-level heading', async () => {
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    const { container } = renderLayoutAt('/contracts/webui/workspaces/soc_ui/soc-overview');
    await screen.findByRole('link', { name: '告警调查' });

    expect(sectionHeadings(container)).toEqual(['SOC 工作区']);
    expect(screen.queryByRole('tablist', { name: 'scenes' })).not.toBeInTheDocument();
    expect(sceneMenuLinks()).toEqual(['态势', 'SOC 总览', '告警调查']);
  });

  it('keeps a page mounted with its state across a partition switch', async () => {
    const user = userEvent.setup();
    localStorage.setItem('flocks_onboarding_dismissed', 'true');
    mockSocWorkspaceNav();

    renderLayoutAt('/sessions');
    await screen.findByRole('button', { name: 'aiWorkbench' });

    await user.click(screen.getByRole('button', { name: 'count-up' }));
    await user.click(screen.getByRole('button', { name: 'count-up' }));
    expect(within(activeProbe().parentElement as HTMLElement).getByTestId('probe-count')).toHaveTextContent('2');

    await user.click(screen.getByRole('link', { name: 'go-soc-alerts' }));
    await waitFor(() => {
      expect(activeProbe()).toHaveTextContent('/contracts/webui/workspaces/soc_ui/soc-alerts');
    });
    // The sessions pane is still mounted, just hidden.
    const panes = document.querySelectorAll('[data-keep-alive-pane]');
    expect(Array.from(panes).map((pane) => pane.getAttribute('data-keep-alive-pane'))).toEqual(['inactive', 'active']);
    expect(panes[0]).toHaveAttribute('aria-hidden', 'true');
    expect(within(panes[0] as HTMLElement).getByTestId('probe-count')).toHaveTextContent('2');
    expect(within(activeProbe().parentElement as HTMLElement).getByTestId('probe-count')).toHaveTextContent('0');

    // Coming back through the top bar finds the page exactly as it was left.
    await user.click(screen.getByRole('tab', { name: 'partitionAgent' }));
    await waitFor(() => {
      expect(activeProbe()).toHaveTextContent('/sessions');
    });
    expect(within(activeProbe().parentElement as HTMLElement).getByTestId('probe-count')).toHaveTextContent('2');
  });
});
