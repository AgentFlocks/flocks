import { describe, expect, it, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { applyMessagePartUpdate, useSessionMessages, useSessions } from './useSessions';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import type { Message } from '@/types';

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

  it('ignores an older-page response after switching sessions', async () => {
    let resolveOlderSessionA: (value: unknown) => void = () => {};
    vi.mocked(client.get).mockImplementation((url: string, config?: any) => {
      if (url.includes('sess-a') && config?.params?.before) {
        return new Promise((resolve) => {
          resolveOlderSessionA = resolve;
        }) as any;
      }
      if (url.includes('sess-a')) {
        return Promise.resolve({
          data: {
            items: [{
              info: {
                id: 'msg-a-new',
                sessionID: 'sess-a',
                role: 'assistant',
                time: { created: 200 },
              },
              parts: [],
            }],
            hasMore: true,
            nextBefore: 'msg-a-new',
          },
        }) as any;
      }
      return Promise.resolve({
        data: [{
          info: {
            id: 'msg-b',
            sessionID: 'sess-b',
            role: 'assistant',
            time: { created: 300 },
          },
          parts: [],
        }],
      }) as any;
    });

    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useSessionMessages(id),
      { initialProps: { id: 'sess-a' } },
    );
    await act(async () => {});

    let olderRequest: Promise<void> = Promise.resolve();
    act(() => {
      olderRequest = result.current.loadOlder();
    });

    rerender({ id: 'sess-b' });
    await act(async () => {});
    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-b']);

    await act(async () => {
      resolveOlderSessionA({
        data: {
          items: [{
            info: {
              id: 'msg-a-old',
              sessionID: 'sess-a',
              role: 'user',
              time: { created: 100 },
            },
            parts: [],
          }],
          hasMore: false,
          nextBefore: null,
        },
      });
      await olderRequest;
    });

    expect(result.current.messages.map((message) => message.id)).toEqual(['msg-b']);
    expect(result.current.loadingOlder).toBe(false);
  });

  it('clears older-page loading when a first-page refetch invalidates it', async () => {
    let resolveOlderPage: (value: unknown) => void = () => {};
    let firstPageRequestCount = 0;
    vi.mocked(client.get).mockImplementation((_url: string, config?: any) => {
      if (config?.params?.before) {
        return new Promise((resolve) => {
          resolveOlderPage = resolve;
        }) as any;
      }

      firstPageRequestCount += 1;
      const messageId = firstPageRequestCount === 1 ? 'msg-current' : 'msg-refreshed';
      return Promise.resolve({
        data: {
          items: [{
            info: {
              id: messageId,
              sessionID: 'sess-1',
              role: 'assistant',
              time: { created: 200 + firstPageRequestCount },
            },
            parts: [],
          }],
          hasMore: true,
          nextBefore: messageId,
        },
      }) as any;
    });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    let olderRequest: Promise<void> = Promise.resolve();
    act(() => {
      olderRequest = result.current.loadOlder();
    });
    expect(result.current.loadingOlder).toBe(true);

    await act(async () => {
      await result.current.refetch();
    });
    expect(result.current.loadingOlder).toBe(false);
    expect(result.current.messages.map((message) => message.id)).toEqual([
      'msg-current',
      'msg-refreshed',
    ]);

    await act(async () => {
      resolveOlderPage({
        data: {
          items: [{
            info: {
              id: 'msg-stale-older',
              sessionID: 'sess-1',
              role: 'user',
              time: { created: 100 },
            },
            parts: [],
          }],
          hasMore: false,
          nextBefore: null,
        },
      });
      await olderRequest;
    });

    expect(result.current.loadingOlder).toBe(false);
    expect(result.current.messages.map((message) => message.id)).toEqual([
      'msg-current',
      'msg-refreshed',
    ]);
  });

  it('does not start an older-page request while a first-page refetch is pending', async () => {
    let resolveRefetch: (value: unknown) => void = () => {};
    let firstPageRequests = 0;
    let olderPageRequests = 0;
    vi.mocked(client.get).mockImplementation((_url: string, config?: any) => {
      if (config?.params?.before) {
        olderPageRequests += 1;
        return Promise.resolve({ data: { items: [], hasMore: false, nextBefore: null } }) as any;
      }

      firstPageRequests += 1;
      if (firstPageRequests === 1) {
        return Promise.resolve({
          data: {
            items: [{
              info: {
                id: 'msg-current',
                sessionID: 'sess-1',
                role: 'assistant',
                time: { created: 200 },
              },
              parts: [],
            }],
            hasMore: true,
            nextBefore: 'old-cursor',
          },
        }) as any;
      }

      return new Promise((resolve) => {
        resolveRefetch = resolve;
      }) as any;
    });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    let refetchRequest: Promise<void> = Promise.resolve();
    act(() => {
      refetchRequest = result.current.refetch();
    });
    await act(async () => {
      await result.current.loadOlder();
    });

    expect(olderPageRequests).toBe(0);

    await act(async () => {
      resolveRefetch({
        data: {
          items: [{
            info: {
              id: 'msg-refreshed',
              sessionID: 'sess-1',
              role: 'assistant',
              time: { created: 300 },
            },
            parts: [],
          }],
          hasMore: true,
          nextBefore: 'new-cursor',
        },
      });
      await refetchRequest;
    });

    expect(result.current.loading).toBe(false);
    expect(olderPageRequests).toBe(0);
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

  it('fetches the first message page and prepends older messages', async () => {
    vi.mocked(client.get)
      .mockResolvedValueOnce({
        data: {
          items: [{
            info: {
              id: 'msg-new',
              sessionID: 'sess-1',
              role: 'assistant',
              time: { created: 200 },
            },
            parts: [],
          }],
          hasMore: true,
          nextBefore: 'msg-new',
        },
      } as any)
      .mockResolvedValueOnce({
        data: {
          items: [{
            info: {
              id: 'msg-old',
              sessionID: 'sess-1',
              role: 'user',
              time: { created: 100 },
              model: { providerID: 'openai', modelID: 'gpt-4o' },
            },
            parts: [{ id: 'part-old', type: 'text', text: 'old' }],
          }],
          hasMore: false,
          nextBefore: null,
        },
      } as any);

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    expect(result.current.messages.map((msg) => msg.id)).toEqual(['msg-new']);
    expect(result.current.hasMore).toBe(true);
    expect(client.get).toHaveBeenCalledWith('/api/session/sess-1/message', {
      params: { page: true, limit: 50, include_archived: true },
    });

    await act(async () => {
      await result.current.loadOlder();
    });

    expect(result.current.messages.map((msg) => msg.id)).toEqual(['msg-old', 'msg-new']);
    expect(result.current.hasMore).toBe(false);
    expect(client.get).toHaveBeenLastCalledWith('/api/session/sess-1/message', {
      params: { page: true, limit: 50, before: 'msg-new', include_archived: true },
    });
  });

  it('starts only one older-page request when loadOlder is called twice before rerender', async () => {
    let resolveOlderPage: (value: unknown) => void = () => {};
    let olderPageRequests = 0;
    vi.mocked(client.get).mockImplementation((_url: string, config?: any) => {
      if (config?.params?.before) {
        olderPageRequests += 1;
        return new Promise((resolve) => {
          resolveOlderPage = resolve;
        }) as any;
      }

      return Promise.resolve({
        data: {
          items: [],
          hasMore: true,
          nextBefore: 'older-cursor',
        },
      }) as any;
    });

    const { result } = renderHook(() => useSessionMessages('sess-1'));
    await act(async () => {});

    let firstRequest: Promise<void> = Promise.resolve();
    let secondRequest: Promise<void> = Promise.resolve();
    act(() => {
      firstRequest = result.current.loadOlder();
      secondRequest = result.current.loadOlder();
    });

    expect(olderPageRequests).toBe(1);

    await act(async () => {
      resolveOlderPage({
        data: {
          items: [],
          hasMore: false,
          nextBefore: null,
        },
      });
      await Promise.all([firstRequest, secondRequest]);
    });
    expect(result.current.loadingOlder).toBe(false);
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
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);

    rerender({ search: 'triage' });

    expect(result.current.loading).toBe(false);
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session-1']);

    await act(async () => {
      resolveSearch([]);
    });
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

  it('uses a custom page size for project session pagination', async () => {
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
      limit: 6,
      offset: 6,
    }));
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
});
