import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SkillPage from './index';

const { statusMock, listMock, refreshMock, toastErrorMock, toastSuccessMock, tMock } = vi.hoisted(() => ({
  statusMock: vi.fn(),
  listMock: vi.fn(),
  refreshMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  tMock: vi.fn((key: string) => key),
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
  }),
}));

vi.mock('@/api/skill', async () => {
  const actual = await vi.importActual<typeof import('@/api/skill')>('@/api/skill');
  return {
    ...actual,
    skillAPI: {
      ...actual.skillAPI,
      status: statusMock,
      list: listMock,
      refresh: refreshMock,
      get: vi.fn(),
      installDeps: vi.fn(),
      delete: vi.fn(),
    },
  };
});

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
      {action}
    </div>
  ),
}));

function makeSkill(name: string) {
  return {
    name,
    description: `${name} description`,
    location: `/tmp/${name}/SKILL.md`,
    source: 'user',
  };
}

function makeUiHiddenSkill(name: string) {
  return {
    ...makeSkill(name),
    ui_hidden: true,
  };
}

describe('SkillPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('refreshes the list when the window regains focus', async () => {
    statusMock
      .mockResolvedValueOnce({
        data: [makeSkill('skill-alpha')],
      })
      .mockResolvedValueOnce({
        data: [makeSkill('skill-alpha'), makeSkill('skill-beta')],
      });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    render(<SkillPage />);

    await waitFor(() => {
      expect(screen.getByText('skill-alpha')).toBeInTheDocument();
    });

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(screen.getByText('skill-beta')).toBeInTheDocument();
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it('does not render UI-hidden internal skills', async () => {
    statusMock.mockResolvedValue({
      data: [makeSkill('visible-skill'), makeUiHiddenSkill('workflow-config-guide')],
    });

    render(<SkillPage />);

    await waitFor(() => {
      expect(screen.getByText('visible-skill')).toBeInTheDocument();
    });

    expect(screen.queryByText('workflow-config-guide')).not.toBeInTheDocument();
  });
});
