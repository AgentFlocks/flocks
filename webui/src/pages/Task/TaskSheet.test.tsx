import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    t: (key: string, options?: Record<string, unknown>) => {
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
        'form.workflowParamsLabel': 'Workflow 参数',
        'form.workflowParamsHint': '（JSON 对象，会作为 workflow inputs 传入）',
        'form.workflowParamsPlaceholder': '{\n  "keyword": "example"\n}',
        'form.workflowParamsInvalid': 'Workflow 参数必须是合法的 JSON 对象',
        'taskSheet.entityType': '任务',
        'taskSheet.createFailed': '创建失败',
        'taskSheet.saveFailed': '保存失败',
        'taskSheet.create.emptyStateTitle': '暂无创建对话',
        'taskSheet.create.guidePanelTitle': 'Rex 辅助创建任务',
        'taskSheet.create.guidePanelDesc': '选择一个引导或案例，Rex 会确认任务目标。',
        'taskSheet.create.guideSectionTitle': '创建引导',
        'taskSheet.create.caseSectionTitle': '创建案例',
        'taskSheet.edit.emptyStateTitle': '暂无编辑对话',
        'taskSheet.edit.guidePanelTitle': 'Rex 辅助修改任务',
        'taskSheet.edit.guidePanelDesc': '选择一个编辑入口，Rex 会基于当前任务配置。',
        'taskSheet.edit.guideSectionTitle': '编辑引导',
        'taskSheet.edit.caseSectionTitle': '编辑案例',
      };
      const objects: Record<string, unknown> = {
        'taskSheet.create.guideActions': [
          { label: '创建定时任务', description: '确认目标、执行对象和频率', prompt: '创建定时任务 prompt' },
        ],
        'taskSheet.create.caseActions': [
          { label: '每日安全日报', description: '每天输出日报', prompt: '每日安全日报 prompt' },
        ],
        'taskSheet.edit.guideActions': [
          { label: '优化当前任务', description: '诊断并调整字段', prompt: '优化当前任务 {{name}}' },
        ],
        'taskSheet.edit.caseActions': [
          { label: '降低频率', description: '减少执行压力', prompt: '降低频率 {{name}}' },
        ],
      };
      if (options?.returnObjects) {
        return objects[key] ?? [];
      }
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
    listSummaries: mocks.workflowList,
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
    onExtractFromRex,
    rexGuideGroups,
    rexGuidePanelTitle,
    rexGuidePanelDesc,
    rexGuideEmptyTitle,
    rexAgentName,
  }: {
    children: React.ReactNode;
    onSubmit: () => void | Promise<void>;
    submitDisabled?: boolean;
    submitLoading?: boolean;
    onExtractFromRex?: (sessionId: string) => Promise<void>;
    rexGuideGroups?: Array<{ title: string; actions: Array<{ label: string }> }>;
    rexGuidePanelTitle?: string;
    rexGuidePanelDesc?: string;
    rexGuideEmptyTitle?: string;
    rexAgentName?: string;
  }) => (
    <div
      data-testid="entity-sheet"
      data-guide-title={rexGuidePanelTitle ?? ''}
      data-guide-desc={rexGuidePanelDesc ?? ''}
      data-guide-empty-title={rexGuideEmptyTitle ?? ''}
      data-rex-agent-name={rexAgentName ?? ''}
      data-guide-group-count={String(rexGuideGroups?.length ?? 0)}
    >
      <button type="button" onClick={onSubmit} disabled={submitDisabled || submitLoading}>
        提交
      </button>
      <button type="button" onClick={() => void onExtractFromRex?.('rex-session')}>
        提取配置
      </button>
      {rexGuideGroups?.map((group) => (
        <section key={group.title}>
          <h2>{group.title}</h2>
          {group.actions.map((action) => (
            <span key={action.label}>{action.label}</span>
          ))}
        </section>
      ))}
      {children}
    </div>
  ),
  useEntitySheet: () => ({
    openRex: vi.fn(),
    openTest: vi.fn(),
  }),
}));

