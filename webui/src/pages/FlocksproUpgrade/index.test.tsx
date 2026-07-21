import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FlocksproUpgradePage from './index';

const { authApi, client, consoleUpgradeApi } = vi.hoisted(() => ({
  authApi: {
    consoleLoginSession: vi.fn(),
    finishConsoleLogin: vi.fn(),
    startConsoleLogin: vi.fn(),
    logoutConsoleLogin: vi.fn(),
  },
  client: {
    get: vi.fn(),
    post: vi.fn(),
  },
  consoleUpgradeApi: {
    listRequests: vi.fn(),
    getProPackageStatus: vi.fn(),
    refreshRequest: vi.fn(),
    createRequest: vi.fn(),
    cancelRequest: vi.fn(),
    startRequest: vi.fn(),
  },
}));

vi.mock('@/api/auth', () => ({
  authApi,
}));

vi.mock('@/api/client', () => ({
  default: client,
}));

vi.mock('@/api/consoleUpgrade', () => ({
  consoleUpgradeApi,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, string>) => {
      if (options?.defaultValue) {
        return options.defaultValue;
      }
      if (key === 'upgrade.installedTitle') {
        return `${options?.productName || ''} ${options?.version || ''}`;
      }
      if (key === 'upgrade.title') {
        return `Upgrade to ${options?.productName || ''}`;
      }
      return key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

describe('FlocksproUpgradePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();

    authApi.consoleLoginSession.mockResolvedValue({
      logged_in: true,
      account_name: 'console@example.com',
    });
    consoleUpgradeApi.listRequests.mockResolvedValue([
      {
        request_id: 'req-1',
        status: 'activated',
        license_id: 'lic-1',
        license_status: 'active',
        details: {
          license_id: 'lic-1',
          license_status: 'active',
          auto_install_version: 'v2026.6.18',
          flockspro_component_version: 'v2026.6.1',
        },
        created_at: '2026-06-29T00:00:00Z',
        updated_at: '2026-06-29T00:00:00Z',
      },
    ]);
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      runtime_importable: false,
      install_marker_present: true,
      installed_version: 'v2026.6.18',
      flockspro_component_version: 'v2026.6.1',
      pro_enabled: false,
      license_status: 'uninstalled',
      inactive_reason: 'flockspro_not_installed',
    });
    client.post.mockResolvedValue({ data: {} });
    client.get.mockResolvedValue({
      data: {
        activated: true,
        active: true,
        pro_enabled: true,
        license_id: 'lic-1',
        license_status: 'active',
      },
    });
  });

  it('keeps the install action visible when only the Pro marker exists', async () => {
    render(
      <MemoryRouter>
        <FlocksproUpgradePage />
      </MemoryRouter>,
    );

    await waitFor(() => expect(consoleUpgradeApi.getProPackageStatus).toHaveBeenCalled());
    expect(await screen.findByRole('button', { name: 'upgrade.startUpgrade' })).toBeInTheDocument();
  });

  it('uses the Pro product name when no custom display name is configured', async () => {
    consoleUpgradeApi.listRequests.mockResolvedValue([]);
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: false,
      runtime_importable: false,
      install_marker_present: false,
      pro_enabled: false,
      license_status: 'uninstalled',
    });

    render(
      <MemoryRouter>
        <FlocksproUpgradePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Upgrade to Flocks Pro')).toBeInTheDocument();
  });

  it('shows the installed bundle version instead of the Pro component version', async () => {
    consoleUpgradeApi.listRequests.mockResolvedValue([
      {
        request_id: 'req-1',
        status: 'activated',
        license_id: 'lic-1',
        license_status: 'active',
        details: {
          license_id: 'lic-1',
          license_status: 'active',
          auto_install_bundle_version: 'v2026.7.3.3',
          flockspro_component_version: 'pro-v2026.7.3.3',
        },
        created_at: '2026-07-03T00:00:00Z',
        updated_at: '2026-07-03T00:00:00Z',
      },
    ]);
    consoleUpgradeApi.getProPackageStatus.mockResolvedValue({
      installed: true,
      runtime_importable: true,
      install_marker_present: true,
      bundle_version: 'v2026.7.3.3',
      flockspro_component_version: 'pro-v2026.7.3.3',
      pro_enabled: true,
      license_status: 'active',
    });

    render(
      <MemoryRouter>
        <FlocksproUpgradePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Flocks Pro v2026.7.3.3')).toBeInTheDocument();
    expect(await screen.findByText('v2026.7.3.3')).toBeInTheDocument();
    expect(screen.queryByText('pro-v2026.7.3.3')).not.toBeInTheDocument();
  });
});
