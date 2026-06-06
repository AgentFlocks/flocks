import { describe, expect, it } from 'vitest';

import { extractDelegateInfo } from './DelegateTaskCard';

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
