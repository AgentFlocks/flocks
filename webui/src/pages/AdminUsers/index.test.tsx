// The open product's account page: the signed-in account and a one-time
// password reset for it (dev's markup; member management is the extension's).
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AdminUsersPage from './index';

const logout = vi.fn(() => Promise.resolve());
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', username: 'admin', role: 'admin', last_login_at: null }, logout }),
}));
vi.mock('@/api/auth', () => ({
  authApi: { resetPassword: vi.fn(() => Promise.resolve({ success: true, temporary_password: 'OTP-1234-5678', must_reset_password: true })) },
}));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => () => Promise.resolve(true) }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/components/common/CopyButton', () => ({ default: () => <button type="button">copy</button> }));
vi.mock('@/components/common/PageHeader', () => ({ default: ({ title, actions }: any) => <div><h1>{title}</h1>{actions}</div> }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k, i18n: { language: 'zh-CN' } }) }));

describe('AdminUsersPage', () => {
  it('lists the signed-in account with every column', () => {
    render(<AdminUsersPage />);
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual([
      'admin.columnUsername', 'admin.columnRole', 'admin.columnLastLogin', 'admin.columnActions',
    ]);
  });

  it('shows the one-time password after a reset and logs out once it is acknowledged', async () => {
    render(<AdminUsersPage />);
    fireEvent.click(screen.getByRole('button', { name: 'admin.resetAction' }));
    expect(await screen.findByText('OTP-1234-5678')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'admin.resetDialogDone' }));
    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('OTP-1234-5678')).not.toBeInTheDocument();
  });
});
