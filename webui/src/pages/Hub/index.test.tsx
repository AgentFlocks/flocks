import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import HubPage from './index';
import type { HubCatalogEntry } from '@/api/hub';

const { hubAPI, toastError } = vi.hoisted(() => ({
  hubAPI: {
    catalog: vi.fn(),
    catalogPage: vi.fn(),
    categories: vi.fn(),
    refresh: vi.fn(),
    install: vi.fn(),
    installStream: vi.fn(),
    update: vi.fn(),
    previewUpdate: vi.fn(),
    uninstall: vi.fn(),
    get: vi.fn(),
    files: vi.fn(),
    fileContent: vi.fn(),
  },
  toastError: vi.fn(),
}));

vi.mock('@/api/hub', () => ({ hubAPI }));
const reaApi = vi.hoisted(() => ({
  status: vi.fn(),
  reserveInstallSession: vi.fn(),
  recordInstallSession: vi.fn(),
  uninstallPreview: vi.fn(),
  uninstall: vi.fn(),
}));
vi.mock('@/api/rea', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/rea')>();
  return { ...actual, reaApi };
});
vi.mock('@/hooks/useDebouncedValue', () => ({
  useDebouncedValue: <T,>(value: T) => value,
}));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks' }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin-1', role: 'admin' } }),
}));
vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({ error: toastError }),
}));
vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en-US' },
  }),
}));

const emptyFacets = {
  type: {},
  category: {},
  tags: {},
  useCases: {},
  state: {},
  trust: {},
  riskLevel: {},
};

function catalogEntry(id: string, name: string, manifestPath = `${id}/manifest.json`) {
  return {
    id,
    type: 'tool' as const,
    name,
    description: `${name} description`,
    version: '1.0.0',
    category: 'security',
    tags: ['security'],
    useCases: ['investigation'],
    domains: [],
    capabilities: [],
    trust: 'verified',
    riskLevel: 'low',
    state: 'available' as const,
    source: 'bundled',
    manifestPath,
    native: false,
  };
}

