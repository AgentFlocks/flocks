import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Message } from '@/types';

import {
  areChatMessagePartsRenderEqual,
  buildTodoWriteSummary,
  dedupeUploadedDocumentAttachments,
  default as SessionChat,
  getEditingActionBarClassName,
  getMessageBubbleClassName,
  getMessageGroupClassName,
  getRegenerateTruncateTarget,
  getStandaloneThinkingBubbleClassName,
  getUserAvatarContainerClassName,
  getUserAvatarSpacerClassName,
  listUploadedDocumentPaths,
  shouldRefetchFinishedMessage,
  truncateToolDisplayText,
} from './SessionChat';

const clientGetMock = vi.fn();
const clientPostMock = vi.fn();
const useSessionMessagesMock = vi.fn();
const tMock = (key: string) => ({
  'chat.placeholder': '请输入消息',
  'chat.emptyText': '暂无消息',
  'chat.sending': '发送中...',
  'chat.thinking': '思考中...',
  'chat.streaming': '继续输出中...',
  'chat.compacting': '压缩中...',
}[key] ?? key);
const pendingQuestionsHookMock = {
  pendingQuestions: {},
  handleQuestionAsked: vi.fn(),
  submitAnswer: vi.fn(),
  submitReject: vi.fn(),
  removeByRequestId: vi.fn(),
  fetchPendingQuestions: vi.fn().mockResolvedValue(undefined),
  clearAll: vi.fn(),
};
const toastMock = {
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}));

vi.mock('@/hooks/useSessions', () => ({
  useSessionMessages: (...args: unknown[]) => useSessionMessagesMock(...args),
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: () => ({ status: 'connected' }),
}));

vi.mock('@/hooks/useReasoningToggle', () => ({
  useReasoningToggle: () => ({
    getPartExpanded: () => false,
    togglePart: vi.fn(),
    isReasoningDone: true,
  }),
}));

vi.mock('@/hooks/usePendingQuestions', () => ({
  usePendingQuestions: () => pendingQuestionsHookMock,
}));

vi.mock('./Toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('@/api/client', () => ({
  __esModule: true,
  default: {
    get: (...args: unknown[]) => clientGetMock(...args),
    post: (...args: unknown[]) => clientPostMock(...args),
  },
  getApiBase: () => '',
}));

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
  clientGetMock.mockResolvedValue({ data: {} });
  clientPostMock.mockResolvedValue({ data: {} });
  pendingQuestionsHookMock.fetchPendingQuestions.mockResolvedValue(undefined);
  useSessionMessagesMock.mockReturnValue({
    messages: [],
    loading: false,
    refetch: vi.fn(),
    addMessage: vi.fn(),
    updateMessage: vi.fn(),
    updateMessagePart: vi.fn(),
    replaceMessageText: vi.fn(),
    truncateAfterMessage: vi.fn(),
  });
});

function makeMessage(overrides: Partial<Message> & { id: string }): Message {
  return {
    id: overrides.id,
    sessionID: 'sess-1',
    role: 'assistant',
    parts: [],
    timestamp: 0,
    ...overrides,
  } as Message;
}

describe('dedupeUploadedDocumentAttachments', () => {
  it('keeps the latest successful document for a workspace path', () => {
    const items = dedupeUploadedDocumentAttachments([
      { id: 'old', status: 'success', workspacePath: '/tmp/uploads/report.pdf', isImage: false },
      { id: 'image', status: 'success', isImage: true, workspacePath: '/tmp/uploads/diagram.png' },
      { id: 'new', status: 'success', workspacePath: '/tmp/uploads/report.pdf', isImage: false },
      { id: 'error', status: 'error', workspacePath: '/tmp/uploads/report.pdf', isImage: false },
    ]);

    expect(items.map((item) => item.id)).toEqual(['image', 'new', 'error']);
  });
});

describe('listUploadedDocumentPaths', () => {
  it('returns unique successful document paths in attachment order', () => {
    expect(listUploadedDocumentPaths([
      { status: 'success', workspacePath: '/tmp/uploads/a.pdf', isImage: false },
      { status: 'success', workspacePath: '/tmp/uploads/a.pdf', isImage: false },
      { status: 'success', workspacePath: '/tmp/uploads/b.pdf', isImage: false },
      { status: 'success', workspacePath: '/tmp/uploads/image.png', isImage: true },
      { status: 'error', workspacePath: '/tmp/uploads/c.pdf', isImage: false },
    ])).toEqual(['/tmp/uploads/a.pdf', '/tmp/uploads/b.pdf']);
  });
});

