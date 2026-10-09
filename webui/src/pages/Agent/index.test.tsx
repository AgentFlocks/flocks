import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { __resetAgentsResourceForTesting } from '@/hooks/useAgents';
import PageDialog from '../PageDialog';
import AgentPage from './index';

const { listMock, refreshMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  refreshMock: vi.fn(),
}));

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

vi.mock('@/api/agent', () => ({
  agentAPI: {
    list: listMock,
    refresh: refreshMock,
  },
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <header><h1>{title}</h1><p>{description}</p></header>
  ),
}));

// Group writes report failures through toasts; these tests never trigger them.
vi.mock('@/components/common/Toast', () => ({ useToast: () => ({ warning: vi.fn(), error: vi.fn(), success: vi.fn() }) }));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, action }: { title: string; action?: ReactNode }) => <div>{title}{action}</div>,
}));

vi.mock('./AgentSheet', () => ({
  default: ({ agent, onClose, onSaved }: {
    agent?: unknown;
    onClose: () => void;
    onSaved: () => void;
  }) => (
    <PageDialog aria-label={agent ? '编辑 Agent' : '创建 Agent'} onClose={onClose}>
      <button onClick={onClose}>关闭面板</button>
      <button onClick={() => {
        // AgentSheet invokes both callbacks after a successful save.
        onSaved();
        onClose();
      }}>保存配置</button>
    </PageDialog>
  ),
}));

function makeAgent(overrides: Record<string, unknown>) {
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  __resetAgentsResourceForTesting();
  refreshMock.mockResolvedValue({ data: { count: 1 } });
});

describe('AgentPage cards', () => {
  it('使用与工作流卡片一致的纯色扁平样式', async () => {
    listMock.mockResolvedValue({
      data: [
        makeAgent({ name: 'rex', nameCn: 'Rex 主智能体', mode: 'primary', native: true, color: '#06b6d4' }),
        makeAgent({
          name: 'analyst',
          nameCn: '分析智能体',
          delegatable: true,
          color: '#ef4444',
          model: { providerID: 'provider', modelID: 'model-x' },
        }),
      ],
    });

    render(<AgentPage />);

    const nativeCard = (await screen.findByText('Rex 主智能体')).closest('div.group') as HTMLElement;
    const customCard = screen.getByText('分析智能体').closest('div.group') as HTMLElement;

    expect(nativeCard).toHaveClass('bg-surface', 'border-line');
    expect(customCard).toHaveClass('bg-surface', 'border-line');
    expect(nativeCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(customCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(nativeCard.querySelector('svg')?.parentElement).toHaveClass('bg-subtle');
    expect(customCard.querySelector('svg')?.parentElement).toHaveClass('bg-subtle');
    expect(within(nativeCard).getByText('内置')).not.toHaveClass('border');
    expect(within(customCard).getByText('自定义')).not.toHaveClass('border');
    expect(within(customCard).getByText('可委托')).not.toHaveClass('border');
    expect(within(customCard).getByText('model-x').parentElement).not.toHaveClass('rounded-full', 'border');
  });
});

describe('AgentPage drawer refresh', () => {
  it.each([
    { path: '创建后关闭', editing: false, action: '关闭面板', refreshCalls: 1 },
    { path: '创建后保存', editing: false, action: '保存配置', refreshCalls: 2 },
    { path: '编辑后保存', editing: true, action: '保存配置', refreshCalls: 0 },
  ])('$path 时后台刷新保留原触发按钮及焦点', async ({ editing, action, refreshCalls }) => {
    const user = userEvent.setup();
    const agent = makeAgent({ name: 'analyst', nameCn: '分析智能体' });
    const pendingRefresh = deferred<{ data: { count: number } }>();
    const pendingList = deferred<{ data: ReturnType<typeof makeAgent>[] }>();
    listMock.mockResolvedValueOnce({ data: [agent] });
    refreshMock.mockReturnValue(pendingRefresh.promise);

    render(<AgentPage />);
    const triggerName = editing ? '编辑' : '创建子 Agent';
    const trigger = await screen.findByRole('button', { name: triggerName });
    listMock.mockReturnValueOnce(pendingList.promise);

    await user.click(trigger);
    const dialog = screen.getByRole('dialog', { name: editing ? '编辑 Agent' : '创建 Agent' });
    expect(within(dialog).getByRole('button', { name: '关闭面板' })).toHaveFocus();
    await user.click(within(dialog).getByRole('button', { name: action }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(refreshMock).toHaveBeenCalledTimes(refreshCalls);
    if (!editing) {
      // Closing restores focus before the refresh API returns. A subsequent
      // loading render must not unmount that same trigger and lose it again.
      expect(trigger).toHaveFocus();
      expect(listMock).toHaveBeenCalledTimes(1);
      await act(async () => { pendingRefresh.resolve({ data: { count: 1 } }); });
    }

    await waitFor(() => { expect(listMock).toHaveBeenCalledTimes(2); });
    expect(trigger).toBeInTheDocument();
    expect(screen.queryByText('loading')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: triggerName })).toBe(trigger);
    expect(trigger).toHaveFocus();

    await act(async () => {
      pendingList.resolve({ data: [{ ...agent, description: '刷新后的 Agent 描述' }] });
    });
    expect(screen.getByText('刷新后的 Agent 描述')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: triggerName })).toBe(trigger);
    expect(trigger).toHaveFocus();
  });
});
