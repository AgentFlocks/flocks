import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Case list (编号 | 场景):
//   FP1 a machine with the Python enterprise package: its licence is the platform row of the licence
//       table, its version is the product's, Console offers the installer, the upgrade downloads and
//       starts it, and the page follows the installer
//   FP2 run by systemd (the offline package): the page downloads the installer and gives root's command
//   FP3 an account that may not write /opt/flockspro yet: root's one-time command first
//   FP4 the open edition with no licence: the open edition's row, the application, no Console check
//   FP5 a refusal from Console is shown as it is; a refused sign-in is offered again; the applications
//       Console could not answer for read the sign-in again
//   FP6 opened again while a download runs in the background: it follows it to its end
//   FP7 no licence, signed in: the form in its dialog is checked as the server checks it, then sent, and
//       the application shows up in the application records
//   FP8 an approved application: taking the licence turns the page into the one for a machine with one
//   FP9 not signed in: no online application until the sign-in, said so; a withdrawal asks first
//   FP10 the Python package without a licence: the application is offered too; a sign-in that fails to
//        start says why; a submission still unanswered is said so
//   FP11 a licence Console already activated that this machine does not hold: it can be taken again,
//        and no second application is offered beside it
//   FP12 a licence id in no particular format (old licences): shown whole in its title, cut in the line
//   FP13 a licence no longer valid: the row says so, red, and why
//   FP14 syncing the licence: the package's own sync where it is there, this edition's otherwise, then
//        what the machine says now; whether the terms changed is said, and so is what stopped it
//   FP15 signing out of the cloud account: the check goes, the sign-in comes back
//   FP16 the backend has no such page: the old version came back after a failed upgrade
//   FP17 a licence no longer valid: the machine may apply again, and can still sync it
//   FP18 Console out of reach: nothing of the cloud account shows; the offline application downloads an
//        application file to hand over; the licence file that comes back is imported in its dialog, and the
//        page moves on to the upgrade by the offline installer
//   FP19 Console answering: the licence file can be imported too while the machine has no licence
//   FP20 an application sent from the account menu's dialog while the page is kept: listed without a reload
//   FP21 the versions shown are written v<year>.<month>.<day>.<build>
//   FP19b the licence import takes the file chosen last (a file only: nothing is pasted)
//   FP5e a cloud sign-in that cannot be read is offered again, not hidden
//   FP22 one panel: the application, the licence information and the product version, in that order
//   FP23 signing out asks first; cancelled, nothing happens
//   FP24 a licence running out (30 days or fewer): said in its status and under the table; still in force
//        nothing can be applied for here, so online, signed out or offline the way is a new licence file
//   FP24b a licence run out: applied for again, so the hint says how (online signed in, signed out, offline)
//   FP25 an approved application's licence is taken without being asked (online, signed in, no licence
//        here), once; offline or signed out it waits for its button
//   FP25b nothing is taken before the first check of Console's reach is back; found offline, nothing at all
//   FP26 the online application is for Pro only in this edition: the modules and scene suites come after it

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
  enterpriseUpgradeApi: {
    situation, check, start, progress, applications, apply, withdraw, take, syncLicence,
    offlineApplication: (...args: unknown[]) => offlineApplication(...args),
    importLicence: (...args: unknown[]) => importLicence(...args),
  },
}));
const { startConsoleLogin, logoutConsoleLogin, clientPost, clientGet, consoleConnectivity, offlineApplication, importLicence } = vi.hoisted(() => ({
  startConsoleLogin: vi.fn(),
  clientGet: vi.fn(),
  logoutConsoleLogin: vi.fn(),
  clientPost: vi.fn(),
  consoleConnectivity: vi.fn(),
  offlineApplication: vi.fn(),
  importLicence: vi.fn(),
}));
vi.mock('@/api/auth', () => ({ authApi: { consoleLoginSession, startConsoleLogin, logoutConsoleLogin, consoleConnectivity } }));
vi.mock('@/api/client', () => ({ default: { get: clientGet, post: clientPost } }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toast }));
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
  consoleConnectivity.mockResolvedValue({ configured: true, online: true, console_host: 'console.example', checked_at: '' });
  consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@example.com' });
  applications.mockResolvedValue({ items: [], note: null });
  clientPost.mockResolvedValue({ data: {} });
  clientGet.mockResolvedValue({ data: { status: 'ok', version: '2026.11.9' } });
  logoutConsoleLogin.mockResolvedValue(undefined);
  // An application file is saved through a link to a blob.
  URL.createObjectURL = vi.fn(() => 'blob:application');
  URL.revokeObjectURL = vi.fn();
});

