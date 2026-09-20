import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SkillPage from './index';
import zhSkill from '@/locales/zh-CN/skill.json';

const { statusMock, listMock, refreshMock, updateGroupMock, getMock, toggleMock, installDepsMock, toastErrorMock, toastSuccessMock, tMock } = vi.hoisted(() => ({
  updateGroupMock: vi.fn(), getMock: vi.fn(), toggleMock: vi.fn(), installDepsMock: vi.fn(),
  statusMock: vi.fn(),
  listMock: vi.fn(),
  refreshMock: vi.fn(),
  toastErrorMock: vi.fn(),
  toastSuccessMock: vi.fn(),
  tMock: vi.fn((key: string) => key),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: toastErrorMock,
    success: toastSuccessMock,
  }),
}));

vi.mock('@/api/skill', async () => {
  const actual = await vi.importActual<typeof import('@/api/skill')>('@/api/skill');
  return {
    ...actual,
    skillAPI: {
      ...actual.skillAPI,
      status: statusMock,
      list: listMock,
      refresh: refreshMock,
      get: getMock,
      updateGroup: updateGroupMock,
      toggle: toggleMock,
      installDeps: installDepsMock,
      delete: vi.fn(),
    },
  };
});

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
      {action}
    </div>
  ),
}));

function makeSkill(name: string) {
  return {
    name,
    description: `${name} description`,
    location: `/tmp/${name}/SKILL.md`,
    source: 'user',
  };
}

function makeUiHiddenSkill(name: string) {
  return {
    ...makeSkill(name),
    ui_hidden: true,
  };
}

vi.mock('./SkillSheet', () => ({ default: () => <div data-testid="skill-sheet" /> }));

