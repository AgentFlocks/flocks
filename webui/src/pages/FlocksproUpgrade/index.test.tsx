import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
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
//   FP7 no licence, signed in to Console: the form is checked as the server checks it, then sent,
//       and the application shows up instead of the form
//   FP8 an approved application: taking the licence turns the page into the one for a machine
//       with a licence (Console checked for the installer)
//   FP9 not signed in: the form cannot be sent and the page says to sign in; what Console could not
//       answer is shown; a withdrawal asks first
//   FP10 the Python package without a licence: the application is offered too; a Console sign-in
//        that fails to start says why there; a submission still unanswered is said so
//   FP11 a licence Console already activated that this machine does not hold: it can be taken
//        again, and no second application is offered beside it

const { situation, check, start, progress, applications, apply, withdraw, take, consoleLoginSession } = vi.hoisted(() => ({
  situation: vi.fn(),
  check: vi.fn(),
  start: vi.fn(),
  progress: vi.fn(),
  applications: vi.fn(),
  apply: vi.fn(),
  withdraw: vi.fn(),
  take: vi.fn(),
  consoleLoginSession: vi.fn(),
}));

vi.mock('@/api/enterpriseUpgrade', () => ({
  enterpriseUpgradeApi: { situation, check, start, progress, applications, apply, withdraw, take },
}));
const { startConsoleLogin } = vi.hoisted(() => ({ startConsoleLogin: vi.fn() }));
vi.mock('@/api/auth', () => ({ authApi: { consoleLoginSession, startConsoleLogin } }));
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
  applications.mockResolvedValue({ items: [], note: null });
});

