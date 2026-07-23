import { describe, expect, it } from 'vitest';

import { resolveSessionChatSSEAction } from './sseActions';

describe('resolveSessionChatSSEAction', () => {
  it('ignores events without a current session or unrelated session payload', () => {
    expect(resolveSessionChatSSEAction({
      type: 'message.updated',
      properties: { info: { sessionID: 'sess-1' } },
    }, null)).toEqual({ kind: 'ignore' });

    expect(resolveSessionChatSSEAction({
      type: 'message.updated',
      properties: { info: { sessionID: 'other-session' } },
    }, 'sess-1')).toEqual({ kind: 'ignore' });

    expect(resolveSessionChatSSEAction({
      type: 'server.heartbeat',
    }, 'sess-1')).toEqual({ kind: 'ignore' });
  });

  it('resolves status events from both status and updated payload shapes', () => {
    expect(resolveSessionChatSSEAction({
      type: 'session.status',
      properties: {
        sessionID: 'sess-1',
        status: { type: 'compacting', message: 'Compacting context' },
      },
    }, 'sess-1')).toEqual({
      kind: 'session-status',
      statusType: 'compacting',
      message: 'Compacting context',
    });

    expect(resolveSessionChatSSEAction({
      type: 'session.updated',
      properties: { id: 'sess-1', status: 'idle' },
    }, 'sess-1')).toEqual({
      kind: 'session-status',
      statusType: 'idle',
    });
  });

  it('resolves message and part update actions for the active session', () => {
    expect(resolveSessionChatSSEAction({
      type: 'message.updated',
      properties: {
        info: {
          id: 'msg-1',
          sessionID: 'sess-1',
          role: 'assistant',
          parts: [],
          timestamp: 1,
        },
      },
    }, 'sess-1')).toMatchObject({
      kind: 'message-updated',
      info: { id: 'msg-1' },
    });

    expect(resolveSessionChatSSEAction({
      type: 'message.removed',
      properties: {
        sessionID: 'sess-1',
        messageID: 'msg-1',
      },
    }, 'sess-1')).toEqual({
      kind: 'message-removed',
      messageID: 'msg-1',
    });

    expect(resolveSessionChatSSEAction({
      type: 'message.part.updated',
      properties: {
        part: {
          id: 'part-1',
          messageID: 'msg-1',
          sessionID: 'sess-1',
          type: 'text',
          text: 'hello',
        },
        delta: 'hello',
      },
    }, 'sess-1')).toMatchObject({
      kind: 'message-part-updated',
      part: { id: 'part-1' },
      delta: 'hello',
    });

    expect(resolveSessionChatSSEAction({
      type: 'message.removed',
      properties: {
        sessionID: 'other-session',
        messageID: 'msg-1',
      },
    }, 'sess-1')).toEqual({ kind: 'ignore' });
  });

  it('resolves questions only when request and call ids are present', () => {
    expect(resolveSessionChatSSEAction({
      type: 'question.asked',
      properties: {
        sessionID: 'sess-1',
        id: 'request-1',
        tool: { callID: 'call-1' },
        questions: [{ question: 'Continue?' }],
      },
    }, 'sess-1')).toEqual({
      kind: 'question-asked',
      callID: 'call-1',
      requestId: 'request-1',
      questions: [{ question: 'Continue?' }],
    });

    expect(resolveSessionChatSSEAction({
      type: 'question.asked',
      properties: { sessionID: 'sess-1', id: 'request-1' },
    }, 'sess-1')).toEqual({ kind: 'ignore' });

    expect(resolveSessionChatSSEAction({
      type: 'question.rejected',
      properties: { sessionID: 'sess-1', requestID: 'request-1' },
    }, 'sess-1')).toEqual({
      kind: 'question-resolved',
      requestId: 'request-1',
    });
  });

  it('resolves compaction, queue, goal, context, and error actions', () => {
    expect(resolveSessionChatSSEAction({
      type: 'session.compaction_progress',
      properties: { sessionID: 'sess-1', stage: 'chunk_done', data: { chunk: 2 } },
    }, 'sess-1')).toEqual({
      kind: 'compaction-progress',
      stage: 'chunk_done',
      data: { chunk: 2 },
    });

    expect(resolveSessionChatSSEAction({
      type: 'session.prompt_queue.updated',
      properties: { sessionID: 'sess-1', items: [{ id: 'queue-1' }] },
    }, 'sess-1')).toMatchObject({
      kind: 'prompt-queue-updated',
      items: [{ id: 'queue-1' }],
    });

    expect(resolveSessionChatSSEAction({
      type: 'session.goal.updated',
      properties: { sessionID: 'sess-1', status: 'active', objective: 'Ship it' },
    }, 'sess-1')).toEqual({
      kind: 'goal-updated',
      goal: { sessionID: 'sess-1', status: 'active', objective: 'Ship it' },
    });

    expect(resolveSessionChatSSEAction({
      type: 'context.usage.updated',
      properties: {
        sessionID: 'sess-1',
        usedTokens: 100,
        contextWindow: 1000,
        percent: 10,
        source: 'estimated',
        estimatedTokens: 100,
        compactedTokens: 0,
        segments: [],
        excludedSegments: [],
      },
    }, 'sess-1')).toMatchObject({
      kind: 'context-usage-updated',
      snapshot: { usedTokens: 100 },
    });

    expect(resolveSessionChatSSEAction({
      type: 'session.error',
      properties: {
        sessionID: 'sess-1',
        error: { message: 'provider unavailable' },
      },
    }, 'sess-1')).toEqual({
      kind: 'session-error',
      message: 'provider unavailable',
    });
  });
});
