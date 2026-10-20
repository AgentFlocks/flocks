import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import SettingsPage from './index';
import { ThemeContext, type Theme, type ThemeMode } from '@/contexts/ThemeContext';
import { ToastProvider } from '@/components/common/Toast';

const { changeLanguage, dataRetentionApi, flocksproUsersApi, setTheme, toolFailureConfigApi, useAuth } = vi.hoisted(() => ({
  changeLanguage: vi.fn(),
  dataRetentionApi: {
    get: vi.fn(),
    preview: vi.fn(),
    update: vi.fn(),
  },
  flocksproUsersApi: {
    hasCapability: vi.fn(),
  },
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

vi.mock('@/api/flocksproUsers', () => ({
  flocksproUsersApi,
}));

vi.mock('@/api/toolFailureConfig', () => ({
  toolFailureConfigApi,
}));

vi.mock('@/api/dataRetention', () => ({
  dataRetentionApi,
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

vi.mock('./ArchivedDataPanel', () => ({
  default: () => <div>archived data page</div>,
}));

vi.mock('@/pages/SecurityConfig', () => ({
  default: () => <div>security config page</div>,
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
    flocksproUsersApi.hasCapability.mockResolvedValue(true);
    toolFailureConfigApi.get.mockResolvedValue({ disableOnRepeatedFailure: true });
    toolFailureConfigApi.update.mockImplementation(async (disableOnRepeatedFailure: boolean) => ({
      disableOnRepeatedFailure,
    }));
    dataRetentionApi.get.mockResolvedValue({
      logDays: 30,
      messageDays: 0,
      sessionDays: 0,
      running: false,
      lastRun: null,
    });
    dataRetentionApi.preview.mockResolvedValue({
      sessions: 0, messages: 0, logPaths: 0, logBytes: 0, recordFiles: 0, recordBytes: 0, skippedBusy: 0,
    });
    dataRetentionApi.update.mockImplementation(async (policy: Record<string, number>) => ({
      ...policy,
      running: true,
      lastRun: null,
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

  it('previews the cleanup and saves the data retention policy only after the admin confirms', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    dataRetentionApi.preview.mockResolvedValue({
      sessions: 2, messages: 5, logPaths: 1, logBytes: 2048, recordFiles: 3, recordBytes: 1024, skippedBusy: 0,
    });

    renderSettings('/settings/preferences');

    const sessions = await screen.findByRole('combobox', { name: 'dataRetentionSessions' });
    await waitFor(() => expect(sessions).toHaveValue('0'));
    const save = screen.getByRole('button', { name: 'dataRetentionSave' });
    expect(save).toBeDisabled();

    await user.selectOptions(sessions, '90');
    await user.selectOptions(screen.getByRole('combobox', { name: 'dataRetentionMessages' }), '30');
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => {
      expect(dataRetentionApi.update).toHaveBeenCalledWith({ logDays: 30, messageDays: 30, sessionDays: 90 });
    });
    expect(dataRetentionApi.preview).toHaveBeenCalledWith({ logDays: 30, messageDays: 30, sessionDays: 90 });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(dataRetentionApi.preview.mock.invocationCallOrder[0])
      .toBeLessThan(dataRetentionApi.update.mock.invocationCallOrder[0]);
    confirmSpy.mockRestore();
  });

  it('keeps the old data retention policy when the admin cancels the deletion', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    dataRetentionApi.preview.mockResolvedValue({
      sessions: 4, messages: 0, logPaths: 0, logBytes: 0, recordFiles: 0, recordBytes: 0, skippedBusy: 0,
    });

    renderSettings('/settings/preferences');

    const sessions = await screen.findByRole('combobox', { name: 'dataRetentionSessions' });
    await waitFor(() => expect(sessions).toHaveValue('0'));
    await user.selectOptions(sessions, '7');
    await user.click(screen.getByRole('button', { name: 'dataRetentionSave' }));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(dataRetentionApi.update).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('saves without asking when the new data retention policy deletes nothing', async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderSettings('/settings/preferences');

    const logs = await screen.findByRole('combobox', { name: 'dataRetentionLogs' });
    await waitFor(() => expect(logs).toHaveValue('30'));
    await user.selectOptions(logs, '0');
    await user.click(screen.getByRole('button', { name: 'dataRetentionSave' }));

    await waitFor(() => {
      expect(dataRetentionApi.update).toHaveBeenCalledWith({ logDays: 0, messageDays: 0, sessionDays: 0 });
    });
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('offers a hand-set retention period as its own choice', async () => {
    dataRetentionApi.get.mockResolvedValue({
      logDays: 45, messageDays: 0, sessionDays: 0, running: false, lastRun: null,
    });

    renderSettings('/settings/preferences');

    const logs = await screen.findByRole('combobox', { name: 'dataRetentionLogs' });
    await waitFor(() => expect(logs).toHaveValue('45'));
    expect(within(logs).getAllByRole('option').map((option) => (option as HTMLOptionElement).value))
      .toEqual(['7', '14', '30', '45', '90', '180', '365', '0']);
  });

it('shows a partial-failure note when the last cleanup had errors', async () => {
    dataRetentionApi.get.mockResolvedValue({
      logDays: 7, messageDays: 0, sessionDays: 0, running: false,
      lastRun: { startedAt: 1, finishedAt: 2, sessions: 0, messages: 0, logPaths: 0, recordFiles: 0, freedBytes: 0, skippedBusy: 0, errors: 3 },
    });

    renderSettings('/settings/preferences');

    const status = await screen.findByText(/dataRetentionPartialFailure/);
    expect(status).toBeInTheDocument();
  });

  it('keeps polling (re-arms) while a cleanup is still running', async () => {
    vi.useFakeTimers();
    try {
      // Never finishes: the timer must re-arm on every tick, not stop after one.
      dataRetentionApi.get.mockResolvedValue({ logDays: 7, messageDays: 0, sessionDays: 0, running: true, lastRun: null });

      renderSettings('/settings/preferences');
      await act(async () => { await Promise.resolve(); });
      expect(screen.getByText('dataRetentionRunning')).toBeInTheDocument();
      const afterMount = dataRetentionApi.get.mock.calls.length;

      for (let i = 0; i < 3; i += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      }

      // Three intervals => at least three more polls (would be one with the bug).
      expect(dataRetentionApi.get.mock.calls.length).toBeGreaterThanOrEqual(afterMount + 3);
      expect(screen.getByText('dataRetentionRunning')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

it('restarts polling when the admin refreshes after a timeout', async () => {
    vi.useFakeTimers();
    try {
      dataRetentionApi.get.mockResolvedValue({ logDays: 7, messageDays: 0, sessionDays: 0, running: true, lastRun: null });

      renderSettings('/settings/preferences');
      await act(async () => { await Promise.resolve(); });

      // Run past the 60s poll limit: it gives up and offers a refresh.
      for (let i = 0; i < 32; i += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      }
      expect(screen.getByText('dataRetentionStatusUnknown')).toBeInTheDocument();
      const refreshButton = screen.getByRole('button', { name: /dataRetentionRefresh/ });

      const before = dataRetentionApi.get.mock.calls.length;
      // The manual refresh fires one request of its own...
      await act(async () => { refreshButton.click(); await vi.advanceTimersByTimeAsync(0); });
      const afterManual = dataRetentionApi.get.mock.calls.length;
      expect(afterManual).toBeGreaterThan(before);
      // ...and a *timed* poll must fire afterwards, proving polling actually
      // resumed (the old `calls > before` check could not tell the resumed poll
      // apart from the manual GET itself).
      await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      expect(dataRetentionApi.get.mock.calls.length).toBeGreaterThan(afterManual);
      expect(screen.getByText('dataRetentionRunning')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the refresh button when a manual refresh after a timeout fails', async () => {
    vi.useFakeTimers();
    try {
      dataRetentionApi.get.mockResolvedValue({ logDays: 7, messageDays: 0, sessionDays: 0, running: true, lastRun: null });

      renderSettings('/settings/preferences');
      await act(async () => { await Promise.resolve(); });

      for (let i = 0; i < 32; i += 1) {
        await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
      }
      expect(screen.getByText('dataRetentionStatusUnknown')).toBeInTheDocument();
      const refreshButton = screen.getByRole('button', { name: /dataRetentionRefresh/ });

      // The refresh itself fails: the unknown state and the button must stay,
      // instead of collapsing into a blank "never ran a cleanup" dead end.
      dataRetentionApi.get.mockRejectedValueOnce(new Error('network'));
      await act(async () => { refreshButton.click(); await vi.advanceTimersByTimeAsync(0); });

      expect(screen.getByText('dataRetentionStatusUnknown')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /dataRetentionRefresh/ })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('hides the data retention policy from members', async () => {
    useAuth.mockReturnValue({
      user: { id: 'user-2', username: 'member', role: 'member', status: 'active', must_reset_password: false },
    });

    renderSettings('/settings/preferences');

    expect(await screen.findByRole('switch', { name: 'toolFailureAutoDisable' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'dataRetentionSessions' })).not.toBeInTheDocument();
    expect(dataRetentionApi.get).not.toHaveBeenCalled();
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

  it('renders management content without a second set of navigation tabs', async () => {
    renderSettings('/settings/system-logs');
    expect(await screen.findByText('system logs page')).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByTestId('settings-content')).toContainElement(screen.getByText('system logs page'));
  });

  it('opens the Flocks Pro page on its own, without the management tabs', async () => {
    const { container } = renderSettings('/settings/flockspro');

    expect(await screen.findByText('flocks pro page')).toBeInTheDocument();
    expect(flocksproUsersApi.hasCapability).toHaveBeenCalled();
    expect(screen.queryByRole('navigation', { name: 'settingsTitle' })).not.toBeInTheDocument();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(container.querySelector('[data-testid="settings-content"]')).toContainElement(screen.getByText('flocks pro page'));
  });

  it('redirects members away from admin-only settings', async () => {
    useAuth.mockReturnValue({
      user: { id: 'user-2', username: 'member', role: 'member', status: 'active', must_reset_password: false },
    });
    renderSettings('/settings/security-config');
    expect(await screen.findByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();
    expect(screen.queryByText('security config page')).not.toBeInTheDocument();
  });

  it('renders security config in settings for Flocks Pro admins', async () => {
    renderSettings('/settings/security-config');

    expect(await screen.findByText('security config page')).toBeInTheDocument();
    expect(flocksproUsersApi.hasCapability).toHaveBeenCalled();
  });

  it('renders audit logs in settings for Flocks Pro admins', async () => {
    renderSettings('/settings/audit-logs');

    expect(await screen.findByText('audit logs page')).toBeInTheDocument();
    expect(flocksproUsersApi.hasCapability).toHaveBeenCalled();
  });

  it('renders archived data management for local users', async () => {
    renderSettings('/settings/archived-data');

    expect(await screen.findByText('archived data page')).toBeInTheDocument();
  });

  it('hides audit logs when Flocks Pro capability is unavailable', async () => {
    flocksproUsersApi.hasCapability.mockResolvedValue(false);

    renderSettings('/settings/security-config');

    expect(await screen.findByRole('heading', { name: 'settingsPreferences' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'auditLogs' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'securityConfig' })).not.toBeInTheDocument();
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
    expect(screen.queryByRole('link', { name: 'Flocks Pro' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'auditLogs' })).not.toBeInTheDocument();
  });
});
