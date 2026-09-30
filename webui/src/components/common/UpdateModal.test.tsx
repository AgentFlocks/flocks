import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateModal from './UpdateModal';
import type { UpdateProgress } from '@/api/update';

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

describe('UpdateModal Pro component updates', () => {
  const proInfo = {
    ...currentVersion,
    edition: 'flockspro' as const,
    has_update: true,
    current_version: '2026.09.01',
    latest_version: 'pro-v2026.10.02',
    current_core_version: '2026.09.15',
    latest_core_version: '2026.10.01',
    current_bundle_version: 'pro-v2026.09.20',
    latest_bundle_version: 'pro-v2026.10.02',
    current_pro_component_version: '2026.09.10',
    latest_pro_component_version: '2026.09.30',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    applyUpdate.mockReset();
  });

  it('shows the actual Pro component target and keeps the current Core separate from the bundle Core', () => {
    render(<UpdateModal initialInfo={proInfo} edition="flockspro" onClose={vi.fn()} />);

    expect(screen.getByText('currentProVersion')).toBeInTheDocument();
    expect(screen.getByText('latestProVersion')).toBeInTheDocument();
    expect(screen.getByText('v2026.09.10')).toBeInTheDocument();
    expect(screen.getAllByText('v2026.09.30').length).toBeGreaterThan(0);
    expect(screen.getByText('coreUnchanged')).toBeInTheDocument();
    expect(screen.getByText('v2026.09.15')).toBeInTheDocument();
    expect(screen.getByText('proUpdateScope')).toBeInTheDocument();
    expect(screen.queryByText('v2026.10.01')).not.toBeInTheDocument();
    expect(screen.queryByText('pro-v2026.10.02')).not.toBeInTheDocument();
    expect(screen.queryByText('newVersionDesc')).not.toBeInTheDocument();
  });

  it('does not substitute a bundle or Core version when component versions are unavailable', () => {
    render(<UpdateModal initialInfo={{ ...proInfo,
      current_pro_component_version: null, latest_pro_component_version: null,
      current_core_version: null,
    }} edition="flockspro" onClose={vi.fn()} />);

    expect(screen.getAllByText('—')).toHaveLength(3);
    expect(screen.queryByText('v2026.09.01')).not.toBeInTheDocument();
    expect(screen.queryByText('pro-v2026.09.20')).not.toBeInTheDocument();
    expect(screen.queryByText('pro-v2026.10.02')).not.toBeInTheDocument();
  });

  it('keeps the backend release target while describing each stage as a Pro component update', async () => {
    applyUpdate.mockImplementation(async (_target: string, onProgress: (progress: UpdateProgress) => void) => {
      for (const stage of ['fetching', 'backing_up', 'applying', 'syncing', 'done'] as const) {
        onProgress({ stage, message: '', success: true,
          ...(stage === 'fetching' ? { pro_component_filename: 'flockspro-2026.09.30.whl' } : {}),
        });
      }
    });
    const user = userEvent.setup();
    render(<UpdateModal initialInfo={proInfo} edition="flockspro" onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));

    expect(applyUpdate).toHaveBeenCalledWith('pro-v2026.10.02', expect.any(Function), 'zh-CN', 'flockspro');
    for (const key of ['fetchingPro', 'backingUpPro', 'applyingPro', 'syncingPro', 'donePro']) {
      expect(screen.getByText(`stageLabels.${key}`)).toBeInTheDocument();
    }
    expect(screen.getByText('confirmProUpgradeDesc')).toBeInTheDocument();
    expect(screen.getByText('coreUnchanged: v2026.09.15')).toBeInTheDocument();
    expect(screen.queryByText('stageLabels.fetching')).not.toBeInTheDocument();
    expect(screen.queryByText('v2026.10.01')).not.toBeInTheDocument();
  });

  it('preserves the normal Core update wording outside Pro updates', async () => {
    applyUpdate.mockImplementation(async (_target: string, onProgress: (progress: UpdateProgress) => void) => {
      onProgress({ stage: 'fetching', message: '', success: true });
    });
    const user = userEvent.setup();
    render(<UpdateModal initialInfo={{ ...currentVersion, has_update: true }} onClose={vi.fn()} />);
    expect(screen.getByText('currentVersion')).toBeInTheDocument();
    expect(screen.queryByText('coreUnchanged')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'confirmAction' }));
    expect(screen.getByText('stageLabels.fetching')).toBeInTheDocument();
    expect(screen.getByText('confirmUpgradeDesc')).toBeInTheDocument();
    expect(screen.queryByText('confirmProUpgradeDesc')).not.toBeInTheDocument();
  });
});
