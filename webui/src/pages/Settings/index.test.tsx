import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import SettingsPage from './index';
import { ThemeContext, type Theme } from '@/contexts/ThemeContext';

const { changeLanguage, setTheme, useAuth } = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
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

vi.mock('@/pages/Config', () => ({
  default: () => <div>account page</div>,
}));

vi.mock('@/pages/SystemLog', () => ({
  default: () => <div>system logs page</div>,
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

  it('renders existing configuration pages inside the settings shell', async () => {
    renderSettings('/settings/models');

    expect(await screen.findByText('models page')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'models' })).toHaveAttribute('href', '/settings/models');
    expect(screen.getByRole('link', { name: 'channels' })).toHaveAttribute('href', '/settings/channels');
  });

  it('returns to the page captured before opening settings', async () => {
    const user = userEvent.setup();

    renderSettings('/settings/models', 'light', {
      from: {
        pathname: '/contracts/webui/workspaces/soc_ui',
        search: '?view=posture',
        hash: '#top',
      },
    });

    await user.click(screen.getByRole('link', { name: 'channels' }));
    expect(await screen.findByText('channels page')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'settingsBack' }));

    expect(await screen.findByTestId('location')).toHaveTextContent(
      '/contracts/webui/workspaces/soc_ui?view=posture#top',
    );
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
  });
});
