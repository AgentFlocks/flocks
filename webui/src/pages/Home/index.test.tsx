import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import Home from './index';

const { createMock, navigateMock, toastErrorMock, useAuthMock, useStatsMock } =
  vi.hoisted(() => ({
    createMock: vi.fn(),
    navigateMock: vi.fn(),
    toastErrorMock: vi.fn(),
    useAuthMock: vi.fn(),
    useStatsMock: vi.fn(),
  }));

vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>(
      'react-router-dom',
    );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('@/api/session', () => ({
  sessionApi: {
    create: createMock,
  },
}));

vi.mock('@/hooks/useStats', () => ({
  useStats: () => useStatsMock(),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: toastErrorMock,
  }),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: useAuthMock,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

describe('Home create WebUI contract page entry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMock.mockResolvedValue({ id: 'session-webui-contract-1' });
    useStatsMock.mockReturnValue({
      stats: null,
      loading: false,
      error: null,
    });
    useAuthMock.mockReturnValue({
      user: {
        id: 'user-1',
        username: 'admin',
        role: 'admin',
        status: 'active',
        must_reset_password: false,
      },
    });
  });

  it('allows admins to create a session and navigate with the guided initial message', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    await user.click(
      screen.getByRole('button', { name: 'createWebUIContractPage' }),
    );

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        title: 'createWebUIContractPageSessionTitle',
      });
    });

    expect(navigateMock).toHaveBeenCalledWith(
      `/sessions?session=session-webui-contract-1&message=${encodeURIComponent('createWebUIContractPageInitialMessage')}`,
    );
  });

  it('hides the create WebUI contract page entry for non-admin users', () => {
    useAuthMock.mockReturnValue({
      user: {
        id: 'user-2',
        username: 'member',
        role: 'member',
        status: 'active',
        must_reset_password: false,
      },
    });

    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    expect(
      screen.queryByRole('button', { name: 'createWebUIContractPage' }),
    ).not.toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('shows the concrete stats load failure instead of a backend-not-running hint', () => {
    useStatsMock.mockReturnValue({
      stats: null,
      loading: false,
      error: new Error('authExpired'),
    });

    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    expect(
      screen.getByText('stats.loadErrorHint.authExpired'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/backend is running/i)).not.toBeInTheDocument();
  });

  it('links dashboard stat cards to their matching pages', () => {
    useStatsMock.mockReturnValue({
      stats: {
        agents: { total: 11 },
        workflows: { total: 7 },
        skills: { total: 14 },
        tools: { total: 202 },
        tasks: { week: 3, scheduledActive: 2 },
        models: { total: 39 },
        system: { status: 'healthy', message: 'healthy' },
      },
      loading: false,
      error: null,
    });

    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole('link', { name: 'stats.agentCount' }),
    ).toHaveAttribute('href', '/agents');
    expect(screen.getByRole('link', { name: 'stats.agentCount' })).toHaveClass(
      'hover:border-purple-200',
    );
    expect(
      screen.getByRole('link', { name: 'stats.workflowCount' }),
    ).toHaveAttribute('href', '/workflows');
    expect(
      screen.getByRole('link', { name: 'stats.workflowCount' }),
    ).toHaveClass('hover:border-teal-200');
    expect(
      screen.getByRole('link', { name: 'stats.skillCount' }),
    ).toHaveAttribute('href', '/skills');
    expect(screen.getByRole('link', { name: 'stats.skillCount' })).toHaveClass(
      'hover:border-green-200',
    );
    expect(
      screen.getByRole('link', { name: 'stats.toolCount' }),
    ).toHaveAttribute('href', '/tools');
    expect(screen.getByRole('link', { name: 'stats.toolCount' })).toHaveClass(
      'hover:border-orange-200',
    );
    expect(
      screen.getByRole('link', { name: 'stats.weeklyTasks' }),
    ).toHaveAttribute('href', '/tasks');
    expect(screen.getByRole('link', { name: 'stats.weeklyTasks' })).toHaveClass(
      'hover:border-amber-200',
    );
    expect(
      screen.getByRole('link', { name: 'stats.activeScheduled' }),
    ).toHaveAttribute('href', '/tasks');
    expect(
      screen.getByRole('link', { name: 'stats.activeScheduled' }),
    ).toHaveClass('hover:border-violet-200');
    expect(
      screen.getByRole('link', { name: 'stats.modelCount' }),
    ).toHaveAttribute('href', '/models');
    expect(screen.getByRole('link', { name: 'stats.modelCount' })).toHaveClass(
      'hover:border-pink-200',
    );
    expect(screen.getByText('stats.systemStatus').closest('a')).toBeNull();
  });
});
