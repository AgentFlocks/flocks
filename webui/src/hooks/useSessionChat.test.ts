/**
 * Regression tests for "non-Session entry first message with images".
 *
 * These cover the chain:
 *   onCreateAndSend(text, imageParts)  →  useSessionChat.createAndSend({ text, imageParts })
 *                                      →  /api/session/{id}/prompt_async  with parts[]
 *
 * The key regression being guarded: before the fix, imageParts were silently
 * dropped when the first message was sent through non-Session chat composers
 * (CreateAgentChat, WorkflowCreate/CreateChatTab, WorkflowDetail/ChatTab,
 * EntitySheet, ChatDialog).  Now createAndSend forwards them into the payload.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockPost = vi.fn();
vi.mock('@/api/client', () => ({
  default: { post: (...args: unknown[]) => mockPost(...args) },
}));

import { renderHook, act } from '@testing-library/react';
import { useSessionChat } from './useSessionChat';
import type { ImagePartData } from '@/utils/imageUpload';

const SESSION_ID = 'sess-abc';

beforeEach(() => {
  vi.clearAllMocks();
  // /api/session creates a new session
  mockPost.mockImplementation((url: string) => {
    if (url === '/api/session') return Promise.resolve({ data: { id: SESSION_ID } });
    return Promise.resolve({ data: {} });
  });
});

describe('useSessionChat.createAndSend — image forwarding', () => {
  it('includes imageParts in the prompt_async payload', async () => {
    const { result } = renderHook(() =>
      useSessionChat({ title: 'Test', autoCreate: false }),
    );

    const img: ImagePartData = {
      url: 'data:image/png;base64,abc',
      mime: 'image/png',
      filename: 'screenshot.png',
    };

    await act(async () => {
      await result.current.createAndSend({ text: 'describe this', imageParts: [img] });
    });

    // Find the prompt_async call
    const promptCall = mockPost.mock.calls.find(([url]: string[]) =>
      url === `/api/session/${SESSION_ID}/prompt_async`,
    );
    expect(promptCall).toBeDefined();

    const payload = promptCall![1] as { parts: unknown[] };
    expect(payload.parts).toEqual([
      { type: 'text', text: 'describe this' },
      { type: 'file', url: img.url, mime: img.mime, filename: img.filename },
    ]);
  });

  it('works for image-only messages (no text)', async () => {
    const { result } = renderHook(() =>
      useSessionChat({ title: 'Test', autoCreate: false }),
    );

    const img: ImagePartData = {
      url: 'data:image/jpeg;base64,xyz',
      mime: 'image/jpeg',
      filename: 'photo.jpg',
    };

    await act(async () => {
      await result.current.createAndSend({ text: '', imageParts: [img] });
    });

    const promptCall = mockPost.mock.calls.find(([url]: string[]) =>
      url === `/api/session/${SESSION_ID}/prompt_async`,
    );
    expect(promptCall).toBeDefined();

    const payload = promptCall![1] as { parts: unknown[] };
    // No text part when text is empty; only the file part.
    expect(payload.parts).toEqual([
      { type: 'file', url: img.url, mime: img.mime, filename: img.filename },
    ]);
  });

  it('works for text-only messages (backward compat — no imageParts arg)', async () => {
    const { result } = renderHook(() =>
      useSessionChat({ title: 'Test', autoCreate: false }),
    );

    await act(async () => {
      await result.current.createAndSend({ text: 'hello' });
    });

    const promptCall = mockPost.mock.calls.find(([url]: string[]) =>
      url === `/api/session/${SESSION_ID}/prompt_async`,
    );
    expect(promptCall).toBeDefined();

    const payload = promptCall![1] as { parts: unknown[] };
    expect(payload.parts).toEqual([{ type: 'text', text: 'hello' }]);
  });

  it('forwards the agent field when provided', async () => {
    const { result } = renderHook(() =>
      useSessionChat({ title: 'Test', autoCreate: false }),
    );

    await act(async () => {
      await result.current.createAndSend({ text: 'hi', agent: 'my-agent' });
    });

    const promptCall = mockPost.mock.calls.find(([url]: string[]) =>
      url === `/api/session/${SESSION_ID}/prompt_async`,
    );
    expect(promptCall![1]).toMatchObject({ agent: 'my-agent' });
  });

  it('resumes from an initial session id without creating another session', async () => {
    const { result } = renderHook(() =>
      useSessionChat({ title: 'Test', autoCreate: false, initialSessionId: 'existing-session' }),
    );

    expect(result.current.sessionId).toBe('existing-session');

    await act(async () => {
      await result.current.createAndSend({ text: 'continue' });
    });

    expect(mockPost.mock.calls.some(([url]) => url === '/api/session')).toBe(false);
    expect(mockPost).toHaveBeenCalledWith(
      '/api/session/existing-session/prompt_async',
      { parts: [{ type: 'text', text: 'continue' }] },
    );
  });
});
