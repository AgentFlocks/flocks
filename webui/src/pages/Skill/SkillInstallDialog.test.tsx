import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SkillInstallDialog from './SkillInstallDialog';

const SAFESKILL_EXAMPLE = 'safeskill://tbx/6ef3925b1f6245bcbd7da39f23c28652/onesig-use@1.0.0';

const { getMock, installMock, toastErrorMock, toastSuccessMock, toastWarningMock, tMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  installMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  toastWarningMock: vi.fn(),
  tMock: vi.fn((key: string) => {
    const labels: Record<string, string> = {
      'installDialog.cancel': 'Cancel',
      'installDialog.install': 'Install',
      'installDialog.installing': 'Installing...',
      'installDialog.sourceHint': 'Supports SafeSkill',
      'installDialog.sourceLabel': 'Source',
      'installDialog.sourcePlaceholder': 'Enter a source',
      'installDialog.success': 'Installed',
      'installDialog.title': 'Install Skill',
      installFailed: 'Install failed',
    };
    return labels[key] ?? key;
  }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: toastErrorMock,
    success: toastSuccessMock,
    warning: toastWarningMock,
  }),
}));

vi.mock('@/api/skill', () => ({
  skillAPI: {
    get: getMock,
    install: installMock,
  },
}));

describe('SkillInstallDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installMock.mockResolvedValue({
      data: {
        success: true,
        skill_name: 'onesig-use',
        location: '/tmp/onesig-use/SKILL.md',
        message: 'installed',
      },
    });
    getMock.mockResolvedValue({
      data: {
        name: 'onesig-use',
        description: 'OneSig',
        location: '/tmp/onesig-use/SKILL.md',
      },
    });
  });

  it('fills and installs a SafeSkill URI from the quick example', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onInstalled = vi.fn();

    render(<SkillInstallDialog onClose={onClose} onInstalled={onInstalled} />);

    await user.click(screen.getByRole('button', { name: 'SafeSkill' }));

    expect(screen.getByRole('textbox')).toHaveValue(SAFESKILL_EXAMPLE);

    await user.click(screen.getByRole('button', { name: 'Install' }));

    await waitFor(() => {
      expect(installMock).toHaveBeenCalledWith({ source: SAFESKILL_EXAMPLE });
    });
    expect(onInstalled).toHaveBeenCalledWith(expect.objectContaining({ name: 'onesig-use' }));
    expect(onClose).toHaveBeenCalled();
  });
});
