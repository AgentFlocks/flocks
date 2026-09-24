// Regression (PLT-0907): clicking the dimmed area outside the one-time-password
// dialog must close it and log out. The centering wrapper (z-50) covers the
// backdrop (z-40), so the outside-click handler has to live on the wrapper.
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

describe('AdminUsersPage credential modal', () => {
  it('closes and logs out when the dimmed area outside the dialog is clicked', async () => {
    render(<AdminUsersPage />);
    fireEvent.click(screen.getByText('admin.resetAction'));
    await waitFor(() => expect(screen.getByText('admin.resetDialogTitle')).toBeTruthy());
    // The element a real pointer hits on the dimmed area is the z-50 wrapper.
    const wrapper = screen.getByText('admin.resetDialogTitle').closest('.z-50') as HTMLElement;
    expect(wrapper).toBeTruthy();
    fireEvent.click(wrapper);
    await waitFor(() => expect(screen.queryByText('admin.resetDialogTitle')).toBeNull());
    expect(logout).toHaveBeenCalledTimes(1);
  });
});

describe('AdminUsersPage narrow viewport', () => {
  // 375px 下卡片只有 291px 宽，带 whitespace-nowrap 的「最近登录」列会把表格撑出 overflow-hidden 卡片、裁掉「重置密码」；<md 隐藏该列
  it('hides the last-login column below md so the action column stays reachable', async () => {
    render(<AdminUsersPage />);
    const th = await screen.findByText('admin.columnLastLogin');
    expect(th).toHaveClass('hidden');
    expect(th).toHaveClass('md:table-cell');
    const actionTh = screen.getByText('admin.columnActions');
    expect(actionTh).not.toHaveClass('hidden');
  });
});
