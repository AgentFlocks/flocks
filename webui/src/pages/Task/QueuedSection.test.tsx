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
  batchCancelExecutions: vi.fn().mockResolvedValue({ data: { cancelled: 0 } }),
  batchDeleteExecutions: vi.fn().mockResolvedValue({ data: { deleted: 0 } }),
  getExecution: vi.fn(),
  markExecutionViewed: vi.fn(),
  cancelExecution: vi.fn(),
  retryExecution: vi.fn(),
  rerunExecution: vi.fn(),
  deleteExecution: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const count = typeof opts?.count === 'number' ? opts.count : 0;
      const translations: Record<string, string> = {
        'queued.filterAll': '全部',
        'queued.filterCompleted': '已完成',
        'queued.filterFailed': '失败',
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
    success: vi.fn(),
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
});
