import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RunTab from './RunTab';
import type { WorkflowExecution } from '@/api/workflow';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    getSampleInputs: vi.fn(),
    saveSampleInputs: vi.fn(),
    run: vi.fn(),
    getExecution: vi.fn(),
    cancelExecution: vi.fn(),
    getService: vi.fn(),
    publish: vi.fn(),
    unpublish: vi.fn(),
    getKafkaConfig: vi.fn(),
    saveKafkaConfig: vi.fn(),
    getSyslogConfig: vi.fn(),
    saveSyslogConfig: vi.fn(),
    getHistory: vi.fn(),
  },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI,
}));

vi.mock('@/components/common/CopyButton', () => ({
  default: ({ text }: { text: string }) => (
    <button type="button" data-testid="copy-button" aria-label={`copy:${text}`}>
      copy
    </button>
  ),
}));

vi.mock('@/components/common/WorkflowStatusBadge', () => ({
  default: ({ status }: { status: string }) => <span>{status}</span>,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'detail.run.testSection': '测试',
        'detail.run.inputParams': '输入参数（JSON）',
        'detail.run.rootObjectRequired': '输入参数必须是 JSON 对象',
        'detail.run.running': '运行中...',
        'detail.run.testRun': '测试运行',
        'detail.run.stopRun': '停止运行',
        'detail.run.stopping': '停止中...',
        'detail.run.cancelRequested': '已请求停止，正在等待当前步骤响应取消',
        'detail.run.outputResults': '输出结果',
        'detail.run.savingSampleInputs': '正在保存输入',
        'detail.run.sampleInputsSaved': '输入已保存',
        'detail.run.sampleInputsSaveFailed': '保存输入参数失败',
        'detail.run.executionLog': `执行日志 (${params?.count ?? 0})`,
        'detail.run.jsonFormatError': 'JSON 格式错误，请检查输入',
        'detail.run.runFailed': '运行失败',
        'detail.run.publishSection': '发布为 API',
        'detail.run.publishFailed': '发布失败',
        'detail.run.stopFailed': '停止失败',
        'detail.run.apiKeyShow': '显示',
        'detail.run.apiKeyHide': '隐藏',
        'detail.run.curlExample': '调用示例（curl）',
        'detail.run.stopService': '停止服务',
        'detail.run.publishDesc': 'desc',
        'detail.run.publishing': '发布中',
        'detail.run.publishAsApi': '发布为 API 服务',
        'detail.run.dockerStarting': 'docker',
        'detail.run.kafkaSection': 'Kafka 配置',
        'detail.run.kafkaExperimental': '实验性',
        'detail.run.inputConfig': '输入配置',
        'detail.run.outputConfig': '输出配置',
        'detail.run.savingConfig': '保存中',
        'detail.run.savedConfig': '已保存',
        'detail.run.saveConfig': '保存配置',
        'detail.run.kafkaHint': 'hint',
        'detail.run.syslogSection': 'Syslog',
        'detail.run.syslogExperimental': '实验性',
        'detail.run.syslogEnabled': '启用',
        'detail.run.syslogProtocol': '协议',
        'detail.run.syslogHost': '地址',
        'detail.run.syslogPort': '端口',
        'detail.run.syslogFormat': '格式',
        'detail.run.syslogInputKey': '键名',
        'detail.run.syslogHint': 'syslog hint',
        'detail.run.historySection': '执行历史',
        'detail.run.noHistory': '暂无执行记录',
        'detail.run.executionNotFound': '未找到指定执行记录',
        'detail.run.noOutput': '无输出数据',
        'detail.run.stepsCompleted': '步已完成',
        'detail.run.stepInputs': '输入',
        'detail.run.stepOutputs': '输出',
      };
      return translations[key] ?? key;
    },
  }),
}));

const baseWorkflow = {
  id: 'wf-1',
  name: 'Demo Workflow',
  category: 'default',
  workflowJson: { start: 'step1', nodes: [], edges: [] },
  status: 'draft' as const,
  createdAt: Date.now(),
  updatedAt: Date.now(),
  stats: {
    callCount: 0,
    successCount: 0,
    errorCount: 0,
    totalRuntime: 0,
    avgRuntime: 0,
    thumbsUp: 0,
    thumbsDown: 0,
  },
};

function ControlledRunTab({
  initialExecution = null,
  workflow = baseWorkflow,
  focusExecutionId,
}: {
  initialExecution?: WorkflowExecution | null;
  workflow?: typeof baseWorkflow;
  focusExecutionId?: string;
}) {
  const [latestExecution, setLatestExecution] = React.useState<WorkflowExecution | null>(initialExecution);

  return (
    <RunTab
      workflow={workflow}
      latestExecution={latestExecution}
      onLatestExecutionChange={setLatestExecution}
      focusExecutionId={focusExecutionId}
    />
  );
}

