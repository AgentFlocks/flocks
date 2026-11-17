import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import QuickDialog, { LICENCE_CHANGED_EVENT } from './QuickDialog';

// Case list (编号 | 场景):
//   QD1 Console answers, signed out: the cloud sign-in first (as the design's login dialog), the licence
//       file importable instead
//   QD2 signed in: the application is checked as the server checks it, sent, the page underneath told,
//       and followed on the page
//   QD3 an application under review: what it is, and the page that follows it, instead of the form
//   QD4 Console out of reach: the offline application, nothing of the cloud account; the application file
//       (with this machine's fingerprint) is downloaded at once, copied or saved again; nothing goes to Console
//   QD6 a licence file imported from the dialog: the page underneath is told, the import is said, and the
//       way on is the Flocks Pro page; a refusal says why and changes nothing
//   QD7 a machine with a licence already: straight on to the Flocks Pro page, as the design's entry does
//   QD8 an install the enterprise core runs: nothing to apply for here

const api = vi.hoisted(() => ({
  situation: vi.fn(),
  applications: vi.fn(),
  apply: vi.fn(),
  offlineApplication: vi.fn(),
  importLicence: vi.fn(),
  consoleLoginSession: vi.fn(),
  startConsoleLogin: vi.fn(),
  consoleConnectivity: vi.fn(),
  copyText: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/api/enterpriseUpgrade', () => ({
  ENTERPRISE_UPGRADE_PAGE: '/settings/flockspro',
  enterpriseUpgradeApi: {
    situation: api.situation,
    applications: api.applications,
    apply: api.apply,
    offlineApplication: api.offlineApplication,
    importLicence: api.importLicence,
  },
}));
vi.mock('@/api/auth', () => ({
  authApi: {
    consoleLoginSession: api.consoleLoginSession,
    startConsoleLogin: api.startConsoleLogin,
    consoleConnectivity: api.consoleConnectivity,
  },
}));
vi.mock('@/utils/clipboard', () => ({ copyText: api.copyText }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => api.toast }));
vi.mock('react-i18next', () => {
  const t = (key: string, options?: Record<string, unknown>) =>
    options ? `${key} ${Object.entries(options).filter(([k]) => k !== 'defaultValue').map(([, v]) => String(v)).join(' ')}`.trim() : key;
  return { useTranslation: () => ({ t }) };
});

const OPEN = {
  available: true, previous: null, platform: 'linux-amd64', account: 'flocks', root: false, systemd: false,
  install_root: '/opt/flockspro', install_root_ready: true, prepare_command: null, can_start: true, upgrade: null,
};
const LICENSED = {
  ...OPEN,
  previous: { version: null, license_id: 'lic_off_1', license_status: 'poc', pro_enabled: false, expires_at: 1802649569, lapsed: false },
};
const OFFLINE_APPLICATION = {
  format: 'flocks-licence-application/1', product: 'Flocks Pro', license_type: 'poc', company: 'Acme', applicant_name: 'Li Lei',
  applicant_email: 'li@acme.example', applicant_phone: '+86 138 0000 0000', sales_rep_name: null, notes: null,
  replaces_license_id: null, machine: '0123456789abcdef0123456789abcdef', machine_kind: 'devid', install_id: 'inst-1',
  hostname: 'host-1', platform: 'linux-amd64', flocks_version: '2026.11.9', applied_by: 'admin', created_at: '2026-11-17T00:00:00Z',
};

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>;
}

function open(onClose = vi.fn()) {
  render(
    <MemoryRouter initialEntries={['/sessions']}>
      <QuickDialog onClose={onClose} />
      <Routes><Route path="*" element={<CurrentPath />} /></Routes>
    </MemoryRouter>,
  );
  return onClose;
}

async function fill(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, email = 'li@acme.example') {
  await user.type(within(dialog).getByLabelText('apply.fields.company'), 'Acme');
  await user.type(within(dialog).getByLabelText('apply.fields.applicantName'), 'Li Lei');
  await user.type(within(dialog).getByLabelText('apply.fields.email'), email);
  await user.type(within(dialog).getByLabelText('apply.fields.phone'), '+86 138 0000 0000');
}

