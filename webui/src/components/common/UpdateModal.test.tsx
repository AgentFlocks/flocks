import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateModal from './UpdateModal';

const { applyUpdate, checkUpdate } = vi.hoisted(() => ({
  applyUpdate: vi.fn(),
  checkUpdate: vi.fn(),
}));

vi.mock('@/api/update', () => ({
  applyUpdate,
  checkUpdate,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

const currentVersion = {
  current_version: '2026.07.10',
  latest_version: '2026.07.10',
  has_update: false,
  release_notes: null,
  release_url: null,
  error: null,
};

describe('UpdateModal update checks', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    checkUpdate.mockResolvedValue(currentVersion);
  });

  it('forces the initial request when Layout opens a manual check', async () => {
    render(
      <UpdateModal
        forceInitialCheck
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'flocks', true);
    });
  });

  it('forces explicit refreshes from the modal', async () => {
    const user = userEvent.setup();
    render(
      <UpdateModal
        initialInfo={currentVersion}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'checkUpdate' }));

    await waitFor(() => {
      expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'flocks', true);
    });
  });
});

describe('UpdateModal deploy-mode notices', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('explains the offline package upgrade path instead of the docker hint', async () => {
    const info = {
      ...currentVersion,
      latest_version: '2026.09.14',
      has_update: true,
      deploy_mode: 'offline' as const,
      update_allowed: false,
    };
    checkUpdate.mockResolvedValue(info);
    render(<UpdateModal initialInfo={info} onClose={vi.fn()} />);

    expect(await screen.findByText('offlineModeTitle')).toBeInTheDocument();
    expect(screen.getByText('offlineModeDesc')).toBeInTheDocument();
    expect(screen.getByText('offlineUpgradeHint')).toBeInTheDocument();
    expect(screen.queryByText('dockerModeTitle')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'confirmAction' })).not.toBeInTheDocument();
  });

  it('shows the offline package guidance even when the check failed or nothing is newer', async () => {
    // R9: offline machines usually cannot reach the update server at all; the .run path must
    // not hide behind has_update
    const failed = {
      ...currentVersion,
      latest_version: null,
      has_update: false,
      error: 'Failed to check for updates. Please check your network connection.',
      deploy_mode: 'offline' as const,
      update_allowed: false,
    };
    checkUpdate.mockResolvedValue(failed);
    const { unmount } = render(<UpdateModal onClose={vi.fn()} />);

    expect(await screen.findByText('offlineModeTitle')).toBeInTheDocument();
    expect(screen.getByText('offlineUpgradeHint')).toBeInTheDocument();
    expect(screen.getByText(failed.error)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'confirmAction' })).not.toBeInTheDocument();
    unmount();

    const upToDate = { ...currentVersion, deploy_mode: 'offline' as const, update_allowed: false };
    checkUpdate.mockResolvedValue(upToDate);
    const second = render(<UpdateModal initialInfo={upToDate} onClose={vi.fn()} />);
    expect(await screen.findByText('offlineModeTitle')).toBeInTheDocument();
    expect(screen.getByText('offlineUpgradeHint')).toBeInTheDocument();
    second.unmount();

    // an offline check reports the local version only: say so instead of claiming "up to date"
    const localOnly = { ...currentVersion, latest_version: null, deploy_mode: 'offline' as const, update_allowed: false };
    checkUpdate.mockResolvedValue(localOnly);
    render(<UpdateModal initialInfo={localOnly} onClose={vi.fn()} />);
    expect(await screen.findByText('offlineNotChecked')).toBeInTheDocument();
    expect(screen.queryByText('upToDate')).not.toBeInTheDocument();
  });

  it('keeps the docker hint for docker deployments', async () => {
    const info = {
      ...currentVersion,
      latest_version: '2026.09.14',
      has_update: true,
      deploy_mode: 'docker' as const,
      update_allowed: false,
    };
    checkUpdate.mockResolvedValue(info);
    render(<UpdateModal initialInfo={info} onClose={vi.fn()} />);

    expect(await screen.findByText('dockerModeTitle')).toBeInTheDocument();
    expect(screen.queryByText('offlineModeTitle')).not.toBeInTheDocument();
  });
});

describe('UpdateModal upgrade results', () => {
  const updatable = { ...currentVersion, latest_version: '2026.10.13', has_update: true };
  const uvMissing = 'Dependency sync failed: uv is required but was not found.';

  beforeEach(() => {
    vi.clearAllMocks();
    checkUpdate.mockResolvedValue(updatable);
  });

  it('stops showing the upgrade as running once it failed', async () => {
    applyUpdate.mockImplementation(async (_version: string, onProgress: (progress: unknown) => void) => {
      onProgress({
        stage: 'fetching',
        message: 'Downloading Flocks Pro bundle',
        bundle_filename: 'flockspro-bundle-v2026.10.13.zip',
        downloaded_bytes: 36 * 1024 * 1024,
        total_bytes: 36 * 1024 * 1024,
        percent: 100,
      });
      onProgress({ stage: 'error', message: uvMissing, success: false });
      throw new Error(uvMissing);
    });
    const user = userEvent.setup();
    render(<UpdateModal initialInfo={updatable} edition="flockspro" onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));

    expect(await screen.findByText(uvMissing)).toBeInTheDocument();
    expect(screen.getByText('upgradeFailed')).toBeInTheDocument();
    expect(screen.queryByText('upgrading')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
  });

  it('stops showing the upgrade as running when the stream ends without a restart', async () => {
    applyUpdate.mockImplementation(async (_version: string, onProgress: (progress: unknown) => void) => {
      onProgress({ stage: 'done', message: 'Already up to date v2026.10.13', success: true });
    });
    const user = userEvent.setup();
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));

    expect(await screen.findByText('stageLabels.done')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText('upgrading')).not.toBeInTheDocument();
    });
  });
});