function catalogPage(items: HubCatalogEntry[], total = items.length) {
  return {
    data: {
      items,
      total,
      offset: 0,
      limit: 25,
      facets: emptyFacets,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderHub() {
  return render(
    <MemoryRouter>
      <HubPage />
    </MemoryRouter>,
  );
}

describe('HubPage catalog loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'preview-token', requiresConfirmation: false, items: [],
    } });
    hubAPI.categories.mockResolvedValue({
      data: { categories: [], tags: [], useCases: [] },
    });
    hubAPI.catalog.mockResolvedValue({ data: [] });
    hubAPI.files.mockResolvedValue({
      data: { name: 'root', path: '', type: 'directory', size: 0, previewable: false, children: [] },
    });
  });

  it('prioritizes installed and updateable plugins in the default catalog request', async () => {
    hubAPI.catalogPage.mockResolvedValue(catalogPage([catalogEntry('available', 'Available')]));

    renderHub();

    await waitFor(() => {
      expect(hubAPI.catalogPage).toHaveBeenCalledWith(expect.objectContaining({
        prioritizeInstalled: true,
      }));
    });
  });

  // A plaza row shows its state twice (narrow-screen badge and the status column); jsdom renders both.
  it.each(['component', 'webui', 'workflow', 'tool'] as const)(
    'shows an updateable %s in the plaza and runs its update action', async (type) => {
      const user = userEvent.setup();
      const entry = { ...catalogEntry('soc-entry', 'SOC Entry'), type, state: 'updateAvailable' as const };
      const updated = { ...entry, state: 'installed' as const, installedVersion: entry.version };
      hubAPI.catalogPage.mockResolvedValueOnce(catalogPage([entry])).mockResolvedValue(catalogPage([updated]));
      hubAPI.catalog.mockResolvedValue({ data: [updated] });
      hubAPI.update.mockResolvedValue({ data: updated });
      hubAPI.previewUpdate.mockResolvedValue({ data: {
        scope: 'global', token: 'preview-token', requiresConfirmation: true,
        items: [{ type, id: 'soc-entry', name: 'SOC Entry', requiresConfirmation: true }],
      } });

      renderHub();
      expect(await screen.findByText('SOC Entry')).toBeInTheDocument();
      expect(screen.getAllByText('Update available').length).toBeGreaterThan(0);
      await user.click(screen.getByRole('button', { name: 'Update' }));
      await screen.findByRole('dialog');
      expect(hubAPI.update).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Back up and overwrite' }));
      await waitFor(() => expect(hubAPI.update).toHaveBeenCalledWith(type, 'soc-entry', 'global', { confirmationToken: 'preview-token', confirmChanges: true }));
      expect((await screen.findAllByText('Installed')).length).toBeGreaterThan(0);
      expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument();
    },
  );

  describe('REA entry', () => {
    const reaEntry = {
      ...catalogEntry('rea', 'REA Reverse Engineering'),
      type: 'skill' as const,
      guidedInstall: { kind: 'rea', title: 'REA 逆向分析' },
    };
    const reaStatus = (overrides: Record<string, unknown> = {}) => ({
      package: 'rea-agents', version: '6.4.0', state: 'not_installed', installed: false, tier: null,
      canInstall: true, badge: true, firstInstallCompleted: false, installSessionId: null, removable: false,
      degradedProblems: [], verification: null, environment: { ok: true, nodeRequirement: '', checks: [] },
      job: null, uninstallJob: null, ...overrides,
    });

    function SessionsProbe() {
      const location = useLocation();
      return <output data-testid="sessions-route">{JSON.stringify(location.state)}</output>;
    }

    function renderWithSessions() {
      hubAPI.catalogPage.mockResolvedValue(catalogPage([reaEntry]));
      return render(
        <MemoryRouter initialEntries={['/hub']}>
          <Routes>
            <Route path="/hub" element={<HubPage />} />
            <Route path="/sessions" element={<SessionsProbe />} />
          </Routes>
        </MemoryRouter>,
      );
    }

    beforeEach(async () => {
      const { __resetReaStateForTesting } = await import('@/features/rea/useRea');
      __resetReaStateForTesting();
      Object.values(reaApi).forEach((fn) => fn.mockReset());
    });

    it('opens a Rex conversation with the install guide filled in instead of copying files', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus());
      renderWithSessions();
      expect(await screen.findByTestId('rea-hub-state')).toHaveTextContent('Not installed');
      const install = await screen.findByRole('button', { name: 'Install' });
      expect(install).toHaveAttribute(
        'title',
        'Fills the REA install guide into the message box of a new Rex conversation, for you to review and send',
      );
      await user.click(install);
      expect(await screen.findByTestId('sessions-route')).toHaveTextContent(
        JSON.stringify({ skipLastSelectedSessionRestore: true, reaEntry: 'install' }),
      );
      expect(hubAPI.previewUpdate).not.toHaveBeenCalled();
      expect(hubAPI.install).not.toHaveBeenCalled();
    });

    it('offers 使用 and 卸载 once REA is ready', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({ state: 'ready', installed: true, badge: false }));
      renderWithSessions();
      expect(await screen.findByTestId('rea-hub-state')).toHaveTextContent('Ready');
      expect(await screen.findByRole('button', { name: 'Uninstall' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Use' })).toHaveAttribute(
        'title',
        'Fills the analysis prompt into the message box of a new Rex conversation; attach the files and send it',
      );
      await user.click(screen.getByRole('button', { name: 'Use' }));
      expect(await screen.findByTestId('sessions-route')).toHaveTextContent('"reaEntry":"use"');
    });

    it.each([
      ['install_failed', { removable: true }, ['Troubleshoot and repair', 'Uninstall installed parts', 'View details']],
      ['install_failed', { removable: false }, ['Troubleshoot and repair', 'View details']],
      ['degraded', {}, ['Troubleshoot and repair', 'Uninstall', 'View details']],
      ['installing', {}, ['View progress']],
      ['uninstalling', {}, ['View progress']],
      ['uninstall_failed', {}, ['Retry uninstall', 'View details']],
    ])('state %s offers its actions', async (state, overrides, names) => {
      reaApi.status.mockResolvedValue(reaStatus({ state, ...overrides }));
      renderWithSessions();
      const actions = await screen.findByTestId('rea-hub-actions');
      await waitFor(() => expect(actions).toHaveAttribute('data-rea-state', state));
      expect(Array.from(actions.querySelectorAll('button')).map((button) => button.getAttribute('aria-label'))).toEqual(names);
    });

    it('shows administrators the full diagnostics that conversations keep as <path>', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'install_failed',
        job: {
          id: 'j', status: 'failed', stage: 'components', progress: 30, tier: 'full', startedAt: 1, errorCode: 'blocked',
          errorDetail: 'ghidra: /srv/tools/ghidra exists',
          blockers: [{ code: 'ghidra_dir_occupied', path: '/srv/tools/ghidra', env: 'FLOCKS_REA_GHIDRA_DIR' }],
          components: { ghidra: { status: 'blocked', detail: '/srv/tools/ghidra' }, skill: { status: 'reused' } },
          validation: {},
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('Stage: Installing missing components');
      expect(diagnostics).toHaveTextContent('Cause: Needs your action');
      expect(diagnostics).toHaveTextContent(
        '/srv/tools/ghidra already exists and is not a complete Ghidra 12.1.4: move it away, or set FLOCKS_REA_GHIDRA_DIR to another directory, then retry.',
      );
      expect(diagnostics).toHaveTextContent('Ghidra 12.1.4: needs your action; /srv/tools/ghidra');
      expect(diagnostics).not.toHaveTextContent('skill reverse-engineer-anything');  // only what failed
    });

    it('names what a degraded install lacks, with its path, in the diagnostics', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'degraded',
        degradedProblems: [{ code: 'jdk_missing', path: '/srv/jdk-21' }, { code: 'ghidra_decompiler_missing', platform: 'linux_arm_64' }],
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('JDK 21 of the full tier missing (/srv/jdk-21)');
      expect(diagnostics).toHaveTextContent('Ghidra decompiler for this host missing (linux_arm_64)');
    });

    it('offers the diagnostics while REA works but its last install (an upgrade) did not pass', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'ready', installed: true, tier: 'light',
        job: {
          id: 'j', status: 'failed', stage: 'components', progress: 30, tier: 'full', startedAt: 1, errorCode: 'blocked',
          blockers: [{ code: 'ghidra_dir_occupied', path: '/srv/tools/ghidra', env: 'FLOCKS_REA_GHIDRA_DIR' }],
          components: {}, validation: {},
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      expect(await screen.findByTestId('rea-install-diagnostics')).toHaveTextContent('/srv/tools/ghidra already exists');
    });

    it('shows the recorded failed checks when the job is gone after a restart', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'install_failed', job: null,
        verification: { ok: false, tier: 'full', capabilities: {
          native: { status: 'failed', static: 'failed', runtime: 'incomplete', notes: ['provider_unavailable at /srv/ghidra'] },
          javascript: { status: 'passed', static: 'passed', runtime: 'passed', notes: [] },
        } },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('native decompilation: failed / incomplete; provider_unavailable at /srv/ghidra');
      expect(diagnostics).not.toHaveTextContent('No further diagnostics.');
      expect(diagnostics).not.toHaveTextContent('JavaScript');
    });

    it('offers what conversations leave out even when every check passed', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'ready', installed: true, tier: 'light', badge: false,
        verification: { ok: true, tier: 'light', capabilities: {
          javascript: {
            status: 'passed', static: 'passed', runtime: 'passed', actual: '22',
            notes: ['REA 定位 calc 于 /srv/app/index.js:2-4'], hiddenDetails: 1,
          },
          dotnet: { status: 'passed', static: 'passed', runtime: 'passed', notes: [], hiddenDetails: 0 },
        } },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('JavaScript: passed / passed; REA 定位 calc 于 /srv/app/index.js:2-4; Actual output: 22');
      expect(diagnostics).not.toHaveTextContent('.NET');
    });

    it('shows the output and what failed REA calls returned for a failed check', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'install_failed',
        job: {
          id: 'j', status: 'failed', stage: 'validate', progress: 90, tier: 'light', startedAt: 1, errorCode: 'validation_failed',
          blockers: [], components: {},
          validation: {
            javascript: {
              status: 'failed', static: 'failed', runtime: 'failed', notes: [],
              actual: "Error: Cannot find module '/srv/rea/index.js'", hiddenDetails: 2,
              calls: [
                { tool: 'rea_analyze_javascript_application', ok: false, summary: 'provider_unavailable at /srv/rea' },
                { tool: 'rea_close_binary', ok: true, summary: '{}' },
              ],
            },
          },
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent("Actual output: Error: Cannot find module '/srv/rea/index.js'");
      expect(diagnostics).toHaveTextContent('rea_analyze_javascript_application: provider_unavailable at /srv/rea');
      expect(diagnostics).not.toHaveTextContent('rea_close_binary');
    });

    it('keeps the recorded checks in view when an upgrade fails before validating', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'ready', installed: true, tier: 'light', badge: false,
        verification: { ok: true, tier: 'light', capabilities: {
          javascript: { status: 'passed', static: 'passed', runtime: 'passed', notes: ['REA 定位 calc 于 /srv/app/index.js:2-4'], hiddenDetails: 1 },
        } },
        job: {
          id: 'j', status: 'failed', stage: 'components', progress: 30, tier: 'full', startedAt: 1, errorCode: 'blocked',
          blockers: [{ code: 'ghidra_dir_occupied', path: '/srv/tools/ghidra', env: 'FLOCKS_REA_GHIDRA_DIR' }],
          components: {}, validation: {},
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      const diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('/srv/tools/ghidra already exists');
      expect(diagnostics).toHaveTextContent('Recorded checks');
      expect(diagnostics).toHaveTextContent('JavaScript: passed / passed; REA 定位 calc 于 /srv/app/index.js:2-4');
      expect(diagnostics).not.toHaveTextContent('Checks of this install attempt');
    });

    it('lists the failed attempt and the earlier record apart, and the same checks once', async () => {
      const user = userEvent.setup();
      const failedNative = {
        status: 'failed', static: 'failed', runtime: 'incomplete', notes: [], hiddenDetails: 1,
        calls: [{ tool: 'rea_procedure_pseudo_code', ok: false, summary: 'provider_unavailable at /srv/ghidra' }],
      };
      const recordedJs = { status: 'passed', static: 'passed', runtime: 'passed', notes: ['note at /srv/app'], hiddenDetails: 1 };
      // The attempt could not write its record: the earlier one is still what REA runs on.
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'ready', installed: true, tier: 'light', badge: false,
        verification: { ok: true, tier: 'light', capabilities: { javascript: recordedJs } },
        job: {
          id: 'j', status: 'failed', stage: 'validate', progress: 90, tier: 'full', startedAt: 1, errorCode: 'install_failed',
          blockers: [], components: {}, validation: { native: failedNative },
        },
      }));
      const { unmount } = renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      let diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).toHaveTextContent('Checks of this install attempt');
      expect(diagnostics).toHaveTextContent('native decompilation: failed / incomplete; rea_procedure_pseudo_code: provider_unavailable at /srv/ghidra');
      expect(diagnostics).toHaveTextContent('Recorded checks');
      expect(diagnostics).toHaveTextContent('JavaScript: passed / passed; note at /srv/app');
      unmount();

      // A failed validation writes its own record: shown once.
      const { __resetReaStateForTesting } = await import('@/features/rea/useRea');
      __resetReaStateForTesting();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'install_failed',
        verification: { ok: false, tier: 'full', capabilities: { native: failedNative } },
        job: {
          id: 'j', status: 'failed', stage: 'validate', progress: 90, tier: 'full', startedAt: 1, errorCode: 'validation_failed',
          blockers: [], components: {}, validation: { native: failedNative },
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      diagnostics = await screen.findByTestId('rea-install-diagnostics');
      expect(diagnostics).not.toHaveTextContent('Checks of this install attempt');
      expect(within(diagnostics).getAllByText(/rea_procedure_pseudo_code: provider_unavailable/)).toHaveLength(1);
    });

    it('offers members no diagnostics', async () => {
      reaApi.status.mockResolvedValue(reaStatus({ state: 'install_failed', canInstall: false }));
      renderWithSessions();
      const actions = await screen.findByTestId('rea-hub-actions');
      await waitFor(() => expect(actions).toHaveAttribute('data-rea-state', 'install_failed'));
      expect(screen.queryByRole('button', { name: 'View details' })).not.toBeInTheDocument();
    });

    it('says why REA needs repair under its state', async () => {
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'degraded',
        degradedProblems: [{ code: 'service_down', status: 'failed', error: 'boom' }, { code: 'skill_missing' }],
      }));
      renderWithSessions();
      expect(await screen.findByTestId('rea-hub-state-reason')).toHaveTextContent('MCP service rea not running; Skill missing');
      expect(screen.getByTestId('rea-hub-state')).toHaveTextContent('Needs repair');
    });

    it('names the failed step of an install that did not pass', async () => {
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'install_failed',
        job: { id: 'j', status: 'failed', stage: 'components', progress: 10, tier: 'light', startedAt: 1, errorCode: 'component_failed' },
      }));
      renderWithSessions();
      expect(await screen.findByTestId('rea-hub-state-reason')).toHaveTextContent('A component failed');
    });

    it('asks before stopping related tasks, then uninstalls and shows the result', async () => {
      const user = userEvent.setup();
      const current = { value: reaStatus({ state: 'ready', installed: true }) };
      reaApi.status.mockImplementation(() => Promise.resolve(current.value));
      reaApi.uninstallPreview.mockResolvedValue({
        state: 'ready',
        removes: ['MCP 服务 rea 的配置和运行中的服务', '技能 reverse-engineer-anything'],
        keeps: ['JDK 21', '/opt/ghidra_*', '/root/.npm/_npx'],
        tasks: [{ kind: 'session', id: 'ses_a', title: '分析样本 A' }],
      });
      reaApi.uninstall.mockImplementation(async () => {
        current.value = reaStatus({
          state: 'not_installed', badge: false,
          uninstallJob: {
            id: 'u1', status: 'succeeded', removed: ['MCP 服务 rea 的配置'], kept: ['/opt/ghidra_*'], residues: [],
            stoppedTasks: [{ kind: 'session', id: 'ses_a', title: '分析样本 A' }], startedAt: 1, finishedAt: 2,
          },
        });
        return current.value;
      });
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'Uninstall' }));
      const dialog = await screen.findByTestId('rea-hub-dialog');
      expect(await screen.findByTestId('rea-uninstall-tasks')).toHaveTextContent('分析样本 A');
      expect(dialog).toHaveTextContent('/root/.npm/_npx');
      await user.click(within(dialog).getByRole('button', { name: 'Stop the tasks and uninstall' }));
      expect(reaApi.uninstall).toHaveBeenCalledWith(true);
      expect(await screen.findByTestId('rea-uninstall-result')).toHaveAttribute('data-result', 'succeeded');
      expect(screen.getByTestId('rea-hub-dialog')).toHaveTextContent('Stopped tasks');
    });

    it('cancelling stops nothing and uninstalls nothing', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({ state: 'ready', installed: true }));
      reaApi.uninstallPreview.mockResolvedValue({ state: 'ready', removes: ['x'], keeps: ['y'], tasks: [{ kind: 'install', id: 'j', title: '' }] });
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'Uninstall' }));
      expect(await screen.findByTestId('rea-uninstall-tasks')).toHaveTextContent('REA install and validation');
      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByTestId('rea-hub-dialog')).not.toBeInTheDocument();
      expect(reaApi.uninstall).not.toHaveBeenCalled();
    });

    it('asks again when tasks started after the preview', async () => {
      const user = userEvent.setup();
      const { ReaApiError } = await import('@/api/rea');
      reaApi.status.mockResolvedValue(reaStatus({ state: 'ready', installed: true }));
      reaApi.uninstallPreview.mockResolvedValue({ state: 'ready', removes: ['x'], keeps: ['y'], tasks: [] });
      reaApi.uninstall.mockRejectedValueOnce(new ReaApiError('confirm', 'tasks_running', 409, {
        tasks: [{ kind: 'session', id: 'ses_b', title: '新任务' }],
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'Uninstall' }));
      const dialog = await screen.findByTestId('rea-hub-dialog');
      const confirm = within(dialog).getByRole('button', { name: 'Uninstall' });
      await waitFor(() => expect(confirm).toBeEnabled());
      await user.click(confirm);
      expect(reaApi.uninstall).toHaveBeenCalledWith(false);
      expect(await screen.findByTestId('rea-uninstall-tasks')).toHaveTextContent('新任务');
      expect(screen.getByRole('button', { name: 'Stop the tasks and uninstall' })).toBeEnabled();
    });

    it('retries in the same dialog after the uninstall failed in the background', async () => {
      const user = userEvent.setup();
      const failed = reaStatus({
        state: 'uninstall_failed',
        uninstallJob: {
          id: 'u1', status: 'failed', removed: [], kept: [], residues: ['技能 reverse-engineer-anything'],
          stoppedTasks: [], errorDetail: 'Permission denied', startedAt: 1, finishedAt: 2,
        },
      });
      const current = { value: reaStatus({ state: 'ready', installed: true }) };
      reaApi.status.mockImplementation(() => Promise.resolve(current.value));
      reaApi.uninstallPreview.mockResolvedValue({ state: 'ready', removes: ['x'], keeps: ['y'], tasks: [] });
      reaApi.uninstall.mockImplementation(async () => {
        current.value = failed;
        return current.value;
      });
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'Uninstall' }));
      const dialog = await screen.findByTestId('rea-hub-dialog');
      const confirm = within(dialog).getByRole('button', { name: 'Uninstall' });
      await waitFor(() => expect(confirm).toBeEnabled());
      await user.click(confirm);
      expect(await screen.findByTestId('rea-uninstall-result')).toHaveAttribute('data-result', 'failed');
      await user.click(within(screen.getByTestId('rea-hub-dialog')).getByRole('button', { name: 'Retry uninstall' }));
      const again = within(screen.getByTestId('rea-hub-dialog')).getByRole('button', { name: 'Uninstall' });
      await waitFor(() => expect(again).toBeEnabled());
      await user.click(again);
      await waitFor(() => expect(reaApi.uninstall).toHaveBeenCalledTimes(2));
    });

    it('shows what a failed uninstall left behind', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'uninstall_failed',
        uninstallJob: {
          id: 'u1', status: 'failed', removed: ['MCP 服务 rea 的配置'], kept: [], residues: ['技能 reverse-engineer-anything'],
          stoppedTasks: [], errorDetail: 'Permission denied', startedAt: 1, finishedAt: 2,
        },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View details' }));
      expect(await screen.findByTestId('rea-uninstall-result')).toHaveAttribute('data-result', 'failed');
      const dialog = screen.getByTestId('rea-hub-dialog');
      expect(dialog).toHaveTextContent('技能 reverse-engineer-anything');
      expect(dialog).toHaveTextContent('Permission denied');
      expect(within(dialog).getByRole('button', { name: 'Retry uninstall' })).toBeInTheDocument();
    });

    it('shows install progress with the stage', async () => {
      const user = userEvent.setup();
      reaApi.status.mockResolvedValue(reaStatus({
        state: 'installing',
        job: { id: 'j', status: 'running', stage: 'components', progress: 10, tier: 'full', startedAt: 1 },
      }));
      renderWithSessions();
      await user.click(await screen.findByRole('button', { name: 'View progress' }));
      expect(await screen.findByTestId('rea-hub-dialog')).toHaveTextContent('full tier · Installing missing components · 10%');
    });
  });

  it('lets the user cancel a customized component update from the plaza', async () => {
    const entry = { ...catalogEntry('soc_ui', 'SOC Pages'), type: 'webui' as const, state: 'updateAvailable' as const };
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'custom', requiresConfirmation: true,
      items: [{ id: 'soc_ui', type: 'webui', name: 'SOC Pages', requiresConfirmation: true, baselineKnown: true,
        changes: [{ path: 'package/page.tsx', kind: 'modified' }] }],
    } });
    renderHub();
    await userEvent.click(await screen.findByRole('button', { name: 'Update' }));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(hubAPI.update).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Update' })).toBeEnabled();
  });

  it('shows an incomplete suite and repairs it through backup confirmation', async () => {
    const entry = { ...catalogEntry('soc-workspace', 'SOC Suite'), type: 'component' as const, state: 'partial' as const };
    const repaired = { ...entry, state: 'installed' as const };
    hubAPI.catalogPage.mockResolvedValueOnce(catalogPage([entry])).mockResolvedValue(catalogPage([repaired]));
    hubAPI.catalog.mockResolvedValue({ data: [repaired] });
    hubAPI.update.mockResolvedValue({ data: repaired });
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'repair-token', requiresConfirmation: true,
      items: [{ type: 'component', id: entry.id, name: entry.name, requiresConfirmation: true }],
    } });

    renderHub();
    expect((await screen.findAllByText('Incomplete')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Uninstall' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Complete installation' }));
    await screen.findByRole('dialog');
    expect(hubAPI.previewUpdate).toHaveBeenCalledWith('component', entry.id);
    expect(hubAPI.install).not.toHaveBeenCalled();
    expect(hubAPI.installStream).not.toHaveBeenCalled();
    expect(hubAPI.update).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Back up and overwrite' }));
    await waitFor(() => expect(hubAPI.update).toHaveBeenCalledWith('component', entry.id, 'global', {
      confirmationToken: 'repair-token', confirmChanges: true,
    }));
    expect((await screen.findAllByText('Installed')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Complete installation' })).not.toBeInTheDocument();
  });

  it('cancels an incomplete suite repair without installing or updating', async () => {
    const entry = { ...catalogEntry('soc-workspace', 'SOC Suite'), type: 'component' as const, state: 'partial' as const };
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI.previewUpdate.mockResolvedValue({ data: {
      scope: 'global', token: 'repair-token', requiresConfirmation: true,
      items: [{ type: 'component', id: entry.id, name: entry.name, requiresConfirmation: true }],
    } });

    renderHub();
    await userEvent.click(await screen.findByRole('button', { name: 'Complete installation' }));
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(hubAPI.install).not.toHaveBeenCalled();
    expect(hubAPI.installStream).not.toHaveBeenCalled();
    expect(hubAPI.update).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Complete installation' })).toBeEnabled();
  });

  it('ignores an older search response that finishes after the latest query', async () => {
    const oldSearch = deferred<ReturnType<typeof catalogPage>>();
    const latestSearch = deferred<ReturnType<typeof catalogPage>>();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial', 'Initial result')]))
      .mockImplementationOnce(() => oldSearch.promise)
      .mockImplementationOnce(() => latestSearch.promise);

    renderHub();
    expect(await screen.findByText('Initial result')).toBeInTheDocument();

    const search = screen.getByPlaceholderText('Search plugin name, description, tag, use case');
    fireEvent.change(search, { target: { value: 'old' } });
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2));
    fireEvent.change(search, { target: { value: 'latest' } });
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(3));

    await act(async () => {
      latestSearch.resolve(catalogPage([catalogEntry('latest', 'Latest result')]));
    });
    expect(await screen.findByText('Latest result')).toBeInTheDocument();

    await act(async () => {
      oldSearch.resolve(catalogPage([catalogEntry('old', 'Old result')]));
    });

    expect(screen.getByText('Latest result')).toBeInTheDocument();
    expect(screen.queryByText('Old result')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ q: 'latest' }),
    );
  });

  it('loads the complete unpaged catalog for directory view', async () => {
    const user = userEvent.setup();
    const pagedEntry = catalogEntry('paged', 'Paged Entry', 'paged-tree/manifest.json');
    const beyondPage = catalogEntry('complete', 'Complete Entry', 'complete-tree/manifest.json');
    hubAPI.catalogPage.mockResolvedValue(catalogPage([pagedEntry], 854));
    hubAPI.catalog.mockResolvedValue({ data: [pagedEntry, beyondPage] });

    renderHub();
    expect(await screen.findByText('Paged Entry')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Directory View' }));

    expect(await screen.findByText('complete-tree')).toBeInTheDocument();
    expect(screen.getByText('paged-tree')).toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
    expect(hubAPI.catalogPage).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 0, limit: 25 }),
    );
    expect(hubAPI.catalog).toHaveBeenCalledWith({
      q: undefined,
      type: undefined,
      useCases: undefined,
      tags: undefined,
      state: undefined,
      prioritizeInstalled: true,
    });
  });

  it('refreshes the selected entity after an action even when it leaves the filtered page', async () => {
    const user = userEvent.setup();
    const available = catalogEntry('action-entry', 'Action Entry');
    const installed = { ...available, state: 'installed' as const, installedVersion: '1.0.0' };
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([available]))
      .mockResolvedValueOnce(catalogPage([]));
    hubAPI.catalog.mockResolvedValue({ data: [installed] });
    // The manifest endpoint intentionally has no dynamic catalog state.
    hubAPI.get.mockResolvedValueOnce({ data: available });
    hubAPI.install.mockResolvedValue({ data: installed });

    renderHub();
    await user.click(await screen.findByText('Action Entry'));
    await waitFor(() => expect(hubAPI.get).toHaveBeenCalledTimes(1));

    const installButtons = screen.getAllByRole('button', { name: 'Install' });
    await user.click(installButtons[installButtons.length - 1]);

    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'action-entry'));
    expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'action-entry', type: 'tool' });
    expect(hubAPI.get).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: 'Uninstall' })).toBeInTheDocument();
    expect(screen.getByText('Installed')).toBeInTheDocument();
  });

  it('does not let a table action started under an old query overwrite the latest query', async () => {
    const user = userEvent.setup();
    const actionEntry = catalogEntry('action-entry', 'Action Entry');
    const latestEntry = catalogEntry('latest-entry', 'Latest Entry');
    const installedEntry = { ...actionEntry, state: 'installed' as const, installedVersion: '1.0.0' };
    const install = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([actionEntry]))
    ));
    hubAPI.install.mockReturnValue(install.promise);
    hubAPI.catalog.mockResolvedValue({ data: [installedEntry] });

    renderHub();
    await user.click(await screen.findByRole('button', { name: 'Install' }));
    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'action-entry'));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      install.resolve();
      await install.promise;
    });

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'action-entry', type: 'tool' });
    });
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
    expect(screen.queryByText('Action Entry')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalogPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'latest' }));
  });

  it('does not let a refresh started under an old query overwrite the latest query', async () => {
    const user = userEvent.setup();
    const initialEntry = catalogEntry('initial-entry', 'Initial Entry');
    const latestEntry = catalogEntry('latest-entry', 'Latest Entry');
    const refresh = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([initialEntry]))
    ));
    hubAPI.refresh.mockReturnValue(refresh.promise);

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      refresh.resolve();
      await refresh.promise;
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
    expect(screen.queryByText('Initial Entry')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalogPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'latest' }));
  });

  it('shows a user-visible error when a manual refresh fails', async () => {
    const user = userEvent.setup();
    hubAPI.catalogPage.mockResolvedValue(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]));
    hubAPI.refresh.mockRejectedValue(new Error('refresh unavailable'));

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Hub refresh failed', 'refresh unavailable');
    });
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
  });

  it('distinguishes a completed manual refresh from a catalog reload failure', async () => {
    const user = userEvent.setup();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]))
      .mockRejectedValueOnce({
        response: { data: { detail: 'catalog temporarily unavailable' } },
        message: 'Request failed with status code 503',
      });
    hubAPI.refresh.mockResolvedValue({ data: { status: 'success' } });

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Hub refresh completed, but catalog reload failed',
        'catalog temporarily unavailable',
      );
    });
    expect(toastError).not.toHaveBeenCalledWith('Hub refresh failed', expect.anything());
    expect(hubAPI.refresh).toHaveBeenCalledTimes(1);
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
  });

  it('does not report a stale manual reload failure after the query changes', async () => {
    const user = userEvent.setup();
    const staleReload = deferred<ReturnType<typeof catalogPage>>();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]))
      .mockImplementationOnce(() => staleReload.promise)
      .mockResolvedValueOnce(catalogPage([catalogEntry('latest-entry', 'Latest Entry')]));
    hubAPI.refresh.mockResolvedValue({ data: { status: 'success' } });

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      staleReload.reject(new Error('obsolete catalog failure'));
      try {
        await staleReload.promise;
      } catch {
        // The component owns this rejected request.
      }
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    expect(toastError).not.toHaveBeenCalledWith(
      'Hub refresh completed, but catalog reload failed',
      expect.anything(),
    );
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
  });

  it.each([
    ['install', 'available', 'Install', 'Install failed'],
    ['update', 'updateAvailable', 'Update', 'Update failed'],
    ['uninstall', 'installed', 'Uninstall', 'Uninstall failed'],
  ] as const)('shows a user-visible error when %s fails', async (action, state, buttonLabel, errorTitle) => {
    const user = userEvent.setup();
    const entry = { ...catalogEntry('action-entry', 'Action Entry'), state };
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI[action].mockRejectedValue({
      response: { data: { detail: `${action} permission denied` } },
      message: `Request failed with status code 422`,
    });

    renderHub();
    await user.click(await screen.findByRole('button', { name: buttonLabel }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        `${errorTitle}: Action Entry`,
        `${action} permission denied`,
      );
    });
    expect(screen.getByRole('button', { name: buttonLabel })).toBeEnabled();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
  });

  it('does not report a completed action as failed when the follow-up reload fails', async () => {
    const user = userEvent.setup();
    const entry = catalogEntry('action-entry', 'Action Entry');
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI.install.mockResolvedValue({ data: { ...entry, state: 'installed' } });
    hubAPI.catalog.mockRejectedValue({
      response: { data: { detail: 'catalog temporarily unavailable' } },
      message: 'Request failed with status code 503',
    });

    renderHub();
    await user.click(await screen.findByRole('button', { name: 'Install' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Action completed, but Hub reload failed: Action Entry',
        'catalog temporarily unavailable',
      );
    });
    expect(toastError).not.toHaveBeenCalledWith(
      'Install failed: Action Entry',
      expect.anything(),
    );
    expect(hubAPI.install).toHaveBeenCalledTimes(1);
  });

  it('does not let a tree action started under old filters overwrite the latest tree', async () => {
    const user = userEvent.setup();
    const actionEntry = catalogEntry('tree-action', 'Tree Action');
    const latestEntry = catalogEntry('tree-latest', 'Tree Latest');
    const installedEntry = { ...actionEntry, state: 'installed' as const, installedVersion: '1.0.0' };
    const install = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([actionEntry]))
    ));
    hubAPI.catalog.mockImplementation(({ q, type }: { q?: string; type?: string }) => {
      if (q === 'tree-action' && type === 'tool') return Promise.resolve({ data: [installedEntry] });
      return Promise.resolve({ data: q === 'latest' ? [latestEntry] : [actionEntry] });
    });
    hubAPI.get.mockResolvedValue({ data: actionEntry });
    hubAPI.install.mockReturnValue(install.promise);

    renderHub();
    expect(await screen.findByText('Tree Action')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Directory View' }));
    await user.click(await screen.findByRole('button', { name: 'tree-action' }));
    await user.click(await screen.findByRole('button', { name: 'Install' }));
    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'tree-action'));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByRole('button', { name: 'tree-latest' })).toBeInTheDocument();

    await act(async () => {
      install.resolve();
      await install.promise;
    });

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'tree-action', type: 'tool' });
    });
    expect(screen.getByRole('button', { name: 'tree-latest' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'tree-action' })).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalog.mock.calls.filter(([params]) => params?.q === undefined)).toHaveLength(1);
  });

  it('leads with the localized label when a workflow plugin ships a broken workflow.json (TOL-1210)', async () => {
    const user = userEvent.setup();
    const wf = { ...catalogEntry('broken-wf', 'Broken WF'), type: 'workflow' as const };
    hubAPI.catalogPage.mockResolvedValue(catalogPage([wf]));
    hubAPI.get.mockResolvedValue({ data: wf });
    hubAPI.fileContent.mockResolvedValue({ data: { path: 'workflow.json', content: '{"nodes": [ ', language: 'json' } });

    renderHub();
    await user.click(await screen.findByText('Broken WF'));

    const error = await screen.findByText(/^Failed to parse workflow\.json: /);
    expect(error).toBeInTheDocument();
    expect(hubAPI.fileContent).toHaveBeenCalledWith('workflow', 'broken-wf', 'workflow.json');
  });
});
