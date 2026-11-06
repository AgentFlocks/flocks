import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateModal from './UpdateModal';

const { applyUpdate, checkUpdate, checkRestartReadiness, enterpriseStart, enterpriseSituation, enterpriseVersionInfo } = vi.hoisted(() => ({
  applyUpdate: vi.fn(),
  checkUpdate: vi.fn(),
  checkRestartReadiness: vi.fn(),
  enterpriseStart: vi.fn(),
  enterpriseSituation: vi.fn(),
  enterpriseVersionInfo: vi.fn(),
}));

vi.mock('@/api/update', () => ({
  applyUpdate,
  checkUpdate,
  DEFAULT_UPDATE_CHANNEL: 'default',
  IncompleteUpdateError: class extends Error {},
  UpdateStreamTimeoutError: class extends Error {},
}));

vi.mock('@/utils/restartPolling', () => ({ checkRestartReadiness }));

vi.mock('@/api/enterpriseUpgrade', () => ({
  ENTERPRISE_UPGRADE_PAGE: '/settings/flockspro',
  EnterpriseUpgradeRefused: class EnterpriseUpgradeRefused extends Error {},
  enterpriseUpgradeApi: { start: enterpriseStart, situation: enterpriseSituation },
  enterpriseVersionInfo,
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

describe('UpdateModal restart stage', () => {
  const updatable = { ...currentVersion, latest_version: '2026.10.20', has_update: true };
  const step = (id: string, status: string) => ({ id, status });
  const running = (current: string) => ({
    phase: 'running',
    step: current,
    steps: ['stop', 'source_replace', 'build_webui', 'start'].map((id) => step(
      id,
      id === current ? 'running' : ['stop', 'source_replace', 'build_webui', 'start'].indexOf(id) < ['stop', 'source_replace', 'build_webui', 'start'].indexOf(current) ? 'done' : 'pending',
    )),
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    checkUpdate.mockResolvedValue(updatable);
    applyUpdate.mockImplementation(async (_version: string, onProgress: (progress: unknown) => void) => {
      onProgress({ stage: 'applying', message: 'Staging v2026.10.20 for handoff...', success: null });
      onProgress({ stage: 'restarting', message: 'Restarting service...', success: null, progress_token: 'tok-1' });
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('lists the restart steps and stops at a failed step with its reason', async () => {
    checkRestartReadiness
      .mockResolvedValueOnce({ ready: false, upgrade: running('build_webui') })
      .mockResolvedValueOnce({
        ready: false,
        upgrade: {
          phase: 'failed',
          step: 'build_webui',
          failed_step: 'build_webui',
          error: 'npm run build exited with code 1',
          steps: [step('stop', 'done'), step('source_replace', 'done'), step('build_webui', 'failed'), step('start', 'pending')],
        },
      });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    const list = await screen.findByRole('list', { name: 'handoff.title' });
    expect(within(list).getAllByRole('listitem').map((item) => item.getAttribute('data-status')))
      .toEqual(['done', 'done', 'running', 'pending']);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(await screen.findByText('handoff.failed handoff.recoveryHint')).toBeInTheDocument();
    expect(screen.getByText('upgradeFailed')).toBeInTheDocument();
    expect(screen.queryByText('waitingRestart')).not.toBeInTheDocument();
    expect(checkRestartReadiness).toHaveBeenCalledWith('tok-1');

    await vi.advanceTimersByTimeAsync(10_000);
    expect(checkRestartReadiness).toHaveBeenCalledTimes(2);
  });

  describe('when the upgrade fails and the previous version is put back', () => {
    const failed = {
      phase: 'failed',
      step: 'check_runtime',
      failed_step: 'check_runtime',
      error: 'npm was not found.',
      steps: [step('stop', 'done'), step('source_replace', 'done'), step('check_runtime', 'failed'), step('start', 'pending')],
    };
    const reload = vi.fn();

    beforeEach(() => {
      reload.mockClear();
      vi.stubGlobal('location', { ...window.location, reload });
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it('says why while it is restored, then that the previous version is back, without reloading', async () => {
      checkRestartReadiness
        .mockResolvedValueOnce({ ready: false, upgrade: { ...failed, recovery: 'restoring' } })
        // The port on its way from the handoff to the restored service.
        .mockResolvedValueOnce({ ready: false, reason: 'health check failed' })
        .mockResolvedValueOnce({ ready: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'confirmAction' }));
      await vi.advanceTimersByTimeAsync(1_500);
      expect(await screen.findByText('handoff.failed handoff.restoring')).toBeInTheDocument();
      expect(screen.getByText('handoff.restoringTitle')).toBeInTheDocument();
      expect(screen.getByText('waitingRestart')).toBeInTheDocument();
      expect(screen.queryByText('restarting')).not.toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(2_000);
      expect(screen.getByText('handoff.failed handoff.restoring')).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(2_000);
      expect(await screen.findByText('handoff.failed handoff.restored')).toBeInTheDocument();
      expect(screen.getByText('upgradeFailed')).toBeInTheDocument();
      expect(screen.queryByText('waitingRestart')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'laterAction' })).toBeInTheDocument();
      expect(reload).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(10_000);
      expect(checkRestartReadiness).toHaveBeenCalledTimes(3);

      // Closing it brings the page back up to date: it was loaded while the service was away.
      await user.click(screen.getByRole('button', { name: 'laterAction' }));
      expect(reload).toHaveBeenCalledOnce();
    });

    it('stops at once when the previous version could not be put back', async () => {
      checkRestartReadiness
        .mockResolvedValueOnce({ ready: false, upgrade: { ...failed, recovery: 'restoring' } })
        .mockResolvedValueOnce({ ready: false, upgrade: { ...failed, recovery: 'failed' } });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'confirmAction' }));
      await vi.advanceTimersByTimeAsync(1_500);
      await vi.advanceTimersByTimeAsync(2_000);

      expect(await screen.findByText('handoff.failed handoff.recoveryHint')).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(10_000);
      expect(checkRestartReadiness).toHaveBeenCalledTimes(2);
    });

    it('still reloads a service that came up from an upgrade that worked', async () => {
      checkRestartReadiness
        .mockResolvedValueOnce({ ready: false, upgrade: running('build_webui') })
        .mockResolvedValueOnce({ ready: true });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'confirmAction' }));
      await vi.advanceTimersByTimeAsync(1_500);
      await vi.advanceTimersByTimeAsync(2_000);

      await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    });
  });

  it('shows a refusal before the service is touched on the step that refused', async () => {
    applyUpdate.mockImplementation(async (_version: string, onProgress: (progress: unknown) => void) => {
      onProgress({ stage: 'fetching', message: 'Downloading source archive...', success: null });
      onProgress({ stage: 'checking', message: 'Checking Node.js and npm...', success: null });
      onProgress({ stage: 'error', message: '这台机器上找不到 npm。升级没有开始，服务和程序都没有动，可以接着用。', success: false });
      throw new Error('这台机器上找不到 npm。升级没有开始，服务和程序都没有动，可以接着用。');
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));

    expect(await screen.findByText(/升级没有开始，服务和程序都没有动/)).toBeInTheDocument();
    expect(screen.getByText('stageLabels.checking').className).toContain('text-red-600');
    expect(screen.getByText('stageLabels.fetching').className).not.toContain('text-red-600');
    expect(checkRestartReadiness).not.toHaveBeenCalled();
  });

  it('keeps waiting while the handoff answers, even when one step outlasts the restart timeout', async () => {
    let polls = 0;
    const failed = {
      phase: 'failed',
      step: 'build_webui',
      failed_step: 'build_webui',
      error: 'npm run build exited with code 1',
      steps: [step('stop', 'done'), step('source_replace', 'done'), step('build_webui', 'failed'), step('start', 'pending')],
    };
    checkRestartReadiness.mockImplementation(async () => {
      polls += 1;
      // every poll costs a minute; the WebUI build runs for 12 of them
      vi.setSystemTime(Date.now() + 60_000);
      return { ready: false, upgrade: polls <= 12 ? running('build_webui') : failed };
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    for (let poll = 0; poll < 12; poll += 1) {
      await vi.advanceTimersByTimeAsync(2_100);
    }
    expect(polls).toBeGreaterThanOrEqual(12);
    expect(screen.queryByText('restartTimeout')).not.toBeInTheDocument();
    expect(screen.getByText('waitingRestart')).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2_100);
    expect(await screen.findByText('handoff.failed handoff.recoveryHint')).toBeInTheDocument();
    expect(screen.queryByText('restartTimeout')).not.toBeInTheDocument();
  });

  it('gives up after half an hour even while the handoff keeps answering', async () => {
    checkRestartReadiness.mockImplementation(async () => {
      vi.setSystemTime(Date.now() + 10 * 60_000);
      return { ready: false, reason: 'upgrade handoff running: build_webui', upgrade: running('build_webui') };
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    for (let poll = 0; poll < 4; poll += 1) {
      await vi.advanceTimersByTimeAsync(2_100);
    }

    expect(await screen.findByText('restartTimeout')).toBeInTheDocument();
    expect(checkRestartReadiness).toHaveBeenCalledTimes(3);
  });

  it('times out when nothing answers for the restart timeout', async () => {
    checkRestartReadiness.mockImplementation(async () => {
      vi.setSystemTime(Date.now() + 60_000);
      return { ready: false, reason: 'health check failed' };
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={updatable} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    for (let poll = 0; poll < 7; poll += 1) {
      await vi.advanceTimersByTimeAsync(2_100);
    }

    expect(await screen.findByText('restartTimeout')).toBeInTheDocument();
    expect(checkRestartReadiness).toHaveBeenCalledTimes(5);
  });
});

const refused = (message: string) => Object.assign(new Error(message), { refused: true });

describe('UpdateModal on a machine with an enterprise licence', () => {
  const enterprise = { installRoot: '/home/me/flockspro', canStart: true };
  const offered = { ...currentVersion, current_version: '2026.10.30', latest_version: '2026.11.04.31', has_update: true, channel: 'enterprise' };
  const location = { reload: vi.fn(), assign: vi.fn() };
  const realLocation = window.location;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.defineProperty(window, 'location', { configurable: true, value: location });
    enterpriseVersionInfo.mockResolvedValue(offered);
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
  });

  const opened = async (props: Record<string, unknown> = {}) => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={offered} enterprise={enterprise} onClose={vi.fn()} {...props} />);
    return user;
  };

  it('checks with Console, not this project\'s releases, and says where the program goes', async () => {
    const user = await opened();
    expect(screen.getByText('enterpriseDesc')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'checkUpdate' }));
    await waitFor(() => expect(enterpriseVersionInfo).toHaveBeenCalledTimes(1));
    expect(checkUpdate).not.toHaveBeenCalled();
  });

  it('shows the download, stays through the installer and reloads on what answers next', async () => {
    let finishStart: (state: unknown) => void = () => {};
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => new Promise((resolve) => {
      onEvent({ stage: 'downloading', version: '2026.11.04.31' });
      onEvent({ stage: 'downloading', received: 43, total: 100 });
      finishStart = resolve;
    }));
    enterpriseSituation
      .mockResolvedValueOnce({ available: true, upgrade: { phase: 'installing' } })
      .mockRejectedValueOnce(new Error('out of reach'))
      .mockRejectedValueOnce(new Error('still out of reach'))
      .mockResolvedValue({ available: false, upgrade: null });
    const user = await opened();

    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '43');
    expect(screen.getByText('enterpriseStageLabels.fetching')).toBeInTheDocument();
    expect(applyUpdate).not.toHaveBeenCalled();

    await act(async () => { finishStart({ phase: 'installing', version: '2026.11.04.31' }); });
    expect(screen.getByText('enterpriseStageLabels.restarting')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText('enterpriseInstalling')).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1_500); // this server still reports it
    expect(enterpriseSituation).toHaveBeenCalledTimes(1);
    expect(location.reload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000); // this server has stopped
    await vi.advanceTimersByTimeAsync(2_000); // nothing answers yet
    expect(location.reload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000); // the enterprise edition answers
    expect(location.reload).toHaveBeenCalledTimes(1);
  });

  it('does not take a server that answers badly for one thing or the other', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing' });
    enterpriseSituation
      .mockRejectedValueOnce({ response: { status: 502 } })
      .mockResolvedValueOnce({ available: true, upgrade: { phase: 'installing' } })
      .mockResolvedValue({ available: true, upgrade: { phase: 'installing' } });
    checkRestartReadiness.mockResolvedValue({ ready: true });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    await vi.advanceTimersByTimeAsync(4_000);
    expect(location.reload).not.toHaveBeenCalled();
    expect(screen.getByText('enterpriseStageLabels.restarting')).toBeInTheDocument();
  });

  it('reloads when another build refuses the question and the service is up', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing' });
    enterpriseSituation.mockRejectedValue({ response: { status: 404 } });
    checkRestartReadiness.mockResolvedValue({ ready: true });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(location.reload).toHaveBeenCalledTimes(1);
  });

  it('does not take a refusal for the new edition before the service is up', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing' });
    enterpriseSituation.mockRejectedValue({ response: { status: 404 } });
    checkRestartReadiness.mockResolvedValue({ ready: false });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(location.reload).not.toHaveBeenCalled();
    expect(screen.getByText('enterpriseStageLabels.restarting')).toBeInTheDocument();
  });

  it('says what failed when the installer reports it, the open edition being back, and the dialog can be closed', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing' });
    enterpriseSituation
      .mockRejectedValueOnce(new Error('stopped while it tried'))
      .mockResolvedValue({ available: true, upgrade: { phase: 'failed', message: '安装没有完成' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await screen.findByText('安装没有完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
    expect(location.reload).not.toHaveBeenCalled();
  });

  it('says where it stopped and why when the installer ended with no message of its own', async () => {
    const log = '/home/me/.flocks/enterprise/install.log';
    enterpriseStart.mockResolvedValue({ phase: 'installing', log });
    enterpriseSituation.mockResolvedValue({
      available: true,
      upgrade: {
        phase: 'failed',
        failed_step: 'install',
        exit_code: 1,
        reason: 'the core did not come up',
        recovery: 'restored',
        log,
        log_tail: ['== bash flockspro.run', 'install-managed: stopping the open edition', 'the core did not come up'],
      },
    });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('the core did not come up');
    expect(screen.getByText('failure.stoppedAt')).toBeInTheDocument();
    expect(screen.getByText('failure.exitCode')).toBeInTheDocument();
    expect(screen.getByText('failure.recovery.restored')).toBeInTheDocument();
    expect(screen.getByText(log)).toBeInTheDocument();
    expect(screen.getByTestId('upgrade-failure-log')).toHaveTextContent('install-managed: stopping the open edition');
    // The step it stopped at is no step that went through.
    expect(screen.getByText('enterpriseStageLabels.restarting')).toHaveClass('text-red-600');
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
    expect(location.reload).not.toHaveBeenCalled();
  });

  it('marks the download when that is where it stopped, and no later step', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a1' });
      onEvent({ stage: 'downloading', received: 10, total: 100 });
      return Promise.reject(refused('下载中断'));
    });
    enterpriseSituation.mockResolvedValue({ available: true, upgrade: { attempt: 'a1', phase: 'failed', failed_step: 'download', message: '下载中断', reason: '下载中断', recovery: 'unchanged' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('下载中断');
    expect(screen.getByText('enterpriseStageLabels.fetching')).toHaveClass('text-red-600');
    expect(screen.queryByText('enterpriseStageLabels.restarting')).not.toBeInTheDocument();
    expect(screen.getByText('failure.recovery.unchanged')).toBeInTheDocument();
  });

  it('shows the failed install step even when it failed as it began', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a1' });
      return Promise.reject(refused('安装包没有起来'));
    });
    enterpriseSituation.mockResolvedValue({ available: true, upgrade: { attempt: 'a1', phase: 'failed', failed_step: 'install', message: '安装包没有起来' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('enterpriseStageLabels.restarting')).toHaveClass('text-red-600');
    expect(screen.getByTestId('upgrade-failure-reason')).toHaveTextContent('安装包没有起来');
  });

  it('says what the server said when it was refused before it began, and asks nothing else', async () => {
    enterpriseStart.mockRejectedValue(refused('已经在升级了'));
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('已经在升级了');
    expect(screen.getByText('upgradeFailed', { selector: 'span' })).toBeInTheDocument();
    expect(enterpriseSituation).not.toHaveBeenCalled();
  });

  it('does not take the failure of an earlier attempt for this one', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a2' });
      return Promise.reject(refused('请先完成云账号登录'));
    });
    enterpriseSituation.mockResolvedValue({
      available: true,
      upgrade: { attempt: 'a1', phase: 'failed', failed_step: 'install', exit_code: 1, reason: '上一次的原因', recovery: 'restored', log: '/x/install.log', log_tail: ['old'] },
    });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('请先完成云账号登录');
    expect(screen.queryByText('上一次的原因')).not.toBeInTheDocument();
    expect(screen.queryByTestId('upgrade-failure-log')).not.toBeInTheDocument();
    expect(screen.getByText('failure.recovery.unchanged')).toBeInTheDocument();
    expect(screen.getByText('failure.stoppedAt')).toBeInTheDocument();
  });

  it('follows the attempt when the stream goes and the install goes on', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a1' });
      return Promise.reject(new Error('the upgrade stream ended early'));
    });
    enterpriseSituation
      .mockResolvedValueOnce({ available: true, upgrade: { attempt: 'a1', phase: 'installing', log: '/x/install.log' } })
      .mockResolvedValue({ available: false, upgrade: null });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('enterpriseStageLabels.restarting')).toBeInTheDocument();
    expect(screen.queryByTestId('upgrade-failure')).not.toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1_500);
    expect(location.reload).toHaveBeenCalledTimes(1);
  });

  it('marks the download when the stream went during it and that is where it failed', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a1' });
      onEvent({ stage: 'downloading', received: 10, total: 100 });
      return Promise.reject(new Error('the upgrade stream ended early'));
    });
    enterpriseSituation
      .mockResolvedValueOnce({ available: true, upgrade: { attempt: 'a1', phase: 'downloading', received: 10, total: 100 } })
      .mockResolvedValue({ available: true, upgrade: { attempt: 'a1', phase: 'failed', failed_step: 'download', message: '下载中断', reason: '下载中断', recovery: 'unchanged' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('enterpriseStageLabels.fetching')).toBeInTheDocument();
    expect(screen.queryByText('enterpriseStageLabels.restarting')).not.toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('下载中断');
    expect(screen.getByText('enterpriseStageLabels.fetching')).toHaveClass('text-red-600');
    expect(screen.queryByText('enterpriseStageLabels.restarting')).not.toBeInTheDocument();
  });

  it('cannot say how it ended when the stream goes and nothing answers', async () => {
    enterpriseStart.mockImplementation((onEvent: (event: unknown) => void) => {
      onEvent({ stage: 'started', attempt: 'a1' });
      return Promise.reject(new Error('the upgrade stream ended early'));
    });
    enterpriseSituation.mockRejectedValue(new Error('no answer'));
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('failure.recovery.unknown')).toBeInTheDocument();
    expect(screen.queryByText('failure.recovery.unchanged')).not.toBeInTheDocument();
  });

  it('does not take how another attempt ended for how this one did', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing', attempt: 'a1', log: '/x/install.log' });
    enterpriseSituation.mockResolvedValue({ available: true, upgrade: { attempt: 'a2', phase: 'failed', failed_step: 'install', reason: '另一次的原因', recovery: 'restored' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('failure.superseded');
    expect(screen.queryByText('另一次的原因')).not.toBeInTheDocument();
    expect(screen.getByText('failure.recovery.unknown')).toBeInTheDocument();
  });

  it('does not take something else answering on the port for this server', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing', attempt: 'a1', log: '/x/install.log' });
    enterpriseSituation
      .mockResolvedValueOnce('hello')
      .mockResolvedValue({ available: true, upgrade: { attempt: 'a1', phase: 'failed', failed_step: 'install', reason: 'something still listens', recovery: 'restored' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(location.reload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_100);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('something still listens');
    expect(screen.getByText('failure.recovery.restored')).toBeInTheDocument();
    expect(location.reload).not.toHaveBeenCalled();
  });

  it('says where the log is while it installs, and when nothing comes back says that and where to look', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing', attempt: 'a1', log: '/x/install.log' });
    enterpriseSituation.mockRejectedValue(new Error('no answer'));
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(screen.getByText('failure.logPath/x/install.log')).toBeInTheDocument();
    expect(screen.queryByTestId('upgrade-failure')).not.toBeInTheDocument();
    vi.setSystemTime(Date.now() + 31 * 60 * 1000);
    await vi.advanceTimersByTimeAsync(4_000);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('restartTimeout');
    expect(screen.getByText('/x/install.log')).toBeInTheDocument();
    expect(screen.getByText('failure.recovery.unknown')).toBeInTheDocument();
    expect(screen.getByText('enterpriseStageLabels.restarting')).toHaveClass('text-red-600');
    expect(location.reload).not.toHaveBeenCalled();
  });

  it('does not say the machine was left alone when nothing says so', async () => {
    enterpriseStart.mockResolvedValue({ phase: 'installing', log: '/x/install.log' });
    enterpriseSituation.mockResolvedValue({ available: true, upgrade: { phase: 'failed', failed_step: 'install', log: '/x/install.log' } });
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByTestId('upgrade-failure')).toBeInTheDocument();
    expect(screen.queryByText('failure.recovery.unchanged')).not.toBeInTheDocument();
  });

  it('keeps to the way the shown version came when the machine changes under it', async () => {
    // The dialog shows Console's version; a check that could not ask has taken the machine's kind away.
    enterpriseStart.mockResolvedValue({ phase: 'installing' });
    enterpriseSituation.mockResolvedValue({ available: true, upgrade: { phase: 'installing' } });
    const user = await opened({ enterprise: null });
    await user.click(screen.getByRole('button', { name: 'enterpriseOpenPage' }));
    expect(applyUpdate).not.toHaveBeenCalled();
    expect(location.assign).toHaveBeenCalledWith('/settings/flockspro');
  });

  it('says what stopped it before the installer ran', async () => {
    enterpriseStart.mockRejectedValue(new Error('下载安装包失败'));
    const user = await opened();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('下载安装包失败')).toBeInTheDocument();
    // Nothing is running to wait for.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(enterpriseSituation).not.toHaveBeenCalled();
  });

  it('leaves what root has to run to the Flocks Pro page', async () => {
    const user = await opened({ enterprise: { installRoot: '/opt/flockspro', canStart: false } });
    await user.click(screen.getByRole('button', { name: 'enterpriseOpenPage' }));
    expect(location.assign).toHaveBeenCalledWith('/settings/flockspro');
    expect(enterpriseStart).not.toHaveBeenCalled();

    location.assign.mockClear();
    enterpriseStart.mockResolvedValue({ phase: 'command', command: 'bash x.run' });
    const started = await opened({ initialInfo: { ...offered }, enterprise });
    await started.click(screen.getAllByRole('button', { name: 'confirmAction' }).at(-1)!);
    await waitFor(() => expect(location.assign).toHaveBeenCalledWith('/settings/flockspro'));
  });

  it('shows a Console that will not say, and the way to the page that deals with it', async () => {
    const failed = { ...offered, latest_version: null, has_update: false, error: 'Console 要先登录才给安装包' };
    await opened({ initialInfo: failed });
    expect(screen.getByText('Console 要先登录才给安装包')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'enterpriseOpenPage' })).toHaveAttribute('href', '/settings/flockspro');
    expect(screen.queryByRole('button', { name: 'confirmAction' })).not.toBeInTheDocument();
  });
});

