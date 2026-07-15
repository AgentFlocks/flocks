import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { QueuedPrompt } from '@/api/session';
import { getQueuedPromptText, useSessionPromptQueue } from './useSessionPromptQueue';

const listPromptQueueMock = vi.fn();
const enqueuePromptMock = vi.fn();
const updateQueuedPromptMock = vi.fn();
const removeQueuedPromptMock = vi.fn();
const runQueuedPromptNowMock = vi.fn();

vi.mock('@/api/session', () => ({
  sessionApi: {
    listPromptQueue: (...args: unknown[]) => listPromptQueueMock(...args),
    enqueuePrompt: (...args: unknown[]) => enqueuePromptMock(...args),
    updateQueuedPrompt: (...args: unknown[]) => updateQueuedPromptMock(...args),
    removeQueuedPrompt: (...args: unknown[]) => removeQueuedPromptMock(...args),
    runQueuedPromptNow: (...args: unknown[]) => runQueuedPromptNowMock(...args),
  },
}));

function buildQueuedPrompt(overrides: Partial<QueuedPrompt> = {}): QueuedPrompt {
  return {
    id: 'queue-1',
    sessionID: 'sess-1',
    parts: [{ type: 'text', text: 'queued text' }],
    status: 'pending',
    createdAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('getQueuedPromptText', () => {
  it('prefers display text and falls back to the first text part', () => {
    expect(getQueuedPromptText(buildQueuedPrompt({
      displayText: 'Display text',
      parts: [{ type: 'text', text: 'raw text' }],
    }))).toBe('Display text');

    expect(getQueuedPromptText(buildQueuedPrompt({
      displayText: '',
      parts: [{ type: 'text', text: 'raw text' }],
    }))).toBe('raw text');
  });
});

describe('useSessionPromptQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPromptQueueMock.mockResolvedValue({ items: [] });
    enqueuePromptMock.mockResolvedValue({});
    updateQueuedPromptMock.mockResolvedValue({});
    removeQueuedPromptMock.mockResolvedValue({});
    runQueuedPromptNowMock.mockResolvedValue({});
  });

  it('refreshes the queue for the active session', async () => {
    const item = buildQueuedPrompt();
    listPromptQueueMock.mockResolvedValue({ items: [item] });
    const { result } = renderHook(() => useSessionPromptQueue('sess-1'));

    await act(async () => {
      await result.current.refresh();
    });

    expect(listPromptQueueMock).toHaveBeenCalledWith('sess-1');
    expect(result.current.items).toEqual([item]);
  });

  it('ignores stale refresh responses after a session switch', async () => {
    const request = deferred<{ items: QueuedPrompt[] }>();
    listPromptQueueMock.mockReturnValue(request.promise);
    const { result, rerender } = renderHook(
      ({ sessionId }: { sessionId: string }) => useSessionPromptQueue(sessionId),
      { initialProps: { sessionId: 'sess-1' } },
    );

    act(() => {
      void result.current.refresh();
    });

    await act(async () => {
      rerender({ sessionId: 'sess-2' });
    });

    await act(async () => {
      request.resolve({ items: [buildQueuedPrompt({ sessionID: 'sess-1' })] });
      await request.promise;
    });

    expect(result.current.items).toEqual([]);
  });

  it('lets pushed queue items supersede an older refresh response', async () => {
    const request = deferred<{ items: QueuedPrompt[] }>();
    listPromptQueueMock.mockReturnValue(request.promise);
    const pushed = buildQueuedPrompt({ id: 'queue-pushed', displayText: 'pushed' });
    const stale = buildQueuedPrompt({ id: 'queue-stale', displayText: 'stale' });
    const { result } = renderHook(() => useSessionPromptQueue('sess-1'));

    act(() => {
      void result.current.refresh();
      result.current.applyItems([pushed]);
    });

    await act(async () => {
      request.resolve({ items: [stale] });
      await request.promise;
    });

    expect(result.current.items).toEqual([pushed]);
    expect(result.current.expanded).toBe(true);
  });

  it('enqueues a prompt and refreshes the queue', async () => {
    const item = buildQueuedPrompt();
    listPromptQueueMock.mockResolvedValue({ items: [item] });
    const { result } = renderHook(() => useSessionPromptQueue('sess-1'));

    await act(async () => {
      await result.current.enqueue({
        parts: [{ type: 'text', text: 'next' }],
        agent: 'rex',
      });
    });

    expect(enqueuePromptMock).toHaveBeenCalledWith('sess-1', {
      parts: [{ type: 'text', text: 'next' }],
      agent: 'rex',
    });
    expect(result.current.items).toEqual([item]);
  });

  it('saves edited queued text and clears edit state', async () => {
    const item = buildQueuedPrompt();
    const updated = buildQueuedPrompt({ displayText: 'updated' });
    listPromptQueueMock.mockResolvedValue({ items: [updated] });
    const { result } = renderHook(() => useSessionPromptQueue('sess-1'));

    act(() => {
      result.current.startEdit(item);
      result.current.setEditingText('  updated  ');
    });

    await act(async () => {
      await result.current.saveEdit(item);
    });

    expect(updateQueuedPromptMock).toHaveBeenCalledWith('sess-1', 'queue-1', 'updated');
    expect(result.current.editingId).toBeNull();
    expect(result.current.editingText).toBe('');
    expect(result.current.actionId).toBeNull();
    expect(result.current.items).toEqual([updated]);
  });

  it('removes and runs queued prompts while clearing matching edit state', async () => {
    const item = buildQueuedPrompt();
    const { result } = renderHook(() => useSessionPromptQueue('sess-1'));

    act(() => {
      result.current.startEdit(item);
    });

    await act(async () => {
      await result.current.remove(item);
    });

    expect(removeQueuedPromptMock).toHaveBeenCalledWith('sess-1', 'queue-1');
    expect(result.current.editingId).toBeNull();

    act(() => {
      result.current.startEdit(item);
    });

    await act(async () => {
      await result.current.runNow(item);
    });

    expect(runQueuedPromptNowMock).toHaveBeenCalledWith('sess-1', 'queue-1');
    expect(result.current.editingId).toBeNull();
    expect(result.current.actionId).toBeNull();
  });
});