describe('SkillPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    const labels: Record<string, string> = { 'table.type': zhSkill.table.type, 'table.name': zhSkill.table.name,
      'table.source': zhSkill.table.source, 'table.enabled': zhSkill.table.enabled, 'table.actions': zhSkill.table.actions };
    tMock.mockImplementation((key: string) => labels[key] ?? key);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    updateGroupMock.mockResolvedValue({ data: {} });
    refreshMock.mockResolvedValue({ data: {} });
  });

  it.each(['project', 'flocks'])('blocks builtin %s group writes and mixed-group operations, preserving the default table', async (source) => {
    let inventory = [
      { ...makeSkill('builtin'), source, group: 'Mixed', group_readonly: false },
      { ...makeSkill('custom'), group: 'Mixed' },
      { ...makeUiHiddenSkill('invisible'), group: 'Hidden' },
    ];
    statusMock.mockImplementation(async () => ({ data: inventory }));
    listMock.mockImplementation(async () => ({ data: inventory }));
    updateGroupMock.mockImplementation(async (name, group) => {
      inventory = inventory.map((skill) => skill.name === name ? { ...skill, group } : skill);
      return { data: {} };
    });
    render(<SkillPage />);
    await screen.findByText('builtin');
    expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['类型', '名称', '来源', '启用', '操作']);
    expect(screen.queryByText('Hidden')).not.toBeInTheDocument();
    const builtin = screen.getByText('builtin').closest('tr')!;
    const custom = screen.getByText('custom').closest('tr')!;
    expect(builtin).toHaveAttribute('draggable', 'false');
    expect(builtin).toHaveAttribute('title', 'pluginGroups:readOnly.builtinSkill');
    expect(within(builtin).getByText('table.builtin')).toBeInTheDocument();
    expect(within(custom).getByText('table.custom')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
    fireEvent.keyDown(builtin, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.keyDown(custom, { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(updateGroupMock).toHaveBeenCalledExactlyOnceWith('custom', null));
    expect(refreshMock).not.toHaveBeenCalled();
    expect(screen.getAllByRole('switch')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'view.cards' }));
    expect(screen.getByText('builtin').closest('article')).toHaveAttribute('draggable', 'false');
    expect(screen.getByText('custom description')).toBeInTheDocument();
    expect(screen.getAllByRole('switch')).toHaveLength(2);
  });

  it('honors the server shipped flag without relying on a source label', async () => {
    statusMock.mockResolvedValue({ data: [{ ...makeSkill('shipped'), group: 'System', group_readonly: true }] });
    render(<SkillPage />);
    await screen.findByText('shipped');
    const row = screen.getByText('shipped').closest('tr')!;
    expect(row).toHaveAttribute('draggable', 'false');
    expect(row).toHaveAttribute('title', 'pluginGroups:readOnly.system');
    fireEvent.keyDown(row, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'create' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
  });

  it.each(['move', 'create'])('rechecks selected Skill ownership before %s', async (operation) => {
    const custom = { ...makeSkill('custom'), group: 'Ops', group_readonly: false };
    statusMock.mockResolvedValue({ data: [custom] });
    listMock.mockResolvedValue({ data: [{ ...custom, group_readonly: true }] });
    render(<SkillPage />);
    await screen.findByText('custom');
    if (operation === 'move') fireEvent.keyDown(screen.getByText('custom').closest('tr')!, { key: 'm', altKey: true });
    else {
      fireEvent.click(screen.getByRole('button', { name: 'create' }));
      fireEvent.change(screen.getByRole('textbox', { name: 'dialog.name' }), { target: { value: 'New' } });
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'custom' } });
    }
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:errors.readOnlyMembers'));
    expect(updateGroupMock).not.toHaveBeenCalled();
  });

  it('keeps the original list columns readable and horizontally scrollable on narrow screens', async () => {
    statusMock.mockResolvedValue({ data: [makeSkill('long-readable-skill-name')] });
    render(<SkillPage />);
    await screen.findByText('long-readable-skill-name');
    const table = screen.getByRole('table');
    expect(table).toHaveClass('min-w-[max(100%,40rem)]');
    expect(table.parentElement).toHaveClass('overflow-x-auto');
    expect(table.parentElement).not.toHaveClass('overflow-hidden');
    expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['类型', '名称', '来源', '启用', '操作']);
    expect(within(table).getByRole('button', { name: 'table.edit' })).toBeEnabled();
  });

  it.each(['create', 'rename'])('checks fresh skill group names before %s writes', async (operation) => {
    const skill = { ...makeSkill('custom'), group: 'Ops' };
    statusMock.mockResolvedValue({ data: [skill] });
    listMock.mockResolvedValue({ data: [skill, { ...makeSkill('new'), group: 'Existing' }] });
    render(<SkillPage />);
    await screen.findByText('custom');
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'dialog.name' }), { target: { value: 'Existing' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'custom' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:validation.duplicate'));
    expect(updateGroupMock).not.toHaveBeenCalled();
  });

  it.each(['rename', 'delete'])('prechecks newly discovered readonly group members before %s writes', async (operation) => {
    const custom = { ...makeSkill('custom'), group: 'Ops' };
    statusMock.mockResolvedValue({ data: [custom] });
    listMock.mockResolvedValue({ data: [custom, { ...makeSkill('builtin'), source: 'flocks', group: 'Ops' }] });
    render(<SkillPage />);
    await screen.findByText('custom');
    fireEvent.click(screen.getByRole('button', { name: `${operation}Named` }));
    if (operation === 'rename') {
      fireEvent.change(screen.getByRole('textbox', { name: 'dialog.name' }), { target: { value: 'Renamed' } });
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    }
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:errors.readOnlyMembers'));
    expect(updateGroupMock).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('retains click, enable and dependency-install actions in the alternate view', async () => {
    const skill = { ...makeSkill('needs-deps'), eligible: false, missing: ['binary'], install_specs: [{ kind: 'pip', package: 'demo' }] };
    statusMock.mockResolvedValue({ data: [skill] });
    getMock.mockResolvedValue({ data: skill });
    toggleMock.mockResolvedValue({ data: { disabled: true } });
    installDepsMock.mockResolvedValue({ data: { results: [{ success: true }] } });
    render(<SkillPage />);
    await screen.findByText('needs-deps');
    fireEvent.click(screen.getByRole('button', { name: 'view.cards' }));
    const card = screen.getByText('needs-deps').closest('article')!;
    fireEvent.click(within(card).getByRole('switch'));
    await waitFor(() => expect(toggleMock).toHaveBeenCalledWith('needs-deps'));
    fireEvent.click(within(card).getByRole('button', { name: 'eligibility.installDeps' }));
    await waitFor(() => expect(installDepsMock).toHaveBeenCalledWith('needs-deps'));
    fireEvent.click(within(card).getByRole('button', { name: 'table.edit' }));
    await waitFor(() => expect(getMock).toHaveBeenCalledWith('needs-deps'));
    expect(await screen.findByTestId('skill-sheet')).toBeInTheDocument();
  });

  it('loads the original full list before whole-group edits and reports exact partial failures', async () => {
    let inventory = [{ ...makeSkill('first'), group: 'Ops' as string | null }, { ...makeSkill('failed'), group: 'Ops' as string | null }];
    statusMock.mockImplementation(async () => ({ data: inventory }));
    listMock.mockImplementation(async () => ({ data: inventory }));
    updateGroupMock.mockImplementation(async (name, group) => {
      if (name === 'failed') throw new Error('write denied');
      inventory = inventory.map((skill) => skill.name === name ? { ...skill, group } : skill);
      return { data: {} };
    });
    render(<SkillPage />);
    await screen.findByText('first');
    fireEvent.click(screen.getByRole('button', { name: 'deleteNamed' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('failed: write denied'));
    expect(listMock).toHaveBeenCalledOnce();
    expect(updateGroupMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Ops 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ungrouped 1' })).toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it('refreshes the list when the window regains focus', async () => {
    statusMock
      .mockResolvedValueOnce({
        data: [makeSkill('skill-alpha')],
      })
      .mockResolvedValueOnce({
        data: [makeSkill('skill-alpha'), makeSkill('skill-beta')],
      });
    refreshMock.mockResolvedValue({ data: { status: 'success' } });

    render(<SkillPage />);

    await waitFor(() => {
      expect(screen.getByText('skill-alpha')).toBeInTheDocument();
    });

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(screen.getByText('skill-beta')).toBeInTheDocument();
    });

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(toastErrorMock).not.toHaveBeenCalled();
  });

  it('does not render UI-hidden internal skills', async () => {
    statusMock.mockResolvedValue({
      data: [makeSkill('visible-skill'), makeUiHiddenSkill('workflow-config-guide')],
    });

    render(<SkillPage />);

    await waitFor(() => {
      expect(screen.getByText('visible-skill')).toBeInTheDocument();
    });

    expect(screen.queryByText('workflow-config-guide')).not.toBeInTheDocument();
  });
});
