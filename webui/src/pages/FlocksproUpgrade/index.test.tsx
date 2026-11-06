import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Case list (编号 | 场景):
//   FP1 a machine with the Python enterprise package: its licence is shown on the card the enterprise
//       edition's page has, Console offers the installer, the upgrade downloads and starts it, and
//       the page follows the installer
//   FP2 run by systemd (the offline package): the page downloads the installer and gives root's command
//   FP3 an account that may not write /opt/flockspro yet: root's one-time command first
//   FP4 the open edition with no licence: no Console check, only the way by hand
//   FP5 a refusal from Console is shown as it is
//   FP6 opened again while a download runs in the background: it follows it to its end
//   FP7 no licence, signed in to Console: the form in its dialog is checked as the server checks it,
//       then sent, and the application shows up as the current request
//   FP8 an approved application: taking the licence turns the page into the one for a machine
//       with a licence (Console checked for the installer)
//   FP9 not signed in: the form cannot be opened and the page says to sign in; what Console could not
//       answer is shown; a withdrawal asks first
//   FP10 the Python package without a licence: the application is offered too; a Console sign-in
//        that fails to start says why there; a submission still unanswered is said so
//   FP11 a licence Console already activated that this machine does not hold: it can be taken
//        again, and no second application is offered beside it
//   FP12 a licence id in no particular format (old licences): shown whole, cut only in the line
//   FP13 a licence no longer valid: the card says so, red
//   FP14 syncing the licence: the package's own sync where it is there, this edition's otherwise,
//        then what the machine says now; whether the terms changed is said, and so is what stopped it
//   FP15 signing out of the cloud account: the check goes, the sign-in comes back
//   FP16 the backend has no such page: the old version came back after a failed upgrade
//   FP17 a licence no longer valid: the machine may apply again, and the card stays to sync it

const { situation, check, start, progress, applications, apply, withdraw, take, syncLicence, consoleLoginSession } = vi.hoisted(() => ({
  situation: vi.fn(),
  syncLicence: vi.fn(),
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
  enterpriseUpgradeApi: { situation, check, start, progress, applications, apply, withdraw, take, syncLicence },
}));
const { startConsoleLogin, logoutConsoleLogin, clientPost } = vi.hoisted(() => ({
  startConsoleLogin: vi.fn(),
  logoutConsoleLogin: vi.fn(),
  clientPost: vi.fn(),
}));
vi.mock('@/api/auth', () => ({ authApi: { consoleLoginSession, startConsoleLogin, logoutConsoleLogin } }));
vi.mock('@/api/client', () => ({ default: { get: vi.fn(), post: clientPost } }));
vi.mock('react-i18next', () => {
  // One function, as react-i18next hands out for a language.
  const t = (key: string, options?: Record<string, unknown>) =>
    options ? `${key} ${Object.entries(options).filter(([k]) => k !== 'defaultValue').map(([, v]) => String(v)).join(' ')}`.trim() : key;
  return { useTranslation: () => ({ t }) };
});

import FlocksproUpgradePage from './index';

const previous = {
  version: '2026.10.19',
  license_id: 'lic-old-7',
  license_status: 'commercial',
  pro_enabled: true,
  expires_at: 1798761600,
  max_admins: 3,
  max_members: 20,
  last_sync_at: 1793811952,
  install_id: 'inst-1',
  fingerprint: 'fp-1',
};
const base = {
  available: true,
  previous,
  platform: 'linux-arm64',
  account: 'flocks',
  root: false,
  systemd: false,
  install_root: '/opt/flockspro',
  install_root_ready: true,
  prepare_command: null,
  can_start: true,
  upgrade: null,
};
const offer = { url: 'https://console.example/r.run', sha256: 'a'.repeat(64), release_id: '2026.11.5-linux-arm64', version: '2026.11.5', size: 10, filename: 'flockspro-2026.11.5-linux-arm64.run' };

