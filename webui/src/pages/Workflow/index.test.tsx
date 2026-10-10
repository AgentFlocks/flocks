import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { WorkflowSummary } from '@/api/workflow';
import WorkflowPage from './index';

const { mockNavigate, mockUseWorkflows, mockLanguage, nativeWorkflowAPI, toastWarning } = vi.hoisted(() => ({
  toastWarning: vi.fn(),
  nativeWorkflowAPI: { update: vi.fn(), listSummaries: vi.fn() },
  mockNavigate: vi.fn(),
  mockUseWorkflows: vi.fn(),
  mockLanguage: { current: 'zh-CN' },
}));


beforeEach(() => {
  window.localStorage.clear();
});

vi.mock('@/api/workflow', () => ({ workflowAPI: nativeWorkflowAPI }));
vi.mock('@/components/common/Toast', async (importOriginal) => ({
  // Keep the module's other exports (GroupNav uses useOptionalToast).
  ...(await importOriginal<typeof import('@/components/common/Toast')>()), useToast: () => ({ warning: toastWarning }) }));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        pageTitle: '工作流',
        pageDescription: '管理和执行工作流',
        createWorkflow: '创建工作流',
        'section.custom': '自定义工作流',
        'section.builtin': '内置工作流',
        'stats.nodes': '节点',
        'integration.aria': '发布与触发状态',
        'integration.api': 'API',
        'integration.trigger': 'Trigger',
        'integration.moreStatuses': '更多状态',
        'integration.triggerType.syslog': 'Syslog',
        'integration.triggerType.kafka': 'Kafka',
        'integration.triggerType.schedule': 'Schedule',
        'integration.triggerType.webhook': 'Webhook',
        'integration.state.enabled': '启用',
        'integration.state.disabled': '关闭',
        'integration.detailState.unconfigured': '未配置',
        'integration.detailState.error': '异常',
        noDescription: '无描述',
      };
      return translations[key] ?? key;
    },
    i18n: { language: mockLanguage.current },
  }),
}));

vi.mock('@/hooks/useWorkflow', () => ({
  useWorkflows: () => mockUseWorkflows(),
}));

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

