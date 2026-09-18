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
