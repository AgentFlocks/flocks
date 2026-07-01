import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import SettingsPage from './index';
import { ThemeContext, type Theme } from '@/contexts/ThemeContext';

const { changeLanguage, flocksproUsersApi, setTheme, useAuth } = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
  flocksproUsersApi: {
    hasCapability: vi.fn(),
  },
  setTheme: vi.fn(),
  useAuth: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: {
      language: 'zh-CN',
      changeLanguage,
    },
  }),
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth,
}));

vi.mock('@/api/flocksproUsers', () => ({
  flocksproUsersApi,
}));

vi.mock('@/pages/Config', () => ({
  default: () => <div>account page</div>,
}));

vi.mock('@/pages/SystemLog', () => ({
  default: () => <div>system logs page</div>,
}));

vi.mock('@/pages/AuditLogs', () => ({
  default: () => <div>audit logs page</div>,
}));

vi.mock('@/pages/FlocksproUpgrade', () => ({
  default: () => <div>flocks pro page</div>,
}));

vi.mock('@/pages/Model', () => ({
  default: () => <div>models page</div>,
}));

vi.mock('@/pages/Channel', () => ({
  default: () => <div>channels page</div>,
}));

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</div>;
}

function renderSettings(path: string, theme: Theme = 'light', state?: Record<string, unknown>) {
  return render(
    <ThemeContext.Provider
      value={{
        theme,
        effectiveTheme: theme,
        toggleTheme: vi.fn(),
        setTheme,
        setTemporaryThemeOverride: vi.fn(),
      }}
    >
      <MemoryRouter initialEntries={[state ? { pathname: path, state } : path]}>
        <Routes>
          <Route path="/settings/:sectionId?" element={<SettingsPage />} />
          <Route path="/models" element={<div>models page</div>} />
          <Route path="/channels" element={<div>channels page</div>} />
          <Route path="/contracts/webui/workspaces/:workspaceId" element={<LocationProbe />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </ThemeContext.Provider>,
  );
}

describe('SettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    flocksproUsersApi.hasCapability.mockResolvedValue(true);
    useAuth.mockReturnValue({
      user: {
        id: 'user-1',
        username: 'admin',
        role: 'admin',
        status: 'active',
        must_reset_password: false,
      },
    });
  });

  it('renders preference controls for language and theme', async () => {
    const user = userEvent.setup();

    renderSettings('/settings/preferences', 'light');

    expect(screen.getByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'EN' }));
    expect(changeLanguage).toHaveBeenCalledWith('en-US');

    await user.click(screen.getByRole('button', { name: 'darkTheme' }));
    expect(setTheme).toHaveBeenCalledWith('dark');
  });

  it('redirects legacy model and channel settings URLs to workspace pages', async () => {
    const { unmount } = renderSettings('/settings/models');

    expect(await screen.findByText('models page')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'models' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'channels' })).not.toBeInTheDocument();

    unmount();
    renderSettings('/settings/channels');

    expect(await screen.findByText('channels page')).toBeInTheDocument();
  });

  it('returns to the page captured before opening settings', async () => {
    const user = userEvent.setup();

    renderSettings('/settings/system-logs', 'light', {
      from: {
        pathname: '/contracts/webui/workspaces/scene_workspace',
        search: '?view=posture',
        hash: '#top',
      },
    });

    expect(await screen.findByText('system logs page')).toBeInTheDocument();
    await user.click(screen.getAllByRole('link', { name: 'accountManagement' })[0]);
    expect(await screen.findByText('account page')).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: 'settingsBack' })[0]);

    expect(await screen.findByTestId('location')).toHaveTextContent(
      '/contracts/webui/workspaces/scene_workspace?view=posture#top',
    );
  });

  it('keeps return and section navigation available outside the desktop sidebar', async () => {
    renderSettings('/settings/system-logs');

    expect(await screen.findByText('system logs page')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'settingsBack' })).toHaveLength(2);

    const mobileNav = screen.getByRole('navigation', { name: 'settingsTitle' });
    expect(within(mobileNav).getByRole('link', { name: 'accountManagement' })).toHaveAttribute('href', '/settings/account');
    expect(within(mobileNav).getByRole('link', { name: 'auditLogs' })).toHaveAttribute('href', '/settings/audit-logs');
    expect(within(mobileNav).queryByRole('link', { name: 'models' })).not.toBeInTheDocument();
    expect(within(mobileNav).queryByRole('link', { name: 'channels' })).not.toBeInTheDocument();
  });

  it('renders audit logs in settings for Flocks Pro admins', async () => {
    renderSettings('/settings/audit-logs');

    expect(await screen.findByText('audit logs page')).toBeInTheDocument();
    expect(flocksproUsersApi.hasCapability).toHaveBeenCalled();
  });

  it('hides audit logs when Flocks Pro capability is unavailable', async () => {
    flocksproUsersApi.hasCapability.mockResolvedValue(false);

    renderSettings('/settings/audit-logs');

    expect(await screen.findByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'auditLogs' })).not.toBeInTheDocument();
  });

  it('hides Flocks Pro settings for non-admin users', async () => {
    useAuth.mockReturnValue({
      user: {
        id: 'user-2',
        username: 'member',
        role: 'member',
        status: 'active',
        must_reset_password: false,
      },
    });

    renderSettings('/settings/flockspro');

    expect(await screen.findByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'flocksproUpgrade' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'auditLogs' })).not.toBeInTheDocument();
  });
});