const pending = {
  request_id: 'req_1', status: 'pending', license_type: 'poc', company: 'Acme', applicant_name: 'Li Lei',
  applicant_email: 'li@acme.example', reason: null, suggestion: null, license_id: null, license_status: null,
  expires_at: null, created_at: '2026-10-30T09:30:00+00:00', updated_at: null, can_withdraw: true, can_take: false,
};

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

  it('FP4 the open edition without a licence: the application, and the way by hand', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('machine.openTitle')).toBeInTheDocument();
    expect(await screen.findByText('apply.title')).toBeInTheDocument();
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

  it('FP5b offers to sign in again once Console refused the sign-in it had', async () => {
    // Revoked by a sign-out in the enterprise edition, say: the server forgets it, the page reads it again.
    situation.mockResolvedValue(base);
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    check.mockRejectedValue({ response: { data: { detail: 'Console 要先登录才给安装包：点「登录 Console」后再检查' } } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/ops@example\.com/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: /upgrade\.check/ }));
    expect(await screen.findByText(/要先登录才给安装包/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /upgrade\.loginConsole/ })).toBeInTheDocument();
    expect(consoleLoginSession).toHaveBeenCalledTimes(2);
  });

  it('FP5c reads the sign-in again when starting the upgrade fails', async () => {
    situation.mockResolvedValue(base);
    check.mockResolvedValue(offer);
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    start.mockRejectedValue(new Error('Console 要先登录才给安装包：点「登录 Console」后再检查'));
    progress.mockResolvedValue(null);
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: /upgrade\.check/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.start 2026.11.5' }));
    expect(await screen.findByRole('button', { name: /upgrade\.loginConsole/ })).toBeInTheDocument();
    expect(consoleLoginSession).toHaveBeenCalledTimes(2);
  });

  it('FP5d reads the sign-in again when Console could not answer for the applications', async () => {
    // A refusal while listing comes back as the listing's note, not as an error.
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [pending], note: 'Console 登录已失效：点「登录 Console」后再试' });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/Console 登录已失效/)).toBeInTheDocument();
    await waitFor(() => expect(consoleLoginSession).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('apply.signInFirst')).toBeInTheDocument();
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

  it('FP7 checks the form, sends it, and shows the application instead', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    apply.mockResolvedValue(pending);
    render(<FlocksproUpgradePage />);

    const send = await screen.findByRole('button', { name: 'apply.submit' });
    await userEvent.type(screen.getByLabelText('apply.fields.company'), 'Acme');
    await userEvent.type(screen.getByLabelText('apply.fields.applicantName'), 'Li Lei');
    await userEvent.type(screen.getByLabelText('apply.fields.email'), 'li@acme');
    await userEvent.type(screen.getByLabelText('apply.fields.phone'), '12-34');
    await userEvent.click(send);
    expect(screen.getByText('apply.problems.email')).toBeInTheDocument();
    expect(screen.getByText('apply.problems.phone')).toBeInTheDocument();
    expect(apply).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText('apply.fields.email'), '.example');
    await userEvent.clear(screen.getByLabelText('apply.fields.phone'));
    await userEvent.type(screen.getByLabelText('apply.fields.phone'), '+86 138 0000 0000');
    await userEvent.selectOptions(screen.getByLabelText('apply.fields.licenseType'), 'commercial');
    applications.mockResolvedValue({ items: [pending], note: null });
    await userEvent.click(send);

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply.mock.calls[0][0]).toMatchObject({
      license_type: 'commercial', company: 'Acme', applicant_name: 'Li Lei',
      applicant_email: 'li@acme.example', applicant_phone: '+86 138 0000 0000', sales_rep_name: null, notes: null,
    });
    const list = await screen.findByRole('list', { name: 'apply.listLabel' });
    expect(within(list).getByText('apply.status.pending')).toBeInTheDocument();
    expect(within(list).getByText('Acme')).toBeInTheDocument();
    // One open application at a time: the form is gone.
    expect(screen.queryByRole('button', { name: 'apply.submit' })).not.toBeInTheDocument();
  });

  it('FP8 takes an approved licence and moves on to the upgrade', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({
      items: [{ ...pending, status: 'approved', license_id: 'lic_1', expires_at: 1798761600, reason: '通过', can_take: true }],
      note: null,
    });
    take.mockResolvedValue({
      application: { ...pending, status: 'activated', license_id: 'lic_1', can_take: false, can_withdraw: false },
      situation: { ...base, previous: { version: null, license_id: 'lic_1', license_status: null, pro_enabled: false, expires_at: null } },
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/apply\.issuedUntil lic_1/)).toBeInTheDocument();
    expect(screen.getByText('apply.reason 通过')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'apply.take' }));

    await waitFor(() => expect(take).toHaveBeenCalledWith('req_1'));
    // A licence from the application, no Python package: not called the old edition.
    expect(await screen.findByText('machine.licensedTitle')).toBeInTheDocument();
    expect(screen.getByText('machine.licensedNote')).toBeInTheDocument();
    expect(screen.getByText('lic_1')).toBeInTheDocument();
    expect(screen.queryByText('apply.title')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /upgrade\.check/ })).toBeInTheDocument();
  });

  it('FP9 asks to sign in first, shows what Console could not answer, and asks before a withdrawal', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [pending], note: 'Console 登录已失效：点「登录 Console」后再试' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('apply.signInFirst')).toBeInTheDocument();
    expect(screen.getByText(/Console 登录已失效/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'apply.withdraw' }));
    expect(confirm).toHaveBeenCalled();
    expect(withdraw).not.toHaveBeenCalled();

    // With nothing open, the form shows but cannot be sent before signing in.
    applications.mockResolvedValue({ items: [], note: null });
    cleanup();
    render(<FlocksproUpgradePage />);
    expect(await screen.findByRole('button', { name: 'apply.submit' })).toBeDisabled();
    expect(screen.getByText('apply.submitNeedsSignIn')).toBeInTheDocument();
    confirm.mockRestore();
  });

  it('FP10 offers the application to the Python package without a licence, and says why sign-in failed', async () => {
    situation.mockResolvedValue({
      ...base,
      previous: { version: '2026.10.19', license_id: null, license_status: 'unactivated', pro_enabled: false, expires_at: null },
    });
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [], note: null, pending_submission: true });
    startConsoleLogin.mockRejectedValue({ response: { data: { detail: 'Console 登录服务不可用' } } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('machine.previousTitle')).toBeInTheDocument();
    expect(await screen.findByText('apply.title')).toBeInTheDocument();
    expect(screen.getByText('apply.pendingSubmission')).toBeInTheDocument();
    // No licence, so nothing to check with Console yet.
    expect(screen.queryByRole('button', { name: /upgrade\.check/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /upgrade\.loginConsole/ }));
    const section = screen.getByRole('region', { name: 'apply.title' });
    expect(await within(section).findByText('Console 登录服务不可用')).toBeInTheDocument();
  });

  it('FP11 takes again a licence Console already activated, with no second application beside it', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({
      items: [{ ...pending, status: 'activated', license_id: 'lic_1', expires_at: 1798761600, can_take: true, can_withdraw: false }],
      note: null,
      pending_submission: true,
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByRole('button', { name: 'apply.take' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'apply.withdraw' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'apply.submit' })).not.toBeInTheDocument();
    expect(screen.queryByText('apply.pendingSubmission')).not.toBeInTheDocument();
  });
});
