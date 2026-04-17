import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { TaskExecution, TaskListParams } from '@/api/task';
import QueuedSection from './QueuedSection';

const mocks = vi.hoisted(() => ({
  useTaskExecutions: vi.fn(),
  refetch: vi.fn(),
  confirm: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  batchCancelExecutions: vi.fn().mockResolvedValue({ data: { cancelled: 0 } }),
  batchDeleteExecutions: vi.fn().mockResolvedValue({ data: { deleted: 0 } }),
  getExecution: vi.fn(),
  markExecutionViewed: vi.fn(),
  cancelExecution: vi.fn(),
  retryExecution: vi.fn(),
  rerunExecution: vi.fn(),
  deleteExecution: vi.fn(),
  copyText: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const count = typeof opts?.count === 'number' ? opts.count : 0;
      const translations: Record<string, string> = {
        'queued.filterAll': '全部',
        'queued.filterCompleted': '已完成',
        'queued.filterFailed': '失败',
        'queued.workflowId': 'Workflow ID',
        'queued.workflowInput': '输入参数',
        'queued.workflowResult': '执行结果',
        'queued.workflowError': '错误信息',
        'queued.workflowJsonFormat': 'JSON format',
        'queued.batchCancel': '批量取消',
        'queued.batchDelete': '批量删除',
        'queued.confirmBatchCancelBtn': '确认批量取消',
        'queued.emptyTitle': '暂无任务',
        'queued.emptyDescription': '暂无任务描述',
        'queued.colStatus': '状态',
        'queued.colSource': '来源',
        'queued.colName': '名称',
        'queued.colMode': '模式',
        'queued.colPriority': '优先级',
        'queued.colTime': '时间',
        'button.copy': '复制',
        'clipboard.copySuccessTitle': '已复制到剪贴板',
        'clipboard.copyFailedTitle': '复制失败',
        'clipboard.copyFailedDescription': '复制描述',
      };
      if (key === 'queued.selectedCount') {
        return `已选 ${count} 项`;
      }
      if (key === 'queued.pagination') {
        return `共 ${opts?.total ?? 0} 条，第 ${opts?.page ?? 1}/${opts?.totalPages ?? 1} 页`;
      }
      return translations[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    error: mocks.toastError,
    success: mocks.toastSuccess,
    info: vi.fn(),
    warning: vi.fn(),
    addToast: vi.fn(),
    removeToast: vi.fn(),
    toasts: [],
  }),
}));

vi.mock('@/components/common/ConfirmDialog', () => ({
  useConfirm: () => mocks.confirm,
}));

vi.mock('@/hooks/useTasks', () => ({
  useTaskExecutions: (params?: TaskListParams) => mocks.useTaskExecutions(params),
}));

vi.mock('@/api/task', () => ({
  taskAPI: {
    batchCancelExecutions: mocks.batchCancelExecutions,
    batchDeleteExecutions: mocks.batchDeleteExecutions,
    getExecution: mocks.getExecution,
    markExecutionViewed: mocks.markExecutionViewed,
    cancelExecution: mocks.cancelExecution,
    retryExecution: mocks.retryExecution,
    rerunExecution: mocks.rerunExecution,
    deleteExecution: mocks.deleteExecution,
  },
}));

vi.mock('@/utils/clipboard', () => ({
  copyText: (...args: unknown[]) => mocks.copyText(...args),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title }: { title: string }) => <div>{title}</div>,
}));

vi.mock('@/components/common/SessionChat', () => ({
  default: () => <div>session-chat</div>,
}));

vi.mock('./components', () => ({
  StatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
  PriorityBadge: ({ priority }: { priority: string }) => <span>{priority}</span>,
  SourceBadge: ({ sourceType }: { sourceType: string }) => <span>{sourceType}</span>,
  ModeBadge: ({ mode, agent }: { mode: string; agent: string }) => <span>{mode}:{agent}</span>,
  ActionButton: ({ label, onClick }: { label: string; onClick: () => void }) => (
    <button onClick={onClick}>{label}</button>
  ),
}));

vi.mock('./helpers', () => ({
  PAGE_SIZE: 20,
  formatTime: (value?: string) => value ?? '',
  formatDuration: (value?: number) => String(value ?? ''),
}));

function buildExecution(
  id: string,
  title: string,
  status: TaskExecution['status'] = 'queued',
  overrides: Partial<TaskExecution> = {},
): TaskExecution {
  return {
    id,
    schedulerID: `scheduler-${id}`,
    title,
    description: '',
    priority: 'normal',
    source: { sourceType: 'user_conversation' },
    triggerType: 'run_once',
    status,
    deliveryStatus: 'viewed',
    queuedAt: '2026-04-16T00:00:00Z',
    startedAt: undefined,
    completedAt: status === 'completed' ? '2026-04-16T00:10:00Z' : undefined,
    durationMs: undefined,
    sessionID: undefined,
    resultSummary: undefined,
    error: undefined,
    executionInputSnapshot: {},
    workspaceDirectory: undefined,
    retry: {
      maxRetries: 3,
      retryCount: 0,
      retryDelaySeconds: 60,
      retryAfter: undefined,
    },
    executionMode: 'agent',
    agentName: 'rex',
    workflowID: undefined,
    createdAt: '2026-04-16T00:00:00Z',
    updatedAt: '2026-04-16T00:00:00Z',
    ...overrides,
  };
}