describe('getMessageBubbleClassName', () => {
  // The bubble's max width is owned by its outer container (`max-w-[80%]` for
  // user, `w-full` for assistant; see SessionChat.tsx), so the inner bubble
  // only controls its own intrinsic sizing (`w-auto` vs `w-full`).  Previously
  // the inner bubble also pinned `max-w-2xl`, but the unified chat redesign
  // moved that responsibility outward.  Tests here therefore assert width
  // semantics, not the legacy `max-w-2xl` literal.
  it('keeps non-editing user bubbles auto-sized in full layout', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('w-auto');
    expect(className).not.toContain('w-full');
  });

  it('expands editing user bubbles to full width in full layout', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: true,
    });

    expect(className).toContain('w-full');
    expect(className).not.toContain('w-auto');
  });

  it('keeps assistant bubbles full width regardless of editing state', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: false,
      isEditing: true,
    });

    expect(className).toContain('w-full');
  });
});

describe('getMessageGroupClassName', () => {
  it('caps full-layout user messages at 80% width', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('max-w-[80%]');
    expect(className).toContain('w-fit');
  });

  it('expands editing user messages to the 80% container width', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: true,
      isEditing: true,
    });

    expect(className).toContain('w-[80%]');
    expect(className).toContain('max-w-[80%]');
  });

  it('keeps assistant messages full width in full layout', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: false,
      isEditing: false,
    });

    expect(className).toBe('w-full');
  });
});

describe('getEditingActionBarClassName', () => {
  it('keeps editing actions right-aligned inside the bubble', () => {
    const className = getEditingActionBarClassName();

    expect(className).toContain('justify-end');
    expect(className).toContain('w-full');
    expect(className).toContain('mt-3');
  });
});

describe('getStandaloneThinkingBubbleClassName', () => {
  it('matches the standard assistant bubble sizing in full layout', () => {
    expect(getStandaloneThinkingBubbleClassName(false)).toBe(
      getMessageBubbleClassName({ compact: false, isUser: false, isEditing: false }),
    );
  });

  it('matches the standard assistant bubble sizing in compact layout', () => {
    expect(getStandaloneThinkingBubbleClassName(true)).toBe(
      getMessageBubbleClassName({ compact: true, isUser: false, isEditing: false }),
    );
  });
});

describe('getUserAvatarContainerClassName', () => {
  it('moves the user avatar to the bubble side without affecting bubble spacing', () => {
    const className = getUserAvatarContainerClassName(false);

    expect(className).toContain('absolute');
    expect(className).toContain('left-full');
    expect(className).toContain('ml-2.5');
    expect(className).toContain('translate-y-1/2');
    expect(className).toContain('h-8');
  });

  it('keeps the compact avatar aligned to the compact header height', () => {
    expect(getUserAvatarContainerClassName(true)).toContain('h-7');
  });
});

describe('getUserAvatarSpacerClassName', () => {
  it('uses a shorter spacer in full layout to keep the top gap compact', () => {
    expect(getUserAvatarSpacerClassName(false)).toBe('h-4');
  });

  it('uses a proportional spacer in compact layout', () => {
    expect(getUserAvatarSpacerClassName(true)).toBe('h-3.5');
  });
});

describe('SessionChat standalone thinking indicator', () => {
  it('keeps only the bouncing dots during the initial assistant loading state', async () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'user-1',
          role: 'user',
          parts: [{ id: 'user-1-part', type: 'text', text: 'hello' }] as Message['parts'],
        }),
      ],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    const { container } = render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      initialMessage: 'hello',
    }));

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({ parts: expect.any(Array) }),
      );
    });

    await waitFor(() => {
      expect(container.querySelectorAll('.animate-bounce').length).toBeGreaterThanOrEqual(3);
      expect(container.textContent).not.toContain('思考中...');
    });
  });
});

describe('truncateToolDisplayText', () => {
  it('returns short text unchanged', () => {
    expect(truncateToolDisplayText('bash')).toBe('bash');
  });

  it('truncates long text with an ellipsis', () => {
    const long = 'python3 -c "' + 'x'.repeat(200) + '"';
    const result = truncateToolDisplayText(long, 120);
    expect(result.length).toBe(121);
    expect(result.endsWith('…')).toBe(true);
    expect(result.startsWith('python3 -c "')).toBe(true);
  });
});