function makeWorkflow(overrides: Partial<WorkflowSummary> = {}): WorkflowSummary {
  return {
    id: 'wf-1',
    name: '默认工作流',
    category: 'default',
    status: 'draft' as const,
    source: 'project' as const,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    nodeCount: 1,
    stats: {
      callCount: 0,
      successCount: 0,
      errorCount: 0,
      totalRuntime: 0,
      avgRuntime: 0,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    ...overrides,
  };
}

describe('WorkflowPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLanguage.current = 'zh-CN';
    mockUseWorkflows.mockReturnValue({
      workflows: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it('keeps native cards/sections/actions and saves draft groups through the singular native route', async () => {
    const workflows = [makeWorkflow({ id: 'draft', name: 'Draft workflow', source: 'global', group: 'Ops' }), makeWorkflow({ id: 'builtin', name: 'Built-in workflow', source: 'project', group: 'Ops' })];
    const refetch = vi.fn().mockResolvedValue(undefined);
    mockUseWorkflows.mockReturnValue({ workflows, loading: false, error: null, refetch });
    nativeWorkflowAPI.listSummaries.mockResolvedValue({ data: workflows });
    nativeWorkflowAPI.update.mockResolvedValue({ data: {} });
    render(<WorkflowPage />);
    const original = screen.getByText('Draft workflow').closest('div.group')!;
    const text = original.textContent;
    fireEvent.click(screen.getByRole('button', { name: 'view.list' }));
    const list = screen.getByText('Draft workflow').closest('div.group')!;
    expect(list.textContent).toBe(text);
    expect(list).toHaveClass('sm:flex-row');
    fireEvent.click(list);
    expect(mockNavigate).toHaveBeenCalledWith('/workflows/draft');
    mockNavigate.mockClear();
    fireEvent.click(within(list as HTMLElement).getByRole('button', { name: 'editGroup' }));
    expect(mockNavigate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(nativeWorkflowAPI.update).toHaveBeenCalledExactlyOnceWith('draft', { group: null }));
    expect(refetch).toHaveBeenCalledWith({ silent: true, rejectOnError: true });
    expect(screen.getByRole('region', { name: '自定义工作流' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '内置工作流' })).toBeInTheDocument();
  });

  it('uses server ownership rather than project source for group permissions', async () => {
    const workflows = [
      makeWorkflow({ id: 'shipped', name: 'Shipped workflow', group: 'Mixed', group_readonly: true }),
      makeWorkflow({ id: 'project-custom', name: 'User project workflow', group: 'Mixed', group_readonly: false }),
    ];
    mockUseWorkflows.mockReturnValue({ workflows, loading: false, error: null, refetch: vi.fn() });
    nativeWorkflowAPI.listSummaries.mockResolvedValue({ data: workflows });
    render(<WorkflowPage />);
    const locked = screen.getByText('Shipped workflow').closest('div.group')!.parentElement!;
    const dataTransfer = { setData: vi.fn() };
    expect(fireEvent.dragStart(locked, { dataTransfer })).toBe(false);
    expect(dataTransfer.setData).not.toHaveBeenCalled();
    fireEvent.click(within(locked).getByRole('button', { name: 'editGroup' }));
    expect(toastWarning).toHaveBeenCalledTimes(2);
    expect(toastWarning).toHaveBeenLastCalledWith('pluginGroups:readOnly.system');
    expect(nativeWorkflowAPI.update).not.toHaveBeenCalled();
    expect(nativeWorkflowAPI.listSummaries).not.toHaveBeenCalled();
    fireEvent.keyDown(locked, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
    fireEvent.click(within(screen.getByText('User project workflow').closest('div.group') as HTMLElement).getByRole('button', { name: 'editGroup' }));
    expect(mockNavigate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(nativeWorkflowAPI.update).toHaveBeenCalledExactlyOnceWith('project-custom', { group: null }));
  });

  it.each(['create', 'rename', 'delete'])('prechecks fresh workflow ownership before %s', async (operation) => {
    const workflow = makeWorkflow({ id: 'changed', group: 'Ops', group_readonly: false });
    mockUseWorkflows.mockReturnValue({ workflows: [workflow], loading: false, error: null, refetch: vi.fn() });
    nativeWorkflowAPI.listSummaries.mockResolvedValue({ data: [{ ...workflow, group_readonly: true }] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<WorkflowPage />);
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : `${operation}Named` }));
    if (operation !== 'delete') {
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New' } });
      if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'changed' } });
      fireEvent.click(screen.getByRole('button', { name: 'save' }));
    }
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:errors.readOnlyMembers'));
    expect(nativeWorkflowAPI.update).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('renames the complete native summary scope, including workflows beyond page 12', async () => {
    const workflows = Array.from({ length: 15 }, (_, index) => makeWorkflow({ id: `wf-${index}`, name: `Workflow ${index}`, source: 'global', group: 'Ops' }));
    mockUseWorkflows.mockReturnValue({ workflows, loading: false, error: null, refetch: vi.fn().mockResolvedValue(undefined) });
    nativeWorkflowAPI.listSummaries.mockResolvedValue({ data: workflows });
    nativeWorkflowAPI.update.mockResolvedValue({ data: {} });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<WorkflowPage />);
    expect(screen.queryByText('Workflow 14')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Renamed' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(nativeWorkflowAPI.update).toHaveBeenCalledTimes(15));
    expect(nativeWorkflowAPI.listSummaries).toHaveBeenCalledOnce();
    expect(nativeWorkflowAPI.update).toHaveBeenLastCalledWith('wf-14', { group: 'Renamed' });
  });

  it.each(['create', 'rename'])('rejects a concurrently created group before %s writes', async (operation) => {
    const workflow = makeWorkflow({ id: 'draft', group: 'Ops' });
    mockUseWorkflows.mockReturnValue({ workflows: [workflow], loading: false, error: null, refetch: vi.fn() });
    nativeWorkflowAPI.listSummaries.mockResolvedValue({ data: [workflow, makeWorkflow({ id: 'new', group: 'Existing' })] });
    render(<WorkflowPage />);
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Existing' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'draft' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:validation.duplicate'));
    expect(nativeWorkflowAPI.update).not.toHaveBeenCalled();
  });

  it('handles a rejected manual refresh without reporting a successful reload', async () => {
    const refetch = vi.fn().mockRejectedValue(new Error('offline'));
    mockUseWorkflows.mockReturnValue({ workflows: [makeWorkflow()], loading: false, error: 'offline', refetch });
    render(<WorkflowPage />);
    fireEvent.click(screen.getByTitle('common:button.refresh'));
    await waitFor(() => expect(screen.getByTitle('common:button.refresh')).not.toBeDisabled());
    expect(screen.getByRole('alert')).toHaveTextContent('offline');
    expect(screen.queryByTitle('common:button.refreshed')).not.toBeInTheDocument();
    expect(refetch).toHaveBeenCalledWith({ silent: true, rejectOnError: true });
  });

  it('按 source 将工作流分到自定义和内置分组', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [
        makeWorkflow({ id: 'wf-global', name: 'Global Workflow', source: 'global' }),
        makeWorkflow({ id: 'wf-project', name: 'Project Workflow', source: 'project' }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const customRegion = screen.getByRole('region', { name: '自定义工作流' });
    const builtinRegion = screen.getByRole('region', { name: '内置工作流' });

    expect(within(customRegion).getByText('Global Workflow')).toBeInTheDocument();
    expect(within(customRegion).queryByText('Project Workflow')).not.toBeInTheDocument();
    expect(within(builtinRegion).getByText('Project Workflow')).toBeInTheDocument();
    expect(within(builtinRegion).queryByText('Global Workflow')).not.toBeInTheDocument();
  });

  it('按当前语言展示本地化工作流名称', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [
        makeWorkflow({
          id: 'wf-localized',
          name: 'localized_workflow',
          source: 'global',
          nameI18n: {
            'zh-CN': '中文工作流',
            'en-US': 'English Workflow',
          },
        }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    expect(screen.getByText('中文工作流')).toBeInTheDocument();
    expect(screen.queryByText('localized_workflow')).not.toBeInTheDocument();
  });

  it('从创建入口进入时显式开启新建草稿', async () => {
    const user = userEvent.setup();
    mockUseWorkflows.mockReturnValue({
      workflows: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const createButtons = screen.getAllByRole('button', { name: /创建工作流/ });
    await user.click(createButtons[0]);
    await user.click(createButtons[1]);

    expect(mockNavigate).toHaveBeenCalledTimes(2);
    expect(mockNavigate).toHaveBeenNthCalledWith(
      1,
      '/workflows/new',
      expect.objectContaining({
        state: expect.objectContaining({
          freshCreate: true,
          ts: expect.any(Number),
        }),
      }),
    );
    expect(mockNavigate).toHaveBeenNthCalledWith(
      2,
      '/workflows/new',
      expect.objectContaining({
        state: expect.objectContaining({
          freshCreate: true,
          ts: expect.any(Number),
        }),
      }),
    );
  });

  it('只保留左侧业务分组一个筛选入口，分区计数跟随所选分组', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [
        makeWorkflow({ id: 'custom-a', name: 'Custom A', source: 'global' }),
        makeWorkflow({ id: 'custom-b', name: 'Custom B', source: 'global' }),
        makeWorkflow({ id: 'custom-c', name: 'Custom C', source: 'global', group: '安全研判' }),
        makeWorkflow({ id: 'builtin-a', name: 'Builtin A', source: 'project', group: '安全研判', group_readonly: true }),
        makeWorkflow({ id: 'builtin-b', name: 'Builtin B', source: 'project', group: '安全研判', group_readonly: true }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    const sectionCount = (name: string) =>
      within(screen.getByRole('region', { name })).getByRole('heading', { name }).nextElementSibling?.textContent;

    render(<WorkflowPage />);

    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(sectionCount('自定义工作流')).toBe('3');
    expect(sectionCount('内置工作流')).toBe('2');

    fireEvent.click(screen.getByTitle('ungrouped'));
    expect(sectionCount('自定义工作流')).toBe('2');
    expect(screen.queryByRole('region', { name: '内置工作流' })).not.toBeInTheDocument();
    expect(screen.queryByText('emptyState.title')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('安全研判'));
    expect(sectionCount('自定义工作流')).toBe('1');
    expect(within(screen.getByRole('region', { name: '自定义工作流' })).getByText('Custom C')).toBeInTheDocument();
    expect(sectionCount('内置工作流')).toBe('2');
    expect(within(screen.getByRole('region', { name: '内置工作流' })).getByText('Builtin A')).toBeInTheDocument();
  });

  it('从第 3 页切到只有 2 页的分组时回到第 1 页', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: Array.from({ length: 26 }, (_, index) =>
        makeWorkflow({ id: `wf-${index}`, name: `Workflow ${index}`, source: 'global', group: index < 14 ? 'Ops' : null })),
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);
    const custom = screen.getByRole('region', { name: '自定义工作流' });
    fireEvent.click(within(custom).getByRole('button', { name: '3' }));
    expect(within(custom).getByText('25–26 / 26')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Ops'));
    const opsCustom = screen.getByRole('region', { name: '自定义工作流' });
    expect(within(opsCustom).getByText(/ \/ 14$/)).toHaveTextContent('1–12 / 14');
  });

  it('所选分组下没有工作流时只显示一条空提示', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({ id: 'builtin-a', name: 'Builtin A', source: 'project', group: '安全研判', group_readonly: true })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);
    fireEvent.click(screen.getByTitle('ungrouped'));

    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    expect(screen.getAllByText('emptyState.title')).toHaveLength(1);
  });

  it('卡片列数跟随分区宽度，长名称换行显示而不是截成一个字', () => {
    const longName = '多源威胁情报关联分析与自动化处置编排工作流';
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({ id: 'wf-long', name: longName, source: 'global' })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const title = screen.getByText(longName);
    const grid = title.closest('div.group')!.parentElement!.parentElement!;
    expect(grid.className).toContain('grid-cols-[repeat(auto-fill,minmax(min(20rem,100%),1fr))]');
    expect(grid.className).not.toMatch(/\b(sm|md|lg|xl):grid-cols-/);
    expect(title).toHaveAttribute('title', longName);
    expect(title).toHaveClass('line-clamp-2', 'break-words');
    expect(title).not.toHaveClass('truncate');
    expect(screen.getByText('badge.custom')).toHaveClass('whitespace-nowrap');
  });

  it('没有自定义工作流时不渲染空分组', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({ id: 'wf-project-only', name: 'Project Only', source: 'project' })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    expect(screen.queryByRole('region', { name: '自定义工作流' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '内置工作流' })).toBeInTheDocument();
  });

  it('展示 API 与 Trigger 的聚合运行状态', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({
        integrationStatus: {
          api: { configured: true, state: 'running' },
          trigger: {
            configured: true,
            state: 'error',
            count: 2,
            items: [
              { id: 'syslog-default', type: 'syslog', state: 'running', rawState: 'listening' },
              { id: 'kafka-default', type: 'kafka', state: 'error', rawState: 'failed' },
            ],
          },
        },
      })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    expect(within(screen.getByLabelText('API：启用')).getByText('启用')).toHaveClass('text-green-600');
    expect(within(screen.getByLabelText('Syslog：启用')).getByText('启用')).toHaveClass('text-green-600');
    const kafkaStatus = screen.getByLabelText('Kafka：关闭（异常）');
    expect(within(kafkaStatus).getByText('关闭')).toHaveClass('text-red-600');
    expect(kafkaStatus).toHaveAttribute('title', 'Kafka：关闭（异常）');
    expect(screen.queryByText('草稿')).not.toBeInTheDocument();
  });

  it('未配置发布能力时使用灰色状态', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow()],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const apiStatus = screen.getByLabelText('API：关闭（未配置）');
    const triggerStatus = screen.getByLabelText('Trigger：关闭（未配置）');
    expect(within(apiStatus).getByText('关闭')).toHaveClass('text-gray-400');
    expect(within(triggerStatus).getByText('关闭')).toHaveClass('text-gray-400');
    expect(apiStatus).toHaveAttribute('title', 'API：关闭（未配置）');
  });

  it('Trigger 状态过多时保持单行并通过浮层展示其余状态', async () => {
    const user = userEvent.setup();
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({
        integrationStatus: {
          api: { configured: true, state: 'running' },
          trigger: {
            configured: true,
            state: 'error',
            count: 4,
            items: [
              { id: 'syslog-default', type: 'syslog', state: 'running' },
              { id: 'kafka-default', type: 'kafka', state: 'running' },
              { id: 'schedule-default', type: 'schedule', state: 'error' },
              { id: 'webhook-default', type: 'webhook', state: 'unconfigured' },
            ],
          },
        },
      })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const overflowStatus = screen.getByRole('button', { name: '更多状态：2' });
    expect(overflowStatus.parentElement).toHaveClass('h-5');
    expect(screen.queryByText('Schedule')).not.toBeInTheDocument();
    expect(screen.queryByText('Webhook')).not.toBeInTheDocument();

    await user.hover(overflowStatus);
    const hoverTooltip = screen.getByRole('tooltip');
    expect(within(hoverTooltip).getByText('Schedule')).toBeInTheDocument();
    expect(within(hoverTooltip).getByText('Webhook')).toBeInTheDocument();
    expect(within(hoverTooltip).queryByText('异常')).not.toBeInTheDocument();
    expect(within(hoverTooltip).queryByText('未配置')).not.toBeInTheDocument();
    expect(hoverTooltip).not.toHaveClass('-translate-y-full');
    expect(hoverTooltip).toHaveStyle({ maxHeight: '240px' });
    expect(hoverTooltip.querySelector('.overflow-y-auto')).toBeInTheDocument();

    await user.unhover(overflowStatus);
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());

    vi.spyOn(overflowStatus, 'getBoundingClientRect').mockReturnValue({
      x: 280,
      y: 700,
      width: 20,
      height: 16,
      top: 700,
      right: 300,
      bottom: 716,
      left: 280,
      toJSON: () => ({}),
    });
    await user.click(overflowStatus);
    expect(screen.getByRole('tooltip')).toHaveClass('-translate-y-full');
    expect(mockNavigate).not.toHaveBeenCalled();

    await user.unhover(overflowStatus);
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());
  });

  it('窄卡片放不下第二个 Trigger 时把它收进 +N 浮层，不再裁切', async () => {
    const user = userEvent.setup();
    // jsdom has no layout. Give the status line 200px and every element a
    // width of 10px per character, so after API (50) and Syslog (80) the Kafka
    // status (70) plus the "+N" button no longer fit.
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.getAttribute('aria-label') === '发布与触发状态' ? 200 : 0;
      },
    });
    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const width = (this.textContent || '').length * 10;
      return { x: 0, y: 0, width, height: 16, top: 0, left: 0, right: width, bottom: 16, toJSON: () => ({}) } as DOMRect;
    });
    try {
      mockUseWorkflows.mockReturnValue({
        workflows: [makeWorkflow({
          integrationStatus: {
            api: { configured: true, state: 'running' },
            trigger: {
              configured: true,
              state: 'running',
              count: 4,
              items: [
                { id: 'syslog-default', type: 'syslog', state: 'running' },
                { id: 'kafka-default', type: 'kafka', state: 'running' },
                { id: 'schedule-default', type: 'schedule', state: 'running' },
                { id: 'webhook-default', type: 'webhook', state: 'running' },
              ],
            },
          },
        })],
        loading: false,
        error: null,
        refetch: vi.fn(),
      });

      render(<WorkflowPage />);

      expect(screen.getByLabelText('Syslog：启用')).toBeInTheDocument();
      expect(screen.queryByLabelText('Kafka：启用')).not.toBeInTheDocument();
      const overflowStatus = screen.getByRole('button', { name: '更多状态：3' });
      await user.hover(overflowStatus);
      const tooltip = screen.getByRole('tooltip');
      expect(within(tooltip).getByText('Kafka')).toBeInTheDocument();
      expect(within(tooltip).getByText('Schedule')).toBeInTheDocument();
      expect(within(tooltip).getByText('Webhook')).toBeInTheDocument();
    } finally {
      rectSpy.mockRestore();
      delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
    }
  });

  it('Trigger 数量不变但标签变宽时重新测量', () => {
    // 230px line, 10px per character: Syslog + Kafka fit next to API and "+2";
    // the same slot becoming "Schedule" (wider) must push it into "+N".
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
      configurable: true,
      get(this: HTMLElement) {
        return this.getAttribute('aria-label') === '发布与触发状态' ? 230 : 0;
      },
    });
    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const width = (this.textContent || '').length * 10;
      return { x: 0, y: 0, width, height: 16, top: 0, left: 0, right: width, bottom: 16, toJSON: () => ({}) } as DOMRect;
    });
    const withSecondTrigger = (type: 'kafka' | 'schedule') => makeWorkflow({
      integrationStatus: {
        api: { configured: true, state: 'running' },
        trigger: {
          configured: true,
          state: 'running',
          count: 4,
          items: [
            { id: 'trigger-1', type: 'syslog', state: 'running' },
            { id: 'trigger-2', type, state: 'running' },
            { id: 'trigger-3', type: 'webhook', state: 'running' },
            { id: 'trigger-4', type: 'webhook', state: 'running' },
          ],
        },
      },
    });
    try {
      mockUseWorkflows.mockReturnValue({ workflows: [withSecondTrigger('kafka')], loading: false, error: null, refetch: vi.fn() });
      const view = render(<WorkflowPage />);
      expect(screen.getByRole('button', { name: '更多状态：2' })).toBeInTheDocument();

      mockUseWorkflows.mockReturnValue({ workflows: [withSecondTrigger('schedule')], loading: false, error: null, refetch: vi.fn() });
      view.rerender(<WorkflowPage />);

      expect(screen.getByRole('button', { name: '更多状态：3' })).toBeInTheDocument();
      expect(screen.queryByLabelText('Schedule：启用')).not.toBeInTheDocument();
    } finally {
      rectSpy.mockRestore();
      delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
    }
  });
});
