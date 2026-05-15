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
});