beforeEach(() => {
  vi.clearAllMocks();
  consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@example.com' });
  applications.mockResolvedValue({ items: [], note: null });
  clientPost.mockResolvedValue({ data: {} });
  logoutConsoleLogin.mockResolvedValue(undefined);
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

    expect(await screen.findByText('machine.previousTitle 2026.10.19')).toBeInTheDocument();
    expect(screen.getByText('machine.previousNote')).toBeInTheDocument();
    const card = screen.getByTestId('licence-card');
    expect(within(card).getByText(/licence\.id.*lic-old-7/)).toBeInTheDocument();
    expect(within(card).getByText('licenceStatus.commercial')).toBeInTheDocument();
    expect(within(card).getByText('licence.adminQuota 3 / licence.memberQuota 20')).toBeInTheDocument();
    expect(await screen.findByText('ops@example.com')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'upgrade.check' }));
    expect(await screen.findByText('upgrade.offer 2026.11.5 linux-arm64')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'upgrade.start 2026.11.5' }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    const status = await screen.findByRole('status');
    expect(within(status).getByText('progress.installing 2026.11.5')).toBeInTheDocument();
    // While it runs, nothing starts a second one.
    expect(screen.getByRole('button', { name: 'upgrade.check' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'consoleLogin.logoutAction' })).toBeDisabled();
  });

  it('FP16 over the older backend the old version came back with, it says the machine went back', async () => {
    situation.mockRejectedValue({ response: { status: 404, data: { detail: 'Not Found' } } });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByText('errors.wentBack')).toBeInTheDocument();
    expect(screen.queryByText('Not Found')).not.toBeInTheDocument();
  });

  it('FP2 on a systemd machine gives root the command for the downloaded installer', async () => {
    situation.mockResolvedValue({ ...base, systemd: true, can_start: false });
    check.mockResolvedValue(offer);
    start.mockResolvedValue({ phase: 'command', command: 'sudo bash /var/lib/flocks/.flocks/run/enterprise-upgrade/flockspro-2026.11.5-linux-arm64.run' });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('upgrade.systemd')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'upgrade.check' }));
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.download' }));

    expect(await screen.findByText('progress.command')).toBeInTheDocument();
    expect(screen.getByText(/sudo bash \/var\/lib\/flocks\/\.flocks\/run\/enterprise-upgrade\//)).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole('button', { name: 'upgrade.recheck' }));
    await waitFor(() => expect(screen.queryByText(/sudo install -d/)).not.toBeInTheDocument());
  });

  it('FP4 the open edition without a licence: the application, and no way by hand', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('machine.openTitle')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'apply.open' })).toBeInTheDocument();
    expect(await screen.findByText('apply.none')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'upgrade.check' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('licence-card')).not.toBeInTheDocument();
    expect(screen.queryByText(/\.run\b/)).not.toBeInTheDocument();
  });

  it('FP4b a Windows machine: where its program goes', async () => {
    situation.mockResolvedValue({
      ...base, platform: 'windows-amd64', install_root: 'C:\\Program Files\\FlocksPro', account: 'ops',
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('upgrade.whatChangesWindows C:\\Program Files\\FlocksPro')).toBeInTheDocument();
  });

  it('FP5 shows what Console said', async () => {
    situation.mockResolvedValue(base);
    check.mockRejectedValue({ response: { data: { detail: 'Console 还没有给授权 lic-old-7 的新企业版安装包（linux-arm64）' } } });
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.check' }));
    expect(await screen.findByText(/Console 还没有给授权 lic-old-7/)).toBeInTheDocument();
    expect(start).not.toHaveBeenCalled();
  });

  it('FP5b offers to sign in again once Console refused the sign-in it had', async () => {
    // Revoked by a sign-out in the enterprise edition, say: the server forgets it, the page reads it again.
    situation.mockResolvedValue(base);
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    check.mockRejectedValue({ response: { data: { detail: 'Console 要先登录才给安装包：点「云账号登录」后再检查' } } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/ops@example\.com/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.check' }));
    expect(await screen.findByText(/要先登录才给安装包/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'consoleLogin.loginAction' })).toBeInTheDocument();
    expect(screen.getByText('consoleLogin.unbound')).toBeInTheDocument();
    expect(consoleLoginSession).toHaveBeenCalledTimes(2);
  });

  it('FP5c reads the sign-in again when starting the upgrade fails', async () => {
    situation.mockResolvedValue(base);
    check.mockResolvedValue(offer);
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    start.mockRejectedValue(new Error('Console 要先登录才给安装包：点「云账号登录」后再检查'));
    progress.mockResolvedValue(null);
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.check' }));
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.start 2026.11.5' }));
    expect(await screen.findByRole('button', { name: 'consoleLogin.loginAction' })).toBeInTheDocument();
    expect(consoleLoginSession).toHaveBeenCalledTimes(2);
  });

  it('FP5d reads the sign-in again when Console could not answer for the applications', async () => {
    // A refusal while listing comes back as the listing's note, not as an error.
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession
      .mockResolvedValueOnce({ logged_in: true, account_name: 'ops@example.com' })
      .mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [pending], note: '云账号登录已失效：点「云账号登录」后再试' });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/云账号登录已失效/)).toBeInTheDocument();
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

    const open = await screen.findByRole('button', { name: 'apply.open' });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    const dialog = screen.getByRole('dialog', { name: 'apply.title' });
    const send = within(dialog).getByRole('button', { name: 'apply.submit' });
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
    // One open application at a time: the dialog is gone and does not open again.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeDisabled();
  });

  it('FP7b a form Console refused stays open and says why', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    apply.mockRejectedValue({ response: { data: { detail: '这台机器的开源版太旧，不能直接申请企业版' } } });
    render(<FlocksproUpgradePage />);

    const open = await screen.findByRole('button', { name: 'apply.open' });
    await waitFor(() => expect(open).toBeEnabled());
    await userEvent.click(open);
    await userEvent.type(screen.getByLabelText('apply.fields.company'), 'Acme');
    await userEvent.type(screen.getByLabelText('apply.fields.applicantName'), 'Li Lei');
    await userEvent.type(screen.getByLabelText('apply.fields.email'), 'li@acme.example');
    await userEvent.type(screen.getByLabelText('apply.fields.phone'), '+86 138 0000 0000');
    await userEvent.click(screen.getByRole('button', { name: 'apply.submit' }));

    const dialog = screen.getByRole('dialog', { name: 'apply.title' });
    expect(await within(dialog).findByText(/开源版太旧/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'apply.cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('FP8 takes an approved licence and moves on to the upgrade', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({
      items: [{ ...pending, status: 'approved', license_id: 'lic_1', expires_at: 1798761600, reason: '通过', can_take: true }],
      note: null,
    });
    take.mockResolvedValue({
      application: { ...pending, status: 'activated', license_id: 'lic_1', can_take: false, can_withdraw: false },
      situation: { ...base, previous: { version: null, license_id: 'lic_1', license_status: 'poc', pro_enabled: false, expires_at: 1798761600 } },
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(/apply\.issuedUntil lic_1/)).toBeInTheDocument();
    expect(screen.getByText('apply.reason 通过')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'apply.take' }));

    await waitFor(() => expect(take).toHaveBeenCalledWith('req_1'));
    // A licence from the application, no Python package: not called the old edition.
    expect(await screen.findByText('machine.licensedTitle')).toBeInTheDocument();
    expect(screen.getByText('machine.licensedNote')).toBeInTheDocument();
    expect(screen.getByText(/licence\.id.*lic_1/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'apply.open' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'upgrade.check' })).toBeInTheDocument();
  });

  it('FP9 asks to sign in first, shows what Console could not answer, and asks before a withdrawal', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [pending], note: '云账号登录已失效：点「云账号登录」后再试' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('apply.signInFirst')).toBeInTheDocument();
    expect(screen.getByText(/云账号登录已失效/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'apply.withdraw' }));
    expect(confirm).toHaveBeenCalled();
    expect(withdraw).not.toHaveBeenCalled();

    // With nothing open, the form cannot be opened before signing in.
    applications.mockResolvedValue({ items: [], note: null });
    cleanup();
    render(<FlocksproUpgradePage />);
    expect(await screen.findByRole('button', { name: 'apply.open' })).toBeDisabled();
    expect(await screen.findByText('apply.none')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeDisabled();
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

    expect(await screen.findByText('machine.previousTitle 2026.10.19')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'apply.open' })).toBeInTheDocument();
    expect(await screen.findByText('apply.pendingSubmission')).toBeInTheDocument();
    // No licence, so nothing to check with Console yet.
    expect(screen.queryByRole('button', { name: 'upgrade.check' })).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: 'consoleLogin.loginAction' }));
    const section = screen.getByRole('region', { name: 'consoleLogin.title' });
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
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeDisabled();
    expect(screen.queryByText('apply.pendingSubmission')).not.toBeInTheDocument();
  });

  it.each(['ubuntu-nn', 'zj6.29-centos9', 'new-pro-7.1zj'])('FP12 shows the licence %s as it is', async (id) => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, license_id: id } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText(new RegExp(`licence\\.id.*${id.replace(/\./g, '\\.')}`))).toHaveAttribute('title', id);
    expect(screen.getByRole('button', { name: 'upgrade.check' })).toBeEnabled();
    expect(screen.queryByText('licence.invalidHint')).not.toBeInTheDocument();
  });

  it('FP12b cuts a long licence id in the line only', async () => {
    const id = 'ubuntu-22.04-customer-site-with-a-very-long-name-01';
    situation.mockResolvedValue({ ...base, previous: { ...previous, license_id: id } });
    render(<FlocksproUpgradePage />);

    const line = await screen.findByText(/licence\.id/);
    expect(line).toHaveAttribute('title', id);
    expect(line.textContent).not.toContain(id);

    await userEvent.click(screen.getByRole('button', { name: 'licence.showDetails' }));
    expect(screen.getByText(id)).toBeInTheDocument();
    expect(screen.getByText('inst-1')).toBeInTheDocument();
    expect(screen.getByText('fp-1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'licence.hideDetails' }));
    expect(screen.queryByText(id)).not.toBeInTheDocument();
  });

  it.each([
    ['expired by its status', { license_status: 'expired' }],
    ['revoked', { license_status: 'revoked' }],
    ['past its date', { expires_at: 1000000000 }],
  ])('FP13 a licence %s is shown as no longer valid', async (_name, change) => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, ...change } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('licence.invalidHint')).toBeInTheDocument();
    expect(screen.getByTestId('licence-card').className).toContain('border-red-200');
  });

  it('FP13b a licence that lost its heartbeat is not called invalid: the new edition takes it', async () => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, pro_enabled: false } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByTestId('licence-card')).toHaveClass('border-emerald-200');
    expect(screen.queryByText('licence.invalidHint')).not.toBeInTheDocument();
  });

  it('FP13c a licence with no date and no quota shows nothing it does not know', async () => {
    situation.mockResolvedValue({
      ...base,
      previous: { version: null, license_id: 'lic_1', license_status: null, pro_enabled: false, expires_at: null },
    });
    render(<FlocksproUpgradePage />);

    const card = await screen.findByTestId('licence-card');
    expect(within(card).getByText('licence.noPackage')).toBeInTheDocument();
    expect(within(card).getAllByText('-')).toHaveLength(4);
  });

  it('FP14 syncs the licence: the package first, then what the machine says', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    situation.mockResolvedValue({ ...base, previous: { ...previous, max_admins: 9 } });
    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(clientPost).toHaveBeenCalledWith('/api/flockspro/license/refresh');
    expect(await screen.findByText('licence.adminQuota 9 / licence.memberQuota 20')).toBeInTheDocument();
  });

  it('FP14c a sync that fails says so, and what the machine says stays', async () => {
    situation.mockResolvedValue(base);
    clientPost.mockRejectedValue({ response: { status: 502, data: { detail: '连不上 Console' } } });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('连不上 Console')).toBeInTheDocument();
    expect(screen.getByTestId('licence-card')).toBeInTheDocument();
  });

  const noPackage = { ...previous, version: null };

  it('FP14b a machine with no package syncs through this edition: no call to a route it does not have', async () => {
    situation.mockResolvedValue({ ...base, previous: noPackage });
    syncLicence.mockResolvedValue({ changed: true });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    situation.mockResolvedValue({ ...base, previous: { ...noPackage, expires_at: 1801367245 } });
    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('licence.syncChanged')).toBeInTheDocument();
    expect(syncLicence).toHaveBeenCalledTimes(1);
    expect(clientPost).not.toHaveBeenCalled();
    expect(within(screen.getByTestId('licence-card')).getByText(new Date(1801367245 * 1000).getFullYear() + '-01-31', { exact: false })).toBeInTheDocument();
  });

  it('FP14d a package that did not load (no route of its own): the same', async () => {
    situation.mockResolvedValue(base);
    clientPost.mockRejectedValue({ response: { status: 404 } });
    syncLicence.mockResolvedValue({ changed: false });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    const before = situation.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('licence.syncUnchanged')).toBeInTheDocument();
    expect(clientPost).toHaveBeenCalledWith('/api/flockspro/license/refresh');
    expect(syncLicence).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(situation.mock.calls.length).toBe(before + 1));
  });

  it('FP14e what stopped this edition\'s sync is said, and the sign-in is read again', async () => {
    situation.mockResolvedValue({ ...base, previous: noPackage });
    syncLicence.mockRejectedValue({ response: { status: 409, data: { detail: '云账号登录已失效：点「云账号登录」后再试' } } });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    const before = consoleLoginSession.mock.calls.length;
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('云账号登录已失效：点「云账号登录」后再试')).toBeInTheDocument();
    await waitFor(() => expect(consoleLoginSession.mock.calls.length).toBeGreaterThan(before));
    expect(await screen.findByText('consoleLogin.unbound')).toBeInTheDocument();
    expect(screen.queryByText('licence.syncUnchanged')).not.toBeInTheDocument();
  });

  it('FP14f the package says nothing of what changed: no word from the page either', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('licence-card');

    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    await waitFor(() => expect(clientPost).toHaveBeenCalled());
    expect(syncLicence).not.toHaveBeenCalled();
    expect(screen.queryByText('licence.syncChanged')).not.toBeInTheDocument();
    expect(screen.queryByText('licence.syncUnchanged')).not.toBeInTheDocument();
  });

  const lapsed = { ...noPackage, license_status: 'poc', expires_at: 1790740045, lapsed: true };

  it('FP17 a licence no longer valid: apply again, sync, and no word of it being carried over', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    render(<FlocksproUpgradePage />);

    const card = await screen.findByTestId('licence-card');
    expect(card.className).toContain('border-red-200');
    expect(screen.getByText('licence.invalidHint')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'apply.open' })).toBeEnabled();
    expect(within(card).getByRole('button', { name: 'licence.sync' })).toBeInTheDocument();
    expect(screen.getByText('machine.lapsedNote')).toBeInTheDocument();
    expect(screen.queryByText('machine.licensedNote')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'upgrade.check' })).toBeInTheDocument();
  });

  it('FP17b renewed at Console: a sync brings it back to a machine with a licence, no application', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    syncLicence.mockResolvedValue({ changed: true });
    render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'apply.open' });

    situation.mockResolvedValue({ ...base, previous: { ...lapsed, expires_at: 4102444800, lapsed: false } });
    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('licence.syncChanged')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('licence-card')).toHaveClass('border-emerald-200'));
    expect(screen.queryByRole('button', { name: 'apply.open' })).not.toBeInTheDocument();
    expect(screen.getByText('machine.licensedNote')).toBeInTheDocument();
  });

  it('FP17c still over after a sync: the machine can still apply, and the page says Console had nothing new', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    syncLicence.mockResolvedValue({ changed: false });
    render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'apply.open' });

    await userEvent.click(screen.getByRole('button', { name: 'licence.sync' }));

    expect(await screen.findByText('licence.syncUnchanged')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeEnabled();
  });

  it('FP17e an approved application that cannot be taken stands in nothing\'s way; one that can, does', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    const approved = { ...pending, status: 'approved', license_id: 'zj-remote login', can_withdraw: true };
    applications.mockResolvedValue({ items: [{ ...approved, can_take: false }], note: null });
    const { unmount } = render(<FlocksproUpgradePage />);
    await screen.findByText('req_1');
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeEnabled();
    unmount();

    applications.mockResolvedValue({ items: [{ ...approved, can_take: true }], note: null });
    const second = render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'apply.take' });
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeDisabled();
    second.unmount();

    // Over by the terms it was issued with: it can be taken (it may have been renewed), and waits on nothing.
    applications.mockResolvedValue({ items: [{ ...approved, can_take: true, licence_over: true }], note: null });
    render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'apply.take' });
    expect(screen.getByRole('button', { name: 'apply.open' })).toBeEnabled();
  });

  it('FP17d not lapsed by the server\'s word (a clock of its own in the browser): no application', async () => {
    situation.mockResolvedValue({ ...base, previous: { ...noPackage, expires_at: 1790740045, lapsed: false } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('licence.invalidHint')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'apply.open' })).not.toBeInTheDocument();
  });

  it('FP15 signs out of the cloud account', async () => {
    situation.mockResolvedValue(base);
    check.mockResolvedValue(offer);
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.check' }));
    expect(await screen.findByText(/upgrade\.offer/)).toBeInTheDocument();

    consoleLoginSession.mockResolvedValue({ logged_in: false });
    await userEvent.click(await screen.findByRole('button', { name: 'consoleLogin.logoutAction' }));

    expect(logoutConsoleLogin).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: 'consoleLogin.loginAction' })).toBeInTheDocument();
    expect(screen.getByText('upgrade.loginFirst')).toBeInTheDocument();
    expect(screen.queryByText(/upgrade\.offer/)).not.toBeInTheDocument();
  });

  it('FP15b a sign-out that fails says so', async () => {
    situation.mockResolvedValue(base);
    logoutConsoleLogin.mockRejectedValue(new Error('boom'));
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'consoleLogin.logoutAction' }));
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });
});
