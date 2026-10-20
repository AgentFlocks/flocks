import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateModal from './UpdateModal';

const { applyUpdate, checkUpdate } = vi.hoisted(() => ({
  applyUpdate: vi.fn(),
  checkUpdate: vi.fn(),
}));

vi.mock('@/api/update', () => ({
  applyUpdate,
  checkUpdate,
  DEFAULT_UPDATE_CHANNEL: 'default',
  IncompleteUpdateError: class extends Error {},
  UpdateStreamTimeoutError: class extends Error {},
}));

const { checkRestartReadiness } = vi.hoisted(() => ({ checkRestartReadiness: vi.fn() }));
vi.mock('@/utils/restartPolling', () => ({ checkRestartReadiness }));

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
      expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'default', true);
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
      expect(checkUpdate).toHaveBeenCalledWith('zh-CN', 'default', true);
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

  const withUpdate = {
    ...currentVersion,
    latest_version: '2026.09.14',
    has_update: true,
    update_allowed: false,
  };

  it('shows the managed-package notice instead of Docker guidance on managed installs', () => {
    render(<UpdateModal initialInfo={{ ...withUpdate, deploy_mode: 'managed' }} onClose={vi.fn()} />);
    expect(screen.getByText('managedModeTitle')).toBeTruthy();
    expect(screen.getByText('managedModeDesc')).toBeTruthy();
    expect(screen.queryByText('dockerModeTitle')).toBeNull();
    expect(screen.queryByText('dockerUpgradeHint')).toBeNull();
  });

  it('keeps the Docker guidance for docker installs', () => {
    render(<UpdateModal initialInfo={{ ...withUpdate, deploy_mode: 'docker' }} onClose={vi.fn()} />);
    expect(screen.getByText('dockerModeTitle')).toBeTruthy();
    expect(screen.queryByText('managedModeTitle')).toBeNull();
  });

  it.each(['docker', 'managed'] as const)('does not offer an upgrade for %s', (deploy_mode) => {
    render(<UpdateModal initialInfo={{ ...withUpdate, deploy_mode }} onClose={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'confirmAction' })).toBeNull();
    expect(applyUpdate).not.toHaveBeenCalled();
  });

});

describe('UpdateModal terminal states', () => {
  // dev's progress card: the header X closes it once nothing runs, and an
  // error adds the 「稍后」 button; the footer spinner only shows while upgrading.
  const initialInfo = { ...currentVersion, latest_version: '2026.09.20', has_update: true };
  const headerClose = () => document.querySelector<HTMLButtonElement>('button:has(svg.lucide-x)')!;
  beforeEach(() => {
    vi.clearAllMocks();
    checkRestartReadiness.mockResolvedValue({ ready: false, reason: 'still restarting' });
  });
  afterEach(() => vi.useRealTimers());

  it('stops the spinner and allows closing after done without restarting', async () => {
    applyUpdate.mockImplementation(async (_version, progress) => progress({ stage: 'done', success: true, message: 'Complete' }));
    const onClose = vi.fn();
    render(<UpdateModal initialInfo={initialInfo} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'confirmAction' }));
    await waitFor(() => expect(headerClose()).not.toBeDisabled());
    expect(screen.queryByText('upgrading')).toBeNull();
    fireEvent.click(headerClose());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('cannot dismiss an in-progress upgrade using the backdrop or the close button', async () => {
    applyUpdate.mockImplementationOnce(() => new Promise(() => {}));
    const onClose = vi.fn();
    render(<UpdateModal initialInfo={initialInfo} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(headerClose()).toBeDisabled();
    fireEvent.click(document.querySelector('div.fixed.inset-0')!);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('reports early EOF and releases the close controls', async () => {
    const { IncompleteUpdateError } = await import('@/api/update');
    applyUpdate.mockRejectedValue(new IncompleteUpdateError());
    render(<UpdateModal initialInfo={initialInfo} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'confirmAction' }));
    await screen.findByText('streamInterrupted');
    expect(screen.queryByText('upgrading')).toBeNull();
    expect(headerClose()).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
  });

  it('surfaces server errors without retaining the progress spinner', async () => {
    applyUpdate.mockRejectedValue(new Error('Update refused'));
    render(<UpdateModal initialInfo={initialInfo} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'confirmAction' }));
    await screen.findByText('Update refused');
    expect(screen.queryByText('upgrading')).toBeNull();
  });

  it('reports a silent connection timeout and allows closing', async () => {
    const { UpdateStreamTimeoutError } = await import('@/api/update');
    applyUpdate.mockRejectedValue(new UpdateStreamTimeoutError());
    render(<UpdateModal initialInfo={initialInfo} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'confirmAction' }));
    await screen.findByText('streamTimeout');
    expect(screen.queryByText('upgrading')).toBeNull();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
  });

  it('keeps restart polling after EOF then unlocks the dialog on restart timeout', async () => {
    vi.useFakeTimers();
    applyUpdate.mockImplementation(async (_version, progress) => progress({ stage: 'restarting', message: 'Restarting' }));
    render(<UpdateModal initialInfo={initialInfo} onClose={vi.fn()} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'confirmAction' })); });
    expect(headerClose()).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(303_000); });
    expect(checkRestartReadiness).toHaveBeenCalled();
    expect(screen.getByText('restartTimeout')).toBeInTheDocument();
    expect(document.querySelector('.animate-spin')).toBeNull();
    expect(headerClose()).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
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
        message: 'Downloading bundle',
        bundle_filename: 'bundle-v2026.10.13.zip',
        downloaded_bytes: 36 * 1024 * 1024,
        total_bytes: 36 * 1024 * 1024,
        percent: 100,
      });
      onProgress({ stage: 'error', message: uvMissing, success: false });
      throw new Error(uvMissing);
    });
    const user = userEvent.setup();
    render(<UpdateModal initialInfo={updatable} channel="bundle" onClose={vi.fn()} />);

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
