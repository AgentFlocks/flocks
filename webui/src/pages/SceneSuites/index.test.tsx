import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import SceneSuitesPage from './index';
import { __resetSceneSuiteUpdatesForTesting } from '@/hooks/useSceneSuiteUpdates';
import { SCENE_SUITES_CHANGED_EVENT } from '@/utils/sceneSuites';

const { hubAPI, webuiContractPagesAPI, flocksproUsersApi, toast } = vi.hoisted(() => ({
  hubAPI: {
    sceneSuites: vi.fn(),
    install: vi.fn(),
    installStream: vi.fn(),
    update: vi.fn(),
    previewUpdate: vi.fn(),
    uninstall: vi.fn(),
    setNativeSceneEnabled: vi.fn(),
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

function renderPage(route = '/scenes/suites') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <SceneSuitesPage />
    </MemoryRouter>,
  );
}

function NavigationProbe() {
  const location = useLocation();
  return <>
    <output data-testid="location">{location.pathname}{location.search}</output>
    <Link to="/agents">Leave manager</Link>
    <Link to="/scenes/suites?workspace=code_audit_ui&reason=pages-unavailable">Change target</Link>
  </>;
}

function renderNavigablePage(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <NavigationProbe />
      <Routes>
        <Route path="/scenes/suites" element={<SceneSuitesPage />} />
        <Route path="/agents" element={<div>Agent page</div>} />
      </Routes>
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
    vi.resetAllMocks();
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'preview-token', requiresConfirmation: false, items: [],
    } });
    __resetSceneSuiteUpdatesForTesting();
    hubAPI.sceneSuites.mockResolvedValue({ data: [SOC, CODE_AUDIT] });
    flocksproUsersApi.hasCapability.mockResolvedValue(false);
    hubAPI.install.mockResolvedValue({});
    hubAPI.installStream.mockResolvedValue(undefined);
    hubAPI.update.mockResolvedValue({});
    hubAPI.uninstall.mockResolvedValue({});
    webuiContractPagesAPI.setWorkspaceEnabled.mockResolvedValue({});
    hubAPI.setNativeSceneEnabled.mockResolvedValue({});
  });

  it('installs a native scene through Add scene and exposes its own entry', async () => {
    const monitor = { ...SOC, id: 'host-security-monitor', nameCn: '安全运营监测', workspaceId: 'host-security-monitor', workspaceKind: 'native', workspaceEntryRoute: '/suites/host-security-monitor/session' };
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...monitor, state: 'available', installedVersion: null, workspaceEnabled: null }] });
    hubAPI.installStream.mockImplementation(async () => {
      hubAPI.sceneSuites.mockResolvedValue({ data: [monitor] });
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '安装', exact: true }));
    await waitFor(() => expect(hubAPI.setNativeSceneEnabled).toHaveBeenCalledWith('host-security-monitor', true));
    expect(webuiContractPagesAPI.setWorkspaceEnabled).not.toHaveBeenCalled();
    expect(await screen.findByRole('link', { name: '进入场景' })).toHaveAttribute('href', monitor.workspaceEntryRoute);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('disables and re-enables the native scene without using a contract workspace', async () => {
    const monitor = { ...SOC, id: 'host-security-monitor', nameCn: '安全运营监测', workspaceId: 'host-security-monitor', workspaceKind: 'native', workspaceEntryRoute: '/suites/host-security-monitor/session' };
    hubAPI.sceneSuites.mockResolvedValue({ data: [monitor] });
    hubAPI.setNativeSceneEnabled.mockImplementation(async (_id, enabled) => {
      hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...monitor, workspaceEnabled: enabled }] });
    });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: '停用', exact: true }));
    expect(await screen.findByRole('button', { name: '启用', exact: true })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '进入场景' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '启用', exact: true }));
    expect(await screen.findByRole('link', { name: '进入场景' })).toBeInTheDocument();
    expect(hubAPI.setNativeSceneEnabled.mock.calls).toEqual([['host-security-monitor', false], ['host-security-monitor', true]]);
    expect(webuiContractPagesAPI.setWorkspaceEnabled).not.toHaveBeenCalled();
  });

  it.each(['updateAvailable', 'partial'] as const)('cancels a customized %s suite without updating or enabling its workspace', async (state) => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state }] });
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'custom', requiresConfirmation: true,
      items: [{ id: 'soc_ui', type: 'webui', name: 'SOC 页面', requiresConfirmation: true, baselineKnown: false, changes: [] }],
    } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: state === 'partial' ? '补全安装' : '更新' }));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(hubAPI.update).not.toHaveBeenCalled();
    expect(webuiContractPagesAPI.setWorkspaceEnabled).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: state === 'partial' ? '补全安装' : '更新' })).toBeEnabled();
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
    expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('code_audit_ui', true);
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

  it.each([
    ['停用', true, false, '已停用'],
    ['启用', false, true, '已启用'],
  ] as const)('confirms %s with a toast that actually says so', async (button, enabledBefore, enabledAfter, message) => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: enabledBefore }] });
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: button }));

    await waitFor(() => expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', enabledAfter));
    // The toast used to come out empty: the text key was derived from the action name.
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(message));
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

  it('updates a suite in one call so its pages come along, and leaves the scene enabled', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'updateAvailable' as const, workspaceEnabled: false }] });
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'preview-token', requiresConfirmation: true,
      items: [{ type: 'component', id: 'soc-workspace', name: 'SOC 套件', requiresConfirmation: true }],
    } });
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '更新' }));

    await screen.findByRole('dialog');
    expect(hubAPI.update).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '备份并覆盖更新' }));
    await waitFor(() => expect(hubAPI.update).toHaveBeenCalledWith('component', 'soc-workspace', 'global', { confirmationToken: 'preview-token', confirmChanges: true }));
    expect(hubAPI.update).toHaveBeenCalledTimes(1);
    // A scene left disabled by an earlier 停用 must not stay disabled after an
    // update the user just asked for.
    await waitFor(() => expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('已更新，场景已启用'));
  });

  it('tells apart an update that finished from an enable that failed after it', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'updateAvailable' as const, workspaceEnabled: false }] });
    webuiContractPagesAPI.setWorkspaceEnabled.mockRejectedValue(new Error('workspace state locked'));
    const user = userEvent.setup();
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    await user.click(within(soc).getByRole('button', { name: '更新' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('更新已完成，但启用失败。请点击“启用”重试。', 'workspace state locked'));
  });

  it('explains an update that only concerns the scene pages', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{
      ...SOC,
      state: 'updateAvailable' as const,
      workspaceVersion: '1.1.7',
      workspaceLatestVersion: '1.1.8',
    }] });
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    expect(within(soc).getByText('有更新')).toBeInTheDocument();
    // The suite version alone (1.0.0 → 1.0.0) would make the badge look wrong.
    expect(soc.textContent).toContain('已装 v1.0.0 · 最新 v1.0.0 · 场景页面 v1.1.7 → v1.1.8');
  });

  it.each([
    ['matches the catalog', '1.1.8', '1.1.8'],
    ['is newer than the catalog', '1.1.10', '1.1.9'],
  ])('does not mention the page package while it %s', async (_label, installed, latest) => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceVersion: installed, workspaceLatestVersion: latest }] });
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    expect(soc.textContent).not.toContain('场景页面');
  });

  it('compares page package versions numerically, not as strings', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{
      ...SOC, state: 'updateAvailable' as const, workspaceVersion: '1.1.9', workspaceLatestVersion: '1.1.10',
    }] });
    renderPage();

    const soc = await waitFor(() => card('soc-workspace'));
    expect(soc.textContent).toContain('场景页面 v1.1.9 → v1.1.10');
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

  it('re-enables a reinstalled scene and refreshes navigation without waiting for SSE', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'available', workspaceEnabled: false, installedVersion: null }] });
    const changed = vi.fn();
    window.addEventListener(SCENE_SUITES_CHANGED_EVENT, changed);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '安装' }));

    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true);
    expect(toast.success).toHaveBeenCalledWith('安装完成，场景已启用');
    window.removeEventListener(SCENE_SUITES_CHANGED_EVENT, changed);
  });

  it('resolves a missing workspace mapping from the post-install suite list', async () => {
    hubAPI.sceneSuites
      .mockResolvedValueOnce({ data: [{ ...SOC, state: 'available', workspaceId: null, installedVersion: null }] })
      .mockResolvedValue({ data: [{ ...SOC, workspaceId: 'actual_new_workspace', workspaceEnabled: false }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '安装' }));

    await waitFor(() => expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('actual_new_workspace', true));
    expect(webuiContractPagesAPI.setWorkspaceEnabled).not.toHaveBeenCalledWith('soc-workspace', true);
  });

  it('keeps a successful install and offers Enable when auto-enabling fails', async () => {
    hubAPI.sceneSuites
      .mockResolvedValueOnce({ data: [{ ...SOC, state: 'available', workspaceEnabled: false, installedVersion: null }] })
      .mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: false }] });
    webuiContractPagesAPI.setWorkspaceEnabled.mockRejectedValue({ response: { data: { detail: '无权启用此工作区' } } });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '安装' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      '安装已完成，但启用失败。请点击“启用”重试。', '无权启用此工作区',
    ));
    expect(await screen.findByRole('button', { name: '启用' })).toBeEnabled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('shows a backend error detail for an unsuccessful uninstall', async () => {
    hubAPI.uninstall.mockRejectedValue({ response: { data: { detail: '场景正在运行，请稍后重试' } } });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '卸载' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      '操作失败: SOC 工作区场景套件', '场景正在运行，请稍后重试',
    ));
  });

  it('refreshes the list and navigation after uninstalling a scene', async () => {
    const changed = vi.fn();
    window.addEventListener(SCENE_SUITES_CHANGED_EVENT, changed);
    hubAPI.sceneSuites
      .mockResolvedValueOnce({ data: [SOC] })
      .mockResolvedValue({ data: [{ ...SOC, state: 'available', installedVersion: null }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '卸载' }));

    expect(await screen.findByRole('button', { name: '安装' })).toBeEnabled();
    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(SCENE_SUITES_CHANGED_EVENT, changed);
  });

  it('focuses the requested disabled scene and explains how to open it', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: false }] });
    renderPage('/scenes/suites?workspace=soc_ui');
    expect(await screen.findByText('此场景尚未启用，点击“启用”即可打开场景。')).toBeInTheDocument();
    await waitFor(() => expect(card('soc-workspace')).toHaveFocus());
    expect(screen.getByRole('button', { name: '启用' })).toBeEnabled();
  });

  it('explains why an enabled scene with unavailable pages opened its manager', async () => {
    renderPage('/scenes/suites?workspace=soc_ui&reason=pages-unavailable');
    expect(await screen.findByText('此场景已启用，页面尚未就绪。请刷新状态；如安装异常，请更新或重新安装场景。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
  });

  it('shows an existing workspace as incomplete and completes it without uninstalling', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'partial', installedVersion: null }] });
    const user = userEvent.setup();
    renderPage();
    const soc = await waitFor(() => card('soc-workspace'));
    expect(within(soc).getByText('待补全')).toBeInTheDocument();
    expect(within(soc).queryByText('未安装')).not.toBeInTheDocument();
    expect(within(soc).getByText('此场景已存在，部分套件组件缺失。点击“补全安装”恢复完整套件。')).toBeInTheDocument();
    expect(within(soc).getByRole('button', { name: '停用' })).toBeEnabled();
    hubAPI.sceneSuites.mockResolvedValue({ data: [SOC] });
    await user.click(within(soc).getByRole('button', { name: '补全安装' }));
    await waitFor(() => expect(hubAPI.installStream).toHaveBeenCalledWith('component', 'soc-workspace', expect.any(Function)));
    expect(hubAPI.uninstall).not.toHaveBeenCalled();
    expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true);
    expect(await within(card('soc-workspace')).findByText('已安装')).toBeInTheDocument();
  });

  it('offers completion instead of Enable when the partial suite has no workspace', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'partial', workspaceEnabled: null }] });
    renderPage('/scenes/suites?workspace=soc_ui&reason=pages-unavailable');
    const soc = await waitFor(() => card('soc-workspace'));
    expect(within(soc).getByRole('button', { name: '补全安装' })).toBeEnabled();
    expect(within(soc).queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
    expect(within(soc).queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
    expect(within(soc).getByText('套件安装不完整，点击“补全安装”恢复缺失的场景组件。')).toBeInTheDocument();
    expect(within(soc).queryByText(/此场景已启用/)).not.toBeInTheDocument();
  });

  it('keeps an incomplete disabled workspace available for enabling', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'partial', workspaceEnabled: false }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '启用' }));
    await waitFor(() => expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true));
    expect(screen.getByRole('button', { name: '补全安装' })).toBeEnabled();
  });

  it('keeps the incomplete state and error detail when completion fails', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'partial', installedVersion: null }] });
    hubAPI.installStream.mockRejectedValue(new Error('required workflow unavailable'));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: '补全安装' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      '操作失败: SOC 工作区场景套件', 'required workflow unavailable',
    ));
    expect(screen.getByText('待补全')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '补全安装' })).toBeEnabled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(webuiContractPagesAPI.setWorkspaceEnabled).not.toHaveBeenCalled();
  });

  it.each(['/scenes/suites', '/scenes/suites?workspace=soc_ui&reason=pages-unavailable'])(
    'does not navigate back after leaving an installation started at %s', async (route) => {
      let finishInstall: () => void = () => {};
      hubAPI.installStream.mockReturnValue(new Promise<void>((resolve) => { finishInstall = resolve; }));
      hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'available', installedVersion: null }] });
      const user = userEvent.setup();
      renderNavigablePage(route);
      await user.click(await screen.findByRole('button', { name: '安装' }));
      await user.click(screen.getByRole('link', { name: 'Leave manager' }));
      expect(screen.getByTestId('location')).toHaveTextContent('/agents');

      await act(async () => finishInstall());

      expect(webuiContractPagesAPI.setWorkspaceEnabled).toHaveBeenCalledWith('soc_ui', true);
      expect(screen.getByTestId('location')).toHaveTextContent('/agents');
      expect(screen.getByText('Agent page')).toBeInTheDocument();
    },
  );

  it('does not navigate back when enabling finishes after leaving the manager', async () => {
    let finishEnable: () => void = () => {};
    webuiContractPagesAPI.setWorkspaceEnabled.mockReturnValue(new Promise<void>((resolve) => { finishEnable = resolve; }));
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: false }] });
    const user = userEvent.setup();
    renderNavigablePage('/scenes/suites?workspace=soc_ui&reason=pages-unavailable');
    await user.click(await screen.findByRole('button', { name: '启用' }));
    await user.click(screen.getByRole('link', { name: 'Leave manager' }));
    await act(async () => finishEnable());
    expect(screen.getByTestId('location')).toHaveTextContent('/agents');
  });

  it('clears the resolved reason for the current scene while preserving other parameters', async () => {
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, workspaceEnabled: false }] });
    const user = userEvent.setup();
    renderNavigablePage('/scenes/suites?workspace=soc_ui&reason=pages-unavailable&keep=yes');
    await user.click(await screen.findByRole('button', { name: '启用' }));
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/scenes/suites?workspace=soc_ui&keep=yes'));
  });

  it('preserves a newer target selected while another scene is installing', async () => {
    let finishInstall: () => void = () => {};
    hubAPI.installStream.mockReturnValue(new Promise<void>((resolve) => { finishInstall = resolve; }));
    hubAPI.sceneSuites.mockResolvedValue({ data: [{ ...SOC, state: 'available', installedVersion: null }] });
    const user = userEvent.setup();
    renderNavigablePage('/scenes/suites?workspace=soc_ui&reason=pages-unavailable');
    await user.click(await screen.findByRole('button', { name: '安装' }));
    await user.click(screen.getByRole('link', { name: 'Change target' }));
    await act(async () => finishInstall());
    expect(screen.getByTestId('location')).toHaveTextContent('/scenes/suites?workspace=code_audit_ui&reason=pages-unavailable');
  });
});