describe('Flocks Pro quick dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.situation.mockResolvedValue(OPEN);
    api.applications.mockResolvedValue({ items: [], note: null });
    api.consoleLoginSession.mockResolvedValue({ logged_in: false });
    api.consoleConnectivity.mockResolvedValue({ configured: true, online: true, console_host: 'console.example', checked_at: '' });
    api.copyText.mockResolvedValue(undefined);
    // The application file is saved through a link to a blob.
    URL.createObjectURL = vi.fn(() => 'blob:application');
    URL.revokeObjectURL = vi.fn();
  });

  it('QD1 online and signed out: the cloud sign-in first, the licence file importable instead', async () => {
    const user = userEvent.setup();
    api.startConsoleLogin.mockResolvedValue({ passport_login_url: `${window.location.pathname}#signed-in` });
    open();
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.signInTitle' });
    expect(within(dialog).getByText('licencePage.apply.signInHint')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('apply.fields.company')).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'licencePage.info.import' })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.launcher.signIn' }));
    await waitFor(() => expect(api.startConsoleLogin).toHaveBeenCalledWith(`${window.location.origin}/flockspro-upgrade/callback`));
  });

  it('QD2 signed in: the application is checked, sent, and followed on the page', async () => {
    const user = userEvent.setup();
    api.consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@example.com' });
    api.apply.mockResolvedValue({ request_id: 'req_9', status: 'pending' });
    const onClose = open();
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.title' });
    expect(within(dialog).getByText('licencePage.dialog.onlineTip')).toBeInTheDocument();
    await fill(user, dialog, 'li@acme');
    await user.click(within(dialog).getByRole('button', { name: 'apply.submit' }));
    expect(within(dialog).getByText('apply.problems.email')).toBeInTheDocument();
    expect(api.apply).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText('apply.fields.email'), '.example');
    const changed = vi.fn();
    window.addEventListener(LICENCE_CHANGED_EVENT, changed);
    await user.click(within(dialog).getByRole('button', { name: 'apply.submit' }));
    // The Flocks Pro page kept underneath lists the application from now on.
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    window.removeEventListener(LICENCE_CHANGED_EVENT, changed);
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith(expect.objectContaining({
      license_type: 'poc', company: 'Acme', applicant_email: 'li@acme.example', sales_rep_name: null, notes: null,
    })));
    expect(await within(dialog).findByText('quick.submitted req_9')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.launcher.toRecords' }));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/flockspro');
  });

  it('QD3 an application under review stands in the way of a new one', async () => {
    api.consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@example.com' });
    api.applications.mockResolvedValue({ items: [{ request_id: 'req_1', status: 'pending', can_take: false }], note: null });
    open();
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.title' });
    expect(await within(dialog).findByText('quick.inReview req_1 apply.status.pending')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('apply.fields.company')).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'licencePage.launcher.toRecords' })).toBeInTheDocument();
  });

  it('QD4 offline: the application file at once, with this machine, and nothing of the cloud account', async () => {
    const user = userEvent.setup();
    api.consoleConnectivity.mockResolvedValue({ configured: true, online: false, console_host: 'console.example', checked_at: '' });
    api.offlineApplication.mockResolvedValue(OFFLINE_APPLICATION);
    open();
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.title' });
    expect(within(dialog).getByText('licencePage.dialog.offlineTip')).toBeInTheDocument();
    // Offline there is no cloud account to sign in to: nothing of it shows.
    expect(within(dialog).queryByText(/licencePage\.apply\.signInHint|licencePage\.launcher\.signIn/)).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'licencePage.launcher.signIn' })).toBeNull();
    await fill(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.dialog.download' }));

    expect(await within(dialog).findByTestId('offline-application')).toHaveTextContent('0123456789abcdef0123456789abcdef');
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(api.offlineApplication).toHaveBeenCalledWith(expect.objectContaining({ company: 'Acme', applicant_name: 'Li Lei' }));
    expect(api.apply).not.toHaveBeenCalled();
    expect(api.applications).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole('button', { name: 'offlineApply.copy' }));
    expect(api.copyText).toHaveBeenCalledWith(expect.stringContaining('0123456789abcdef0123456789abcdef'));
  });

  it('QD6 a licence file imported: the page underneath is told and the way on is the Flocks Pro page', async () => {
    const user = userEvent.setup();
    api.importLicence.mockRejectedValueOnce({ response: { data: { detail: '这份授权绑定的是另一台机器：请用这台机器的申请信息重新申请' } } });
    const onClose = open();
    let dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.signInTitle' });
    await user.click(within(dialog).getByRole('button', { name: 'licencePage.info.import' }));
    dialog = await screen.findByRole('dialog', { name: 'licencePage.import.title' });
    const go = within(dialog).getByRole('button', { name: 'licencePage.import.go' });
    expect(go).toBeDisabled();
    await user.upload(within(dialog).getByTestId('quick-import-file'), new File(['{"key":"a.b.c"}'], 'license.json', { type: 'application/json' }));
    expect(within(dialog).getByText('license.json')).toBeInTheDocument();
    await user.click(go);
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('另一台机器');
    expect(onClose).not.toHaveBeenCalled();

    api.importLicence.mockResolvedValue({ licence: { license_id: 'lic_off_1', license_status: 'poc', expires_at: 1802649569 }, situation: LICENSED });
    const changed = vi.fn();
    window.addEventListener(LICENCE_CHANGED_EVENT, changed);
    await user.click(go);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    window.removeEventListener(LICENCE_CHANGED_EVENT, changed);
    expect(api.importLicence).toHaveBeenLastCalledWith('{"key":"a.b.c"}');
    expect(api.toast.success).toHaveBeenCalledWith('licencePage.launcher.importedTitle', expect.stringContaining('lic_off_1'));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/flockspro');
  });

  it('QD7 a machine with a licence already: straight on to the Flocks Pro page', async () => {
    api.situation.mockResolvedValue(LICENSED);
    const onClose = open();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/flockspro');
    expect(api.apply).not.toHaveBeenCalled();
  });

  it('QD8 an install the enterprise core runs: nothing to apply for here', async () => {
    api.situation.mockResolvedValue({ ...OPEN, available: false });
    open();
    const dialog = await screen.findByRole('dialog', { name: 'licencePage.launcher.title' });
    expect(within(dialog).getByText('quick.unavailable')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('apply.fields.company')).toBeNull();
  });
});
