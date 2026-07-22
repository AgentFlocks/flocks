import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { WorkflowSummary } from '@/api/workflow';
import WorkflowPage from './index';

const { mockNavigate, mockUseWorkflows, mockLanguage } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockUseWorkflows: vi.fn(),
  mockLanguage: { current: 'zh-CN' },
}));

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
});
