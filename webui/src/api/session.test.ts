import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

describe('sessionApi message actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPatch.mockResolvedValue({ data: { ok: true } });
    mockPost.mockResolvedValue({ data: { ok: true } });
  });

  it('updates a message part through the patch endpoint', async () => {
    const { sessionApi } = await import('./session');

    await sessionApi.updateMessagePart('session-1', 'msg-1', 'part-1', {
      id: 'part-1',
      messageID: 'msg-1',
      sessionID: 'session-1',
      type: 'text',
      text: 'edited text',
    });

    expect(mockPatch).toHaveBeenCalledWith(
      '/api/session/session-1/message/msg-1/part/part-1',
      expect.objectContaining({
        id: 'part-1',
        messageID: 'msg-1',
        sessionID: 'session-1',
        type: 'text',
        text: 'edited text',
      }),
    );
  });

  it('calls the resend endpoint with timeout disabled', async () => {
    const { sessionApi } = await import('./session');

    await sessionApi.resendMessage('session-1', 'msg-1', 'part-9', 'updated prompt');

    expect(mockPost).toHaveBeenCalledWith(
      '/api/session/session-1/message/msg-1/resend',
      { text: 'updated prompt', partID: 'part-9' },
      { timeout: 0 },
    );
  });

  it('calls the regenerate endpoint with timeout disabled', async () => {
    const { sessionApi } = await import('./session');

    await sessionApi.regenerateMessage('session-1', 'msg-2');

    expect(mockPost).toHaveBeenCalledWith(
      '/api/session/session-1/message/msg-2/regenerate',
      {},
      { timeout: 0 },
    );
  });
});
