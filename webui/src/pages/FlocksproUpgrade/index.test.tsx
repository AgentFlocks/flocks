import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FlocksproUpgradePage from './index';

const { authApi, client, consoleUpgradeApi, checkRestartReadiness } = vi.hoisted(() => ({
  checkRestartReadiness: vi.fn(),
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

vi.mock('@/utils/restartPolling', () => ({ checkRestartReadiness }));

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

  it('shows the restart steps and stops at the step the upgrade handoff reports as failed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      consoleUpgradeApi.startRequest.mockImplementation(async (_requestId: string, onProgress: (progress: unknown) => void) => {
        onProgress({ stage: 'restarting', message: 'Restarting service...', success: null, progress_token: 'tok-9' });
      });
      checkRestartReadiness.mockResolvedValue({
        ready: false,
        upgrade: {
          phase: 'failed',
          step: 'build_webui',
          failed_step: 'build_webui',
          error: 'npm run build exited with code 1',
          steps: [
            { id: 'stop', status: 'done' },
            { id: 'build_webui', status: 'failed' },
            { id: 'start', status: 'pending' },
          ],
        },
      });
      render(
        <MemoryRouter>
          <FlocksproUpgradePage />
        </MemoryRouter>,
      );

      fireEvent.click(await screen.findByRole('button', { name: 'upgrade.startUpgrade' }));
      await vi.advanceTimersByTimeAsync(1_500);

      expect(await screen.findByText('handoff.failed handoff.recoveryHint')).toBeInTheDocument();
      expect(checkRestartReadiness).toHaveBeenCalledWith('tok-9');
      const list = screen.getByRole('list', { name: 'handoff.title' });
      expect(within(list).getAllByRole('listitem').map((item) => item.getAttribute('data-status')))
        .toEqual(['done', 'failed', 'pending']);
      await vi.advanceTimersByTimeAsync(10_000);
      expect(checkRestartReadiness).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  const startRestartStage = async () => {
    consoleUpgradeApi.startRequest.mockImplementation(async (_requestId: string, onProgress: (progress: unknown) => void) => {
      onProgress({ stage: 'restarting', message: 'Restarting service...', success: null, progress_token: 'tok-9' });
    });
    render(
      <MemoryRouter>
        <FlocksproUpgradePage />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'upgrade.startUpgrade' }));
  };
  const buildingWebui = {
    phase: 'running',
    step: 'build_webui',
    steps: [
      { id: 'stop', status: 'done' },
      { id: 'build_webui', status: 'running' },
      { id: 'start', status: 'pending' },
    ],
  };

  it('keeps waiting while the upgrade handoff answers, even when one step outlasts the restart timeout', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const originalLocation = window.location;
    try {
      let polls = 0;
      checkRestartReadiness.mockImplementation(async () => {
        polls += 1;
        // every poll costs a minute; the WebUI build runs for 12 of them, then the service is up
        vi.setSystemTime(Date.now() + 60_000);
        return polls <= 12 ? { ready: false, upgrade: buildingWebui } : { ready: true };
      });
      const reload = vi.fn();
      Object.defineProperty(window, 'location', { configurable: true, value: { ...window.location, reload } });
      await startRestartStage();

      for (let poll = 0; poll < 12; poll += 1) {
        await vi.advanceTimersByTimeAsync(2_100);
      }
      expect(polls).toBeGreaterThanOrEqual(12);
      expect(screen.queryByText(/upgrade\.restartTimeout/)).not.toBeInTheDocument();
      expect(screen.getByText('upgrade.waitingRestart')).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(2_100);
      expect(reload).toHaveBeenCalled();
      expect(screen.queryByText(/upgrade\.restartTimeout/)).not.toBeInTheDocument();
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
      vi.useRealTimers();
    }
  });

  it('gives up after half an hour even while the upgrade handoff keeps answering', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      checkRestartReadiness.mockImplementation(async () => {
        vi.setSystemTime(Date.now() + 10 * 60_000);
        return { ready: false, reason: 'upgrade handoff running: build_webui', upgrade: buildingWebui };
      });
      await startRestartStage();

      for (let poll = 0; poll < 4; poll += 1) {
        await vi.advanceTimersByTimeAsync(2_100);
      }

      expect(await screen.findByText(/upgrade\.restartTimeout/)).toBeInTheDocument();
      expect(checkRestartReadiness).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});
