import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import Home from './index';

const { createMock, navigateMock, toastErrorMock, useAuthMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  navigateMock: vi.fn(),
  toastErrorMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
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
  useStats: () => ({
    stats: null,
    loading: false,
    error: null,
  }),
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

describe('Home create user defined page entry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMock.mockResolvedValue({ id: 'session-user-defined-1' });
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

    await user.click(screen.getByRole('button', { name: 'createUserDefinedPage' }));

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({ title: 'createUserDefinedPageSessionTitle' });
    });

    expect(navigateMock).toHaveBeenCalledWith(
      `/sessions?session=session-user-defined-1&message=${encodeURIComponent('createUserDefinedPageInitialMessage')}`,
    );
  });

  it('hides the create user defined page entry for non-admin users', () => {
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

    expect(screen.queryByRole('button', { name: 'createUserDefinedPage' })).not.toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });
});
