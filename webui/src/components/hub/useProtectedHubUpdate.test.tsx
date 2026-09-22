import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useProtectedHubUpdate } from './useProtectedHubUpdate';

const { hubAPI, done, error } = vi.hoisted(() => ({
  hubAPI: { previewUpdate: vi.fn(), update: vi.fn() }, done: vi.fn(), error: vi.fn(),
}));
vi.mock('@/api/hub', () => ({ hubAPI }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'zh-CN' } }) }));
const changedPlan = {
  type: 'component', id: 'soc-workspace', scope: 'global', token: 'first', requiresConfirmation: true,
  items: [
    { type: 'workflow', id: 'triage', name: '研判工作流', requiresConfirmation: true, baselineKnown: true,
      changes: [{ path: 'package/workflow.json', kind: 'modified' }] },
    { type: 'webui', id: 'soc_ui', name: 'SOC 页面', requiresConfirmation: true, baselineKnown: false, changes: [] },
  ],
};
function Harness() {
  const { update, dialog } = useProtectedHubUpdate();
  return <><button onClick={() => void update('component', 'soc-workspace').then(done).catch(error)}>更新</button>{dialog}</>;
}

describe('protected Hub updates', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    hubAPI.previewUpdate.mockResolvedValue({ data: changedPlan });
    hubAPI.update.mockResolvedValue({ data: { backupPath: '/data/hub/backups/snapshot' } });
  });

  it('updates clean components without opening a dialog', async () => {
    hubAPI.previewUpdate.mockResolvedValue({ data: { ...changedPlan, requiresConfirmation: false, items: [] } });
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(true));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(hubAPI.update).toHaveBeenCalledWith('component', 'soc-workspace', 'global', { confirmationToken: 'first', confirmChanges: false });
  });

  it('lists all affected components and cancellation leaves the whole suite unchanged', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('研判工作流')).toBeInTheDocument();
    expect(screen.getByText(/修改: package\/workflow.json/)).toBeInTheDocument();
    expect(screen.getByText('SOC 页面')).toBeInTheDocument();
    expect(screen.getByText('缺少原始版本基准，无法确认本地是否修改。')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(done).toHaveBeenCalledWith(false);
    expect(hubAPI.update).not.toHaveBeenCalled();
  });

  it('requires explicit confirmation and shows the persistent backup location', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await screen.findByRole('dialog');
    expect(hubAPI.update).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '备份并覆盖更新' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(true));
    expect(hubAPI.update).toHaveBeenCalledWith('component', 'soc-workspace', 'global', { confirmationToken: 'first', confirmChanges: true });
    expect(screen.getByRole('status')).toHaveTextContent('/data/hub/backups/snapshot');
  });

  it('requests confirmation again when files change after preview', async () => {
    hubAPI.update.mockRejectedValueOnce({ response: { status: 409, data: { detail: {
      code: 'hub_update_confirmation_required', plan: { ...changedPlan, token: 'second' },
    } } } });
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await userEvent.click(await screen.findByRole('button', { name: '备份并覆盖更新' }));
    await screen.findByRole('dialog');
    expect(hubAPI.update).toHaveBeenCalledTimes(1);
    expect(done).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: '备份并覆盖更新' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(true));
    expect(hubAPI.update).toHaveBeenLastCalledWith('component', 'soc-workspace', 'global', { confirmationToken: 'second', confirmChanges: true });
  });

  it('leaving the page cancels pending confirmation', async () => {
    const view = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await screen.findByRole('dialog');
    view.unmount();
    await waitFor(() => expect(done).toHaveBeenCalledWith(false));
    expect(hubAPI.update).not.toHaveBeenCalled();
  });

  it('leaving during preview does not start an update', async () => {
    let resolve!: (value: unknown) => void;
    hubAPI.previewUpdate.mockReturnValue(new Promise(r => { resolve = r; }));
    const view = render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    view.unmount();
    await act(async () => resolve({ data: { ...changedPlan, requiresConfirmation: false } }));
    expect(done).toHaveBeenCalledWith(false);
    expect(hubAPI.update).not.toHaveBeenCalled();
  });

  it('Escape cancels and Tab stays within the confirmation dialog', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: '更新' }));
    await screen.findByRole('dialog');
    expect(screen.getByRole('button', { name: '取消' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole('button', { name: '备份并覆盖更新' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: '取消' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(done).toHaveBeenCalledWith(false);
    expect(hubAPI.update).not.toHaveBeenCalled();
  });

  it('surfaces backup failures without claiming success', async () => {
    hubAPI.update.mockRejectedValue(new Error('Backup failed; update cancelled'));
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '更新' }));
    await userEvent.click(await screen.findByRole('button', { name: '备份并覆盖更新' }));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(done).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
