import { describe, expect, it } from 'vitest';

import { shouldForwardSSEEventToParent } from './sseRouting';

describe('shouldForwardSSEEventToParent', () => {
  it('forwards global list refresh events', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'session.updated',
      properties: { id: 'other-session' },
    }, 'session-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'task.updated',
      properties: { executionID: 'task-1' },
    }, 'session-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'workflow.updated',
      properties: { workflowID: 'workflow-1' },
    }, 'session-1')).toBe(true);
  });

  it('forwards events that belong to the active session', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'message.updated',
      properties: { info: { sessionID: 'session-1' } },
    }, 'session-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'message.part.updated',
      properties: { part: { sessionID: 'session-1' } },
    }, 'session-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'message.removed',
      properties: { sessionID: 'session-1', messageID: 'message-1' },
    }, 'session-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'session.status',
      properties: { sessionID: 'session-1' },
    }, 'session-1')).toBe(true);
  });

  it('does not forward unrelated chat events', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'message.updated',
      properties: { info: { sessionID: 'other-session' } },
    }, 'session-1')).toBe(false);
    expect(shouldForwardSSEEventToParent({
      type: 'message.updated',
      properties: { info: { sessionID: 'other-session' } },
    }, null)).toBe(false);
    expect(shouldForwardSSEEventToParent({
      type: 'message.updated',
      properties: undefined,
    }, 'session-1')).toBe(false);
  });
});
