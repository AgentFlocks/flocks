import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateModal from './UpdateModal';

const { applyUpdate, checkUpdate, checkRestartReadiness } = vi.hoisted(() => ({
  applyUpdate: vi.fn(),
  checkUpdate: vi.fn(),
  checkRestartReadiness: vi.fn(),
}));

vi.mock('@/api/update', () => ({
  applyUpdate,
  checkUpdate,
}));

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
