import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Case list (编号 | 场景):
//   FP1 a machine with the Python enterprise package: its licence is shown, Console offers the
//       installer, the upgrade downloads and starts it, and the page follows the installer
//   FP2 run by systemd (the offline package): the page downloads the installer and gives root's command
//   FP3 an account that may not write /opt/flockspro yet: root's one-time command first
//   FP4 the open edition with no licence: no Console check, only the way by hand
//   FP5 a refusal from Console is shown as it is
//   FP6 opened again while a download runs in the background: it follows it to its end

const { situation, check, start, progress, consoleLoginSession } = vi.hoisted(() => ({
  situation: vi.fn(),
  check: vi.fn(),
  start: vi.fn(),
  progress: vi.fn(),
  consoleLoginSession: vi.fn(),
}));

vi.mock('@/api/enterpriseUpgrade', () => ({ enterpriseUpgradeApi: { situation, check, start, progress } }));
vi.mock('@/api/auth', () => ({ authApi: { consoleLoginSession, startConsoleLogin: vi.fn() } }));
vi.mock('react-i18next', () => {
  // One function, as react-i18next hands out for a language.
  const t = (key: string, options?: Record<string, unknown>) =>
    options ? `${key} ${Object.entries(options).filter(([k]) => k !== 'defaultValue').map(([, v]) => String(v)).join(' ')}`.trim() : key;
  return { useTranslation: () => ({ t }) };
});

import FlocksproUpgradePage from './index';

const base = {
  available: true,
  previous: { version: '2026.10.19', license_id: 'lic-old-7', license_status: 'commercial', pro_enabled: true, expires_at: 1798761600 },
  platform: 'linux-arm64',
  account: 'flocks',
  root: false,
  systemd: false,
  install_root: '/opt/flockspro',
  install_root_ready: true,
  prepare_command: null,
  can_start: true,
  manual_command: 'bash flockspro-<版本>-linux-arm64.run',
  upgrade: null,
};
const offer = { url: 'https://console.example/r.run', sha256: 'a'.repeat(64), release_id: '2026.11.5-linux-arm64', version: '2026.11.5', size: 10, filename: 'flockspro-2026.11.5-linux-arm64.run' };

beforeEach(() => {
  vi.clearAllMocks();
  consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@example.com' });
});

describe('Flocks Pro page of the open edition', () => {
  it('FP1 shows the previous licence, checks Console and starts the installer', async () => {
    situation.mockResolvedValue(base);
    check.mockResolvedValue(offer);
    start.mockImplementation(async (onEvent: (e: unknown) => void) => {
      onEvent({ stage: 'downloading', version: '2026.11.5', received: 5, total: 10 });
      return { phase: 'installing', version: '2026.11.5', pid: 4242, log_tail: ['== bash flockspro-2026.11.5-linux-arm64.run'] };
    });
    progress.mockResolvedValue({ phase: 'installing', version: '2026.11.5', log_tail: [] });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('machine.previousTitle')).toBeInTheDocument();
    expect(screen.getByText('lic-old-7')).toBeInTheDocument();
    expect(screen.getByText('machine.pythonEdition 2026.10.19')).toBeInTheDocument();
    expect(screen.getByText('upgrade.consoleAccount ops@example.com')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /upgrade\.check/ }));
    expect(await screen.findByText('upgrade.offer 2026.11.5')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'upgrade.start 2026.11.5' }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    const status = await screen.findByRole('status');
    expect(within(status).getByText('progress.installing 2026.11.5')).toBeInTheDocument();
    // While it runs, nothing starts a second one.
    expect(screen.getByRole('button', { name: /upgrade\.check/ })).toBeDisabled();
  });

  it('FP2 on a systemd machine gives root the command for the downloaded installer', async () => {
    situation.mockResolvedValue({ ...base, systemd: true, can_start: false, manual_command: 'sudo bash flockspro-<版本>-linux-arm64.run' });
    check.mockResolvedValue(offer);
    start.mockResolvedValue({ phase: 'command', command: 'sudo bash /var/lib/flocks/.flocks/run/enterprise-upgrade/flockspro-2026.11.5-linux-arm64.run' });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('upgrade.systemd')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /upgrade\.check/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.download' }));

    expect(await screen.findByText('progress.command')).toBeInTheDocument();
    expect(screen.getByText(/sudo bash \/var\/lib\/flocks\/\.flocks\/run\/enterprise-upgrade\//)).toBeInTheDocument();
    expect(screen.getByText('upgrade.manual root')).toBeInTheDocument();
  });

  it('FP3 asks root to hand the install directory over first', async () => {
    situation.mockResolvedValue({
      ...base,
      install_root_ready: false,
      can_start: false,
      prepare_command: 'sudo install -d -o flocks -g flocks -m 0755 /opt/flockspro',
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('sudo install -d -o flocks -g flocks -m 0755 /opt/flockspro')).toBeInTheDocument();
    situation.mockResolvedValue(base);
    await userEvent.click(screen.getByRole('button', { name: /upgrade\.recheck/ }));
    await waitFor(() => expect(screen.queryByText(/sudo install -d/)).not.toBeInTheDocument());
  });

  it('FP4 the open edition without a licence: only the way by hand', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('machine.openTitle')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /upgrade\.check/ })).not.toBeInTheDocument();
    expect(screen.getByText('bash flockspro-<版本>-linux-arm64.run')).toBeInTheDocument();
  });

  it('FP5 shows what Console said', async () => {
    situation.mockResolvedValue(base);
    check.mockRejectedValue({ response: { data: { detail: 'Console 还没有给授权 lic-old-7 的新企业版安装包（linux-arm64）' } } });
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: /upgrade\.check/ }));
    expect(await screen.findByText(/Console 还没有给授权 lic-old-7/)).toBeInTheDocument();
    expect(start).not.toHaveBeenCalled();
  });

  it('FP6 follows a download it did not start until it ends', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      situation.mockResolvedValue({ ...base, upgrade: { phase: 'downloading', version: '2026.11.5', received: 1, total: 4 } });
      progress
        .mockResolvedValueOnce({ phase: 'downloading', version: '2026.11.5', received: 3, total: 4 })
        .mockResolvedValue({ phase: 'command', version: '2026.11.5', command: 'sudo bash /x/flockspro-2026.11.5-linux-arm64.run' });
      render(<FlocksproUpgradePage />);

      expect(await screen.findByText('25%')).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(3100);
      expect(await screen.findByText('75%')).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(3100);
      expect(await screen.findByText('sudo bash /x/flockspro-2026.11.5-linux-arm64.run')).toBeInTheDocument();
      expect(screen.getByText('progress.command')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
