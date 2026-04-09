import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ExecutionPanel from './ExecutionPanel';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'editor.execution.title': '执行结果',
        'editor.execution.statusRunning': '执行中',
        'editor.execution.statusSuccess': '成功',
        'editor.execution.statusError': '失败',
        'editor.execution.statusTimeout': '超时',
        'editor.execution.statusCancelled': '已取消',
        'editor.execution.duration': '耗时',
        'editor.execution.startTime': '开始',
        'editor.execution.runAgain': '再次执行',
        'editor.execution.tabResult': '执行结果',
        'editor.execution.tabLog': '执行日志',
        'editor.execution.inputParams': '输入参数',
        'editor.execution.outputResults': '输出结果',
        'editor.execution.errorMessage': '错误信息',
        'editor.execution.noLog': '暂无执行日志',
        'editor.execution.stepLabel': '步骤',
        'editor.execution.stop': '停止',
        'editor.execution.cancelledMessage': '执行已被手动停止',
        'detail.run.stopping': '停止中...',
      };
      return translations[key] ?? key;
    },
  }),
}));

describe('ExecutionPanel', () => {
  it('shows stop button for running executions', async () => {
    const user = userEvent.setup();
    const onStop = vi.fn();

    render(
      <ExecutionPanel
        execution={{
          id: 'exec-running',
          workflowId: 'wf-1',
          inputParams: { topic: 'demo' },
          status: 'running',
          startedAt: Date.now(),
          executionLog: [],
        }}
        onClose={vi.fn()}
        onStop={onStop}
      />
    );

    await user.click(screen.getByRole('button', { name: '停止' }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('renders cancelled executions as a dedicated state', () => {
    render(
      <ExecutionPanel
        execution={{
          id: 'exec-cancelled',
          workflowId: 'wf-1',
          inputParams: {},
          status: 'cancelled',
          startedAt: Date.now(),
          executionLog: [],
          errorMessage: 'Run cancelled: run_id=abc123',
        }}
        onClose={vi.fn()}
      />
    );

    expect(screen.getAllByText('已取消').length).toBeGreaterThan(0);
    expect(screen.getByText('执行已被手动停止')).toBeInTheDocument();
  });
});
