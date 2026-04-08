import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RunTab from './RunTab';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    getSampleInputs: vi.fn(),
    run: vi.fn(),
    getExecution: vi.fn(),
    cancelExecution: vi.fn(),
    getService: vi.fn(),
    publish: vi.fn(),
    unpublish: vi.fn(),
    getKafkaConfig: vi.fn(),
    saveKafkaConfig: vi.fn(),
    getHistory: vi.fn(),
  },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI,
}));

vi.mock('@/components/common/CopyButton', () => ({
  default: () => null,
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
        'detail.run.running': '运行中...',
        'detail.run.testRun': '测试运行',
        'detail.run.stopRun': '停止运行',
        'detail.run.stopping': '停止中...',
        'detail.run.outputResults': '输出结果',
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
        'detail.run.historySection': '执行历史',
        'detail.run.noHistory': '暂无执行记录',
        'detail.run.noOutput': '无输出数据',
        'detail.run.stepsCompleted': '步已完成',
        'detail.run.stepInputs': '输入',
        'detail.run.stepOutputs': '输出',
      };
      return translations[key] ?? key;
    },
  }),
}));

describe('RunTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workflowAPI.getSampleInputs.mockResolvedValue({ data: { sampleInputs: {} } });
    workflowAPI.getService.mockResolvedValue({ data: null });
    workflowAPI.getKafkaConfig.mockResolvedValue({ data: null });
    workflowAPI.getHistory.mockResolvedValue({ data: [] });
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

    render(
      <RunTab
        workflow={{
          id: 'wf-1',
          name: 'Demo Workflow',
          category: 'default',
          workflowJson: { start: 'step1', nodes: [], edges: [] },
          status: 'draft',
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
        }}
      />
    );

    await user.click(screen.getByRole('button', { name: '测试运行' }));

    await waitFor(() => {
      expect(workflowAPI.run).toHaveBeenCalledTimes(1);
    });

    const stopButton = await screen.findByRole('button', { name: '停止运行' });
    await user.click(stopButton);

    expect(workflowAPI.cancelExecution).toHaveBeenCalledWith('wf-1', 'exec-1');
  });
});
