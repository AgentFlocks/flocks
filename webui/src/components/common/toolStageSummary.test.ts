import { describe, expect, it } from 'vitest';

import { buildRunWorkflowHeaderSummary } from './toolStageSummary';

describe('buildRunWorkflowHeaderSummary', () => {
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
      ),
    ).toBe('keyword-search-summary running · 2/10 node:validate_input');
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
      ),
    ).toBe('keyword-search-summary queued');
  });

  it('returns empty for non-workflow tools or non-running states', () => {
    expect(buildRunWorkflowHeaderSummary('bash', { status: 'running' })).toBe('');
    expect(buildRunWorkflowHeaderSummary('run_workflow', { status: 'completed' })).toBe('');
  });
});
