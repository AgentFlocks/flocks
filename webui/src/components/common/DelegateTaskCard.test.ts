import { describe, expect, it } from 'vitest';

import { buildParallelDelegateGroupParts, extractDelegateInfo } from './DelegateTaskCard';

describe('extractDelegateInfo', () => {
  it('shows a launched background subagent as running until child completion arrives', () => {
    const info = extractDelegateInfo({
      status: 'completed',
      input: {
        description: 'inspect permissions',
        prompt: 'Inspect permissions',
        subagent_type: 'explore',
        run_in_background: true,
      },
      output: 'Background task launched successfully.',
      metadata: {
        sessionId: 'ses-child',
        taskId: 'bg-task',
        status: 'running',
        background: true,
      },
    } as any, 'Subtask');

    expect(info.status).toBe('running');
    expect(info.isBackground).toBe(true);
    expect(info.childSessionId).toBe('ses-child');
  });

  it('shows a background subagent as completed after parent tool part is updated', () => {
    const info = extractDelegateInfo({
      status: 'completed',
      input: {
        description: 'inspect permissions',
        prompt: 'Inspect permissions',
        subagent_type: 'explore',
        run_in_background: true,
      },
      output: 'done',
      metadata: {
        sessionId: 'ses-child',
        taskId: 'bg-task',
        status: 'completed',
        background: true,
      },
    } as any, 'Subtask');

    expect(info.status).toBe('completed');
  });
});

describe('buildParallelDelegateGroupParts', () => {
  it('groups foreground sibling delegate task parts and keeps child session ids', () => {
    const grouped = buildParallelDelegateGroupParts([
      {
        id: 'part-a',
        type: 'tool',
        tool: 'delegate_task',
        callID: 'call-a',
        state: {
          status: 'running',
          input: {
            description: 'inspect auth',
            prompt: 'Inspect auth',
            subagent_type: 'explore',
          },
          metadata: {
            sessionId: 'ses-a',
            status: 'running',
          },
          time: { start: 100 },
        },
      },
      {
        id: 'part-b',
        type: 'tool',
        tool: 'task',
        callID: 'call-b',
        state: {
          status: 'running',
          input: {
            description: 'inspect API',
            prompt: 'Inspect API',
            subagent_type: 'explore',
          },
          metadata: {
            sessionId: 'ses-b',
            status: 'running',
          },
          time: { start: 120 },
        },
      },
    ] as any);

    expect(grouped).toHaveLength(1);
    const state = grouped[0].state as any;
    expect(state.metadata.parallel).toBe(true);
    expect(state.metadata.children).toHaveLength(2);
    expect(state.metadata.children[0].sessionId).toBe('ses-a');
    expect(state.metadata.children[1].sessionId).toBe('ses-b');
    expect(state.status).toBe('running');
  });

  it('does not group background or batch delegate parts', () => {
    const parts = [
      {
        id: 'part-bg',
        type: 'tool',
        tool: 'delegate_task',
        state: {
          status: 'completed',
          input: {
            description: 'background work',
            prompt: 'Work',
            subagent_type: 'explore',
            run_in_background: true,
          },
          metadata: {
            sessionId: 'ses-bg',
            background: true,
          },
        },
      },
      {
        id: 'part-batch',
        type: 'tool',
        tool: 'delegate_task',
        state: {
          status: 'running',
          input: {
            tasks: [{ prompt: 'A', subagent_type: 'explore' }],
          },
          metadata: {
            parallel: true,
            children: [],
          },
        },
      },
    ] as any;

    expect(buildParallelDelegateGroupParts(parts)).toBe(parts);
  });
});
