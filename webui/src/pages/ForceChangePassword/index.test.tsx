// Regression (PLT-0892): the success notice must be visible before the auth refresh
// replaces this page; previously setSuccess ran after refresh() had unmounted it.
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ForceChangePasswordPage from './index';

const refresh = vi.fn(() => Promise.resolve());
const changePassword = vi.fn(() => Promise.resolve());

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'tmp' }, refresh, logout: vi.fn() }),
}));
vi.mock('@/api/auth', () => ({ authApi: { changePassword: (...args: unknown[]) => changePassword(...args) } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('@/components/layout/AuthLayout', () => ({ default: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock('@/components/common/PasswordInput', () => ({
  default: (props: any) => <input {...props} type="password" />,
}));

describe('ForceChangePasswordPage', () => {
  it('shows the success notice before refreshing auth', async () => {
    render(<ForceChangePasswordPage />);
    const inputs = document.querySelectorAll('input[type=password]');
    fireEvent.change(inputs[0], { target: { value: 'old-pass-1' } });
    fireEvent.change(inputs[1], { target: { value: 'Fresh!Pass1' } });
    fireEvent.change(inputs[2], { target: { value: 'Fresh!Pass1' } });
    fireEvent.submit(document.querySelector('form')!);
    await waitFor(() => expect(changePassword).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('forceChange.success')).toBeTruthy());
    // refresh happens only after the notice was rendered
    expect(refresh).not.toHaveBeenCalled();
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });
});
