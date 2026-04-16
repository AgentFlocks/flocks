import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TaskSheet from './TaskSheet';

const mocks = vi.hoisted(() => ({
  createScheduler: vi.fn(),
  updateScheduler: vi.fn(),
  toastError: vi.fn(),
  agentList: vi.fn(),
  workflowList: vi.fn(),
  getMessages: vi.fn(),
  clientPost: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'form.titleLabel': '标题 *',
        'form.titlePlaceholder': '输入任务标题',
        'form.descLabel': '描述',
        'form.descPlaceholder': '任务描述（可选）',
        'form.priorityLabel': '优先级',
        'form.scheduleKindLabel': '调度',
        'form.modeLabel': '执行模式',
        'form.scheduleConfig': '调度配置',
        'form.freqRecurringLabel': '执行频率',
        'form.selectFrequency': '请选择频率',
        'form.customOption': '自定义…',
        'form.timezoneLabel': '时区',
        'form.cronDescLabel': '周期描述',
        'form.cronDescHint': '（展示用，留空则自动生成）',
        'form.cronDescPlaceholder': '例：每天早上9点',
        'form.additionalInfoLabel': '任务补充信息',
        'form.additionalInfoHint': '（Agent 执行时的具体指令，可选）',
        'form.additionalInfoPlaceholder': '例：查询 threatbook.cn 的威胁情报，生成详细报告',
        'form.immediateOption': '立即执行',
        'form.onceAtTimeOption': '指定时间',
        'form.recurringOption': '循环执行',
        'form.agentName': 'Agent 名称',
        'form.timezoneShanghai': 'Asia/Shanghai（北京时间 UTC+8）',
        'form.normalLabel': '普通',
        'form.selectWorkflow': '请选择 Workflow',
        'taskSheet.entityType': '任务',
        'taskSheet.createFailed': '创建失败',
        'taskSheet.saveFailed': '保存失败',
      };
      return translations[key] ?? key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/api/task', () => ({
  taskAPI: {
    createScheduler: mocks.createScheduler,
    updateScheduler: mocks.updateScheduler,
  },
}));

vi.mock('@/api/agent', () => ({
  agentAPI: {
    list: mocks.agentList,
  },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    list: mocks.workflowList,
  },
}));

vi.mock('@/api/session', () => ({
  sessionApi: {
    getMessages: mocks.getMessages,
  },
}));

vi.mock('@/api/client', () => ({
  default: {
    post: mocks.clientPost,
  },
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

vi.mock('@/components/common/EntitySheet', () => ({
  __esModule: true,
  default: ({
    children,
    onSubmit,
    submitDisabled,
    submitLoading,
  }: {
    children: React.ReactNode;
    onSubmit: () => void | Promise<void>;
    submitDisabled?: boolean;
    submitLoading?: boolean;
  }) => (
    <div>
      <button type="button" onClick={onSubmit} disabled={submitDisabled || submitLoading}>
        提交
      </button>
      {children}
    </div>
  ),
  useEntitySheet: () => ({
    openRex: vi.fn(),
    openTest: vi.fn(),
  }),
}));

vi.mock('@/components/common/PillGroup', () => ({
  __esModule: true,
  default: ({
    options,
    value,
    onChange,
  }: {
    options: Array<{ value: string; label: string }>;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <div>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

vi.mock('@/hooks/useTasks', () => ({
  useTaskExecutionsByScheduler: () => ({
    records: [],
    total: 0,
    loading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/utils/agentDisplay', () => ({
  getAgentDisplayDescription: () => '',
}));

vi.mock('./components', () => ({
  StatusBadge: ({ status }: { status: string }) => <span>{status}</span>,
}));

vi.mock('./helpers', () => ({
  CRON_PRESETS: [
    { key: 'daily0900', value: '0 9 * * *' },
    { key: 'custom', value: '__custom__' },
  ],
  describeCron: (cron: string) => `cron:${cron}`,
  formatDuration: (value?: number) => String(value ?? ''),
  formatTime: (value?: string) => value ?? '',
}));

describe('TaskSheet', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createScheduler.mockResolvedValue({ data: { id: 'task_1' } });
    mocks.updateScheduler.mockResolvedValue({ data: { id: 'task_1' } });
    mocks.agentList.mockImplementation(() => new Promise(() => {}));
    mocks.workflowList.mockImplementation(() => new Promise(() => {}));
    mocks.getMessages.mockResolvedValue([]);
    mocks.clientPost.mockResolvedValue({ data: {} });
  });

  it('创建循环任务时展示并提交 timezone 与 cronDescription', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSaved = vi.fn();

    render(
      <TaskSheet
        defaultScheduleKind="recurring"
        onClose={onClose}
        onSaved={onSaved}
      />,
    );

    const timezoneSelect = screen
      .getAllByRole('combobox')
      .find((element) => (element as HTMLSelectElement).value === 'Asia/Shanghai');
    expect(timezoneSelect).toBeDefined();
    expect(screen.getByPlaceholderText('例：每天早上9点')).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText('输入任务标题'), '每天同步情报');
    await user.type(screen.getByPlaceholderText('0 9 * * 1-5'), '0 8 * * *');
    await user.selectOptions(timezoneSelect as HTMLSelectElement, 'UTC');
    await user.type(screen.getByPlaceholderText('cron:0 8 * * *'), '每天 UTC 08:00');

    await user.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      expect(mocks.createScheduler).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '每天同步情报',
          type: 'scheduled',
          priority: 'normal',
          executionMode: 'agent',
          agentName: 'rex',
          cron: '0 8 * * *',
          timezone: 'UTC',
          cronDescription: '每天 UTC 08:00',
        }),
      );
    });

    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
