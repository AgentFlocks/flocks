import { describe, expect, it } from 'vitest';

import {
  extractDelegateInfo,
  shouldRenderDelegateTaskCard,
} from './DelegateTaskCard';

// ---------------------------------------------------------------------------
// shouldRenderDelegateTaskCard
// ---------------------------------------------------------------------------

describe('shouldRenderDelegateTaskCard', () => {
  it('returns true for delegate_task tool regardless of input', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'delegate_task',
        state: { input: {}, output: '' },
      } as any),
    ).toBe(true);
  });

  it('returns true for task tool (alias) regardless of input', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'task',
        state: { input: {}, output: '' },
      } as any),
    ).toBe(true);
  });

  it('returns false for unknown tool with no delegate input', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'unknown',
        state: { input: { something: 'else' }, output: '' },
      } as any),
    ).toBe(false);
  });

  it('returns false for MCP tool even if output contains task_metadata block', () => {
    // Critical regression guard: wecom_mcp / threatbook_mcp style tools must
    // not be misclassified just because their output happens to embed
    // <task_metadata>session_id: ...</task_metadata>.
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'wecom_mcp',
        state: {
          input: { action: 'send_message' },
          output: '<task_metadata>\nsession_id: wxwork-msg-12345\n</task_metadata>',
        },
      } as any),
    ).toBe(false);
  });

  it('returns false for a real non-delegate tool name', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'bash',
        state: { input: { command: 'ls' }, output: 'ok' },
      } as any),
    ).toBe(false);
  });

  it('returns true for unknown tool with subagent_type input + task_metadata output', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'unknown',
        state: {
          input: { subagent_type: 'explore', prompt: 'investigate' },
          output: '<task_metadata>\nsession_id: ses-x\n</task_metadata>',
        },
      } as any),
    ).toBe(true);
  });

  it('returns true for unknown tool with category input + task_metadata output', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'unknown',
        state: {
          input: { category: 'quick', prompt: 'summarize' },
          output: '<task_metadata>\nsession_id: ses-y\n</task_metadata>',
        },
      } as any),
    ).toBe(true);
  });

  it('returns false for unknown tool with delegate input but no session_id', () => {
    expect(
      shouldRenderDelegateTaskCard({
        tool: 'unknown',
        state: {
          input: { subagent_type: 'explore' },
          output: 'no metadata here',
        },
      } as any),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// extractDelegateInfo
// ---------------------------------------------------------------------------

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

  it('extracts session_id from <task_metadata> when nested metadata is missing', () => {
    const info = extractDelegateInfo({
      status: 'completed',
      input: { subagent_type: 'explore', prompt: 'investigate' },
      output:
        'Some text\n<task_metadata>\nsession_id: ses-from-output\n</task_metadata>\nMore text',
    } as any, 'Subtask');

    expect(info.childSessionId).toBe('ses-from-output');
  });

  it('strips the <task_metadata> block from the rendered output', () => {
    const info = extractDelegateInfo({
      status: 'completed',
      input: { subagent_type: 'explore', prompt: 'investigate' },
      output:
        'Result text\n<task_metadata>\nsession_id: ses-x\n</task_metadata>',
    } as any, 'Subtask');

    expect(info.output).toBe('Result text');
  });
});
