import { describe, expect, it, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { applyMessagePartUpdate, useSessionMessages } from './useSessions';
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
    it('appends part to the last in-progress assistant message when messageID does not match', () => {
      const partInfo = { id: 'p1', messageID: 'msg-unknown', sessionID: 'sess-1', type: 'text', text: 'hello' };
      const prev: Message[] = [
        makeMsg({ id: 'msg-1', role: 'assistant', parts: [] }),
      ];
      const result = applyMessagePartUpdate(prev, partInfo);
      expect(result[0].parts).toHaveLength(1);
      expect((result[0].parts as any[])[0].id).toBe('p1');
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

  it('resets known part tracking when session changes', async () => {
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

    // Switch to a different session — messages and knownPartIds should reset
    await act(async () => {
      rerender({ id: 'sess-b' });
    });

    expect(result.current.messages).toHaveLength(0);
  });
});