vi.mock('@/components/common/useRexComposerControls', () => ({
  useRexComposerControls: () => ({
    rexAgentName: 'rex',
    rexMentionAgents: [],
    rexModel: null,
    rexSupportsVision: false,
    rexContextWindowTokens: null,
    rexToolbarSlot: null,
    rexCenterToolbarSlot: null,
    rexComposerTextareaMinHeight: 48,
    rexComposerTextareaMaxHeight: 120,
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

  it('创建任务页接入与 Agent 创建页一致的 Rex 引导配置', () => {
    render(
      <TaskSheet
        defaultScheduleKind="recurring"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    const sheet = screen.getByTestId('entity-sheet');
    expect(sheet).toHaveAttribute('data-guide-title', 'Rex 辅助创建任务');
    expect(sheet).toHaveAttribute('data-guide-empty-title', '暂无创建对话');
    expect(sheet).toHaveAttribute('data-rex-agent-name', 'rex');
    expect(sheet).toHaveAttribute('data-guide-group-count', '2');
    expect(screen.getByText('创建引导')).toBeInTheDocument();
    expect(screen.getByText('创建定时任务')).toBeInTheDocument();
    expect(screen.getByText('创建案例')).toBeInTheDocument();
    expect(screen.getByText('每日安全日报')).toBeInTheDocument();
  });

  it('从 Rex 提取配置时保留 timezone 与 cronDescription', async () => {
    mocks.getMessages
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        {
          info: { role: 'assistant', finish: 'stop' },
          parts: [
            {
              type: 'text',
              text: '```json\n{"title":"东京巡检","scheduleKind":"recurring","cron":"0 1 * * *","timezone":"Asia/Tokyo","cronDescription":"每天东京时间 01:00","userPrompt":"检查重点资产"}\n```',
            },
          ],
        },
      ]);

    render(
      <TaskSheet
        defaultScheduleKind="recurring"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '提取配置' }));

    await waitFor(() => {
      expect(mocks.clientPost).toHaveBeenCalledWith(
        '/api/session/rex-session/prompt_async',
        expect.objectContaining({
          parts: [
            expect.objectContaining({
              text: expect.stringContaining('"timezone"'),
            }),
          ],
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByDisplayValue('东京巡检')).toBeInTheDocument();
    }, { timeout: 3000 });
    const timezoneSelect = screen
      .getAllByRole('combobox')
      .find((element) => Array
        .from((element as HTMLSelectElement).options)
        .some((option) => option.value === 'Asia/Tokyo')) as HTMLSelectElement | undefined;
    expect(timezoneSelect?.value).toBe('Asia/Tokyo');
    expect(screen.getByDisplayValue('每天东京时间 01:00')).toBeInTheDocument();
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

  it('创建 workflow 定时任务时提交 JSON 参数到 context', async () => {
    const user = userEvent.setup();

    render(
      <TaskSheet
        defaultScheduleKind="recurring"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Workflow' }));
    await user.type(screen.getByPlaceholderText('输入任务标题'), '每日工作流');
    await user.type(screen.getByPlaceholderText('0 9 * * 1-5'), '0 8 * * *');

    const paramsField = screen.getByDisplayValue('{}');
    fireEvent.change(paramsField, { target: { value: '{"keyword":"ioc","limit":10}' } });

    await user.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      expect(mocks.createScheduler).toHaveBeenCalledWith(
        expect.objectContaining({
          executionMode: 'workflow',
          context: { keyword: 'ioc', limit: 10 },
        }),
      );
    });
  });

  it('workflow 参数不是 JSON 对象时阻止提交', async () => {
    const user = userEvent.setup();

    render(
      <TaskSheet
        defaultScheduleKind="recurring"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Workflow' }));
    await user.type(screen.getByPlaceholderText('输入任务标题'), '错误参数');
    await user.type(screen.getByPlaceholderText('0 9 * * 1-5'), '0 8 * * *');

    const paramsField = screen.getByDisplayValue('{}');
    fireEvent.change(paramsField, { target: { value: '[]' } });

    await user.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => {
      expect(mocks.toastError).toHaveBeenCalledWith('创建失败', 'Workflow 参数必须是合法的 JSON 对象');
    });
    expect(mocks.createScheduler).not.toHaveBeenCalled();
  });
});
