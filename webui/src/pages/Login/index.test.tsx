import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LoginPage from './index';

const { login } = vi.hoisted(() => ({ login: vi.fn() }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ login }) }));
vi.mock('@/contexts/ProductNameContext', () => ({ useProductName: () => ({ productName: 'Flocks' }) }));
vi.mock('@/components/layout/AuthLayout', () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

const username = () => screen.getByPlaceholderText('fields.usernamePlaceholder');
const password = () => screen.getByPlaceholderText('fields.passwordPlaceholder');
const submit = () => fireEvent.click(screen.getByRole('button', { name: 'actions.login' }));

describe('LoginPage (v0.21 #1)', () => {
  beforeEach(() => {
    login.mockReset();
  });

  it('asks for each empty field under it instead of trying to log in', () => {
    render(<LoginPage />);
    submit();

    expect(screen.getByText('login.usernameRequired')).toBeInTheDocument();
    expect(screen.getByText('login.passwordRequired')).toBeInTheDocument();
    expect(username()).toHaveAttribute('aria-invalid', 'true');
    expect(login).not.toHaveBeenCalled();
  });

  it('treats a username of spaces as empty and clears a message once the field is filled', () => {
    render(<LoginPage />);
    fireEvent.change(username(), { target: { value: '   ' } });
    fireEvent.change(password(), { target: { value: 'secret' } });
    submit();

    expect(screen.getByText('login.usernameRequired')).toBeInTheDocument();
    expect(screen.queryByText('login.passwordRequired')).not.toBeInTheDocument();
    fireEvent.change(username(), { target: { value: 'admin' } });
    expect(screen.queryByText('login.usernameRequired')).not.toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it('shows the server answer for filled-in credentials', async () => {
    login.mockRejectedValue({ response: { data: { message: '用户名或密码错误' } } });
    render(<LoginPage />);
    fireEvent.change(username(), { target: { value: 'admin' } });
    fireEvent.change(password(), { target: { value: 'wrong' } });
    submit();

    await waitFor(() => expect(login).toHaveBeenCalledWith('admin', 'wrong'));
    expect(await screen.findByText('用户名或密码错误')).toBeInTheDocument();
  });
});
