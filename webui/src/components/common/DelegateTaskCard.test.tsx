import { describe, expect, it } from 'vitest';

import { shouldRenderDelegateTaskCard } from './DelegateTaskCard';
import type { MessagePart } from '../../types';

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
