import { describe, expect, it } from 'vitest';

import { buildRunWorkflowHeaderSummary } from './toolStageSummary';

describe('buildRunWorkflowHeaderSummary', () => {
  const zhT = (key: string, options?: Record<string, unknown>) => {
    switch (key) {
      case 'chat.tool.workflowPhase.running':
        return '执行中';
      case 'chat.tool.workflowPhase.queued':
        return '排队中';
      case 'chat.tool.workflowStep':
        return `步骤：${String(options?.step ?? '')}`;
      case 'chat.tool.workflowNode':
        return `节点：${String(options?.node ?? '')}`;
      default:
        return key;
    }
  };

  it('returns a running workflow header summary with total nodes and node id', () => {
    expect(
      buildRunWorkflowHeaderSummary(
        'run_workflow',
        {
          status: 'running',
          input: {
            workflow: '/tmp/keyword-search-summary/workflow.json',
          },
          metadata: {
            workflow_name: 'keyword-search-summary',
            phase: 'running',
            current_node_id: 'validate_input',
            step_index: 2,
            total_nodes: 10,
          },
        },
        zhT,
      ),
    ).toBe('keyword-search-summary 执行中 · 2/10 · 节点：validate_input');
  });

  it('shows queued phase before the first node starts', () => {
    expect(
      buildRunWorkflowHeaderSummary(
        'run_workflow',
        {
          status: 'running',
          metadata: {
            workflow_name: 'keyword-search-summary',
            phase: 'queued',
            step_index: 0,
          },
        },
        zhT,
      ),
    ).toBe('keyword-search-summary 排队中');
  });

  it('falls back to concise english labels when no translator is provided', () => {
    expect(
      buildRunWorkflowHeaderSummary(
        'run_workflow',
        {
          status: 'running',
          metadata: {
            workflow_name: 'keyword-search-summary',
            phase: 'running',
            current_node_id: 'validate_input',
            step_index: 2,
            total_nodes: 10,
          },
        },
      ),
    ).toBe('keyword-search-summary running · 2/10 · node:validate_input');
  });

  it('returns empty for non-workflow tools or non-running states', () => {
    expect(buildRunWorkflowHeaderSummary('bash', { status: 'running' })).toBe('');
    expect(buildRunWorkflowHeaderSummary('run_workflow', { status: 'completed' })).toBe('');
  });
});
