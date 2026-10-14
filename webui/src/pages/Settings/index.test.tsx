import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import SettingsPage from './index';
import { ThemeContext, type Theme, type ThemeMode } from '@/contexts/ThemeContext';
import { ToastProvider } from '@/components/common/Toast';

const { changeLanguage, setTheme, toolFailureConfigApi, useAuth } = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
  setTheme: vi.fn(),
  toolFailureConfigApi: {
    get: vi.fn(),
    update: vi.fn(),
  },
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

vi.mock('@/api/toolFailureConfig', () => ({
  toolFailureConfigApi,
}));

vi.mock('@/pages/Config', () => ({
  default: () => <div>account page</div>,
}));

vi.mock('@/pages/SystemLog', () => ({
  default: () => <div>system logs page</div>,
}));

vi.mock('@/pages/VoiceSettings', () => ({
  default: () => <div>voice settings page</div>,
}));

vi.mock('./ArchivedDataPanel', () => ({
  default: () => <div>archived data page</div>,
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

function renderSettings(path: string, theme: Theme = 'light', state?: Record<string, unknown>, mode: ThemeMode = theme) {
  return render(
    <ToastProvider>
      <ThemeContext.Provider
        value={{
          theme,
          mode,
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
      </ThemeContext.Provider>
    </ToastProvider>,
  );
}

describe('SettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    toolFailureConfigApi.get.mockResolvedValue({ disableOnRepeatedFailure: true });
    toolFailureConfigApi.update.mockImplementation(async (disableOnRepeatedFailure: boolean) => ({
      disableOnRepeatedFailure,
    }));
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

  it('marks "follow system" as the selected theme and lets the user pick it', async () => {
    const user = userEvent.setup();

    // Resolved dark (the OS is dark) but the stored choice is "system".
    renderSettings('/settings/preferences', 'dark', undefined, 'system');

    expect(screen.getByRole('button', { name: 'systemTheme' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'darkTheme' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'lightTheme' })).toHaveAttribute('aria-pressed', 'false');

    await user.click(screen.getByRole('button', { name: 'lightTheme' }));
    expect(setTheme).toHaveBeenCalledWith('light');
    await user.click(screen.getByRole('button', { name: 'systemTheme' }));
    expect(setTheme).toHaveBeenCalledWith('system');
  });

  it('loads and updates repeated tool failure auto-disable', async () => {
    const user = userEvent.setup();
    toolFailureConfigApi.get.mockResolvedValue({ disableOnRepeatedFailure: false });

    renderSettings('/settings/preferences');

    const autoDisableSwitch = await screen.findByRole('switch', {
      name: 'toolFailureAutoDisable',
    });
    await waitFor(() => {
      expect(autoDisableSwitch).toHaveAttribute('aria-checked', 'false');
    });

    await user.click(autoDisableSwitch);

    await waitFor(() => {
      expect(toolFailureConfigApi.update).toHaveBeenCalledWith(true);
      expect(autoDisableSwitch).toHaveAttribute('aria-checked', 'true');
    });
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

  it('switches between management pages with tabs at the top, whatever the workspace sidebar shows', async () => {
    const user = userEvent.setup();
    const { container } = renderSettings('/settings/system-logs');

    expect(await screen.findByText('system logs page')).toBeInTheDocument();
    expect(container.querySelector('aside')).toBeNull();
    const tabs = screen.getByRole('navigation', { name: 'settingsTitle' });
    await waitFor(() => expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      // Security config and audit logs are extension sections; a stock build has none.
      'settingsPreferences', 'archivedData', 'accountManagement', 'systemLog', 'networkProxy', 'voiceInput',
    ]));
    expect(within(tabs).getByRole('tab', { name: 'systemLog' })).toHaveAttribute('aria-selected', 'true');
    expect(within(tabs).queryByRole('tab', { name: /flocks/i })).not.toBeInTheDocument();

    await user.click(within(tabs).getByRole('tab', { name: 'archivedData' }));
    expect(await screen.findByText('archived data page')).toBeInTheDocument();
    expect(within(tabs).getByRole('tab', { name: 'archivedData' })).toHaveAttribute('aria-selected', 'true');
  });

  it('lists only the tabs a member may open', async () => {
    useAuth.mockReturnValue({
      user: { id: 'user-2', username: 'member', role: 'member', status: 'active', must_reset_password: false },
    });
    renderSettings('/settings/preferences');

    const tabs = await screen.findByRole('navigation', { name: 'settingsTitle' });
    expect(within(tabs).getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'settingsPreferences', 'archivedData', 'accountManagement', 'systemLog',
    ]);
  });

  it('renders archived data management for local users', async () => {
    renderSettings('/settings/archived-data');

    expect(await screen.findByText('archived data page')).toBeInTheDocument();
  });

  it('renders shared voice settings for an administrator without any extension or license', async () => {
    renderSettings('/settings/voice-input');
    expect(await screen.findByText('voice settings page')).toBeInTheDocument();
  });

  it('keeps shared voice configuration restricted to administrators', async () => {
    useAuth.mockReturnValue({ user: { id: 'member', role: 'user' } });
    renderSettings('/settings/voice-input');
    expect(await screen.findByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();
    expect(screen.queryByText('voice settings page')).not.toBeInTheDocument();
  });

});
