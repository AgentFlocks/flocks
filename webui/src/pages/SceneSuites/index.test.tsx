import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import SceneSuitesPage from './index';

const { hubAPI, webuiContractPagesAPI, flocksproUsersApi, toast } = vi.hoisted(() => ({
  hubAPI: {
    sceneSuites: vi.fn(),
    install: vi.fn(),
    installStream: vi.fn(),
    update: vi.fn(),
    uninstall: vi.fn(),
  },
  webuiContractPagesAPI: {
    setWorkspaceEnabled: vi.fn(),
  },
  flocksproUsersApi: { hasCapability: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/api/hub', () => ({ hubAPI }));
vi.mock('@/api/webuiContractPages', () => ({ webuiContractPagesAPI }));
vi.mock('@/api/flocksproUsers', () => ({ flocksproUsersApi }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toast }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}));

const SOC = {
  id: 'soc-workspace',
  name: 'SOC Workspace Component',
  nameCn: 'SOC 工作区场景套件',
  description: 'soc',
  descriptionCn: 'SOC 套件',
  version: '1.0.0',
  installedVersion: '1.0.0',
  edition: 'oss' as const,
  state: 'installed' as const,
  workspaceId: 'soc_ui',
  workspaceEnabled: true,
};

const CODE_AUDIT = {
  id: 'code-audit-workspace',
  name: 'Code Audit Workspace Component',
  nameCn: '代码审计场景套件',
  description: 'audit',
  descriptionCn: '代码审计套件',
  version: '1.0.0',
  installedVersion: null,
  edition: 'pro' as const,
  state: 'available' as const,
  workspaceId: 'code_audit_ui',
  workspaceEnabled: null,
};

function renderPage() {
  return render(
    <MemoryRouter>
      <SceneSuitesPage />
    </MemoryRouter>,
  );
}

function card(suiteId: string): HTMLElement {
  const el = document.querySelector(`[data-suite-id="${suiteId}"]`);
  if (!el) throw new Error(`suite card not rendered: ${suiteId}`);
  return el as HTMLElement;
}

describe('SceneSuitesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hubAPI.sceneSuites.mockResolvedValue({ data: [SOC, CODE_AUDIT] });
    flocksproUsersApi.hasCapability.mockResolvedValue(false);
    hubAPI.install.mockResolvedValue({});
    hubAPI.update.mockResolvedValue({});
    hubAPI.uninstall.mockResolvedValue({});
    webuiContractPagesAPI.setWorkspaceEnabled.mockResolvedValue({});
  });

  it('offers install, disable and uninstall for an installed suite', async () => {
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    expect(within(soc).getByText('SOC 工作区场景套件')).toBeInTheDocument();
    expect(within(soc).getByRole('button', { name: '停用' })).toBeInTheDocument();
    expect(within(soc).getByRole('button', { name: '卸载' })).toBeInTheDocument();
    expect(within(soc).queryByRole('button', { name: '安装' })).not.toBeInTheDocument();
  });

  it('locks a Pro suite behind an upgrade link in the open-source edition', async () => {
    renderPage();

    const audit = await waitFor(() => card('code-audit-workspace'));
    await waitFor(() => expect(flocksproUsersApi.hasCapability).toHaveBeenCalled());
    await waitFor(() => {
      expect(within(audit).getByRole('link', { name: '去升级' })).toHaveAttribute('href', '/settings/flockspro');
    });
    expect(within(audit).queryByRole('button', { name: '安装' })).not.toBeInTheDocument();
    expect(within(audit).getByText('需要 Flocks Pro')).toBeInTheDocument();
  });

  it('lets a Pro edition install a Pro suite', async () => {
    flocksproUsersApi.hasCapability.mockResolvedValue(true);
    const user = userEvent.setup();
    renderPage();

    const audit = await waitFor(() => card('code-audit-workspace'));
    const install = await within(audit).findByRole('button', { name: '安装' });
    await user.click(install);

    // Installing a suite streams per-component progress, so it goes through
    // installStream rather than the plain install call.
    await waitFor(() => expect(hubAPI.installStream).toHaveBeenCalledWith(
      'component', 'code-audit-workspace', expect.any(Function),
    ));
    expect(hubAPI.install).not.toHaveBeenCalled();
    // The list is reloaded so the card reflects the new state.
    await waitFor(() => expect(hubAPI.sceneSuites).toHaveBeenCalledTimes(2));
  });

  it('shows per-component install progress while a suite installs', async () => {
    flocksproUsersApi.hasCapability.mockResolvedValue(true);
    // Drive the progress callback the way the server stream would.
    hubAPI.installStream.mockImplementation(async (_type, _id, onProgress) => {
      onProgress({
        event: 'start',
        id: 'code-audit-workspace',
        type: 'component',
        name: 'Code Audit Workspace',
        total: 2,
        items: [
          { type: 'webui', id: 'code_audit_ui', status: 'pending' },
          { type: 'tool', id: 'code_audit_tool', status: 'pending' },
        ],
      });
      onProgress({
        event: 'item',
        id: 'code-audit-workspace',
        type: 'component',
        name: 'Code Audit Workspace',
        total: 2,
        item: { type: 'webui', id: 'code_audit_ui', status: 'installed' },
      });
    });
    const user = userEvent.setup();
    renderPage();

    const audit = await waitFor(() => card('code-audit-workspace'));
    await user.click(await within(audit).findByRole('button', { name: '安装' }));

    // The panel lists the suite's own components, not just a spinner.
    // 面板逐个列出套件的子件（显示名与「类型 · id」各一处，故用 All 变体）。
    expect((await screen.findAllByText(/code_audit_ui/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/code_audit_tool/).length).toBeGreaterThan(0);
    // 走的是流式接口，不是普通 install。
    expect(hubAPI.install).not.toHaveBeenCalled();
  });

  it('disables a workspace through the contract API', async () => {
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '停用' }));

    await waitFor(() => {
      expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', false);
    });
  });

  it('enables a workspace that is installed but turned off', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: false }] });
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '启用' }));

    await waitFor(() => {
      expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true);
    });
  });

  it('updates a suite in one call so its pages come along', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'updateAvailable' as const }] });
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '更新' }));

    await waitFor(() => expect(hubAPI.update).toHaveBeenCalledWith('component', 'soc-workspace'));
    expect(hubAPI.update).toHaveBeenCalledTimes(1);
  });

  it('reports a failed action instead of pretending it worked', async () => {
    hubAPI.uninstall.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '卸载' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });
});
