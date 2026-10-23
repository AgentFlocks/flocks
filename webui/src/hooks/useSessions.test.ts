import { describe, expect, it, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { applyMessagePartUpdate, useSessionMessages, useSessions } from './useSessions';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import type { Message } from '@/types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function sessionListItem(id: string, projectID: string, updated = 1) {
  return {
    id,
    projectID,
    effectiveProjectID: projectID,
    title: id,
    time: { created: updated, updated },
    category: 'user',
  };
}

// ---------------------------------------------------------------------------
// Mocks — keep API calls from running in unit tests
// ---------------------------------------------------------------------------
vi.mock('@/api/session', () => ({ sessionApi: { list: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/api/client', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: [] }) },
}));

// Minimal message factory
function makeMsg(overrides: Partial<Message> & { id: string }): Message {
  return {
    sessionID: 'sess-1',
    role: 'assistant',
    parts: [],
    timestamp: 0,
    ...overrides,
  } as unknown as Message;
}

describe('applyMessagePartUpdate', () => {
  describe('message not found', () => {
    it('creates a placeholder for the part message instead of reusing a previous assistant', () => {
      const partInfo = { id: 'p1', messageID: 'msg-unknown', sessionID: 'sess-1', type: 'text', text: 'hello' };
      const prev: Message[] = [
        makeMsg({ id: 'msg-1', role: 'assistant', parts: [], finish: null } as any),
      ];
      const result = applyMessagePartUpdate(prev, partInfo);
      expect(result).toHaveLength(2);
      expect(result[0].id).toBe('msg-1');
      expect(result[0].parts).toHaveLength(0);
      expect(result[1].id).toBe('msg-unknown');
      expect((result[1].parts as any[])[0].id).toBe('p1');
    });

    it('skips finished assistant messages when looking for in-progress message', () => {
      const partInfo = { id: 'p1', messageID: 'msg-unknown', sessionID: 'sess-1', type: 'text', text: 'hi' };
      const prev: Message[] = [
        makeMsg({ id: 'msg-1', role: 'assistant', parts: [], finish: 'stop' } as any),
      ];
      const result = applyMessagePartUpdate(prev, partInfo);
      // should create a new placeholder message
      expect(result).toHaveLength(2);
      expect(result[1].id).toBe('msg-unknown');
      expect((result[1].parts as any[])[0].id).toBe('p1');
    });

    it('creates a new placeholder message when no in-progress assistant exists', () => {
      const partInfo = { id: 'p1', messageID: 'msg-new', sessionID: 'sess-1', type: 'text', text: 'hello' };
      const prev: Message[] = [makeMsg({ id: 'msg-user', role: 'user', parts: [] })];
      const result = applyMessagePartUpdate(prev, partInfo);
      expect(result).toHaveLength(2);
      expect(result[1].id).toBe('msg-new');
      expect(result[1].role).toBe('assistant');
    });
  });

  describe('message found', () => {
    it('appends a new part when the part id does not exist', () => {
      const partInfo = { id: 'p2', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'world' };
      const prev: Message[] = [
        makeMsg({ id: 'msg-1', parts: [{ id: 'p1', type: 'text', text: 'hello' } as any] }),
      ];
      const result = applyMessagePartUpdate(prev, partInfo);
      expect((result[0].parts as any[])).toHaveLength(2);
      expect((result[0].parts as any[])[1].id).toBe('p2');
    });

    it('removes temp parts before appending a new real part', () => {
      const partInfo = { id: 'p-real', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'x' };
      const prev: Message[] = [
        makeMsg({
          id: 'msg-1',
          parts: [{ id: 'temp-abc', type: 'text', text: '' } as any],
        }),
      ];
      const result = applyMessagePartUpdate(prev, partInfo);
      const parts = result[0].parts as any[];
      expect(parts).toHaveLength(1);
      expect(parts[0].id).toBe('p-real');
    });

    it('updates existing text part with accumulated text when delta is provided', () => {
      const existing = { id: 'p1', messageID: 'msg-1', type: 'text', text: 'hello ' };
      const partInfo = { id: 'p1', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'hello world' };
      const prev: Message[] = [makeMsg({ id: 'msg-1', parts: [existing as any] })];
      const result = applyMessagePartUpdate(prev, partInfo, ' world');
      const parts = result[0].parts as any[];
      expect(parts[0].text).toBe('hello world');
    });

    it('is idempotent when the same part snapshot is delivered again (SSE reconnect / re-delivery)', () => {
      // The part carries the full text; the delta is only an append hint. Replaying the
      // identical event must neither duplicate the part nor double the text.
      const partInfo = { id: 'p1', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'hello world' };
      const first = applyMessagePartUpdate([makeMsg({ id: 'msg-1', parts: [] })], partInfo, 'hello world');
      const replayed = applyMessagePartUpdate(first, partInfo, 'hello world');
      const again = applyMessagePartUpdate(replayed, { ...partInfo }, ' world');
      for (const state of [first, replayed, again]) {
        const parts = state[0].parts as any[];
        expect(parts).toHaveLength(1);
        expect(parts[0].id).toBe('p1');
        expect(parts[0].text).toBe('hello world');
      }
      expect(replayed).toHaveLength(1);
    });

    it('replaces existing part without delta for non-text types', () => {
      const existing = { id: 'p1', messageID: 'msg-1', type: 'tool', state: { status: 'pending' } };
      const partInfo = { id: 'p1', messageID: 'msg-1', sessionID: 'sess-1', type: 'tool', state: { status: 'completed' } };
      const prev: Message[] = [makeMsg({ id: 'msg-1', parts: [existing as any] })];
      const result = applyMessagePartUpdate(prev, partInfo);
      const parts = result[0].parts as any[];
      expect((parts[0] as any).state.status).toBe('completed');
    });

    it('does not mutate the original messages array', () => {
      const partInfo = { id: 'p1', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'hi' };
      const originalParts = [{ id: 'p-old', type: 'text', text: 'old' } as any];
      const prev: Message[] = [makeMsg({ id: 'msg-1', parts: originalParts })];
      applyMessagePartUpdate(prev, partInfo, 'hi');
      expect(originalParts).toHaveLength(1);
      expect(originalParts[0].id).toBe('p-old');
    });
  });

  describe('streaming text accumulation', () => {
    it('supports reasoning type delta update', () => {
      const existing = { id: 'r1', messageID: 'msg-1', type: 'reasoning', text: 'think ' };
      const partInfo = { id: 'r1', messageID: 'msg-1', sessionID: 'sess-1', type: 'reasoning', text: 'think more' };
      const prev: Message[] = [makeMsg({ id: 'msg-1', parts: [existing as any] })];
      const result = applyMessagePartUpdate(prev, partInfo, ' more');
      expect((result[0].parts as any[])[0].text).toBe('think more');
    });

    it('supports thinking type delta update', () => {
      const existing = { id: 't1', messageID: 'msg-1', type: 'thinking', text: 'a' };
      const partInfo = { id: 't1', messageID: 'msg-1', sessionID: 'sess-1', type: 'thinking', text: 'ab' };
      const prev: Message[] = [makeMsg({ id: 'msg-1', parts: [existing as any] })];
      const result = applyMessagePartUpdate(prev, partInfo, 'b');
      expect((result[0].parts as any[])[0].text).toBe('ab');
    });
  });
});

// ---------------------------------------------------------------------------
// updateMessagePart scheduling behaviour
// Verifies observable state changes (not internal scheduling details):
//  - first call with a new part ID causes immediate state update
//  - subsequent calls with the same part ID accumulate content correctly
// ---------------------------------------------------------------------------
describe('updateMessagePart scheduling', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.mocked(client.get).mockReset();
    vi.mocked(client.get).mockResolvedValue({ data: [] });
    vi.useRealTimers();
  });

  it('keeps parentID from fetched messages for regenerate truncation', async () => {
    vi.mocked(client.get).mockResolvedValueOnce({
      data: [{
        info: {
          id: 'msg-2',
          sessionID: 'sess-1',
          role: 'assistant',
          parentID: 'msg-1',
          time: { created: 123 },
        },
        parts: [],
      }],
    } as any);

    const { result } = renderHook(() => useSessionMessages('sess-1'));

    await act(async () => {});

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].parentID).toBe('msg-1');
  });

  it('keeps assistant error info from fetched messages', async () => {
    vi.mocked(client.get).mockResolvedValueOnce({
      data: [{
        info: {
          id: 'msg-error',
          sessionID: 'sess-1',
          role: 'assistant',
          finish: 'error',
          error: {
            name: 'APIConnectionError',
            data: { message: 'Connection error.' },
          },
          time: { created: 123 },
        },
        parts: [],
      }],
    } as any);

    const { result } = renderHook(() => useSessionMessages('sess-1'));

    await act(async () => {});

    expect(result.current.messages).toHaveLength(1);
    expect((result.current.messages[0].error as any).data.message).toBe('Connection error.');
    expect(result.current.messages[0].finish).toBe('error');
  });

  it('first appearance of a new part updates messages state immediately', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    // Wait for the initial fetchMessages effect to settle so it doesn't wipe state
    await act(async () => {});

    const newPart = { id: 'part-new', messageID: 'msg-1', sessionID: 'sess-1', type: 'text', text: 'hello' };

    await act(async () => {
      result.current.updateMessagePart(newPart);
    });

    const msgs = result.current.messages;
    // A placeholder message should have been created with the part
    const created = msgs.find((m: any) => m.id === 'msg-1');
    expect(created).toBeDefined();
    expect((created!.parts as any[])[0].id).toBe('part-new');
    expect((created!.parts as any[])[0].text).toBe('hello');
  });

  it('second call with same part ID accumulates delta content correctly', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    // Wait for initial fetch to settle
    await act(async () => {});

    const part = { id: 'part-known', messageID: 'msg-2', sessionID: 'sess-1', type: 'text', text: 'hello' };
    const delta = { ...part, text: 'hello world' };

    // First call — registers the part
    await act(async () => {
      result.current.updateMessagePart(part);
    });

    // Second call — content delta on the same part
    await act(async () => {
      result.current.updateMessagePart(delta, ' world');
    });

    const msgs = result.current.messages;
    const msg = msgs.find((m: any) => m.id === 'msg-2');
    expect(msg).toBeDefined();
    expect((msg!.parts as any[])[0].text).toBe('hello world');
  });

  it('removes a failed assistant placeholder by message id', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({ id: 'keep' }));
      result.current.addMessage(makeMsg({ id: 'remove-me' }));
    });

    await act(async () => {
      result.current.removeMessage('remove-me');
    });

    expect(result.current.messages.map(message => message.id)).toEqual(['keep']);
  });

  it('applies every known part update without waiting for an animation frame', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    const part = { id: 'part-batched', messageID: 'msg-batched', sessionID: 'sess-1', type: 'text', text: 'h' };

    await act(async () => {
      result.current.updateMessagePart(part);
    });
    expect((result.current.messages[0].parts as any[])[0].text).toBe('h');

    await act(async () => {
      result.current.updateMessagePart({ ...part, text: 'he' }, 'e');
    });
    expect((result.current.messages[0].parts as any[])[0].text).toBe('he');

    await act(async () => {
      result.current.updateMessagePart({ ...part, text: 'hel' }, 'l');
    });
    expect((result.current.messages[0].parts as any[])[0].text).toBe('hel');

    await act(async () => {
      result.current.updateMessagePart({ ...part, text: 'hello' }, 'lo');
    });
    expect((result.current.messages[0].parts as any[])[0].text).toBe('hello');
  });

  it('commits the final delta before an immediately following finish update', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    const part = {
      id: 'part-final',
      messageID: 'msg-final',
      sessionID: 'sess-1',
      type: 'text',
      text: 'almost',
    };
    await act(async () => {
      result.current.updateMessagePart(part);
    });

    await act(async () => {
      result.current.updateMessagePart({ ...part, text: 'almost done' }, ' done');
      result.current.updateMessage({
        id: 'msg-final',
        sessionID: 'sess-1',
        role: 'assistant',
        finish: 'stop',
      });
    });

    const message = result.current.messages.find((item) => item.id === 'msg-final');
    expect((message?.parts as any[])[0].text).toBe('almost done');
    expect(message?.finish).toBe('stop');
  });

  it('resets streamed messages when session changes', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id?: string }) => useSessionMessages(id),
      { initialProps: { id: 'sess-a' } },
    );
    // Wait for initial fetch to settle
    await act(async () => {});

    const part = { id: 'part-sess-a', messageID: 'msg-1', sessionID: 'sess-a', type: 'text', text: 'data' };

    await act(async () => {
      result.current.updateMessagePart(part);
    });

    // Switching sessions must clear streamed state before the next paint.
    await act(async () => {
      rerender({ id: 'sess-b' });
    });

    expect(result.current.messages).toHaveLength(0);
  });

  it('ignores a stale first-page response after switching sessions', async () => {
    let resolveSessionA: (value: unknown) => void = () => {};
    let resolveSessionB: (value: unknown) => void = () => {};
    vi.mocked(client.get).mockImplementation((url: string) => new Promise((resolve) => {
      if (url.includes('sess-a')) resolveSessionA = resolve;
      if (url.includes('sess-b')) resolveSessionB = resolve;
    }) as any);

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useSessionMessages(id),
      { initialProps: { id: 'sess-a' } },
    );
    await act(async () => {});

    rerender({ id: 'sess-b' });
    await act(async () => {});

    await act(async () => {
      resolveSessionB({
        data: [{
          info: {
            id: 'msg-b',
            sessionID: 'sess-b',
            role: 'assistant',
            time: { created: 200 },
          },
          parts: [],
        }],
      });
    });
    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-b']);

    await act(async () => {
      resolveSessionA({
        data: [{
          info: {
            id: 'msg-a',
            sessionID: 'sess-a',
            role: 'assistant',
            time: { created: 100 },
          },
          parts: [],
        }],
      });
    });

    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-b']);
    expect(result.current.loading).toBe(false);
  });

  it('replaceMessageText updates the targeted text part by partId', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-edit',
        role: 'user',
        parts: [
          { id: 'part-1', type: 'text', text: 'before-1' } as any,
          { id: 'part-2', type: 'text', text: 'before-2' } as any,
        ],
      }));
    });

    await act(async () => {
      result.current.replaceMessageText('msg-edit', 'part-2', 'after');
    });

    const msg = result.current.messages.find((item) => item.id === 'msg-edit');
    expect(msg).toBeDefined();
    expect((msg!.parts as any[])[0].text).toBe('before-1');
    expect((msg!.parts as any[])[1].text).toBe('after');
  });

  it('inserts late user metadata before an already streamed assistant child', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'old-assistant',
        role: 'assistant',
        parts: [{ id: 'old-text', type: 'text', text: 'old reply' } as any],
        finish: 'stop',
      } as any));
      result.current.updateMessagePart({
        id: 'new-text',
        messageID: 'new-assistant',
        sessionID: 'sess-1',
        type: 'text',
        text: 'new reply',
      });
      result.current.updateMessage({
        id: 'new-assistant',
        sessionID: 'sess-1',
        role: 'assistant',
        parentID: 'new-user',
        time: { created: 200 },
      });
      result.current.updateMessage({
        id: 'new-user',
        sessionID: 'sess-1',
        role: 'user',
        time: { created: 100 },
      });
    });

    expect(result.current.messages.map((msg) => msg.id)).toEqual([
      'old-assistant',
      'new-user',
      'new-assistant',
    ]);
    expect((result.current.messages[2].parts as any[])[0].text).toBe('new reply');
  });

  it('moves a replaced temp user before an already streamed assistant child', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.updateMessagePart({
        id: 'new-text',
        messageID: 'new-assistant',
        sessionID: 'sess-1',
        type: 'text',
        text: 'new reply',
      });
      result.current.updateMessage({
        id: 'new-assistant',
        sessionID: 'sess-1',
        role: 'assistant',
        parentID: 'new-user',
        time: { created: 200 },
      });
      result.current.addMessage(makeMsg({
        id: 'temp-user',
        role: 'user',
        parts: [{ id: 'temp-user-text', type: 'text', text: 'hello' } as any],
      }));
      result.current.updateMessage({
        id: 'new-user',
        sessionID: 'sess-1',
        role: 'user',
        time: { created: 100 },
      });
    });

    expect(result.current.messages.map((msg) => msg.id)).toEqual([
      'new-user',
      'new-assistant',
    ]);
    expect((result.current.messages[0].parts as any[])[0].text).toBe('hello');
    expect((result.current.messages[1].parts as any[])[0].text).toBe('new reply');
  });

  it('keeps one user message when refetch and delayed SSE reconcile its optimistic ID', async () => {
    const optimisticId = 'msg_000000000001abcdefghijklmn';
    vi.mocked(client.get).mockResolvedValueOnce({ data: [] });
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: optimisticId,
        role: 'user',
        timestamp: 100,
        parts: [{
          id: `temp-${optimisticId}-text`,
          type: 'text',
          text: '测试 question 工具',
        }] as Message['parts'],
      }));
    });

    vi.mocked(client.get).mockResolvedValueOnce({
      data: {
        items: [
          {
            info: {
              id: optimisticId,
              sessionID: 'sess-1',
              role: 'user',
              time: { created: 200 },
            },
            parts: [{
              id: 'user-real-text',
              messageID: optimisticId,
              sessionID: 'sess-1',
              type: 'text',
              text: '测试 question 工具',
            }],
          },
          {
            info: {
              id: 'assistant-question',
              sessionID: 'sess-1',
              role: 'assistant',
              parentID: optimisticId,
              time: { created: 201 },
            },
            parts: [{
              id: 'question-part',
              messageID: 'assistant-question',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'question',
              callID: 'question-call',
              state: { status: 'running' },
            }],
          },
        ],
        hasMore: false,
        nextBefore: null,
      },
    });

    await act(async () => {
      await result.current.refetch();
      result.current.updateMessage({
        id: optimisticId,
        sessionID: 'sess-1',
        role: 'user',
        time: { created: 200 },
      });
    });

    expect(result.current.messages.filter((message) => message.role === 'user'))
      .toHaveLength(1);
    expect(result.current.messages.map((message) => message.id)).toEqual([
      optimisticId,
      'assistant-question',
    ]);
    expect(result.current.messages[0].parts.map((part) => part.id)).toEqual([
      'user-real-text',
    ]);
  });

  it('truncateAfterMessage keeps the target by default', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({ id: 'msg-1', role: 'user' }));
      result.current.addMessage(makeMsg({ id: 'msg-2', role: 'assistant' }));
      result.current.addMessage(makeMsg({ id: 'msg-3', role: 'assistant' }));
    });

    await act(async () => {
      result.current.truncateAfterMessage('msg-2');
    });

    expect(result.current.messages.map((msg) => msg.id)).toEqual(['msg-1', 'msg-2']);
  });

  it('truncateAfterMessage can also remove the target message', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({ id: 'msg-1', role: 'user' }));
      result.current.addMessage(makeMsg({ id: 'msg-2', role: 'assistant' }));
      result.current.addMessage(makeMsg({ id: 'msg-3', role: 'assistant' }));
    });

    await act(async () => {
      result.current.truncateAfterMessage('msg-2', { includeTarget: true });
    });

    expect(result.current.messages.map((msg) => msg.id)).toEqual(['msg-1']);
  });

  it('markMessageStopped keeps partial text and freezes running tools', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-stop',
        role: 'assistant',
        parts: [
          { id: 'reason-1', type: 'reasoning', text: '分析中' } as any,
          {
            id: 'tool-1',
            type: 'tool',
            state: {
              status: 'running',
              title: 'Read file',
              time: { start: 100 },
            },
          } as any,
          { id: 'text-1', type: 'text', text: '已经输出一半' } as any,
        ],
      }));
    });

    await act(async () => {
      result.current.markMessageStopped('msg-stop');
    });

    const msg = result.current.messages.find((item) => item.id === 'msg-stop');
    expect(msg?.finish).toBe('stop');
    expect((msg?.parts as any[])[2].text).toBe('已经输出一半');
    expect((msg?.parts as any[])[1].state.status).toBe('error');
    expect((msg?.parts as any[])[1].state.error).toBe('Tool execution was interrupted');
    expect((msg?.parts as any[])[1].state.time.end).toBeDefined();
  });

  it('refetch preserves locally stopped assistant content when backend snapshot is weaker', async () => {
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-stop',
        role: 'assistant',
        parts: [
          { id: 'tool-1', type: 'tool', state: { status: 'completed', output: 'ok' } } as any,
          { id: 'text-1', type: 'text', text: '保留这段已输出文本' } as any,
        ],
      }));
      result.current.markMessageStopped('msg-stop');
    });

    vi.mocked(client.get).mockResolvedValueOnce({
      data: [{
        info: {
          id: 'msg-stop',
          sessionID: 'sess-1',
          role: 'assistant',
          time: { created: 123 },
        },
        parts: [],
      }],
    } as any);

    await act(async () => {
      await result.current.refetch();
    });

    const msg = result.current.messages.find((item) => item.id === 'msg-stop');
    expect(msg?.finish).toBe('stop');
    expect(msg?.parts).toHaveLength(2);
    expect((msg?.parts as any[])[1].text).toBe('保留这段已输出文本');
    expect((msg?.parts as any[])[0].state.status).toBe('completed');
  });

  it('fetches the latest bounded message page in one request', async () => {
    vi.mocked(client.get).mockResolvedValueOnce({
      data: [
        {
          info: {
            id: 'msg-old',
            sessionID: 'sess-1',
            role: 'user',
            time: { created: 100 },
          },
          parts: [{ id: 'part-old', type: 'text', text: 'old' }],
        },
        {
          info: {
            id: 'msg-new',
            sessionID: 'sess-1',
            role: 'assistant',
            time: { created: 200 },
          },
          parts: [],
        },
      ],
    } as any);

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    expect(result.current.messages.map((msg) => msg.id)).toEqual(['msg-old', 'msg-new']);
    expect(client.get).toHaveBeenCalledOnce();
    expect(client.get).toHaveBeenCalledWith('/api/session/sess-1/message', {
      params: { page: true, limit: 50, include_archived: true },
    });
  });

  it('keeps the oldest loaded cursor across a latest-page refresh', async () => {
    const row = (n: number) => ({ info: { id: `msg-${n}`, sessionID: 'sess-1', role: 'user', time: { created: n } }, parts: [] });
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: { items: [row(5), row(6)], hasMore: true, nextBefore: 'msg-5' } })
      .mockResolvedValueOnce({ data: { items: [row(3), row(4)], hasMore: true, nextBefore: 'msg-3', hasNewer: true, nextAfter: 'msg-4' } })
      .mockResolvedValueOnce({ data: { items: [row(5), row(6)], hasMore: true, nextBefore: 'msg-5' } })
      .mockResolvedValueOnce({ data: { items: [row(1), row(2)], hasMore: false, nextBefore: null, hasNewer: true, nextAfter: 'msg-2' } });
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    await act(async () => { await result.current.loadOlder(); });
    await act(async () => { await result.current.refetch(); });
    await act(async () => { await result.current.loadOlder(); });
    expect(vi.mocked(client.get).mock.calls[3][1]?.params).toMatchObject({ before: 'msg-3' });
    expect(result.current.messages.map(item => item.id)).toEqual(['msg-1', 'msg-2', 'msg-3', 'msg-4', 'msg-5', 'msg-6']);
    expect(result.current.hasOlder).toBe(false);
    expect(result.current.hasNewer).toBe(false);
  });

  it('loads an absent search target and keeps incoming tail events outside a historical window', async () => {
    const row = (n: number) => ({ info: { id: `msg-${n}`, sessionID: 'sess-1', role: 'user', time: { created: n } }, parts: [] });
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: { items: [row(99)], hasMore: true, nextBefore: 'msg-99' } })
      .mockResolvedValueOnce({ data: { items: [row(10), row(11)], hasMore: true, nextBefore: 'msg-10', hasNewer: true, nextAfter: 'msg-11' } })
      .mockResolvedValueOnce({ data: { items: [row(12)], hasMore: true, nextBefore: 'msg-12', hasNewer: true, nextAfter: 'msg-12' } })
      .mockResolvedValueOnce({ data: { items: [row(99), row(100)], hasMore: true, nextBefore: 'msg-99' } });
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    await act(async () => { await result.current.focusMessage('msg-10'); });
    expect(vi.mocked(client.get).mock.calls[1][1]?.params).toMatchObject({ around: 'msg-10' });
    act(() => result.current.updateMessage({ id: 'msg-100', sessionID: 'sess-1', role: 'assistant', time: { created: 100 } }));
    expect(result.current.messages.map(item => item.id)).toEqual(['msg-10', 'msg-11']);
    await act(async () => { await result.current.loadNewer(); });
    expect(result.current.messages.map(item => item.id)).toEqual(['msg-10', 'msg-11', 'msg-12']);
    await act(async () => { await result.current.loadLatest(); });
    expect(result.current.messages.map(item => item.id)).toEqual(['msg-99', 'msg-100']);
    expect(result.current.hasNewer).toBe(false);
  });

  it('deduplicates in-flight older pages and ignores them after switching sessions', async () => {
    const pending = deferred<any>();
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: { items: [{ info: { id: 'old', role: 'user', time: { created: 5 } }, parts: [] }], hasMore: true, nextBefore: 'old' } })
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce({ data: { items: [{ info: { id: 'new', role: 'user', time: { created: 6 } }, parts: [] }], hasMore: false } });
    const { result, rerender } = renderHook(({ id }) => useSessionMessages(id), { initialProps: { id: 'sess-1' } });
    await act(async () => {});
    act(() => { void result.current.loadOlder(); void result.current.loadOlder(); });
    expect(client.get).toHaveBeenCalledTimes(2);
    rerender({ id: 'sess-2' });
    await act(async () => {});
    await act(async () => { pending.resolve({ data: { items: [], hasMore: false } }); });
    expect(result.current.messages.map(item => item.id)).toEqual(['new']);
    expect(result.current.loadingOlder).toBe(false);
  });

  it('removes messages that are absent from a complete refetch', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce({
        data: [{
          info: {
            id: 'msg-before-clear',
            sessionID: 'sess-1',
            role: 'assistant',
            time: { created: 100 },
          },
          parts: [{ id: 'part-before-clear', type: 'text', text: 'old result' }],
        }],
      } as any)
      .mockResolvedValueOnce({ data: [] });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-before-clear']);

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.messages).toEqual([]);
  });

  it('keeps the current history when a complete refetch fails', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce({
        data: [{
          info: {
            id: 'msg-before-error',
            sessionID: 'sess-1',
            role: 'assistant',
            time: { created: 100 },
          },
          parts: [{ id: 'part-before-error', type: 'text', text: 'keep me' }],
        }],
      } as any)
      .mockRejectedValueOnce(new Error('storage unavailable'));

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-before-error']);
    expect(result.current.error).toBe('storage unavailable');
  });

  it('keeps an optimistic local message until the complete history confirms it', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [] });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-optimistic',
        role: 'user',
        parts: [{ id: 'temp-msg-optimistic-text', type: 'text', text: 'hello' } as any],
      }));
      await result.current.refetch();
    });

    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-optimistic']);
  });

  it('keeps an optimistic message when an older refetch resolves after SSE confirmation', async () => {
    const olderRefetch = deferred<any>();
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: [] })
      .mockReturnValueOnce(olderRefetch.promise);

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-racing',
        role: 'user',
        parts: [{ id: 'temp-msg-racing-text', type: 'text', text: 'hello' } as any],
      }));
    });

    let refetchPromise: Promise<void> = Promise.resolve();
    act(() => {
      refetchPromise = result.current.refetch();
    });
    expect(client.get).toHaveBeenCalledTimes(2);

    act(() => {
      result.current.updateMessage({
        id: 'msg-racing',
        sessionID: 'sess-1',
        role: 'user',
        time: { created: 200 },
      });
    });

    await act(async () => {
      olderRefetch.resolve({ data: [] });
      await refetchPromise;
    });

    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-racing']);
  });

  it('keeps newer SSE message state when an older complete refetch resolves', async () => {
    const olderRefetch = deferred<any>();
    const staleMessage = {
      info: {
        id: 'msg-existing',
        sessionID: 'sess-1',
        role: 'assistant',
        time: { created: 100 },
      },
      parts: [{
        id: 'part-existing',
        messageID: 'msg-existing',
        sessionID: 'sess-1',
        type: 'text',
        text: 'stale text',
      }],
    };
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: [staleMessage] })
      .mockReturnValueOnce(olderRefetch.promise);

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    let refetchPromise: Promise<void> = Promise.resolve();
    act(() => {
      refetchPromise = result.current.refetch();
    });

    act(() => {
      result.current.updateMessagePart({
        id: 'part-existing',
        messageID: 'msg-existing',
        sessionID: 'sess-1',
        type: 'text',
        text: 'new streamed text',
      }, 'new streamed text');
      result.current.updateMessage({
        id: 'msg-new-assistant',
        sessionID: 'sess-1',
        role: 'assistant',
        time: { created: 200 },
      });
    });

    await act(async () => {
      olderRefetch.resolve({ data: [staleMessage] });
      await refetchPromise;
    });

    expect(result.current.messages.map((message) => message.id)).toEqual([
      'msg-existing',
      'msg-new-assistant',
    ]);
    expect((result.current.messages[0].parts as any[])[0].text).toBe('new streamed text');
  });

  it('clears optimistic messages before applying an empty session history', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [] });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    await act(async () => {
      result.current.addMessage(makeMsg({
        id: 'msg-pending-clear',
        role: 'user',
        parts: [{ id: 'temp-msg-pending-clear-text', type: 'text', text: 'hello' } as any],
      }));
      result.current.clearMessages();
      await result.current.refetch();
    });

    expect(result.current.messages).toEqual([]);
  });

  it('keeps messages empty when a request started before clear resolves late', async () => {
    const pendingRequest = deferred<any>();
    vi.mocked(client.get).mockReturnValueOnce(pendingRequest.promise);

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    expect(result.current.loading).toBe(true);

    act(() => {
      result.current.clearMessages();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);

    await act(async () => {
      pendingRequest.resolve({
        data: [{
          info: {
            id: 'msg-deleted-before-response',
            sessionID: 'sess-1',
            role: 'assistant',
            time: { created: 100 },
          },
          parts: [{ id: 'part-deleted-before-response', type: 'text', text: 'stale' }],
        }],
      });
      await pendingRequest.promise;
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('resets a previous loading error when messages are cleared', async () => {
    vi.mocked(client.get).mockRejectedValueOnce(new Error('storage unavailable'));

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    expect(result.current.error).toBe('storage unavailable');

    act(() => {
      result.current.clearMessages();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(client.get).toHaveBeenCalledOnce();
  });

});

describe('message page refresh and deletion races', () => {
  const row = (n: number) => ({
    info: { id: `msg-${n}`, sessionID: 'sess-1', role: 'user', time: { created: n } }, parts: [],
  });
  const page = (first: number, last: number, total: number) => ({ data: {
    items: Array.from({ length: last - first + 1 }, (_, index) => row(first + index)),
    hasMore: first > 1, nextBefore: first > 1 ? `msg-${first}` : null,
    hasNewer: last < total, nextAfter: last < total ? `msg-${last}` : null,
  } });

  afterEach(() => {
    vi.mocked(client.get).mockReset();
    vi.mocked(client.get).mockResolvedValue({ data: [] });
  });

  it('restarts a contiguous latest window after reconnect misses more than one page', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce(page(1, 50, 50))
      .mockResolvedValueOnce(page(61, 110, 110))
      .mockResolvedValueOnce(page(11, 60, 110))
      .mockResolvedValueOnce(page(1, 10, 110));
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    await act(async () => { await result.current.refetch(); });
    expect(result.current.hasOlder).toBe(true);
    expect(result.current.messages.map(message => message.id)).toEqual(
      Array.from({ length: 50 }, (_, index) => `msg-${index + 61}`),
    );
    await act(async () => { await result.current.loadOlder(); });
    expect(vi.mocked(client.get).mock.calls[2][1]?.params).toMatchObject({ before: 'msg-61' });
    await act(async () => { await result.current.loadOlder(); });
    expect(result.current.messages.map(message => message.id)).toEqual(
      Array.from({ length: 110 }, (_, index) => `msg-${index + 1}`),
    );
    expect(result.current.hasOlder).toBe(false);
  });

  it('keeps a newer older-page request pending when a superseded response arrives', async () => {
    const stale = deferred<any>();
    const current = deferred<any>();
    vi.mocked(client.get)
      .mockResolvedValueOnce(page(51, 100, 100))
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(page(151, 200, 200))
      .mockReturnValueOnce(current.promise);
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    act(() => { void result.current.loadOlder(); });
    await act(async () => { await result.current.refetch(); });
    act(() => { void result.current.loadOlder(); });
    expect(client.get).toHaveBeenCalledTimes(4);
    await act(async () => { stale.resolve(page(1, 50, 100)); });
    expect(result.current.loadingOlder).toBe(true);
    expect(result.current.messages[0].id).toBe('msg-151');
    await act(async () => { current.resolve(page(101, 150, 200)); });
    expect(result.current.loadingOlder).toBe(false);
    expect(result.current.messages).toHaveLength(100);
  });

  it('does not mistake a newly received SSE tail message for contiguous loaded history', async () => {
    vi.mocked(client.get).mockResolvedValueOnce(page(1, 50, 50)).mockResolvedValueOnce(page(61, 110, 110));
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    act(() => result.current.updateMessage(row(110).info));
    await act(async () => { await result.current.refetch(); });
    expect(result.current.hasOlder).toBe(true);
    expect(result.current.messages).toHaveLength(50);
    expect(result.current.messages[0].id).toBe('msg-61');
  });

  it('does not resurrect a deleted message from an older refresh snapshot', async () => {
    const stale = deferred<any>();
    vi.mocked(client.get).mockResolvedValueOnce(page(1, 2, 2)).mockReturnValueOnce(stale.promise);
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    act(() => { void result.current.refetch(); });
    act(() => result.current.removeMessage('msg-2'));
    expect(result.current.messages.map(message => message.id)).toEqual(['msg-1']);
    await act(async () => { stale.resolve(page(1, 2, 2)); });
    expect(result.current.messages.map(message => message.id)).toEqual(['msg-1']);
  });

  it('retains deletion protection until both concurrent directional pages settle', async () => {
    const older = deferred<any>();
    const newer = deferred<any>();
    vi.mocked(client.get).mockResolvedValueOnce(page(5, 6, 10))
      .mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    act(() => { void result.current.loadOlder(); void result.current.loadNewer(); });
    act(() => { result.current.removeMessage('msg-4'); result.current.removeMessage('msg-7'); });
    await act(async () => { newer.resolve(page(7, 10, 10)); });
    await act(async () => { older.resolve(page(1, 4, 10)); });
    expect(result.current.messages.map(message => message.id)).toEqual(
      ['msg-1', 'msg-2', 'msg-3', 'msg-5', 'msg-6', 'msg-8', 'msg-9', 'msg-10'],
    );
  });

  it('allows a failed older page to be retried without losing the current window', async () => {
    vi.mocked(client.get).mockResolvedValueOnce(page(3, 4, 4))
      .mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(page(1, 2, 4));
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    await act(async () => { await result.current.loadOlder(); });
    expect(result.current.error).toBe('offline');
    expect(result.current.messages).toHaveLength(2);
    await act(async () => { await result.current.loadOlder(); });
    expect(result.current.error).toBeNull();
    expect(result.current.messages).toHaveLength(4);
  });

  it('keeps SSE update protection when a later page finishes before an earlier refresh', async () => {
    const refresh = deferred<any>();
    const older = deferred<any>();
    vi.mocked(client.get).mockResolvedValueOnce(page(5, 6, 6))
      .mockReturnValueOnce(refresh.promise).mockReturnValueOnce(older.promise);
    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});
    act(() => { void result.current.refetch(); });
    act(() => result.current.updateMessagePart({
      id: 'part-6', messageID: 'msg-6', sessionID: 'sess-1', type: 'text', text: 'new stream content',
    }));
    act(() => { void result.current.loadOlder(); });
    await act(async () => { older.resolve(page(1, 4, 6)); });
    await act(async () => { refresh.resolve(page(5, 6, 6)); });
    expect(result.current.messages.find(message => message.id === 'msg-6')?.parts[0]?.text).toBe('new stream content');
  });
});

describe('useSessions list loading', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('uses the lightweight session manager list endpoint', async () => {
    vi.mocked(sessionApi.list).mockResolvedValueOnce([{
      id: 'session-1',
      title: 'Session',
      time: { created: 1, updated: 2 },
      category: 'user',
    }] as any);

    const { result } = renderHook(() => useSessions('triage'));
    await act(async () => {});

    expect(sessionApi.list).toHaveBeenCalledWith({
      view: 'list',
      manager: true,
      roots: true,
      limit: 100,
      offset: 0,
      search: 'triage',
    });
    expect(result.current.sessions).toHaveLength(1);
  });

  it('keeps an optimistically added session when an older list request returns without it', async () => {
    let resolveList: (value: any[]) => void = () => {};
    vi.mocked(sessionApi.list).mockReturnValueOnce(new Promise((resolve) => {
      resolveList = resolve;
    }) as any);

    const { result } = renderHook(() => useSessions());

    act(() => {
      result.current.addSession({
        id: 'session-new',
        title: 'New Session',
        time: { created: 2, updated: 2 },
        category: 'user',
      } as any);
    });

    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-new']);

    await act(async () => {
      resolveList([{
        id: 'session-old',
        title: 'Old Session',
        time: { created: 1, updated: 1 },
        category: 'user',
      }]);
    });

    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-new', 'session-old']);
  });

  it('preserves the current list when a background refetch fails', async () => {
    vi.mocked(sessionApi.list)
      .mockResolvedValueOnce([{
        id: 'session-1',
        title: 'Session',
        time: { created: 1, updated: 2 },
        category: 'user',
      }] as any)
      .mockRejectedValueOnce(new Error('network down'));

    const { result } = renderHook(() => useSessions());
    await act(async () => {});

    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.error).toBe('network down');
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);
  });

  it('keeps the page mounted while refetching after search changes', async () => {
    let resolveSearch: (value: any[]) => void = () => {};
    vi.mocked(sessionApi.list)
      .mockResolvedValueOnce([{
        id: 'session-1',
        title: 'Session',
        time: { created: 1, updated: 2 },
        category: 'user',
      }] as any)
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveSearch = resolve;
      }) as any);

    const { result, rerender } = renderHook(
      ({ search }) => useSessions(search),
      { initialProps: { search: '' } },
    );
    await act(async () => {});

    expect(result.current.loading).toBe(false);
    expect(result.current.refreshing).toBe(false);
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);

    rerender({ search: 'triage' });

    expect(result.current.loading).toBe(false);
    expect(result.current.refreshing).toBe(true);
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);

    await act(async () => {
      resolveSearch([]);
    });
    expect(result.current.refreshing).toBe(false);
  });

  it('ignores an older search response after the current filter has completed', async () => {
    const olderSearch = deferred<any>();
    vi.mocked(sessionApi.list)
      .mockReturnValueOnce(olderSearch.promise)
      .mockResolvedValueOnce([{ id: 'current-result', title: 'Current', category: 'user', time: { created: 2, updated: 2 } }] as any);
    const { result, rerender } = renderHook(({ search }) => useSessions(search), { initialProps: { search: 'old' } });
    await act(async () => {});
    rerender({ search: 'current' });
    await act(async () => {});
    expect(result.current.sessions.map(session => session.id)).toEqual(['current-result']);
    await act(async () => {
      olderSearch.resolve([{ id: 'old-result', title: 'Old', category: 'user', time: { created: 1, updated: 1 } }]);
    });
    expect(result.current.sessions.map(session => session.id)).toEqual(['current-result']);
  });

  it('loads and tracks pages independently for each project', async () => {
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => {
      if (params.projectID === 'default') {
        return Array.from({ length: 100 }, (_, index) => ({
          id: `default-${index}`,
          projectID: 'legacy-project',
          effectiveProjectID: 'default',
          title: `Default ${index}`,
          time: { created: index, updated: index },
          category: 'user',
        })) as any;
      }
      return [{
        id: 'labs-1',
        projectID: 'prj_labs',
        effectiveProjectID: 'prj_labs',
        title: 'Labs',
        time: { created: 1, updated: 1 },
        category: 'user',
      }] as any;
    });

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
    }));
    await act(async () => {});

    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'default',
      offset: 0,
    }));
    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      offset: 0,
    }));
    expect(result.current.sessions).toHaveLength(101);
    expect(result.current.hasMoreByProject).toEqual({
      default: true,
      prj_labs: false,
    });
  });

  it.each([undefined, 50])('uses the initial page size and load-more size %s for project pagination', async (loadMorePageSize) => {
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => (
      Array.from({ length: params.limit }, (_, index) => ({
        id: `${params.projectID}-${params.offset + index}`,
        projectID: params.projectID,
        effectiveProjectID: params.projectID,
        title: `Session ${params.offset + index}`,
        time: { created: index, updated: index },
        category: 'user',
      })) as any
    ));

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
      pageSize: 6,
      loadMorePageSize,
    }));
    await act(async () => {});

    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'default',
      limit: 6,
      offset: 0,
    }));
    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      limit: 6,
      offset: 0,
    }));
    expect(result.current.sessions).toHaveLength(12);
    expect(result.current.hasMoreByProject).toEqual({
      default: true,
      prj_labs: true,
    });

    await act(async () => {
      await result.current.loadMore('prj_labs');
    });

    expect(sessionApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      limit: loadMorePageSize ?? 6,
      offset: 6,
    }));
    await act(async () => { await result.current.loadMore('prj_labs'); });
    expect(sessionApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      limit: loadMorePageSize ?? 6,
      offset: 6 + (loadMorePageSize ?? 6),
    }));
    const projectDepth = 6 + 2 * (loadMorePageSize ?? 6);
    expect(result.current.sessions).toHaveLength(6 + projectDepth);
    await act(async () => { await result.current.refetch(); });
    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'prj_labs', limit: projectDepth, offset: 0,
    }));
    expect(result.current.sessions).toHaveLength(6 + projectDepth);
  });

  it('preserves each project loaded depth during a background refetch', async () => {
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => (
      Array.from({ length: params.limit }, (_, index) => ({
        id: `${params.projectID}-${params.offset + index}`,
        projectID: params.projectID,
        effectiveProjectID: params.projectID,
        title: `Session ${params.offset + index}`,
        time: { created: index, updated: index },
        category: 'user',
      })) as any
    ));

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
      pageSize: 6,
    }));
    await act(async () => {});

    await act(async () => {
      await result.current.loadMore('prj_labs');
    });
    expect(result.current.sessions).toHaveLength(18);

    await act(async () => {
      await result.current.refetch();
    });

    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'default',
      limit: 6,
      offset: 0,
    }));
    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      limit: 12,
      offset: 0,
    }));
    expect(result.current.sessions).toHaveLength(18);
  });

  it('uses the selected project offset when loading more sessions', async () => {
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => {
      if (params.offset === 0) {
        return [{
          id: `${params.projectID}-1`,
          projectID: params.projectID,
          effectiveProjectID: params.projectID,
          title: 'First page',
          time: { created: 1, updated: 1 },
          category: 'user',
        }] as any;
      }
      return [{
        id: `${params.projectID}-2`,
        projectID: params.projectID,
        effectiveProjectID: params.projectID,
        title: 'Second page',
        time: { created: 2, updated: 2 },
        category: 'user',
      }] as any;
    });

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
    }));
    await act(async () => {});

    await act(async () => {
      await result.current.loadMore('prj_labs');
    });

    expect(sessionApi.list).toHaveBeenLastCalledWith(expect.objectContaining({
      projectID: 'prj_labs',
      offset: 1,
    }));
    expect(result.current.sessions.map((item) => item.id)).toEqual([
      'default-1',
      'prj_labs-1',
      'prj_labs-2',
    ]);
  });

  it('loads more sessions for different projects concurrently', async () => {
    const defaultPage = deferred<any[]>();
    const labsPage = deferred<any[]>();
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => {
      if (params.offset === 0) {
        return [sessionListItem(`${params.projectID}-1`, params.projectID)] as any;
      }
      return params.projectID === 'default' ? defaultPage.promise : labsPage.promise;
    });

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
      pageSize: 1,
    }));
    await act(async () => {});

    let loadDefault!: Promise<void>;
    let loadLabs!: Promise<void>;
    act(() => {
      loadDefault = result.current.loadMore('default');
      loadLabs = result.current.loadMore('prj_labs');
    });
    expect(result.current.loadingMoreProjectIds).toEqual(new Set(['default', 'prj_labs']));

    await act(async () => {
      defaultPage.resolve([sessionListItem('default-2', 'default', 2)] as any);
      await loadDefault;
    });
    expect(result.current.loadingMoreProjectIds).toEqual(new Set(['prj_labs']));

    await act(async () => {
      labsPage.resolve([sessionListItem('prj_labs-2', 'prj_labs', 2)] as any);
      await loadLabs;
    });
    expect(result.current.loadingMoreProjectIds).toEqual(new Set());
    expect(result.current.sessions.map((item) => item.id)).toEqual([
      'default-1',
      'prj_labs-1',
      'default-2',
      'prj_labs-2',
    ]);
  });

  it('clears project pagination state when a background refresh supersedes it', async () => {
    const stalePage = deferred<any[]>();
    vi.mocked(sessionApi.list).mockImplementation(async (params: any) => {
      if (params.offset > 0) return stalePage.promise;
      return [sessionListItem(`${params.projectID}-1`, params.projectID)] as any;
    });

    const { result } = renderHook(() => useSessions('', {
      projectIds: ['default', 'prj_labs'],
      pageSize: 1,
    }));
    await act(async () => {});

    let loadMore!: Promise<void>;
    act(() => {
      loadMore = result.current.loadMore('default');
    });
    expect(result.current.loadingMoreProjectIds).toEqual(new Set(['default']));

    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.loadingMoreProjectIds).toEqual(new Set());

    await act(async () => {
      stalePage.resolve([sessionListItem('default-stale', 'default', 2)] as any);
      await loadMore;
    });
    expect(result.current.sessions.map((item) => item.id)).not.toContain('default-stale');
    expect(result.current.loadingMoreProjectIds).toEqual(new Set());
  });
});


describe('model activity part updates', () => {
  it('retains timing through SSE replacement and a serialized reload', () => {
    const start = { id: 'model-clock', messageID: 'msg-1', sessionID: 'sess-1', type: 'step-start', time: { start: 1000 } };
    const initial = [makeMsg({ id: 'msg-1', parts: [] })];
    const opened = applyMessagePartUpdate(initial, start);
    const closed = applyMessagePartUpdate(opened, { ...start, time: { start: 1000, end: 11000 } });
    expect(opened[0].parts[0].time).toEqual({ start: 1000 });
    expect(JSON.parse(JSON.stringify(closed))[0].parts).toEqual([{ ...start, time: { start: 1000, end: 11000 } }]);
  });
});
