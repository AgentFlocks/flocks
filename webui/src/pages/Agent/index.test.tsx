import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Agent } from '@/api/agent';
import AgentPage from './index';

const { mockUseAgents, nativeAPI, sheetSpy, toastWarning } = vi.hoisted(() => ({
  toastWarning: vi.fn(),
  mockUseAgents: vi.fn(),
  nativeAPI: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), setDelegatable: vi.fn(), refresh: vi.fn() },
  sheetSpy: vi.fn(),
}));

vi.mock('@/api/agent', () => ({ agentAPI: nativeAPI }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => ({ warning: toastWarning }) }));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  nativeAPI.update.mockResolvedValue({ data: {} });
  nativeAPI.delete.mockResolvedValue({ data: {} });
  nativeAPI.refresh.mockResolvedValue({ data: {} });
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      pageTitle: 'AI Agents',
      pageDescription: '管理和配置 AI Agent',
      totalCount: '共 2 个 Agent',
      'section.primary.title': '主 Agent',
      'section.primary.subtitle': '主 Agent 描述',
      'section.sub.title': '子 Agent',
      'section.sub.subtitle': '子 Agent 描述',
      'filter.all': '全部',
      'filter.builtin': '内置',
      'filter.custom': '自定义',
      'filter.aria': '按来源筛选',
      'badge.native': '内置',
      'badge.custom': '自定义',
      'badge.delegatable': '可委托',
      'badge.delete': '删除',
      'badge.edit': '编辑',
      'form.enabled': '启用',
      'form.enabledTip': '允许委派',
      'form.disabledTip': '禁止委派',
      createSubAgent: '创建子 Agent',
    }[key] ?? key),
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => mockUseAgents(),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <header><h1>{title}</h1><p>{description}</p></header>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, action }: { title: string; action?: ReactNode }) => <div>{title}{action}</div>,
}));

vi.mock('./AgentSheet', () => ({
  default: ({ agent, onClose }: { agent?: Agent; onClose: () => void }) => {
    sheetSpy(agent);
    return <div data-testid="agent-sheet"><button onClick={onClose}>Close sheet</button></div>;
  },
}));

function makeAgent(overrides: Partial<Agent>): Agent {
  return {
    name: 'agent',
    description: 'Agent description',
    mode: 'subagent',
    native: false,
    permission: [],
    options: {},
    skills: [],
    tools: [],
    ...overrides,
  };
}

function useInventory(agents: Agent[]) {
  nativeAPI.list.mockResolvedValue({ data: agents });
  const refetch = vi.fn().mockResolvedValue(agents);
  mockUseAgents.mockReturnValue({ agents, loading: false, error: null, refetch });
  return refetch;
}