const row = () => screen.getByTestId('platform-row');
const applyOnline = () => screen.getByRole('button', { name: 'licencePage.apply.applyOnline' });
const sync = () => screen.getByRole('button', { name: /^licencePage\.info\.sync/ });
// The reason a button cannot be used is on its wrapper (a disabled button has no tooltip of its own).
const reasonOf = (button: HTMLElement) => button.parentElement?.getAttribute('title');
const checkVersion = () => screen.getByRole('button', { name: 'licencePage.version.check' });
const FIELD = {
  company: 'licencePage.dialog.placeholders.company',
  name: 'licencePage.dialog.placeholders.name',
  email: 'licencePage.dialog.placeholders.email',
  phone: 'licencePage.dialog.placeholders.phone',
};

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

    expect(await screen.findByTestId('product-version')).toHaveTextContent('licencePage.version.previous v2026.10.19.0');
    expect(screen.getByTestId('saved-hint')).toHaveTextContent('machine.previousNote');
    expect(within(row()).getByTitle('lic-old-7')).toHaveTextContent('lic-old-7');
    expect(within(row()).getByText('licenceStatus.commercial')).toBeInTheDocument();
    expect(within(row()).getByText('licence.adminQuota 3 / licence.memberQuota 20')).toBeInTheDocument();
    expect(within(row()).getByTestId('edition-status')).toHaveTextContent('licencePage.info.licensed');
    expect(await screen.findByText('licencePage.apply.account ops@example.com')).toBeInTheDocument();
    // A machine with a licence has nothing to apply for.
    expect(applyOnline()).toBeDisabled();
    expect(screen.getByTestId('apply-blocked')).toHaveTextContent('licencePage.apply.licensed');

    await userEvent.click(checkVersion());
    expect(await screen.findByText('upgrade.offer v2026.11.05.0 linux-arm64')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'upgrade.start v2026.11.05.0' }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    const status = await screen.findByRole('status');
    expect(within(status).getByText('progress.installing v2026.11.05.0')).toBeInTheDocument();
    // While it runs, nothing starts a second one.
    expect(checkVersion()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'licencePage.apply.logout' })).toBeDisabled();
  });

  it('FP16 over the older backend the old version came back with, it says the machine went back', async () => {
    situation.mockRejectedValue({ response: { status: 404, data: { detail: 'Not Found' } } });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByText('errors.wentBack')).toBeInTheDocument();
    expect(screen.queryByText('Not Found')).not.toBeInTheDocument();
  });

  it('does not say the machine went back when a proxy answers 404 for a service that is restarting', async () => {
    // nginx's own page, as a gateway set up that way sends it for a service it cannot reach: not this service's JSON.
    const proxyPage = '<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n';
    situation.mockRejectedValue({ response: { status: 404, data: proxyPage }, message: 'Request failed with status code 404' });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByText('Request failed with status code 404')).toBeInTheDocument();
    expect(screen.queryByText('errors.wentBack')).not.toBeInTheDocument();
  });

  it('FP2 on a systemd machine gives root the command for the downloaded installer', async () => {
    situation.mockResolvedValue({ ...base, systemd: true, can_start: false });
    check.mockResolvedValue(offer);
    start.mockResolvedValue({ phase: 'command', command: 'sudo bash /var/lib/flocks/.flocks/run/enterprise-upgrade/flockspro-2026.11.5-linux-arm64.run' });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('upgrade.systemd')).toBeInTheDocument();
    await userEvent.click(checkVersion());
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

  it('FP4 the open edition without a licence: the open edition, the application, and no way by hand', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByTestId('product-version')).toHaveTextContent('licencePage.version.open v2026.11.09.0');
    expect(within(row()).getByTestId('edition-status')).toHaveTextContent('licencePage.info.openEdition');
    expect(screen.getByText('licencePage.info.itemsNote')).toBeInTheDocument();
    await waitFor(() => expect(applyOnline()).toBeEnabled());
    expect(await screen.findByText('licencePage.records.empty')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'licencePage.version.check' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('saved-hint')).not.toBeInTheDocument();
    expect(screen.getByText('machine.openNote')).toBeInTheDocument();
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

    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.version.check' }));
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
    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.version.check' }));
    expect(await screen.findByText(/要先登录才给安装包/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'licencePage.apply.login' })).toBeInTheDocument();
    expect(screen.getByTestId('cloud-status')).toHaveTextContent('licencePage.apply.signedOut');
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

    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.version.check' }));
    await userEvent.click(await screen.findByRole('button', { name: 'upgrade.start v2026.11.05.0' }));
    expect(await screen.findByRole('button', { name: 'licencePage.apply.login' })).toBeInTheDocument();
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
    await waitFor(() => expect(consoleLoginSession.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText('licencePage.apply.signInHint')).toBeInTheDocument();
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

  it('FP7 checks the form, sends it, and shows the application in the records', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    apply.mockResolvedValue(pending);
    render(<FlocksproUpgradePage />);

    await waitFor(() => expect(applyOnline()).toBeEnabled());
    await userEvent.click(applyOnline());
    const dialog = screen.getByRole('dialog', { name: 'licencePage.dialog.onlineTitle' });
    expect(within(dialog).getByText('licencePage.dialog.onlineTip')).toBeInTheDocument();
    const send = within(dialog).getByRole('button', { name: 'licencePage.dialog.submit' });
    // Empty: one line says what is missing.
    await userEvent.click(send);
    expect(within(dialog).getByTestId('application-problem')).toHaveTextContent('licencePage.dialog.problems.required');
    await userEvent.type(screen.getByLabelText(FIELD.company), 'Acme');
    await userEvent.type(screen.getByLabelText(FIELD.name), 'Li Lei');
    await userEvent.type(screen.getByLabelText(FIELD.email), 'li@acme');
    await userEvent.type(screen.getByLabelText(FIELD.phone), '12-34');
    await userEvent.click(send);
    expect(within(dialog).getByTestId('application-problem')).toHaveTextContent('licencePage.dialog.problems.email');
    expect(apply).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText(FIELD.email), '.example');
    await userEvent.click(send);
    expect(within(dialog).getByTestId('application-problem')).toHaveTextContent('licencePage.dialog.problems.phone');
    expect(apply).not.toHaveBeenCalled();

    await userEvent.clear(screen.getByLabelText(FIELD.phone));
    await userEvent.type(screen.getByLabelText(FIELD.phone), '+86 138 0000 0000');
    await userEvent.selectOptions(screen.getByLabelText('licencePage.dialog.licenseType'), 'commercial');
    applications.mockResolvedValue({ items: [pending], note: null });
    await userEvent.click(send);

    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
    expect(apply.mock.calls[0][0]).toMatchObject({
      license_type: 'commercial', company: 'Acme', applicant_name: 'Li Lei',
      applicant_email: 'li@acme.example', applicant_phone: '+86 138 0000 0000', sales_rep_name: null, notes: null,
    });
    expect(toast.success).toHaveBeenCalledWith('licencePage.dialog.sent');
    const list = await screen.findByRole('list', { name: 'licencePage.records.title' });
    // One line: what was applied for, its status, then its fields.
    const record = within(list).getByTestId('application-req_1');
    expect(within(record).getByText('licencePage.records.upgrade')).toBeInTheDocument();
    expect(within(record).getByText('licencePage.records.status.pending')).toBeInTheDocument();
    expect(within(record).getByText('licencePage.records.fields.id')).toBeInTheDocument();
    expect(within(record).getByText('Acme')).toBeInTheDocument();
    expect(within(record).getByText('Li Lei')).toBeInTheDocument();
    // One open application at a time: the dialog is gone, does not open again, and the card says why.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(applyOnline()).toBeDisabled();
    expect(reasonOf(applyOnline())).toBe('licencePage.apply.outstanding req_1');
    expect(screen.getByTestId('apply-blocked')).toHaveTextContent('licencePage.apply.outstanding req_1');
    expect(screen.getByTestId('application-outstanding')).toHaveTextContent('licencePage.apply.inReview req_1 licencePage.records.status.pending');
    expect(screen.getByTestId('application-records')).toHaveAttribute('open');
  });

  it('FP7b a form Console refused stays open and says why', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    apply.mockRejectedValue({ response: { data: { detail: '这台机器的开源版太旧，不能直接申请企业版' } } });
    render(<FlocksproUpgradePage />);

    await waitFor(() => expect(applyOnline()).toBeEnabled());
    await userEvent.click(applyOnline());
    await userEvent.type(screen.getByLabelText(FIELD.company), 'Acme');
    await userEvent.type(screen.getByLabelText(FIELD.name), 'Li Lei');
    await userEvent.type(screen.getByLabelText(FIELD.email), 'li@acme.example');
    await userEvent.type(screen.getByLabelText(FIELD.phone), '+86 138 0000 0000');
    await userEvent.click(screen.getByRole('button', { name: 'licencePage.dialog.submit' }));

    const dialog = screen.getByRole('dialog', { name: 'licencePage.dialog.onlineTitle' });
    expect(await within(dialog).findByText(/开源版太旧/)).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole('button', { name: 'licencePage.dialog.cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('FP8 takes an approved licence and moves on to the upgrade', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    // Signed out, nothing is taken without being asked: the record's button takes it.
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({
      items: [{ ...pending, status: 'approved', license_id: 'lic_1', expires_at: 1798761600, reason: '通过', can_take: true }],
      note: null,
    });
    take.mockResolvedValue({
      application: { ...pending, status: 'activated', license_id: 'lic_1', can_take: false, can_withdraw: false },
      situation: { ...base, previous: { version: null, license_id: 'lic_1', license_status: 'poc', pro_enabled: false, expires_at: 1798761600 } },
    });
    render(<FlocksproUpgradePage />);

    const record = await screen.findByTestId('application-req_1');
    expect(within(record).getByText('lic_1')).toBeInTheDocument();
    expect(within(record).getByText('licencePage.records.status.approved')).toBeInTheDocument();
    // Approved: no review note said (that is for a refusal).
    expect(screen.queryByText(/licencePage\.records\.reason/)).toBeNull();
    expect(screen.getByTestId('application-outstanding')).toHaveTextContent('licencePage.apply.toTake req_1');
    expect(take).not.toHaveBeenCalled();
    await userEvent.click(within(record).getByRole('button', { name: 'licencePage.records.take' }));

    await waitFor(() => expect(take).toHaveBeenCalledWith('req_1'));
    // A licence from the application, no Python package: not called the old edition.
    expect(await screen.findByTestId('saved-hint')).toHaveTextContent('licencePage.info.savedPlatform');
    expect(within(row()).getByText('lic_1')).toBeInTheDocument();
    expect(within(row()).getByTestId('edition-status')).toHaveTextContent('licencePage.info.licensed');
    expect(applyOnline()).toBeDisabled();
    expect(checkVersion()).toBeInTheDocument();
  });

  it('FP9 asks to sign in first, shows what Console could not answer, and asks before a withdrawal', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    applications.mockResolvedValue({ items: [pending], note: '云账号登录已失效：点「云账号登录」后再试' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<FlocksproUpgradePage />);

    expect(await screen.findByText('licencePage.apply.signInHint')).toBeInTheDocument();
    expect(screen.getByText(/云账号登录已失效/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'licencePage.records.withdraw' }));
    expect(confirm).toHaveBeenCalled();
    expect(withdraw).not.toHaveBeenCalled();

    // With nothing open, the online form cannot be opened before signing in.
    applications.mockResolvedValue({ items: [], note: null });
    cleanup();
    render(<FlocksproUpgradePage />);
    expect(await screen.findByText('licencePage.records.empty')).toBeInTheDocument();
    expect(applyOnline()).toBeDisabled();
    expect(reasonOf(applyOnline())).toBe('licencePage.apply.signInFirst');
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

    expect(await screen.findByTestId('product-version')).toHaveTextContent('licencePage.version.previous v2026.10.19.0');
    expect(await screen.findByText('apply.pendingSubmission')).toBeInTheDocument();
    // No licence, so nothing to check with Console yet.
    expect(screen.queryByRole('button', { name: 'licencePage.version.check' })).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.apply.login' }));
    const card = screen.getByTestId('licence-apply');
    expect(await within(card).findByText('Console 登录服务不可用')).toBeInTheDocument();
  });

  it('FP11 takes again a licence Console already activated, with no second application beside it', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({
      items: [{ ...pending, status: 'activated', license_id: 'lic_1', expires_at: 1798761600, can_take: true, can_withdraw: false }],
      note: null,
      pending_submission: true,
    });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByRole('button', { name: 'licencePage.records.take' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'licencePage.records.withdraw' })).not.toBeInTheDocument();
    expect(applyOnline()).toBeDisabled();
    expect(screen.queryByText('apply.pendingSubmission')).not.toBeInTheDocument();
  });

  it.each(['ubuntu-nn', 'zj6.29-centos9', 'new-pro-7.1zj'])('FP12 shows the licence %s as it is', async (id) => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, license_id: id } });
    render(<FlocksproUpgradePage />);

    expect(await within(await screen.findByTestId('platform-row')).findByText(id)).toBeInTheDocument();
    expect(within(row()).getByTitle(id)).toBeInTheDocument();
    expect(checkVersion()).toBeEnabled();
    expect(screen.queryByText('licence.invalidHint')).not.toBeInTheDocument();
    expect(screen.queryByTestId('licence-term-hint')).not.toBeInTheDocument();
  });

  it('FP12b cuts a long licence id in the line only', async () => {
    const id = 'ubuntu-22.04-customer-site-with-a-very-long-name-01';
    situation.mockResolvedValue({ ...base, previous: { ...previous, license_id: id } });
    render(<FlocksproUpgradePage />);

    const cell = await within(await screen.findByTestId('platform-row')).findByTitle(id);
    expect(cell.textContent).not.toContain(id);
    expect(cell.textContent).toContain('ubuntu-22.04-c');
  });

  // As the backend reports them: a licence expired, revoked or past its date comes with `lapsed`
  // (flocks/updater/enterprise.py `licence_lapsed`), so the machine may apply again.
  it.each([
    ['expired by its status', { license_status: 'expired', lapsed: true }, 'licenceStatus.expired', 'licencePage.info.term.online licencePage.info.term.expired'],
    ['revoked', { license_status: 'revoked', lapsed: true }, 'licenceStatus.revoked', 'licence.invalidHint'],
    ['past its date', { expires_at: 1000000000, lapsed: true }, 'licencePage.info.lapsed', 'licencePage.info.term.online licencePage.info.term.expired'],
  ])('FP13 a licence %s is shown as no longer valid', async (_name, change, said, hint) => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, ...change } });
    render(<FlocksproUpgradePage />);

    // Run out: what to do about it (here online and signed in); revoked: that it no longer counts.
    expect(await screen.findByText(hint)).toBeInTheDocument();
    const tag = within(row()).getByTestId('edition-status');
    expect(tag).toHaveTextContent(said);
    expect(tag.className).toContain('text-red-700');
  });

  it('FP13b a licence that lost its heartbeat is not called invalid: the new edition takes it', async () => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, pro_enabled: false } });
    render(<FlocksproUpgradePage />);

    expect(await within(await screen.findByTestId('platform-row')).findByTestId('edition-status')).toHaveTextContent('licencePage.info.licensed');
    expect(screen.queryByText('licence.invalidHint')).not.toBeInTheDocument();
    expect(screen.queryByTestId('licence-term-hint')).not.toBeInTheDocument();
  });

  it('FP13c a licence with no date and no quota shows nothing it does not know', async () => {
    situation.mockResolvedValue({
      ...base,
      previous: { version: null, license_id: 'lic_1', license_status: null, pro_enabled: false, expires_at: null },
    });
    render(<FlocksproUpgradePage />);

    const platform = await screen.findByTestId('platform-row');
    expect(within(platform).getByText('lic_1')).toBeInTheDocument();
    // Source, quota and expiry: nothing known.
    expect(within(platform).getAllByText('—')).toHaveLength(3);
  });

  it('FP14 syncs the licence: the package first, then what the machine says', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    situation.mockResolvedValue({ ...base, previous: { ...previous, max_admins: 9 } });
    await userEvent.click(sync());

    expect(clientPost).toHaveBeenCalledWith('/api/flockspro/license/refresh');
    expect(await screen.findByText('licence.adminQuota 9 / licence.memberQuota 20')).toBeInTheDocument();
  });

  it('FP14c a sync that fails says so, and what the machine says stays', async () => {
    situation.mockResolvedValue(base);
    clientPost.mockRejectedValue({ response: { status: 502, data: { detail: '连不上 Console' } } });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    await userEvent.click(sync());

    expect(await screen.findByText('连不上 Console')).toBeInTheDocument();
    expect(screen.getByTestId('platform-row')).toBeInTheDocument();
  });

  const noPackage = { ...previous, version: null };

  it('FP14b a machine with no package syncs through this edition: no call to a route it does not have', async () => {
    situation.mockResolvedValue({ ...base, previous: noPackage });
    syncLicence.mockResolvedValue({ changed: true });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    situation.mockResolvedValue({ ...base, previous: { ...noPackage, expires_at: 1801367245 } });
    await userEvent.click(sync());

    expect(await screen.findByText('licence.syncChanged')).toBeInTheDocument();
    expect(syncLicence).toHaveBeenCalledTimes(1);
    expect(clientPost).not.toHaveBeenCalled();
    expect(within(row()).getByText(new Date(1801367245 * 1000).getFullYear() + '-01-31', { exact: false })).toBeInTheDocument();
  });

  it('FP14d a package that did not load (no route of its own): the same', async () => {
    situation.mockResolvedValue(base);
    clientPost.mockRejectedValue({ response: { status: 404 } });
    syncLicence.mockResolvedValue({ changed: false });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    const before = situation.mock.calls.length;
    await userEvent.click(sync());

    expect(await screen.findByText('licence.syncUnchanged')).toBeInTheDocument();
    expect(clientPost).toHaveBeenCalledWith('/api/flockspro/license/refresh');
    expect(syncLicence).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(situation.mock.calls.length).toBe(before + 1));
  });

  it('FP14e what stopped this edition\'s sync is said, and the sign-in is read again', async () => {
    situation.mockResolvedValue({ ...base, previous: noPackage });
    syncLicence.mockRejectedValue({ response: { status: 409, data: { detail: '云账号登录已失效：点「云账号登录」后再试' } } });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    const before = consoleLoginSession.mock.calls.length;
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    await userEvent.click(sync());

    expect(await screen.findByText('云账号登录已失效：点「云账号登录」后再试')).toBeInTheDocument();
    await waitFor(() => expect(consoleLoginSession.mock.calls.length).toBeGreaterThan(before));
    expect(await screen.findByTestId('cloud-status')).toHaveTextContent('licencePage.apply.signedOut');
    expect(screen.queryByText('licence.syncUnchanged')).not.toBeInTheDocument();
  });

  it('FP14f the package says nothing of what changed: no word from the page either', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());

    await userEvent.click(sync());

    await waitFor(() => expect(clientPost).toHaveBeenCalled());
    expect(syncLicence).not.toHaveBeenCalled();
    expect(screen.queryByText('licence.syncChanged')).not.toBeInTheDocument();
    expect(screen.queryByText('licence.syncUnchanged')).not.toBeInTheDocument();
  });

  it('FP14g the refresh: online and signed in also without a licence (the applications); offline or (this edition) signed out it says why', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    const first = render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    await waitFor(() => expect(sync()).toBeEnabled());
    expect(sync()).toHaveAccessibleName('licencePage.info.sync');
    // No licence: only the applications are read again, no licence sync.
    const listed = applications.mock.calls.length;
    await userEvent.click(sync());
    await waitFor(() => expect(applications.mock.calls.length).toBeGreaterThan(listed));
    expect(syncLicence).not.toHaveBeenCalled();
    expect(clientPost).not.toHaveBeenCalled();
    first.unmount();

    situation.mockResolvedValue({ ...base, previous: noPackage });
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    const second = render(<FlocksproUpgradePage />);
    await screen.findByTestId('cloud-status');
    expect(sync()).toBeDisabled();
    expect(reasonOf(sync())).toBe('licencePage.info.syncSignIn');
    second.unmount();

    situation.mockResolvedValue({ ...base, available: false });
    render(<FlocksproUpgradePage />);
    await screen.findByTestId('platform-row');
    expect(sync()).toBeDisabled();
    expect(reasonOf(sync())).toBe('quick.unavailable');
  });

  const lapsed = { ...noPackage, license_status: 'poc', expires_at: 1790740045, lapsed: true };

  it('FP17 a licence no longer valid: apply again, sync, and no word of it being carried over', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByTestId('licence-term-hint')).toHaveAttribute('data-term', 'expired');
    expect(within(row()).getByTestId('edition-status').className).toContain('text-red-700');
    await waitFor(() => expect(applyOnline()).toBeEnabled());
    expect(sync()).toBeEnabled();
    expect(screen.queryByTestId('saved-hint')).not.toBeInTheDocument();
    expect(checkVersion()).toBeInTheDocument();
  });

  it('FP17b renewed at Console: a sync brings it back to a machine with a licence, no application', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    syncLicence.mockResolvedValue({ changed: true });
    render(<FlocksproUpgradePage />);
    await waitFor(() => expect(applyOnline()).toBeEnabled());

    situation.mockResolvedValue({ ...base, previous: { ...lapsed, expires_at: 4102444800, lapsed: false } });
    await userEvent.click(sync());

    expect(await screen.findByText('licence.syncChanged')).toBeInTheDocument();
    await waitFor(() => expect(within(row()).getByTestId('edition-status')).toHaveTextContent('licencePage.info.licensed'));
    expect(applyOnline()).toBeDisabled();
    expect(screen.getByTestId('saved-hint')).toHaveTextContent('licencePage.info.savedPlatform');
  });

  it('FP17c still over after a sync: the machine can still apply, and the page says Console had nothing new', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    syncLicence.mockResolvedValue({ changed: false });
    render(<FlocksproUpgradePage />);
    await waitFor(() => expect(applyOnline()).toBeEnabled());

    await userEvent.click(sync());

    expect(await screen.findByText('licence.syncUnchanged')).toBeInTheDocument();
    expect(applyOnline()).toBeEnabled();
  });

  it('FP17e an approved application that cannot be taken stands in nothing\'s way; one that can, does', async () => {
    situation.mockResolvedValue({ ...base, previous: lapsed });
    const approved = { ...pending, status: 'approved', license_id: 'zj-remote login', can_withdraw: true };
    applications.mockResolvedValue({ items: [{ ...approved, can_take: false }], note: null });
    const { unmount } = render(<FlocksproUpgradePage />);
    await screen.findByText('req_1');
    await waitFor(() => expect(applyOnline()).toBeEnabled());
    unmount();

    applications.mockResolvedValue({ items: [{ ...approved, can_take: true }], note: null });
    const second = render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'licencePage.records.take' });
    expect(applyOnline()).toBeDisabled();
    second.unmount();

    // Over by the terms it was issued with: it can be taken (it may have been renewed), and waits on nothing.
    applications.mockResolvedValue({ items: [{ ...approved, can_take: true, licence_over: true }], note: null });
    render(<FlocksproUpgradePage />);
    await screen.findByRole('button', { name: 'licencePage.records.take' });
    await waitFor(() => expect(applyOnline()).toBeEnabled());
  });

  it('FP17d not lapsed by the server\'s word (a clock of its own in the browser): no application', async () => {
    situation.mockResolvedValue({ ...base, previous: { ...noPackage, expires_at: 1790740045, lapsed: false } });
    render(<FlocksproUpgradePage />);

    expect(await screen.findByTestId('licence-term-hint')).toHaveAttribute('data-term', 'expired');
    expect(applyOnline()).toBeDisabled();
  });

  it('FP15 signs out of the cloud account', async () => {
    situation.mockResolvedValue(base);
    check.mockResolvedValue(offer);
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.version.check' }));
    expect(await screen.findByText(/upgrade\.offer/)).toBeInTheDocument();

    consoleLoginSession.mockResolvedValue({ logged_in: false });
    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.apply.logout' }));
    const asked = await screen.findByRole('dialog', { name: 'licencePage.apply.logoutTitle' });
    expect(within(asked).getByText('licencePage.apply.logoutConfirm')).toBeInTheDocument();
    await userEvent.click(within(asked).getByRole('button', { name: 'licencePage.apply.logout' }));

    expect(logoutConsoleLogin).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: 'licencePage.apply.login' })).toBeInTheDocument();
    expect(screen.getByText('upgrade.loginFirst')).toBeInTheDocument();
    expect(screen.queryByText(/upgrade\.offer/)).not.toBeInTheDocument();
  });

  it('FP15b a sign-out that fails says so', async () => {
    situation.mockResolvedValue(base);
    logoutConsoleLogin.mockRejectedValue(new Error('boom'));
    render(<FlocksproUpgradePage />);

    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.apply.logout' }));
    const asked = await screen.findByRole('dialog', { name: 'licencePage.apply.logoutTitle' });
    await userEvent.click(within(asked).getByRole('button', { name: 'licencePage.apply.logout' }));
    expect(await screen.findByText('boom')).toBeInTheDocument();
  });

  it('FP18 Console out of reach: the offline way, from the application file to the licence file', async () => {
    const user = userEvent.setup();
    const open = { ...base, previous: null };
    situation.mockResolvedValue(open);
    consoleLoginSession.mockResolvedValue({ logged_in: false });
    consoleConnectivity.mockResolvedValue({ configured: true, online: false, console_host: 'console.example', checked_at: '' });
    offlineApplication.mockResolvedValue({
      format: 'flocks-licence-application/1', product: 'Flocks Pro', license_type: 'poc', company: 'Acme', applicant_name: 'Li Lei',
      applicant_email: 'li@acme.example', applicant_phone: '+86 138 0000 0000', sales_rep_name: null, notes: null,
      replaces_license_id: null, machine: '0123456789abcdef0123456789abcdef', machine_kind: 'devid', install_id: 'inst-1',
      hostname: 'h', platform: 'linux-arm64', flocks_version: '2026.11.9', applied_by: 'admin', created_at: '2026-11-17T00:00:00Z',
    });
    render(<FlocksproUpgradePage />);

    const card = await screen.findByTestId('licence-apply');
    await waitFor(() => expect(within(card).getByTestId('network-status')).toHaveTextContent('licencePage.apply.offline'));
    // Nothing of the cloud account offline: not the account, not whether it is signed in, not the sign-in.
    expect(within(card).queryByText('licencePage.apply.cloud')).toBeNull();
    expect(within(card).queryByTestId('cloud-status')).toBeNull();
    expect(within(card).queryByText('licencePage.apply.signInHint')).toBeNull();
    expect(screen.queryByRole('button', { name: 'licencePage.apply.login' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'licencePage.apply.applyOnline' })).toBeNull();
    // The application makes a file to hand over, downloaded at once; the dialog closes and says so.
    await user.click(screen.getByRole('button', { name: 'licencePage.apply.applyOffline' }));
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.dialog.offlineTitle' });
    expect(within(dialog).getByText('licencePage.dialog.offlineTip')).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(FIELD.company), 'Acme');
    await user.type(within(dialog).getByLabelText(FIELD.name), 'Li Lei');
    await user.type(within(dialog).getByLabelText(FIELD.email), 'li@acme.example');
    await user.type(within(dialog).getByLabelText(FIELD.phone), '+86 138 0000 0000');
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.dialog.download' }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    expect(offlineApplication).toHaveBeenCalledWith(expect.objectContaining({ company: 'Acme', applicant_phone: '+86 138 0000 0000' }));
    expect(toast.success).toHaveBeenCalledWith('licencePage.dialog.downloaded');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(apply).not.toHaveBeenCalled();

    // On to the licence file that comes back: the page moves on to the machine with a licence.
    importLicence.mockResolvedValue({
      licence: { license_id: 'lic_off_1', license_status: 'poc', expires_at: 1802649569 },
      situation: { ...open, previous: { ...previous, version: null, license_id: 'lic_off_1', license_status: 'poc', lapsed: false } },
    });
    await user.click(screen.getByRole('button', { name: 'licencePage.info.import' }));
    const importing = await screen.findByRole('dialog', { name: 'licencePage.import.title' });
    await user.upload(within(importing).getByTestId('licence-import-file'), new File(['{"key":"a.b.c"}'], 'license.json', { type: 'application/json' }));
    await user.click(within(importing).getByRole('button', { name: 'licencePage.import.go' }));
    await waitFor(() => expect(importLicence).toHaveBeenCalledWith('{"key":"a.b.c"}'));
    expect(await within(row()).findByText('lic_off_1')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(toast.success).toHaveBeenCalledWith('licencePage.import.okTitle', expect.stringMatching(/^licenceFile\.importedUntil lic_off_1/));
    // No version check without Console; the offline installer is the way.
    expect(checkVersion()).toBeDisabled();
    expect(reasonOf(checkVersion())).toBe('licencePage.version.checkOffline');
    expect(screen.getByTestId('upgrade-offline')).toHaveTextContent('upgrade.offline');
    expect(sync()).toBeDisabled();
    expect(reasonOf(sync())).toBe('licencePage.info.syncOffline');
    // Offline, nothing is taken or asked of Console without the administrator.
    expect(take).not.toHaveBeenCalled();
  });

  it('FP19 Console answering: the licence file can be imported too while the machine has no licence', async () => {
    const user = userEvent.setup();
    situation.mockResolvedValue({ ...base, previous: null });
    importLicence.mockRejectedValue({ response: { data: { detail: '这份授权已在 2026-01-01 到期，不能导入' } } });
    render(<FlocksproUpgradePage />);
    await waitFor(() => expect(applyOnline()).toBeEnabled());

    await user.click(screen.getByRole('button', { name: 'licencePage.info.import' }));
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.import.title' });
    // A file only: there is nothing to paste.
    expect(within(dialog).queryByRole('textbox')).toBeNull();
    const go = within(dialog).getByRole('button', { name: 'licencePage.import.go' });
    expect(go).toBeDisabled();
    await user.upload(within(dialog).getByTestId('licence-import-file'), new File(['eyJ.a.b'], 'license.json', { type: 'application/json' }));
    await user.click(go);
    expect(importLicence).toHaveBeenCalledWith('eyJ.a.b');
    // Refused: said in a notice, the dialog closes, the machine is as it was.
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('licencePage.import.failTitle', expect.stringContaining('不能导入')));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(within(row()).getByTestId('edition-status')).toHaveTextContent('licencePage.info.openEdition');
  });

  it('FP19b imports the file chosen last', async () => {
    const user = userEvent.setup();
    situation.mockResolvedValue({ ...base, previous: null });
    importLicence.mockRejectedValue({ response: { data: { detail: '不能导入' } } });
    render(<FlocksproUpgradePage />);
    await waitFor(() => expect(applyOnline()).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'licencePage.info.import' }));
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.import.title' });

    await user.upload(within(dialog).getByTestId('licence-import-file'), new File(['{"key":"first.a"}'], 'first.json', { type: 'application/json' }));
    await user.upload(within(dialog).getByTestId('licence-import-file'), new File(['{"key":"second.b"}'], 'second.json', { type: 'application/json' }));
    expect(within(dialog).getByText('second.json')).toBeInTheDocument();
    expect(within(dialog).queryByText('first.json')).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.import.go' }));
    await waitFor(() => expect(importLicence).toHaveBeenLastCalledWith('{"key":"second.b"}'));
  });

  it('FP5e a sign-in that cannot be read is offered again rather than hidden', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    consoleLoginSession.mockRejectedValue(new Error('session down'));
    render(<FlocksproUpgradePage />);
    expect(await screen.findByRole('button', { name: 'licencePage.apply.login' })).toBeInTheDocument();
    expect(screen.getByTestId('cloud-status')).toHaveTextContent('licencePage.apply.signedOut');
  });

  it('FP20 an application sent from the menu dialog shows on the kept page without a reload', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByText('licencePage.records.empty')).toBeInTheDocument();
    expect(applications).toHaveBeenCalledTimes(1);
    applications.mockResolvedValue({ items: [pending], note: null });
    window.dispatchEvent(new Event('flocks-enterprise-licence-changed'));
    expect(await screen.findByText('req_1')).toBeInTheDocument();
    expect(applications).toHaveBeenCalledTimes(2);
  });

  it('FP21 writes every version it shows as v<year>.<month>.<day>.<build>', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    clientGet.mockResolvedValue({ data: { status: 'ok', version: '2026.11.17.2' } });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByTestId('product-version')).toHaveTextContent('licencePage.version.open v2026.11.17.2');
    expect(clientGet).toHaveBeenCalledWith('/api/health');
  });

  it('FP22 one panel: the application, the licence information and the product version, in that order', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    const panel = await screen.findByTestId('licence-panel');
    const sections = [...panel.children].map((child) => child.getAttribute('data-testid'));
    expect(sections).toEqual(['licence-apply', 'licence-info', 'licence-version']);
    // The records are in the licence information, under the table.
    expect(within(screen.getByTestId('licence-info')).getByTestId('application-records')).toBeInTheDocument();
  });

  it('FP23 signing out asks first; cancelled, nothing happens', async () => {
    situation.mockResolvedValue(base);
    render(<FlocksproUpgradePage />);
    await userEvent.click(await screen.findByRole('button', { name: 'licencePage.apply.logout' }));
    const asked = await screen.findByRole('dialog', { name: 'licencePage.apply.logoutTitle' });
    await userEvent.click(within(asked).getByRole('button', { name: 'licencePage.dialog.cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(logoutConsoleLogin).not.toHaveBeenCalled();
    expect(screen.getByTestId('cloud-status')).toHaveTextContent('licencePage.apply.signedIn');
  });

  it.each([
    ['online and signed in', { logged_in: true, account_name: 'ops@example.com' }, true],
    ['signed out', { logged_in: false }, true],
    ['offline', { logged_in: true, account_name: 'ops@example.com' }, false],
  ])('FP24 a licence running out, %s: said in its status and under the table; a new file is the way', async (_name, login, online) => {
    const soon = Math.floor(Date.now() / 1000) + 8 * 86400;
    situation.mockResolvedValue({ ...base, previous: { ...previous, expires_at: soon } });
    consoleLoginSession.mockResolvedValue(login);
    consoleConnectivity.mockResolvedValue({ configured: true, online, console_host: 'console.example', checked_at: '' });
    render(<FlocksproUpgradePage />);
    const hint = await screen.findByTestId('licence-term-hint');
    // Still in force: the application waits (its button says why), so the hint offers no application.
    await waitFor(() => expect(hint).toHaveTextContent('licencePage.info.term.offline licencePage.info.term.near'));
    expect(hint).toHaveAttribute('data-term', 'near');
    const tag = within(row()).getByTestId('edition-status');
    expect(tag).toHaveTextContent('licencePage.info.term.near');
    expect(tag.className).toContain('text-amber-700');
    if (online) {
      expect(applyOnline()).toBeDisabled();
      expect(reasonOf(applyOnline())).toBe('licencePage.apply.licensed');
    }
  });

  it.each([
    ['online and signed in', { logged_in: true, account_name: 'ops@example.com' }, true, 'licencePage.info.term.online'],
    ['signed out', { logged_in: false }, true, 'licencePage.info.term.signedOut'],
    ['offline', { logged_in: true, account_name: 'ops@example.com' }, false, 'licencePage.info.term.offline'],
  ])('FP24b a licence run out, %s: applied for again, and the hint says how', async (_name, login, online, way) => {
    situation.mockResolvedValue({ ...base, previous: { ...previous, expires_at: 1790740045, lapsed: true } });
    consoleLoginSession.mockResolvedValue(login);
    consoleConnectivity.mockResolvedValue({ configured: true, online, console_host: 'console.example', checked_at: '' });
    render(<FlocksproUpgradePage />);
    const hint = await screen.findByTestId('licence-term-hint');
    await waitFor(() => expect(hint).toHaveTextContent(`${way} licencePage.info.term.expired`));
    if (online && login.logged_in) await waitFor(() => expect(applyOnline()).toBeEnabled());
  });

  it('FP25 an approved application is taken without being asked, once; offline or signed out it waits for its button', async () => {
    const approved = { ...pending, status: 'approved', license_id: 'lic_1', expires_at: 1798761600, can_take: true, can_withdraw: false };
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({ items: [approved], note: null });
    take.mockResolvedValue({
      application: { ...approved, status: 'activated', can_take: false },
      situation: { ...base, previous: { version: null, license_id: 'lic_1', license_status: 'poc', pro_enabled: false, expires_at: 1798761600 } },
    });
    const first = render(<FlocksproUpgradePage />);
    await waitFor(() => expect(take).toHaveBeenCalledWith('req_1'));
    expect(toast.success).toHaveBeenCalledWith('licencePage.records.autoTaken req_1');
    expect(await within(row()).findByText('lic_1')).toBeInTheDocument();
    expect(take).toHaveBeenCalledTimes(1);
    first.unmount();

    // A take that did not work is not tried again by itself: its button stays.
    take.mockReset();
    take.mockRejectedValue({ response: { data: { detail: 'Console 没有应答' } } });
    const second = render(<FlocksproUpgradePage />);
    await waitFor(() => expect(take).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Console 没有应答')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'licencePage.records.take' })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(take).toHaveBeenCalledTimes(1);
    second.unmount();

    // Offline: nothing taken without the administrator.
    take.mockReset();
    consoleConnectivity.mockResolvedValue({ configured: true, online: false, console_host: 'console.example', checked_at: '' });
    render(<FlocksproUpgradePage />);
    expect(await screen.findByRole('button', { name: 'licencePage.records.take' })).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(take).not.toHaveBeenCalled();
  });

  it('FP25b nothing is taken before the first check of Console\'s reach is back; found offline, nothing at all', async () => {
    const approved = { ...pending, status: 'approved', license_id: 'lic_1', expires_at: 1798761600, can_take: true, can_withdraw: false };
    situation.mockResolvedValue({ ...base, previous: null });
    applications.mockResolvedValue({ items: [approved], note: null });
    let answer: (value: unknown) => void = () => undefined;
    consoleConnectivity.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    render(<FlocksproUpgradePage />);
    // The sign-in and the approved application are in; the check of Console's reach is not.
    expect(await screen.findByRole('button', { name: 'licencePage.records.take' })).toBeInTheDocument();
    await waitFor(() => expect(consoleLoginSession).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(take).not.toHaveBeenCalled();
    await act(async () => { answer({ configured: true, online: false, console_host: 'console.example', checked_at: '' }); });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(take).not.toHaveBeenCalled();
  });

  it('FP26 the online application is for Pro only in this edition: the modules and scene suites come after it', async () => {
    situation.mockResolvedValue({ ...base, previous: null });
    render(<FlocksproUpgradePage />);
    await waitFor(() => expect(applyOnline()).toBeEnabled());
    await userEvent.click(applyOnline());
    const dialog = screen.getByRole('dialog', { name: 'licencePage.dialog.onlineTitle' });
    const scope = within(dialog).getByTestId('licence-scope');
    const [platform, items] = within(scope).getAllByRole('checkbox');
    expect(platform).toBeChecked();
    expect(platform).toBeDisabled();
    expect(items).not.toBeChecked();
    expect(items).toBeDisabled();
    expect(within(scope).getByText('licencePage.dialog.scopeItemsAfterPro')).toBeInTheDocument();
    // No sales contact field.
    expect(within(dialog).queryByLabelText('apply.fields.salesRep')).toBeNull();
    expect(within(dialog).getByLabelText('licencePage.dialog.notes')).toBeInTheDocument();
  });
});
