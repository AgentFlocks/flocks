import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import DelegateTaskCard, { shouldRenderDelegateTaskCard } from './DelegateTaskCard';
import type { MessagePart } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'delegate.delegatedTo': '委派给 {{agent}}',
        'delegate.pending': '等待中',
        'delegate.running': '执行中',
        'delegate.completed': '已完成',
        'delegate.background': '后台',
        'delegate.elapsedRunning': '已运行',
        'delegate.elapsedDone': '耗时',
        'delegate.steps': '步',
        'delegate.resultSummary': '结果摘要',
        'delegate.viewExecution': '查看执行过程',
        'delegate.subTask': '子任务',
      };
      return (translations[key] ?? key).replace(
        /\{\{(\w+)\}\}/g,
        (_, name: string) => String(options?.[name] ?? ''),
      );
    },
  }),
}));

vi.mock('./DelegateDetailSheet', async () => {
  const ReactModule = await import('react');
  return {
    default: ({
      open,
      sessionId,
    }: {
      open: boolean;
      sessionId: string;
    }) => open
      ? ReactModule.createElement('div', { 'data-testid': 'delegate-detail-sheet' }, sessionId)
      : null,
  };
});

describe('shouldRenderDelegateTaskCard', () => {
  it('does not treat generic tool category fields as delegate tasks', () => {
    const part = {
      id: 'part-wecom',
      type: 'tool',
      tool: 'wecom_mcp',
      state: {
        status: 'completed',
        input: {
          action: 'call',
          category: 'doc',
          method: 'create_doc',
        },
        output: 'ok',
        metadata: {},
      },
    } as MessagePart;

    expect(shouldRenderDelegateTaskCard(part)).toBe(false);
  });

  it('renders known delegate tools as delegate tasks', () => {
    const part = {
      id: 'part-task',
      type: 'tool',
      tool: 'task',
      state: {
        status: 'running',
        input: {
          description: 'Explore issue',
          prompt: 'Find the issue',
          subagent_type: 'explore',
        },
      },
    } as MessagePart;

    expect(shouldRenderDelegateTaskCard(part)).toBe(true);
  });

  it('uses persisted child session metadata as a delegate fallback', () => {
    const part = {
      id: 'part-legacy',
      type: 'tool',
      tool: 'unknown',
      state: {
        status: 'completed',
        input: {
          category: 'task',
          description: 'Legacy task',
        },
        output: 'done',
        metadata: {
          sessionId: 'ses_child',
        },
      },
    } as MessagePart;

    expect(shouldRenderDelegateTaskCard(part)).toBe(true);
  });

  it('does not treat run_workflow with leaked child session metadata as a delegate task', () => {
    const part = {
      id: 'part-run-workflow',
      type: 'tool',
      tool: 'run_workflow',
      state: {
        status: 'running',
        input: {
          workflow: 'loop_host_forensics_fast',
        },
        metadata: {
          workflow_id: 'loop_host_forensics_fast',
          workflow_execution_id: 'wf_exec_123',
          sessionId: 'ses_child_leaked',
        },
      },
    } as MessagePart;

    expect(shouldRenderDelegateTaskCard(part)).toBe(false);
  });
});

describe('DelegateTaskCard process step', () => {
  it('shows delegated agent context and opens the child execution sheet', () => {
    const part = {
      id: 'part-delegate',
      type: 'tool',
      tool: 'delegate_task',
      state: {
        status: 'running',
        input: {
          description: '调研 OpenClaw 最新版本',
          prompt: '调研 GitHub 上的最新发布',
          subagent_type: 'librarian',
          run_in_background: true,
        },
        metadata: {
          sessionId: 'ses-child',
          status: 'running',
          background: true,
          steps: [
            {
              tool: 'websearch',
              title: '搜索最新版本',
              status: 'running',
            },
          ],
          stepCount: 1,
        },
      },
    } as MessagePart;

    render(<DelegateTaskCard part={part} processStep />);

    const processStep = screen.getByTestId('chat-process-delegate-step');
    expect(processStep).toHaveTextContent('委派给 Librarian');
    expect(processStep).toHaveTextContent('调研 OpenClaw 最新版本');
    expect(processStep).toHaveTextContent('执行中');
    expect(processStep).toHaveTextContent('后台');
    expect(processStep.querySelector('summary')).toHaveClass('text-sm');

    fireEvent.click(screen.getByRole('button', { name: /查看执行过程/ }));

    expect(screen.getByTestId('delegate-detail-sheet')).toHaveTextContent('ses-child');
  });
});