describe('UpdateModal on a release its own core installs', () => {
  const managed = {
    ...currentVersion, latest_version: '2026.11.04.33', has_update: true, deploy_mode: 'managed' as const, update_allowed: true, channel: 'flockspro',
  };
  const location = { reload: vi.fn(), assign: vi.fn() };
  const realLocation = window.location;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.defineProperty(window, 'location', { configurable: true, value: location });
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
  });

  const handedOver = (onProgress: (progress: unknown) => void) => {
    onProgress({ stage: 'fetching', message: '', success: null, bundle_filename: 'r.fprb', downloaded_bytes: 5, total_bytes: 10, percent: 50 });
    onProgress({ stage: 'restarting', message: '', success: null, progress_token: 'managed:abc' }); // secret-guard: allow (the test's own request id)
    return Promise.resolve();
  };
  const started = async (outcome: () => Promise<unknown>) => {
    const start = vi.fn(handedOver);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={managed} channel="flockspro" releaseUpdate={{ status: vi.fn(), start, outcome: outcome as never }} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    return start;
  };

  it('applies it through the source, not the built-in updater, and waits for how it ended', async () => {
    const outcome = vi.fn()
      .mockResolvedValueOnce({ state: 'pending' })
      .mockResolvedValueOnce({ state: 'running' })
      .mockRejectedValueOnce(new Error('the service is restarting'))
      .mockRejectedValueOnce({ response: { status: 502 } })
      .mockResolvedValue({ state: 'done' });
    const start = await started(outcome);

    expect(screen.queryByText('managedModeTitle')).not.toBeInTheDocument();
    expect(start).toHaveBeenCalledTimes(1);
    expect(applyUpdate).not.toHaveBeenCalled();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    await vi.advanceTimersByTimeAsync(1_500);
    expect(outcome).toHaveBeenCalledWith('managed:abc');
    for (let asked = 0; asked < 3; asked += 1) {
      expect(location.reload).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(2_000);
    }
    expect(location.reload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(location.reload).toHaveBeenCalledTimes(1);
    // This service stays up until the install restarts it: its answering says nothing.
    expect(checkRestartReadiness).not.toHaveBeenCalled();
  });

  it.each([
    ['failed', '签名不对'],
    ['stalled', '升级单元没有接手'],
    ['unknown', '被另一次升级换掉了'],
  ])('says why when it ended %s, and the dialog can be closed', async (state, detail) => {
    await started(vi.fn().mockResolvedValue({ state, detail }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByText(detail)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
    expect(location.reload).not.toHaveBeenCalled();
  });

  it('shows where a release that did not come up stopped, what it printed and how the machine was left', async () => {
    await started(vi.fn().mockResolvedValue({
      state: 'failed',
      detail: 'release r2 did not come up (no answer); rolled back to r1, which is serving',
      failed_step: 'start',
      recovery: 'rolled_back',
      log: '/opt/flockspro/run/upgrade.log',
      log_tail: ['upgrade: current -> r2', 'Traceback (most recent call last):', 'ModuleNotFoundError: No module named x'],
    }));
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByTestId('upgrade-failure-reason')).toHaveTextContent('rolled back to r1');
    expect(screen.getByText('failure.stoppedAt')).toBeInTheDocument();
    expect(screen.getByText('failure.recovery.rolled_back')).toBeInTheDocument();
    expect(screen.getByText('/opt/flockspro/run/upgrade.log')).toBeInTheDocument();
    expect(screen.getByTestId('upgrade-failure-log')).toHaveTextContent('ModuleNotFoundError: No module named x');
    expect(screen.getByText('stageLabels.restarting')).toHaveClass('text-red-600');
  });

  it('stops at the service refusing the question, which will not change', async () => {
    const outcome = vi.fn().mockRejectedValue({ response: { status: 401, data: { detail: '请重新登录' } } });
    await started(outcome);
    await vi.advanceTimersByTimeAsync(1_500);
    expect(await screen.findByText('请重新登录')).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(4_000);
    expect(outcome).toHaveBeenCalledTimes(1);
  });

  it('says what stopped it before it was handed over', async () => {
    const start = vi.fn().mockRejectedValue(new Error('Console 没有这个平台的发布包'));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={managed} channel="flockspro" releaseUpdate={{ status: vi.fn(), start, outcome: vi.fn() }} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('Console 没有这个平台的发布包')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'laterAction' })).toBeEnabled();
  });

  it('marks the step a stream stopped at', async () => {
    const start = vi.fn().mockImplementation((onProgress: (progress: unknown) => void) => {
      onProgress({ stage: 'fetching', message: '', downloaded_bytes: 10, total_bytes: 100, percent: 10 });
      return Promise.reject(new Error('下载发布包失败：连接被重置'));
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<UpdateModal initialInfo={managed} channel="flockspro" releaseUpdate={{ status: vi.fn(), start, outcome: vi.fn() }} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(await screen.findByText('下载发布包失败：连接被重置')).toBeInTheDocument();
    expect(screen.getByText('stageLabels.fetching')).toHaveClass('text-red-600');
  });

  it('a managed install that is not a release install keeps the notice and no button', async () => {
    const status = vi.fn().mockResolvedValue({ release_install: false });
    checkUpdate.mockResolvedValue({ ...managed, update_allowed: false });
    render(<UpdateModal forceInitialCheck channel="flockspro" releaseUpdate={{ status, start: vi.fn(), outcome: vi.fn() }} onClose={vi.fn()} />);

    expect(await screen.findByText('managedModeTitle')).toBeInTheDocument();
    await waitFor(() => expect(status).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: 'confirmAction' })).not.toBeInTheDocument();
  });

  it('checking by hand asks the source as well', async () => {
    const status = vi.fn().mockResolvedValue({ release_install: true, latest_version: '2026.11.04.34', has_update: true, error: null });
    checkUpdate.mockResolvedValue({ ...managed, update_allowed: false, latest_version: '2026.11.04.30' });
    render(<UpdateModal forceInitialCheck channel="flockspro" releaseUpdate={{ status, start: vi.fn(), outcome: vi.fn() }} onClose={vi.fn()} />);

    expect(await screen.findByRole('button', { name: 'confirmAction' })).toBeInTheDocument();
    expect(screen.queryByText('managedModeTitle')).not.toBeInTheDocument();
  });
});