describe('AgentPage business groups and native layouts', () => {
  it('filters before 12-item sub-agent pagination, keeps primary unpaginated and source chips unchanged', async () => {
    const primary = Array.from({ length: 13 }, (_, index) => makeAgent({ name: `primary-${index}`, mode: 'primary', native: true, group: 'Engineering' }));
    const sub = Array.from({ length: 26 }, (_, index) => makeAgent({ name: `sub-${index}`, native: index < 13, group: index % 2 === 0 ? 'Engineering' : null }));
    useInventory([...primary, ...sub, makeAgent({ name: 'system-agent', tags: ['system'], group: 'Engineering' })]);
    render(<AgentPage />);
    await screen.findByTitle('Engineering');
    expect(screen.getByText('primary-12')).toBeInTheDocument();
    expect(screen.queryByText('sub-12')).not.toBeInTheDocument();
    expect(screen.queryByText('system-agent')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    expect(screen.getByText('sub-12')).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Engineering'));
    expect(screen.getByText('sub-0')).toBeInTheDocument();
    expect(screen.getByText('sub-22')).toBeInTheDocument();
    expect(screen.queryByText('sub-24')).not.toBeInTheDocument();
    expect(screen.queryByText('sub-1')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '内置 13' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '自定义 13' })).toBeInTheDocument();
    expect(screen.getAllByText('Engineering')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    expect(screen.getByText('sub-24')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'view.list' }));
    expect(screen.getByText('sub-24')).toBeInTheDocument();
    expect(screen.queryByText('sub-0')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '自定义 13' }));
    expect(screen.getByText('sub-14')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^ungrouped/ }));
    expect(screen.getByRole('tab', { name: '自定义 13' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('sub-13')).toBeInTheDocument();
    expect(screen.queryByText('sub-0')).not.toBeInTheDocument();
  });

  it('keeps native/custom actions and all agent fields in list view without resetting an open editor', async () => {
    const native = makeAgent({ name: 'builtin', nameCn: '内置智能体', mode: 'primary', native: true, group: 'Engineering' });
    const custom = makeAgent({ name: 'analyst', nameCn: '分析智能体', descriptionCn: '原生描述', delegatable: true, model: { providerID: 'provider', modelID: 'model-x' }, group: 'Engineering' });
    const refetch = useInventory([native, custom]);
    nativeAPI.setDelegatable.mockResolvedValue({ data: { ...custom, delegatable: false } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AgentPage />);
    await screen.findByTitle('Engineering');
    fireEvent.click(screen.getByRole('button', { name: 'view.list' }));
    const nativeRow = screen.getByText('内置智能体').closest('div.group') as HTMLElement;
    const customRow = screen.getByText('分析智能体').closest('div.group') as HTMLElement;
    expect(nativeRow).toHaveClass('lg:flex-row');
    expect(within(nativeRow).getByRole('button', { name: '删除' })).toBeDisabled();
    expect(within(customRow).getByText('原生描述')).toBeInTheDocument();
    expect(within(customRow).getByText('model-x')).toBeInTheDocument();
    expect(within(customRow).getByText('自定义')).toBeInTheDocument();
    expect(within(customRow).getByText('可委托')).toBeInTheDocument();
    fireEvent.click(within(customRow).getByRole('switch'));
    await waitFor(() => expect(nativeAPI.setDelegatable).toHaveBeenCalledWith('analyst', false));
    fireEvent.click(within(customRow).getByRole('button', { name: '编辑' }));
    expect(sheetSpy).toHaveBeenLastCalledWith(expect.objectContaining({ name: 'analyst', model: custom.model }));
    expect(sheetSpy.mock.lastCall?.[0]).toHaveProperty('group', 'Engineering');
    fireEvent.click(screen.getByRole('button', { name: 'view.cards' }));
    expect(screen.getByTestId('agent-sheet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close sheet' }));
    fireEvent.click(within(screen.getByText('分析智能体').closest('div.group') as HTMLElement).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(nativeAPI.delete).toHaveBeenCalledWith('analyst'));
    expect(refetch).toHaveBeenCalled();
    expect(nativeAPI.update).not.toHaveBeenCalled();
  });

  it.each(['cards', 'list'])('offers an explicit group action for a user-defined primary in %s view', async (viewMode) => {
    const custom = makeAgent({ name: 'user-primary', mode: 'primary', native: true, group_readonly: false });
    const refetch = useInventory([custom, makeAgent({ name: 'other', group: 'Engineering' })]);
    render(<AgentPage />);
    if (viewMode === 'list') fireEvent.click(screen.getByRole('button', { name: 'view.list' }));
    const source = screen.getByText('user-primary').closest('div.group')?.parentElement as HTMLElement;
    fireEvent.click(within(source).getByRole('button', { name: 'editGroup' }));
    expect(screen.queryByTestId('agent-sheet')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Engineering' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(nativeAPI.update).toHaveBeenCalledWith('user-primary', { group: 'Engineering' }));
    expect(refetch).toHaveBeenCalledWith(false, true);
    expect(nativeAPI.refresh).not.toHaveBeenCalled();
    expect(nativeAPI.setDelegatable).not.toHaveBeenCalled();
  });

  it('locks actual shipped groups without changing primary/subagent actions or custom movability', async () => {
    useInventory([
      makeAgent({ name: 'rex', mode: 'primary', native: true, group: 'Mixed', group_readonly: true }),
      makeAgent({ name: 'core-sub', native: false, group: 'Mixed', group_readonly: true }),
      makeAgent({ name: 'user-agent', native: true, group: 'Mixed', group_readonly: false }),
    ]);
    render(<AgentPage />);
    const locked = screen.getByText('rex').closest('div.group')!.parentElement!;
    const dataTransfer = { setData: vi.fn() };
    expect(fireEvent.dragStart(locked, { dataTransfer })).toBe(false);
    expect(dataTransfer.setData).not.toHaveBeenCalled();
    expect(toastWarning).toHaveBeenLastCalledWith('pluginGroups:readOnly.system');
    expect(fireEvent.dragStart(screen.getByText('core-sub').closest('div.group')!.parentElement!, { dataTransfer })).toBe(false);
    fireEvent.click(within(locked).getByRole('button', { name: 'editGroup' }));
    expect(toastWarning).toHaveBeenCalledTimes(3);
    expect(nativeAPI.update).not.toHaveBeenCalled();
    expect(nativeAPI.list).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
    fireEvent.keyDown(locked, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'create' }));
    expect(within(screen.getByRole('combobox')).getAllByRole('option').map((option) => option.getAttribute('value'))).toEqual(['', 'user-agent']);
    fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
    fireEvent.keyDown(screen.getByText('user-agent').closest('div.group')!.parentElement!, { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(nativeAPI.update).toHaveBeenCalledExactlyOnceWith('user-agent', { group: null }));
  });

  it.each(['move', 'create', 'rename', 'delete'])('rechecks shipped ownership from fresh Agent rows before %s writes', async (operation) => {
    const agent = makeAgent({ name: 'changed', group: 'Ops', group_readonly: false });
    useInventory([agent]);
    nativeAPI.list.mockResolvedValue({ data: [{ ...agent, group_readonly: true }] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<AgentPage />);
    if (operation === 'move') fireEvent.keyDown(screen.getByText('changed').closest('div.group')!.parentElement!, { key: 'm', altKey: true });
    else fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : `${operation}Named` }));
    if (operation === 'create' || operation === 'rename') fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'changed' } });
    if (operation !== 'delete') fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:errors.readOnlyMembers'));
    expect(nativeAPI.update).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it.each(['create', 'rename'])('checks refreshed native names before %s instead of merging another group', async (operation) => {
    const agent = makeAgent({ name: 'analyst', group: 'Ops' });
    useInventory([agent]);
    nativeAPI.list.mockResolvedValue({ data: [agent, makeAgent({ name: 'new', group: 'Existing' })] });
    render(<AgentPage />);
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Existing' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'analyst' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:validation.duplicate'));
    expect(nativeAPI.update).not.toHaveBeenCalled();
  });

  it('keeps original agents visible when the native inventory reload fails after a move', async () => {
    const refetch = useInventory([makeAgent({ name: 'still-visible', group: 'Engineering' })]);
    refetch.mockRejectedValue(new Error('offline'));
    render(<AgentPage />);
    fireEvent.keyDown(screen.getByText('still-visible').closest('div.group')?.parentElement as HTMLElement, { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('offline'));
    expect(screen.getByText('still-visible')).toBeInTheDocument();
    expect(nativeAPI.update).toHaveBeenCalledWith('still-visible', { group: null });
    expect(nativeAPI.refresh).not.toHaveBeenCalled();
  });
});

