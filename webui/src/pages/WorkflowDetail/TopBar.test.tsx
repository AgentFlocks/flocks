import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import TopBar from './TopBar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, string>) => {
      if (key === 'status.active') return '活跃';
      if (key === 'detail.topBar.runningStage') {
        return `当前阶段：${params?.phase} · 节点：${params?.node}`;
      }
      if (key === 'detail.topBar.phase.running') return '执行中';
      if (key === 'detail.topBar.phase.cancelling') return '取消中';
      if (key === 'detail.topBar.collapsePanel') return '收起面板';
      if (key === 'pageTitle') return '工作流';
      return key;
    },
  }),
}));

describe('TopBar', () => {
  it('在运行中显示当前节点阶段', () => {
    render(
      <MemoryRouter>
        <TopBar
          workflow={{
            id: 'wf-1',
            name: '测试工作流',
            category: 'security',
            status: 'active',
            createdAt: 0,
            updatedAt: 0,
            stats: {
              callCount: 0,
              successCount: 0,
              errorCount: 0,
              totalRuntime: 0,
              avgRuntime: 0,
              thumbsUp: 0,
              thumbsDown: 0,
            },
            workflowJson: {
              start: 'node-1',
              nodes: [{ id: 'node-1', type: 'python', description: 'run workflow' }],
              edges: [],
            },
          }}
          latestExecution={{
            id: 'exec-1',
            workflowId: 'wf-1',
            inputParams: {},
            status: 'running',
            startedAt: 0,
            executionLog: [],
            currentNodeId: 'node-1',
            currentPhase: 'running',
          }}
          panelOpen
          onTogglePanel={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('当前阶段：执行中 · 节点：run workflow')).toBeInTheDocument();
  });

  it('在取消中显示当前取消阶段', () => {
    render(
      <MemoryRouter>
        <TopBar
          workflow={{
            id: 'wf-1',
            name: '测试工作流',
            category: 'security',
            status: 'active',
            createdAt: 0,
            updatedAt: 0,
            stats: {
              callCount: 0,
              successCount: 0,
              errorCount: 0,
              totalRuntime: 0,
              avgRuntime: 0,
              thumbsUp: 0,
              thumbsDown: 0,
            },
            workflowJson: {
              start: 'node-1',
              nodes: [{ id: 'node-1', type: 'python', description: 'run workflow' }],
              edges: [],
            },
          }}
          latestExecution={{
            id: 'exec-1',
            workflowId: 'wf-1',
            inputParams: {},
            status: 'running',
            startedAt: 0,
            executionLog: [],
            currentNodeId: 'node-1',
            currentPhase: 'cancelling',
          }}
          panelOpen
          onTogglePanel={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('当前阶段：取消中 · 节点：run workflow')).toBeInTheDocument();
  });
});
