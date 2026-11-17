import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import QuickDialog, { LICENCE_CHANGED_EVENT } from './QuickDialog';

// Case list (编号 | 场景):
//   QD1 Console answers: the online hint; signed out, the form waits for the sign-in, which is offered
//   QD2 signed in, the application is checked as the server checks it, sent, and followed on the page
//   QD3 an application under review: what it is, and the page that follows it, instead of the form
//   QD4 Console out of reach: the offline hint; the form makes an application file with this machine's
//       fingerprint, copied or downloaded; nothing goes to Console
//   QD5 the administrator takes the other way: online warned it may fail when Console was out of reach
//   QD6 a licence file imported: the machine has a licence, the page underneath is told, and the way
//       on is the upgrade on the Flocks Pro page; a refusal says why and changes nothing
//   QD7 a machine with a licence already: no form, the way on to the upgrade
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
  });

  it('QD1 online and signed out: the form waits for the cloud sign-in', async () => {
    const dialog = await (async () => { open(); return screen.findByRole('dialog'); })();
    expect(await within(dialog).findByText('network.online console.example')).toBeInTheDocument();
    expect(await within(dialog).findByTestId('apply-hint')).toHaveTextContent('quick.onlineHint');
    expect(await within(dialog).findByText('quick.signInFirst')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('apply.fields.company')).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'apply.submit' })).toBeDisabled();
    api.startConsoleLogin.mockRejectedValueOnce(new Error('console down'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'consoleLogin.loginAction' }));
    await waitFor(() => expect(api.startConsoleLogin).toHaveBeenCalledWith(`${window.location.origin}/flockspro-upgrade/callback`));
    expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
  });

  it('QD2 signed in: the application is checked, sent, and followed on the page', async () => {
    const user = userEvent.setup();
    api.consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@acme.example' });
    api.apply.mockResolvedValue({ request_id: 'req_9', status: 'pending' });
    const onClose = open();
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('apply.fields.company')).not.toBeDisabled());
    expect(within(dialog).getByText('ops@acme.example')).toBeInTheDocument();
    await fill(user, dialog, 'not-an-email');
    await user.click(within(dialog).getByRole('button', { name: 'apply.submit' }));
    expect(await within(dialog).findByText('apply.problems.email')).toBeInTheDocument();
    expect(api.apply).not.toHaveBeenCalled();

    await user.clear(within(dialog).getByLabelText('apply.fields.email'));
    await user.type(within(dialog).getByLabelText('apply.fields.email'), 'li@acme.example');
    const changed = vi.fn();
    window.addEventListener(LICENCE_CHANGED_EVENT, changed);
    await user.click(within(dialog).getByRole('button', { name: 'apply.submit' }));
    // The Flocks Pro page kept underneath lists the application from now on.
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    window.removeEventListener(LICENCE_CHANGED_EVENT, changed);
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith(expect.objectContaining({
      license_type: 'poc', company: 'Acme', applicant_email: 'li@acme.example', sales_rep_name: null, notes: null,
    })));
    expect(api.offlineApplication).not.toHaveBeenCalled();
    expect(await within(dialog).findByText('quick.submitted req_9')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'quick.followOnPage' }));
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/flockspro');
  });

  it('QD3 an application under review stands in the way of a new one', async () => {
    api.consoleLoginSession.mockResolvedValue({ logged_in: true, account_name: 'ops@acme.example' });
    api.applications.mockResolvedValue({ items: [{ request_id: 'req_5', status: 'reviewing', can_take: false, licence_over: false }], note: null });
    open();
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('quick.inReview req_5 apply.status.reviewing')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('apply.fields.company')).toBeNull();
  });

  it('QD4 offline: the form makes an application file with this machine, and nothing goes to Console', async () => {
    const user = userEvent.setup();
    api.consoleConnectivity.mockResolvedValue({ configured: true, online: false, console_host: 'console.example', checked_at: '' });
    api.offlineApplication.mockResolvedValue(OFFLINE_APPLICATION);
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    open();
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('network.offline console.example')).toBeInTheDocument();
    expect(await within(dialog).findByTestId('apply-hint')).toHaveTextContent('quick.offlineHint');
    expect(within(dialog).queryByText('quick.signInFirst')).toBeNull();
    await fill(user, dialog);
    await user.click(within(dialog).getByRole('button', { name: 'offlineApply.generate' }));
    const result = await within(dialog).findByTestId('offline-application');
    expect(within(result).getByText(OFFLINE_APPLICATION.machine)).toBeInTheDocument();
    expect(api.apply).not.toHaveBeenCalled();
    expect(api.applications).not.toHaveBeenCalled();

    await user.click(within(result).getByRole('button', { name: 'offlineApply.copy' }));
    const copied = api.copyText.mock.calls[0][0] as string;
    expect(copied).toContain('offlineApply.textTitle');
    expect(copied).toContain(OFFLINE_APPLICATION.machine);
    expect(copied).toContain('Acme');
    await user.click(within(result).getByRole('button', { name: 'offlineApply.download' }));
    expect(createObjectURL).toHaveBeenCalledOnce();
    await user.click(within(dialog).getByRole('button', { name: /quick\.toImport/ }));
    expect(within(dialog).getByRole('tab', { name: 'quick.tabImport' })).toHaveAttribute('aria-selected', 'true');
    expect(within(dialog).getByText('quick.importHintOffline')).toBeInTheDocument();
  });

  it('QD5 the administrator takes the other way; online warned when Console was out of reach', async () => {
    api.consoleConnectivity.mockResolvedValue({ configured: true, online: false, console_host: 'console.example', checked_at: '' });
    open();
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('quick.offlineHint');
    fireEvent.click(within(dialog).getByRole('button', { name: 'quick.modeOnline' }));
    expect(await within(dialog).findByText('quick.onlineUnreachable')).toBeInTheDocument();
    expect(within(dialog).getByTestId('apply-hint')).toHaveTextContent('quick.onlineHint');
  });

  it('QD6 a licence file imported: the page underneath is told and the way on is the upgrade', async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    window.addEventListener(LICENCE_CHANGED_EVENT, changed);
    try {
      api.importLicence.mockRejectedValueOnce({ response: { status: 409, data: { detail: '这份授权绑定的是另一台机器' } } });
      open();
      const dialog = await screen.findByRole('dialog');
      await within(dialog).findByTestId('apply-hint');
      await user.click(within(dialog).getByRole('tab', { name: 'quick.tabImport' }));
      const file = within(dialog).getByTestId('quick-import-file');
      await user.upload(file, new File(['{"key":"a.b.c"}'], 'license.json', { type: 'application/json' }));
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('这份授权绑定的是另一台机器');
      expect(changed).not.toHaveBeenCalled();

      api.importLicence.mockResolvedValueOnce({ licence: { license_id: 'lic_off_1', license_status: 'poc', expires_at: 1802649569 }, situation: LICENSED });
      await user.upload(within(dialog).getByTestId('quick-import-file'), new File(['{"key":"a.b.c"}'], 'license.json', { type: 'application/json' }));
      const licensed = await within(dialog).findByTestId('quick-licensed');
      expect(within(licensed).getByRole('status')).toHaveTextContent('licenceFile.importedUntil lic_off_1');
      expect(api.importLicence).toHaveBeenLastCalledWith('{"key":"a.b.c"}');
      expect(changed).toHaveBeenCalledOnce();
      await user.click(within(licensed).getByRole('button', { name: 'quick.toUpgrade' }));
      expect(screen.getByTestId('path')).toHaveTextContent('/settings/flockspro');
    } finally {
      window.removeEventListener(LICENCE_CHANGED_EVENT, changed);
    }
  });

  it('QD7 a machine with a licence already: no form, the way on to the upgrade', async () => {
    api.situation.mockResolvedValue(LICENSED);
    open();
    const dialog = await screen.findByRole('dialog');
    const licensed = await within(dialog).findByTestId('quick-licensed');
    expect(within(licensed).getByText('quick.licensedTitle lic_off_1')).toBeInTheDocument();
    expect(within(dialog).queryByRole('tab')).toBeNull();
    expect(within(licensed).getByRole('button', { name: 'quick.toUpgrade' })).toBeInTheDocument();
  });

  it('QD8 an install the enterprise core runs: nothing to apply for here', async () => {
    api.situation.mockResolvedValue({ ...OPEN, available: false });
    open();
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('quick.unavailable')).toBeInTheDocument();
    expect(within(dialog).queryByRole('tab')).toBeNull();
  });
});
