import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

  it('does not render the custom page creation entry on the home page', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: 'createUserDefinedPage' })).not.toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });
});