describe('RunTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.history.replaceState(null, '', '/workflows/wf-1');
    workflowAPI.getSampleInputs.mockResolvedValue({ data: { sampleInputs: {} } });
    workflowAPI.saveSampleInputs.mockResolvedValue({ data: { ok: true } });
    workflowAPI.getService.mockResolvedValue({ data: null });
    workflowAPI.getKafkaConfig.mockResolvedValue({ data: null });
    workflowAPI.getSyslogConfig.mockResolvedValue({ data: null });
    workflowAPI.getHistory.mockResolvedValue({ data: [] });
    workflowAPI.getExecution.mockResolvedValue({
      data: {
        id: 'exec-1',
        workflowId: 'wf-1',
        inputParams: {},
        status: 'success',
        startedAt: Date.now(),
        executionLog: [],
      },
    });
    workflowAPI.run.mockResolvedValue({
      data: {
        id: 'exec-1',
        workflowId: 'wf-1',
        inputParams: { topic: 'demo' },
        status: 'running',
        startedAt: Date.now(),
        executionLog: [],
      },
    });
    workflowAPI.cancelExecution.mockResolvedValue({
      data: {
        status: 'accepted',
        message: 'Cancellation requested',
        executionId: 'exec-1',
      },
    });
  });

  it('switches the main action to stop while a test run is active', async () => {
    const user = userEvent.setup();
    const runningExecution = {
      id: 'exec-1',
      workflowId: 'wf-1',
      inputParams: { topic: 'demo' },
      status: 'running' as const,
      startedAt: Date.now(),
      executionLog: [],
    };
    workflowAPI.getHistory.mockResolvedValue({ data: [runningExecution] });
    workflowAPI.getExecution.mockResolvedValue({
      data: {
        ...runningExecution,
        currentPhase: 'cancelling',
        errorMessage: 'Cancellation requested',
      },
    });

    render(
      <ControlledRunTab
        initialExecution={runningExecution}
      />
    );

    const stopButton = await screen.findByRole('button', { name: '停止运行' });
    await user.click(stopButton);

    await waitFor(() => {
      expect(workflowAPI.cancelExecution).toHaveBeenCalledWith('wf-1', 'exec-1');
    });

    expect(await screen.findByRole('button', { name: '停止中...' })).toBeDisabled();
    expect(screen.getByText('cancelling')).toBeInTheDocument();
    expect(screen.getByText('已请求停止，正在等待当前步骤响应取消')).toBeInTheDocument();
  });

  it('keeps the running execution visible when history is temporarily empty', async () => {
    const runningExecution = {
      id: 'exec-keep',
      workflowId: 'wf-1',
      inputParams: { topic: 'demo' },
      status: 'running' as const,
      startedAt: Date.now(),
      executionLog: [],
    };
    workflowAPI.getHistory.mockResolvedValue({ data: [] });

    render(<ControlledRunTab initialExecution={runningExecution} />);

    expect(await screen.findByRole('button', { name: '停止运行' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '测试运行' })).not.toBeInTheDocument();
  });

  it('saves sample inputs before running', async () => {
    const user = userEvent.setup();

    render(<ControlledRunTab />);

    const textarea = screen.getAllByRole('textbox')[0];
    fireEvent.change(textarea, { target: { value: '{"topic":"saved"}' } });
    await user.click(screen.getByRole('button', { name: '测试运行' }));

    await waitFor(() => {
      expect(workflowAPI.saveSampleInputs).toHaveBeenCalledWith('wf-1', { topic: 'saved' });
      expect(workflowAPI.run).toHaveBeenCalledWith('wf-1', { inputs: { topic: 'saved' } });
    });
  });

  it('shows a copy button when output results are available', async () => {
    render(
      <ControlledRunTab
        initialExecution={{
          id: 'exec-output',
          workflowId: 'wf-1',
          inputParams: { topic: 'demo' },
          outputResults: { result: 'ok' },
          status: 'success',
          startedAt: Date.now(),
          executionLog: [],
        }}
      />
    );

    await waitFor(() => {
      const copyButtons = screen.getAllByTestId('copy-button');
      expect(copyButtons.some((button) => button.getAttribute('aria-label') === 'copy:{\n  "result": "ok"\n}')).toBe(true);
    });
  });

  it('expands history execution details directly under the clicked item', async () => {
    const user = userEvent.setup();
    const executions = [
      {
        id: 'exec-first',
        workflowId: 'wf-1',
        inputParams: {},
        outputResults: { marker: 'first' },
        status: 'success' as const,
        startedAt: new Date('2026-01-01T00:00:00Z').getTime(),
        duration: 1,
        executionLog: [],
      },
      {
        id: 'exec-second',
        workflowId: 'wf-1',
        inputParams: {},
        outputResults: { marker: 'second' },
        status: 'success' as const,
        startedAt: new Date('2026-01-02T00:00:00Z').getTime(),
        duration: 2,
        executionLog: [],
      },
    ];
    workflowAPI.getHistory.mockResolvedValue({ data: executions });
    workflowAPI.getExecution.mockImplementation((workflowId: string, executionId: string) => (
      Promise.resolve({ data: executions.find((execution) => execution.id === executionId) })
    ));

    render(
      <RunTab
        workflow={baseWorkflow}
        latestExecution={null}
        sections={['history']}
      />,
    );

    const firstHistoryButton = (await screen.findByText('1.0s')).closest('button');
    const secondHistoryButton = (await screen.findByText('2.0s')).closest('button');
    expect(firstHistoryButton).not.toBeNull();
    expect(secondHistoryButton).not.toBeNull();

    await user.click(firstHistoryButton!);

    const firstDetail = screen.getByText(/"marker": "first"/);
    expect(firstDetail.compareDocumentPosition(secondHistoryButton!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('loads and expands a focused execution even when it is outside recent history', async () => {
    const focusedExecution = {
      id: 'exec-focused',
      workflowId: 'wf-1',
      inputParams: { alert_id: 'alert-focused' },
      outputResults: { marker: 'focused' },
      status: 'success' as const,
      startedAt: new Date('2026-01-03T00:00:00Z').getTime(),
      duration: 3,
      executionLog: [],
    };
    workflowAPI.getHistory.mockResolvedValue({ data: [] });
    workflowAPI.getExecution.mockResolvedValue({ data: focusedExecution });

    render(
      <RunTab
        workflow={baseWorkflow}
        latestExecution={null}
        sections={['history']}
        focusExecutionId="exec-focused"
      />,
    );

    expect(await screen.findByText('3.0s')).toBeInTheDocument();
    expect(screen.getByText(/"marker": "focused"/)).toBeInTheDocument();
    expect(workflowAPI.getExecution).toHaveBeenCalledWith('wf-1', 'exec-focused');
  });

  it('opens embedded history tab when an execution is focused', async () => {
    const focusedExecution = {
      id: 'exec-focused',
      workflowId: 'wf-1',
      inputParams: {},
      outputResults: { marker: 'embedded-focused' },
      status: 'success' as const,
      startedAt: Date.now(),
      duration: 1,
      executionLog: [],
    };
    workflowAPI.getHistory.mockResolvedValue({ data: [focusedExecution] });

    render(
      <RunTab
        workflow={baseWorkflow}
        latestExecution={null}
        sections={['test', 'history']}
        embeddedTabs
        focusExecutionId="exec-focused"
      />,
    );

    expect(await screen.findByText(/"marker": "embedded-focused"/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '执行历史' })[0]).toHaveClass('bg-white');
  });

  it('uses dashboard mock execution fallback with the same execution id as task-center data', async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, '', '/workflows/stream_alert_triage?tab=run&execId=mock-triage-run-002&mockDashboard=1');
    workflowAPI.getHistory.mockResolvedValue({ data: [] });
    workflowAPI.getExecution.mockRejectedValue(new Error('not found'));

    render(
      <RunTab
        workflow={{
          ...baseWorkflow,
          id: 'stream_alert_triage',
        }}
        latestExecution={null}
        sections={['history']}
        focusExecutionId="mock-triage-run-002"
      />,
    );

    expect(await screen.findByText(/"verdict": "疑似攻击行为"/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '执行日志 (2)' }));
    expect(screen.getByText('concurrent_triage')).toBeInTheDocument();
  });

  describe('a test run the server refused', () => {
    const reason = 'Workflow not found: wf-1';
    const earlierRun: WorkflowExecution = {
      id: 'exec-earlier',
      workflowId: 'wf-1',
      inputParams: {},
      outputResults: { marker: 'earlier-run' },
      status: 'success',
      startedAt: new Date('2026-01-01T00:00:00Z').getTime(),
      duration: 1,
      executionLog: [],
    };

    /**
     * Refuse the next test run and, from the moment it is sent, hold back the history responses:
     * the history reloads right after a refusal, and that reload is what used to wipe the reason.
     * The returned function answers the held requests and waits for the page to take the answer in.
     */
    function refuseNextRun() {
      let answer!: (history: WorkflowExecution[] | Error) => void;
      const held = new Promise<{ data: WorkflowExecution[] }>((resolve, reject) => {
        answer = (history) => (history instanceof Error ? reject(history) : resolve({ data: history }));
      });
      held.catch(() => undefined);
      workflowAPI.run.mockImplementationOnce(() => {
        workflowAPI.getHistory.mockReturnValue(held);
        return Promise.reject(new Error(reason));
      });
      return async (history: WorkflowExecution[] | Error) => {
        await waitFor(() => {
          expect(workflowAPI.getHistory.mock.results.some((result) => result.value === held)).toBe(true);
        });
        await act(async () => {
          answer(history);
        });
      };
    }

    it('keeps the reason on screen after the history reloads empty', async () => {
      const user = userEvent.setup();
      const answerHistory = refuseNextRun();

      render(<ControlledRunTab />);
      await screen.findByText('暂无执行记录');
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await answerHistory([]);
      expect(screen.getByText(reason)).toBeInTheDocument();
    });

    it('keeps the reason on screen instead of going back to an earlier run from the history', async () => {
      const user = userEvent.setup();
      workflowAPI.getHistory.mockResolvedValue({ data: [earlierRun] });
      const answerHistory = refuseNextRun();

      render(<ControlledRunTab />);
      expect(await screen.findByText(/"marker": "earlier-run"/)).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await answerHistory([earlierRun]);
      expect(screen.getByText(reason)).toBeInTheDocument();
      expect(screen.queryByText(/"marker": "earlier-run"/)).not.toBeInTheDocument();
    });

    it('keeps the reason on screen when the history cannot be loaded either', async () => {
      const user = userEvent.setup();
      workflowAPI.getHistory.mockResolvedValue({ data: [earlierRun] });
      const answerHistory = refuseNextRun();

      render(<ControlledRunTab />);
      expect(await screen.findByText(/"marker": "earlier-run"/)).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await answerHistory(new Error('Network Error'));
      expect(screen.getByText(reason)).toBeInTheDocument();
    });

    it('keeps the reason on screen when a history response asked for before the refusal arrives after it', async () => {
      const user = userEvent.setup();
      let answerEarlyRequest!: (response: { data: WorkflowExecution[] }) => void;
      workflowAPI.getHistory.mockReturnValueOnce(new Promise((resolve) => {
        answerEarlyRequest = resolve;
      }));
      workflowAPI.run.mockRejectedValue(new Error(reason));

      render(<ControlledRunTab />);
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await act(async () => {
        answerEarlyRequest({ data: [earlierRun] });
      });
      expect(screen.getByText(reason)).toBeInTheDocument();
      expect(screen.queryByText(/"marker": "earlier-run"/)).not.toBeInTheDocument();
    });

    it('keeps the reason when the history answers right behind the refusal, before the page has redrawn', async () => {
      const user = userEvent.setup();
      let answerEarlyRequest!: (response: { data: WorkflowExecution[] }) => void;
      workflowAPI.getHistory.mockReturnValueOnce(new Promise((resolve) => {
        answerEarlyRequest = resolve;
      }));
      workflowAPI.run.mockImplementation(() => {
        const refusal = Promise.reject(new Error(reason));
        // Attached ahead of the tab's own handler: the history request from before the refusal is
        // answered in the same turn, once the tab has reported the refusal and before it redraws.
        refusal.catch(() => queueMicrotask(() => answerEarlyRequest({ data: [earlierRun] })));
        return refusal;
      });

      render(<ControlledRunTab />);
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      expect(screen.queryByText(/"marker": "earlier-run"/)).not.toBeInTheDocument();
    });

    it('keeps the reason on screen when the page was opened on one execution', async () => {
      const user = userEvent.setup();
      workflowAPI.getHistory.mockResolvedValue({ data: [earlierRun] });
      const answerHistory = refuseNextRun();

      render(<ControlledRunTab focusExecutionId="exec-earlier" />);
      expect((await screen.findAllByText(/"marker": "earlier-run"/)).length).toBeGreaterThan(0);
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await answerHistory([earlierRun]);
      expect(screen.getByText(reason)).toBeInTheDocument();
    });

    it('gives way to the next run once the server accepts one', async () => {
      const user = userEvent.setup();
      const answerHistory = refuseNextRun();

      render(<ControlledRunTab />);
      await screen.findByText('暂无执行记录');
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(reason)).toBeInTheDocument();
      await answerHistory([]);
      expect(screen.getByText(reason)).toBeInTheDocument();

      const nextRun: WorkflowExecution = {
        id: 'exec-next',
        workflowId: 'wf-1',
        inputParams: {},
        status: 'running',
        startedAt: Date.now(),
        executionLog: [],
      };
      workflowAPI.run.mockResolvedValue({ data: nextRun });
      workflowAPI.getHistory.mockResolvedValue({
        data: [{ ...nextRun, status: 'success', outputResults: { marker: 'next-run' }, duration: 2 }],
      });
      await user.click(screen.getByRole('button', { name: '测试运行' }));

      expect(await screen.findByText(/"marker": "next-run"/)).toBeInTheDocument();
      expect(screen.queryByText(reason)).not.toBeInTheDocument();
    });
  });

});