describe('AgentPage cards', () => {
  it('使用与工作流卡片一致的纯色扁平样式', () => {
    mockUseAgents.mockReturnValue({
      agents: [
        makeAgent({ name: 'rex', nameCn: 'Rex 主智能体', mode: 'primary', native: true, color: '#06b6d4' }),
        makeAgent({
          name: 'analyst',
          nameCn: '分析智能体',
          delegatable: true,
          color: '#ef4444',
          model: { providerID: 'provider', modelID: 'model-x' },
        }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AgentPage />);

    const nativeCard = screen.getByText('Rex 主智能体').closest('div.group') as HTMLElement;
    const customCard = screen.getByText('分析智能体').closest('div.group') as HTMLElement;

    expect(nativeCard).toHaveClass('bg-white', 'border-gray-200');
    expect(customCard).toHaveClass('bg-white', 'border-gray-200');
    expect(nativeCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(customCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(nativeCard.querySelector('svg')?.parentElement).toHaveClass('bg-gray-100');
    expect(customCard.querySelector('svg')?.parentElement).toHaveClass('bg-gray-100');
    expect(within(nativeCard).getByText('内置')).not.toHaveClass('border');
    expect(within(customCard).getByText('自定义')).not.toHaveClass('border');
    expect(within(customCard).getByText('可委托')).not.toHaveClass('border');
    expect(within(customCard).getByText('model-x').parentElement).not.toHaveClass('rounded-full', 'border');
  });
});