describe('QueuedSection', () => {
  const allTasks = [
    buildExecution('exec-all-1', '全部任务 1'),
    buildExecution('exec-all-2', '全部任务 2'),
  ];
  const completedTasks = [
    buildExecution('exec-done-1', '完成任务 1', 'completed'),
    buildExecution('exec-done-2', '完成任务 2', 'completed'),
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.confirm.mockResolvedValue(true);
    mocks.useTaskExecutions.mockImplementation((params?: TaskListParams) => {
      const tasks = params?.status === 'completed' ? completedTasks : allTasks;
      return {
        tasks,
        total: tasks.length,
        loading: false,
        error: null,
        refetch: mocks.refetch,
      };
    });
  });

  it('切换筛选后会清除不可见列表的选中项', async () => {
    const user = userEvent.setup();

    render(<QueuedSection onRefreshGlobal={vi.fn()} />);

    const [, firstRowCheckbox] = screen.getAllByRole('checkbox');
    await user.click(firstRowCheckbox);

    expect(screen.getByText('已选 1 项')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量取消' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '已完成' }));

    await waitFor(() => {
      expect(screen.queryByText('已选 1 项')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '批量取消' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '批量删除' })).not.toBeInTheDocument();
    });
  });

  it('切换到其他筛选时不会错误保留表头全选状态', async () => {
    const user = userEvent.setup();

    render(<QueuedSection onRefreshGlobal={vi.fn()} />);

    const [headerCheckbox] = screen.getAllByRole('checkbox');
    await user.click(headerCheckbox);

    expect((headerCheckbox as HTMLInputElement).checked).toBe(true);

    await user.click(screen.getByRole('button', { name: '已完成' }));

    await waitFor(() => {
      const [nextHeaderCheckbox] = screen.getAllByRole('checkbox');
      expect((nextHeaderCheckbox as HTMLInputElement).checked).toBe(false);
    });
  });

  it('workflow 任务详情展示执行摘要而不是空会话', async () => {
    const user = userEvent.setup();
    const workflowTask = buildExecution(
      'exec-workflow-1',
      '工作流任务',
      'completed',
      {
        executionMode: 'workflow',
        workflowID: 'keyword-search-summary',
        resultSummary: 'workflow-output',
        executionInputSnapshot: {
          context: {
            keywords: 'agent ai',
          },
        },
      },
    );

    mocks.useTaskExecutions.mockReturnValue({
      tasks: [workflowTask],
      total: 1,
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getExecution.mockResolvedValue({ data: workflowTask });

    render(<QueuedSection onRefreshGlobal={vi.fn()} />);

    await user.click(screen.getByText('工作流任务'));

    expect(await screen.findByText('workflow-output')).toBeInTheDocument();
    expect(screen.getByText('keyword-search-summary')).toBeInTheDocument();
    expect(screen.getByText(/"keywords": "agent ai"/)).toBeInTheDocument();
    expect(screen.queryByText('session-chat')).not.toBeInTheDocument();
  });

  it('workflow 结果支持 JSON format 和复制', async () => {
    const user = userEvent.setup();
    const workflowTask = buildExecution(
      'exec-workflow-2',
      '工作流任务 2',
      'completed',
      {
        executionMode: 'workflow',
        workflowID: 'keyword-search-summary',
        resultSummary: "{'result': {'keywords': 'flocks agent', 'search_success': True, 'result_count': 8}}",
        executionInputSnapshot: {
          context: {
            keywords: 'flocks agent',
          },
        },
      },
    );

    mocks.useTaskExecutions.mockReturnValue({
      tasks: [workflowTask],
      total: 1,
      loading: false,
      error: null,
      refetch: mocks.refetch,
    });
    mocks.getExecution.mockResolvedValue({ data: workflowTask });

    render(<QueuedSection onRefreshGlobal={vi.fn()} />);

    await user.click(screen.getByText('工作流任务 2'));

    await user.click(screen.getByRole('button', { name: 'JSON format' }));

    expect(await screen.findByText(/"search_success": true/)).toBeInTheDocument();
    expect(screen.getByText(/"result_count": 8/)).toBeInTheDocument();

    const copyButtons = screen.getAllByRole('button', { name: '复制' });
    await user.click(copyButtons[1]);

    expect(mocks.copyText).toHaveBeenCalledWith(expect.stringContaining('"keywords": "flocks agent"'));
    expect(mocks.toastSuccess).toHaveBeenCalled();
  });
});