describe('buildTodoWriteSummary', () => {
  it('renders progress from structured todowrite input', () => {
    expect(buildTodoWriteSummary({
      input: {
        todos: [
          { id: '1', content: '定位 todowrite 摘要问题', status: 'in_progress' },
          { id: '2', content: '补充回归测试', status: 'completed' },
          { id: '3', content: '验证 Web UI 展示', status: 'pending' },
        ],
      },
    })).toBe('Progress 1/3 · In progress 1');
  });

  it('prefers current metadata todos when available', () => {
    expect(buildTodoWriteSummary({
      metadata: {
        oldTodos: [
          { id: '1', content: '定位 todowrite 摘要问题', status: 'pending' },
          { id: '2', content: '补充回归测试', status: 'pending' },
        ],
        newTodos: [
          { id: '1', content: '定位 todowrite 摘要问题', status: 'completed' },
          { id: '3', content: '验证 Web UI 展示', status: 'completed' },
        ],
      },
    })).toBe('Completed 2/2');
  });
});

describe('getRegenerateTruncateTarget', () => {
  it('truncates back to the parent user message for assistant regenerations', () => {
    const target = getRegenerateTruncateTarget([
      makeMessage({ id: 'user-1', role: 'user' }),
      makeMessage({ id: 'assistant-1', role: 'assistant', parentID: 'user-1' }),
      makeMessage({ id: 'assistant-2', role: 'assistant', parentID: 'user-1' }),
    ], 'assistant-2');

    expect(target).toEqual({ messageId: 'user-1' });
  });

  it('falls back to removing the target message when parent linkage is unavailable', () => {
    const target = getRegenerateTruncateTarget([
      makeMessage({ id: 'assistant-1', role: 'assistant' }),
    ], 'assistant-1');

    expect(target).toEqual({ messageId: 'assistant-1', includeTarget: true });
  });
});

describe('shouldRefetchFinishedMessage', () => {
  it('skips refetch for the assistant message the user just aborted', () => {
    expect(shouldRefetchFinishedMessage({
      finishedMessageId: 'assistant-1',
      abortedMessageId: 'assistant-1',
    })).toBe(false);
  });

  it('still refetches for unrelated finished messages', () => {
    expect(shouldRefetchFinishedMessage({
      finishedMessageId: 'assistant-2',
      abortedMessageId: 'assistant-1',
    })).toBe(true);
  });
});

describe('areChatMessagePartsRenderEqual', () => {
  it('detects streamed text updates even when a later tool part exists', () => {
    const sharedToolPart = {
      id: 'tool-1',
      type: 'tool',
      tool: 'todowrite',
      state: { status: 'running', metadata: { step: 1 } },
    } as Message['parts'][number];

    expect(areChatMessagePartsRenderEqual(
      [
        { id: 'text-1', type: 'text', text: '现在生成简化版 wor' } as Message['parts'][number],
        sharedToolPart,
      ],
      [
        { id: 'text-1', type: 'text', text: '现在生成简化版 workflow.json' } as Message['parts'][number],
        sharedToolPart,
      ],
    )).toBe(false);
  });

  it('keeps skipping rerenders when semantically identical parts are recreated', () => {
    expect(areChatMessagePartsRenderEqual(
      [
        {
          id: 'tool-1',
          type: 'tool',
          tool: 'question',
          state: { status: 'completed', metadata: { label: 'done' } },
        } as Message['parts'][number],
      ],
      [
        {
          id: 'tool-1',
          type: 'tool',
          tool: 'question',
          state: { status: 'completed', metadata: { label: 'done' } },
        } as Message['parts'][number],
      ],
    )).toBe(true);
  });

  it('detects legacy tool payload updates that still drive the UI', () => {
    expect(areChatMessagePartsRenderEqual(
      [
        {
          id: 'tool-call-1',
          type: 'toolCall',
          toolCall: {
            id: 'call-1',
            name: 'question',
            params: { prompt: 'first' },
          },
        } as Message['parts'][number],
      ],
      [
        {
          id: 'tool-call-1',
          type: 'toolCall',
          toolCall: {
            id: 'call-1',
            name: 'question',
            params: { prompt: 'updated' },
          },
        } as Message['parts'][number],
      ],
    )).toBe(false);
  });
});
