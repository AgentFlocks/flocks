import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import SetupAdminPage from './index';

const { useAuth } = vi.hoisted(() => ({ useAuth: vi.fn() }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/contexts/ProductNameContext', () => ({ useProductName: () => ({ productName: 'Flocks' }) }));
vi.mock('@/components/layout/AuthLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

function auth(setupAllowed: boolean, localSetupUrl: string | null = null) {
  const value = {
    setupAllowed,
    localSetupUrl,
    bootstrapAdmin: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
  };
  useAuth.mockReturnValue(value);
  return value;
}

describe('SetupAdminPage', () => {
  beforeEach(() => useAuth.mockReset());

  it('lets a browser on the server create the first administrator', () => {
    auth(true);
    render(<SetupAdminPage />);

    expect(screen.getByText('setup.submitButton')).toBeInTheDocument();
    expect(screen.queryByText('flocks admin generate-one-time-password')).not.toBeInTheDocument();
  });

  it('tells a browser elsewhere how to get in instead of showing the form', () => {
    const value = auth(false, 'http://127.0.0.1:18573');
    render(<SetupAdminPage />);

    expect(screen.queryByText('setup.submitButton')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('setup.adminPassword')).not.toBeInTheDocument();
    expect(screen.getByText('flocks admin generate-one-time-password')).toBeInTheDocument();
    expect(screen.getByText('http://127.0.0.1:18573')).toBeInTheDocument();
    // The step's own text around the command: "<or run on that machine> … <then sign in>".
    expect(screen.getByText(/^setup\.remoteCommandOr setup\.remoteCommandAfter$/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('setup.remoteRefresh'));
    expect(value.refresh).toHaveBeenCalledTimes(1);
    expect(value.bootstrapAdmin).not.toHaveBeenCalled();
  });

  it('offers only the command when loopback does not reach the service', () => {
    auth(false, null);
    render(<SetupAdminPage />);

    expect(screen.queryByText('setup.remoteLocalBrowser')).not.toBeInTheDocument();
    expect(screen.queryByText(/127\.0\.0\.1/)).not.toBeInTheDocument();
    expect(screen.getByText(/^setup\.remoteCommand setup\.remoteCommandAfter$/)).toBeInTheDocument();
    expect(screen.getByText('flocks admin generate-one-time-password')).toBeInTheDocument();
  });
});
