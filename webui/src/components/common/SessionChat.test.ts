import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Message } from '@/types';

import {
  areChatMessagePartsRenderEqual,
  buildContextUsageBreakdown,
  buildTodoSummary,
  ChatToolPart,
  dedupeUploadedDocumentAttachments,
  default as SessionChat,
  getCompactionDividerClassName,
  getEditingActionBarClassName,
  getMessageBubbleClassName,
  getMessageErrorText,
  getMessageGroupClassName,
  getRenderableThinkingText,
  getRenderableFileUrl,
  getRegenerateTruncateTarget,
  getStandaloneThinkingBubbleClassName,
  getUserAvatarContainerClassName,
  getUserAvatarSpacerClassName,
  hasActiveToolPart,
  isActiveSessionStatus,
  listUploadedDocumentPaths,
  shouldRenderMessage,
  shouldRefetchFinishedMessage,
  truncateToolDisplayText,
} from './SessionChat';

const clientGetMock = vi.fn();
const clientPostMock = vi.fn();
const sessionApiListPromptQueueMock = vi.fn();
const sessionApiEnqueuePromptMock = vi.fn();
const sessionApiUpdateQueuedPromptMock = vi.fn();
const sessionApiRemoveQueuedPromptMock = vi.fn();
const sessionApiRunQueuedPromptNowMock = vi.fn();
const sessionApiUpdateMessagePartMock = vi.fn();
const sessionApiResendMessageMock = vi.fn();
const sessionApiRegenerateMessageMock = vi.fn();
const sessionApiRewindMock = vi.fn();
const sessionApiGetContextUsageMock = vi.fn();
const useSessionMessagesMock = vi.fn();
const useSSEOptionsRef = vi.hoisted(() => ({ current: null as any }));
const tMock = (key: string, options?: Record<string, unknown>) => {
  const value = ({
  'chat.placeholder': '请输入消息',
  'chat.emptyText': '暂无消息',
  'chat.sending': '发送中...',
  'chat.thinking': '思考中...',
  'chat.streaming': '继续输出中...',
  'chat.process.title': '过程（{{count}} 项）',
  'chat.process.reasoningCount': '{{count}} 段思考',
  'chat.process.toolCount': '{{count}} 次工具调用',
  'chat.compacting': '压缩中...',
  'chat.contextCompressed': '上下文已压缩',
  'chat.contextUsage.title': 'Context Usage',
  'chat.contextUsage.close': 'Close',
  'chat.contextUsage.full': '13% Full',
  'chat.contextUsage.tokens': '~13 / 100 Tokens',
  'chat.contextUsage.excludedTokens': '100 excluded',
  'chat.contextUsage.noAttributedSegments': 'No attributed breakdown',
  'chat.contextUsage.breakdown.systemPrompt': 'System prompt',
  'chat.contextUsage.breakdown.toolDefinitions': 'Tool definitions',
  'chat.contextUsage.breakdown.tools': 'Tool calls',
  'chat.contextUsage.breakdown.skillLoad': 'Skill loads',
  'chat.contextUsage.breakdown.agentDelegation': 'Agent delegation',
  'chat.contextUsage.breakdown.conversation': 'Conversation',
  'chat.contextUsage.breakdown.reasoning': 'Reasoning',
  'chat.contextUsage.breakdown.draft': 'Current draft',
  'chat.contextUsage.breakdown.compactedHistory': 'Compacted history',
  'chat.mention.title': '选择 Agent',
  'chat.mention.navigate': '导航',
  'chat.mention.select': '选择',
  'chat.tool.pending': '等待中',
  'chat.tool.running': '执行中',
  'chat.tool.completed': '已完成',
  'chat.tool.error': '失败',
  'chat.tool.inputParams': '输入参数',
  'chat.tool.outputResult': '输出结果',
  'chat.tool.todoStages': 'Todo 阶段',
  'chat.tool.todoStatus.pending': '待办',
  'chat.tool.todoStatus.inProgress': '进行中',
  'chat.tool.todoStatus.completed': '完成',
  'chat.tool.todoStatus.cancelled': '已取消',
  'chat.tool.todoSummary.progress': '进度',
  'chat.tool.todoSummary.inProgress': '进行中',
  'chat.tool.todoSummary.completed': '完成',
  'chat.tool.todoSummary.done': '完成',
  'smartAssistant': '智能助手',
  }[key] ?? key);
  return value.replace(/\{\{(\w+)\}\}/g, (_, name) => String(options?.[name] ?? ''));
};
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
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/hooks/useSessions', () => ({
  useSessionMessages: (...args: unknown[]) => useSessionMessagesMock(...args),
}));

vi.mock('@/hooks/useSSE', () => ({
  useSSE: (options: any) => {
    useSSEOptionsRef.current = options;
    return { status: 'connected' };
  },
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

vi.mock('@/api/session', () => ({
  sessionApi: {
    listPromptQueue: (...args: unknown[]) => sessionApiListPromptQueueMock(...args),
    enqueuePrompt: (...args: unknown[]) => sessionApiEnqueuePromptMock(...args),
    updateQueuedPrompt: (...args: unknown[]) => sessionApiUpdateQueuedPromptMock(...args),
    removeQueuedPrompt: (...args: unknown[]) => sessionApiRemoveQueuedPromptMock(...args),
    runQueuedPromptNow: (...args: unknown[]) => sessionApiRunQueuedPromptNowMock(...args),
    updateMessagePart: (...args: unknown[]) => sessionApiUpdateMessagePartMock(...args),
    resendMessage: (...args: unknown[]) => sessionApiResendMessageMock(...args),
    regenerateMessage: (...args: unknown[]) => sessionApiRegenerateMessageMock(...args),
    rewind: (...args: unknown[]) => sessionApiRewindMock(...args),
    getContextUsage: (...args: unknown[]) => sessionApiGetContextUsageMock(...args),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  if (typeof window.localStorage?.clear !== 'function') {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        clear: vi.fn(),
        getItem: vi.fn(),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      },
    });
  }
  window.localStorage.clear();
  Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    value: vi.fn(),
  });
  clientGetMock.mockResolvedValue({ data: {} });
  clientPostMock.mockResolvedValue({ data: {} });
  sessionApiListPromptQueueMock.mockResolvedValue({ items: [] });
  sessionApiEnqueuePromptMock.mockResolvedValue({});
  sessionApiUpdateQueuedPromptMock.mockResolvedValue({});
  sessionApiRemoveQueuedPromptMock.mockResolvedValue({});
  sessionApiRunQueuedPromptNowMock.mockResolvedValue({});
  sessionApiUpdateMessagePartMock.mockResolvedValue({});
  sessionApiResendMessageMock.mockResolvedValue({});
  sessionApiRegenerateMessageMock.mockResolvedValue({});
  sessionApiRewindMock.mockResolvedValue({});
  sessionApiGetContextUsageMock.mockResolvedValue({
    sessionID: 'sess-1',
    usedTokens: 0,
    contextWindow: 0,
    percent: 0,
    source: 'estimated',
    estimatedTokens: 0,
    compactedTokens: 0,
    segments: [],
    excludedSegments: [],
  });
  pendingQuestionsHookMock.fetchPendingQuestions.mockResolvedValue(undefined);
  useSSEOptionsRef.current = null;
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

describe('buildContextUsageBreakdown', () => {
  it('excludes compacted history from the current used-token total', () => {
    const breakdown = buildContextUsageBreakdown([
      makeMessage({
        id: 'active',
        role: 'user',
        parts: [{ id: 'active-text', type: 'text', text: 'a'.repeat(400) }],
      }),
      makeMessage({
        id: 'archived',
        compacted: true,
        parts: [{ id: 'archived-text', type: 'text', text: 'b'.repeat(800) }],
      }),
    ], 'c'.repeat(40));

    expect(breakdown.usedTokens).toBe(110);
    expect(breakdown.compactedTokens).toBe(200);
    expect(breakdown.segments.map((segment) => [segment.key, segment.tokens])).toEqual([
      ['systemPrompt', 0],
      ['toolDefinitions', 0],
      ['conversation', 110],
      ['reasoning', 0],
      ['tools', 0],
      ['skillLoad', 0],
      ['agentDelegation', 0],
    ]);
    expect(breakdown.excludedSegments).toEqual([]);
  });

  it('counts compacted tool outputs as a small placeholder', () => {
    const compactedTime = { start: 1, compacted: 2 };
    const breakdown = buildContextUsageBreakdown([
      makeMessage({
        id: 'tool-msg',
        parts: [{
          id: 'tool-part',
          type: 'tool',
          tool: 'bash',
          state: {
            status: 'completed',
            input: { command: 'x'.repeat(40) },
            output: 'y'.repeat(800),
            time: compactedTime,
          },
        }],
      }),
    ], '');

    expect(breakdown.usedTokens).toBe(23);
  });

  it('uses backend snapshots when available and adds the local draft on top', () => {
    const breakdown = buildContextUsageBreakdown([], 'd'.repeat(40), {
      sessionID: 'sess-1',
      usedTokens: 130,
      contextWindow: 1000,
      percent: 13,
      source: 'observed',
      lastMessageID: 'assistant-1',
      observedTokens: 130,
      estimatedTokens: 100,
      compactedTokens: 50,
      segments: [
        { key: 'systemPrompt', tokens: 15, included: true, source: 'estimated' },
        { key: 'toolDefinitions', tokens: 10, included: true, source: 'estimated' },
        { key: 'tools', tokens: 40, included: true, source: 'estimated' },
        { key: 'skillLoad', tokens: 20, included: true, source: 'estimated' },
        { key: 'agentDelegation', tokens: 10, included: true, source: 'estimated' },
        { key: 'conversation', tokens: 30, included: true, source: 'estimated' },
        { key: 'reasoning', tokens: 5, included: true, source: 'observed' },
      ],
      excludedSegments: [
        { key: 'compactedHistory', tokens: 50, included: false, source: 'estimated' },
      ],
    });

    expect(breakdown.usedTokens).toBe(140);
    expect(breakdown.compactedTokens).toBe(50);
    expect(breakdown.segments.map((segment) => [segment.key, segment.tokens])).toEqual([
      ['systemPrompt', 15],
      ['toolDefinitions', 10],
      ['conversation', 40],
      ['reasoning', 5],
      ['tools', 40],
      ['skillLoad', 20],
      ['agentDelegation', 10],
    ]);
    expect(breakdown.excludedSegments).toEqual([]);
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

describe('getCompactionDividerClassName', () => {
  it('insets the divider into the assistant content column in full layout', () => {
    const className = getCompactionDividerClassName(false);

    expect(className).toContain('pl-[42px]');
    expect(className).toContain('w-full');
    expect(className).toContain('min-w-0');
  });

  it('uses the compact assistant inset in compact layout', () => {
    expect(getCompactionDividerClassName(true)).toContain('pl-[38px]');
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

describe('getRenderableFileUrl', () => {
  it('converts local file URLs to the guarded file download endpoint', () => {
    expect(getRenderableFileUrl('file:///tmp/channel%20image.png')).toBe(
      '/api/file/download?path=%2Ftmp%2Fchannel%20image.png',
    );
  });

  it('converts Windows file URLs without adding a POSIX root prefix', () => {
    expect(getRenderableFileUrl('file:///C:/Users/demo/Pictures/channel%20image.png')).toBe(
      '/api/file/download?path=C%3A%2FUsers%2Fdemo%2FPictures%2Fchannel%20image.png',
    );
  });

  it('preserves UNC file URL hosts for Windows network paths', () => {
    expect(getRenderableFileUrl('file://server/share/channel%20image.png')).toBe(
      '/api/file/download?path=%2F%2Fserver%2Fshare%2Fchannel%20image.png',
    );
  });

  it('leaves browser-readable URLs unchanged', () => {
    expect(getRenderableFileUrl('https://example.com/image.png')).toBe('https://example.com/image.png');
    expect(getRenderableFileUrl('data:image/png;base64,abc')).toBe('data:image/png;base64,abc');
  });
});

describe('getUserAvatarContainerClassName', () => {
  it('keeps the user avatar inside the message row', () => {
    const className = getUserAvatarContainerClassName(false);

    expect(className).toContain('flex-shrink-0');
    expect(className).not.toContain('absolute');
    expect(className).not.toContain('left-full');
    expect(className).toContain('h-8');
  });

  it('keeps the compact avatar aligned to the compact header height', () => {
    expect(getUserAvatarContainerClassName(true)).toContain('h-7');
  });
});

describe('getUserAvatarSpacerClassName', () => {
  it('does not reserve out-of-flow space in full layout', () => {
    expect(getUserAvatarSpacerClassName(false)).toBe('h-0');
  });

  it('does not reserve out-of-flow space in compact layout', () => {
    expect(getUserAvatarSpacerClassName(true)).toBe('h-0');
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

describe('SessionChat instruction display text', () => {
  it('renders metadata displayText while keeping the raw prompt out of the bubble', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'user-instruction',
          role: 'user',
          parts: [{
            id: 'user-instruction-part',
            type: 'text',
            text: 'Please read guide.md and generate the full workflow configuration.',
            metadata: { displayText: '@@flocks-instruction:智能配置' },
          }] as Message['parts'],
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

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    expect(screen.getByText('智能配置')).toBeInTheDocument();
    expect(screen.queryByText(/Please read guide\.md/)).not.toBeInTheDocument();
  });
});

describe('shouldRenderMessage', () => {
  it('keeps active empty assistant messages eligible for the thinking indicator', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-active',
      role: 'assistant',
      parts: [],
      finish: null,
    }))).toBe(true);
  });

  it('hides stopped empty assistant messages after abort before first content', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-stopped',
      role: 'assistant',
      parts: [],
      finish: 'stop',
    }))).toBe(false);
  });

  it('keeps empty assistant error messages visible', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-error',
      role: 'assistant',
      parts: [],
      finish: 'error',
      error: { code: 'SessionError', message: 'Provider failed' },
    }))).toBe(true);
  });

  it('hides stopped assistant messages that only contain punctuation reasoning', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-dot',
      role: 'assistant',
      finish: 'stop',
      parts: [
        {
          id: 'part-dot',
          messageID: 'assistant-dot',
          sessionID: 'sess-1',
          type: 'reasoning',
          text: '.',
        } as any,
      ],
    }))).toBe(false);
  });
});

describe('getRenderableThinkingText', () => {
  it('filters punctuation-only reasoning previews', () => {
    expect(getRenderableThinkingText({ type: 'reasoning', text: '.' } as any)).toBe('');
    expect(getRenderableThinkingText({ type: 'reasoning', text: '。' } as any)).toBe('');
  });

  it('keeps meaningful reasoning text', () => {
    expect(getRenderableThinkingText({ type: 'reasoning', text: '需要更新 todo 状态' } as any)).toBe('需要更新 todo 状态');
  });
});

describe('getMessageErrorText', () => {
  it('prefers user-facing display messages over raw provider errors', () => {
    expect(getMessageErrorText(makeMessage({
      id: 'assistant-error',
      error: {
        message: 'Connection error.',
        data: {
          displayMessage: 'Model is unavailable. Please check the provider connection and model configuration.',
          message: 'Connection error.',
        },
      } as any,
    }))).toBe('Model is unavailable. Please check the provider connection and model configuration.');
  });

  it('extracts nested provider error messages', () => {
    expect(getMessageErrorText(makeMessage({
      id: 'assistant-error',
      error: {
        name: 'APIConnectionError',
        data: { message: 'Connection error.' },
      } as any,
    }))).toBe('Connection error.');
  });

  it('falls back to the error code', () => {
    expect(getMessageErrorText(makeMessage({
      id: 'assistant-error',
      error: { code: 'SessionError' } as any,
    }))).toBe('SessionError');
  });
});

describe('SessionChat error rendering', () => {
  it('renders empty assistant error messages instead of the thinking indicator', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-error',
          role: 'assistant',
          parts: [],
          finish: 'error',
          error: {
            name: 'APIConnectionError',
            data: { message: 'Connection error.' },
          } as any,
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

    const { container } = render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    expect(screen.getByText('Connection error.')).toBeInTheDocument();
    expect(container.querySelectorAll('.animate-bounce')).toHaveLength(0);
  });
});

describe('SessionChat intermediate process collapse', () => {
  it('collapses reasoning and tool steps by default in embedded workflow panels', async () => {
    const user = userEvent.setup();
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-process',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'reason-1',
              messageID: 'assistant-process',
              sessionID: 'sess-1',
              type: 'reasoning',
              text: '需要先读取工作流文件',
            } as any,
            {
              id: 'tool-1',
              messageID: 'assistant-process',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'read',
              callID: 'call-1',
              state: {
                status: 'completed',
                input: { filePath: 'workflow.md' },
                output: 'workflow content',
              },
            } as any,
            {
              id: 'text-1',
              messageID: 'assistant-process',
              sessionID: 'sess-1',
              type: 'text',
              text: '已读取当前 workflow.md。',
            } as any,
          ],
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

    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    const processGroup = screen.getByTestId('chat-process-group') as HTMLDetailsElement;
    expect(processGroup.open).toBe(false);
    expect(screen.getByText('过程（2 项）')).toBeInTheDocument();
    expect(screen.getByText('1 段思考 · 1 次工具调用')).toBeInTheDocument();
    expect(screen.getByText('已读取当前 workflow.md。')).toBeInTheDocument();

    await user.click(screen.getByText('过程（2 项）'));

    expect(processGroup.open).toBe(true);
    expect(screen.getByText('read')).toBeInTheDocument();
  });
});

describe('SessionChat agent mentions', () => {
  const mentionAgents = [
    {
      name: 'rex',
      description: 'Main orchestrator',
      descriptionCn: '主编排 Agent',
      mode: 'primary',
      permission: [],
      options: {},
      skills: [],
      tools: [],
    },
    {
      name: 'explore',
      description: 'Explore the codebase',
      descriptionCn: '探索代码库',
      mode: 'subagent',
      native: true,
      permission: [],
      options: {},
      skills: [],
      tools: [],
    },
  ];

  it('shows matching agents when typing @', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      mentionAgents,
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '@ex');

    expect(screen.getByText('@explore')).toBeInTheDocument();
    expect(screen.getByText('探索代码库')).toBeInTheDocument();
  });

  it('routes one message to the mentioned agent without changing the default agent', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      agentName: 'rex',
      mentionAgents,
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '@explore summarize this file{enter}');

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({
          agent: 'explore',
          parts: expect.any(Array),
        }),
      );
    });
  });

  it('queues streaming messages to the mentioned agent', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      agentName: 'rex',
      mentionAgents,
      initialMessage: 'start streaming',
    }));

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({ parts: expect.any(Array) }),
      );
    });

    sessionApiEnqueuePromptMock.mockClear();
    await user.type(screen.getByRole('textbox'), '@explore queued message{enter}');

    await waitFor(() => {
      expect(sessionApiEnqueuePromptMock).toHaveBeenCalledWith(
        'sess-1',
        expect.objectContaining({
          agent: 'explore',
          parts: expect.any(Array),
        }),
      );
    });
  });

  it('queues streaming messages to the default agent when no mention is provided', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      agentName: 'rex',
      mentionAgents,
      initialMessage: 'start streaming',
    }));

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({ parts: expect.any(Array) }),
      );
    });

    sessionApiEnqueuePromptMock.mockClear();
    await user.type(screen.getByRole('textbox'), 'queued message{enter}');

    await waitFor(() => {
      expect(sessionApiEnqueuePromptMock).toHaveBeenCalledWith(
        'sess-1',
        expect.objectContaining({
          agent: 'rex',
          parts: expect.any(Array),
        }),
      );
    });
  });

  it('opens a turn picker for bare /rewind instead of sending a command', async () => {
    const user = userEvent.setup();
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'user-1',
          role: 'user',
          timestamp: 1000,
          parts: [{ id: 'user-1-part', type: 'text', text: 'first change' }] as Message['parts'],
        }),
        makeMessage({
          id: 'assistant-1',
          role: 'assistant',
          parentID: 'user-1',
          finish: 'stop',
          parts: [{ id: 'assistant-1-part', type: 'text', text: 'done' }] as Message['parts'],
        }),
        makeMessage({
          id: 'user-2',
          role: 'user',
          timestamp: 2000,
          parts: [{ id: 'user-2-part', type: 'text', text: 'second change' }] as Message['parts'],
        }),
      ],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
      markMessageStopped: vi.fn(),
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    await user.type(screen.getByRole('textbox'), '/rewind{enter}');

    expect(await screen.findByText('Rewind to turn')).toBeInTheDocument();
    expect(screen.getAllByText('second change').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('first change').length).toBeGreaterThanOrEqual(2);
    expect(clientPostMock).not.toHaveBeenCalledWith(
      '/api/session/sess-1/command',
      expect.anything(),
    );
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

describe('buildTodoSummary', () => {
  it('renders progress from structured todo input', () => {
    expect(buildTodoSummary({
      input: {
        action: 'write',
        todos: [
          { id: '1', content: '定位 todo 摘要问题', status: 'in_progress' },
          { id: '2', content: '补充回归测试', status: 'completed' },
          { id: '3', content: '验证 Web UI 展示', status: 'pending' },
        ],
      },
    })).toBe('Progress 1/3 · In progress 1');
  });

  it('prefers current metadata todos when available', () => {
    expect(buildTodoSummary({
      metadata: {
        oldTodos: [
          { id: '1', content: '定位 todo 摘要问题', status: 'pending' },
          { id: '2', content: '补充回归测试', status: 'pending' },
        ],
        newTodos: [
          { id: '1', content: '定位 todo 摘要问题', status: 'completed' },
          { id: '3', content: '验证 Web UI 展示', status: 'completed' },
        ],
      },
    })).toBe('Completed 2/2');
  });

  it('renders a readable fallback for todo actions without structured entries', () => {
    expect(buildTodoSummary({
      input: {
        action: 'write',
        todos: [],
      },
    })).toBe('Update todos');
  });
});

describe('ChatToolPart todo rendering', () => {
  it('renders todo progress and stages without object-object summaries', () => {
    const { container } = render(
      React.createElement(ChatToolPart, {
        part: {
          id: 'todo-part',
          type: 'tool',
          tool: 'todo',
          callID: 'call-todo',
          state: {
            status: 'completed',
            input: {
              action: 'write',
              todos: [
                { id: '1', content: '定位 todo 摘要问题', activeForm: '定位 todo 摘要问题中', status: 'in_progress' },
                { id: '2', content: '补充回归测试', status: 'completed' },
                { id: '3', content: '验证 Web UI 展示', status: 'pending' },
              ],
            },
            output: '{}',
            title: '2 todos',
            metadata: {
              action: 'write',
              newTodos: [
                { id: '1', content: '定位 todo 摘要问题', activeForm: '定位 todo 摘要问题中', status: 'in_progress' },
                { id: '2', content: '补充回归测试', status: 'completed' },
                { id: '3', content: '验证 Web UI 展示', status: 'pending' },
              ],
            },
          },
        } as any,
      }),
    );

    expect(container.textContent).toContain('进度 1/3 · 进行中 1');
    expect(container.textContent).toContain('Todo 阶段');
    expect(container.textContent).toContain('定位 todo 摘要问题中');
    expect(container.textContent).toContain('完成');
    expect(container.textContent).not.toContain('completed');
    expect(container.textContent).not.toContain('输入参数');
    expect(container.textContent).not.toContain('输出结果');
    expect(container.textContent).not.toContain('[object Object]');
  });
});

describe('SessionChat context usage popover', () => {
  it('always shows fixed usage rows and hides compacted history', async () => {
    const user = userEvent.setup();
    sessionApiGetContextUsageMock.mockResolvedValue({
      sessionID: 'sess-1',
      usedTokens: 120,
      contextWindow: 1000,
      percent: 12,
      source: 'estimated',
      estimatedTokens: 120,
      compactedTokens: 0,
      segments: [
        { key: 'systemPrompt', tokens: 80, included: true, source: 'estimated' },
        { key: 'agentDelegation', tokens: 0, included: true, source: 'estimated' },
      ],
      excludedSegments: [
        { key: 'compactedHistory', tokens: 12000, included: false, source: 'estimated' },
      ],
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    const contextButton = await screen.findByRole('button', { name: 'chat.contextUsageTitle' });
    expect(contextButton).toHaveClass('h-6', 'w-6');
    await user.click(contextButton);

    expect(screen.getByText('System prompt')).toBeInTheDocument();
    expect(screen.getByText('Tool definitions')).toBeInTheDocument();
    expect(screen.getByText('Conversation')).toBeInTheDocument();
    expect(screen.getByText('Reasoning')).toBeInTheDocument();
    expect(screen.getByText('Tool calls')).toBeInTheDocument();
    expect(screen.getByText('Skill loads')).toBeInTheDocument();
    expect(screen.getByText('Agent delegation')).toBeInTheDocument();
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText('Compacted history')).not.toBeInTheDocument();
  });

  it('keeps usage visible while recalculating after compaction succeeds', async () => {
    const user = userEvent.setup();
    sessionApiGetContextUsageMock
      .mockResolvedValueOnce({
        sessionID: 'sess-1',
        usedTokens: 900,
        contextWindow: 1000,
        percent: 90,
        source: 'estimated',
        estimatedTokens: 900,
        compactedTokens: 0,
        segments: [
          { key: 'conversation', tokens: 900, included: true, source: 'estimated' },
        ],
        excludedSegments: [],
      })
      .mockResolvedValueOnce({
        sessionID: 'sess-1',
        usedTokens: 420,
        contextWindow: 1000,
        percent: 42,
        source: 'estimated',
        estimatedTokens: 420,
        compactedTokens: 0,
        segments: [
          { key: 'conversation', tokens: 420, included: true, source: 'estimated' },
        ],
        excludedSegments: [],
      });
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'stale-user',
          role: 'user',
          parts: [{ id: 'stale-user-part', type: 'text', text: 'x'.repeat(4000) }] as Message['parts'],
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

    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    const contextButton = await screen.findByRole('button', { name: 'chat.contextUsageTitle' });
    await user.click(contextButton);
    expect(await screen.findByText('Conversation')).toBeInTheDocument();
    expect(screen.getByText('900')).toBeInTheDocument();

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'context.compacted',
        properties: { sessionID: 'sess-1' },
      });
    });

    expect(screen.getByText('900')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('420')).toBeInTheDocument();
    });
    expect(screen.getByText('Conversation')).toBeInTheDocument();
  });

  it('refreshes context usage after compaction fails', async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    sessionApiGetContextUsageMock
      .mockResolvedValueOnce({
        sessionID: 'sess-1',
        usedTokens: 900,
        contextWindow: 1000,
        percent: 90,
        source: 'estimated',
        estimatedTokens: 900,
        compactedTokens: 0,
        segments: [
          { key: 'conversation', tokens: 900, included: true, source: 'estimated' },
        ],
        excludedSegments: [],
      })
      .mockResolvedValueOnce({
        sessionID: 'sess-1',
        usedTokens: 420,
        contextWindow: 1000,
        percent: 42,
        source: 'estimated',
        estimatedTokens: 420,
        compactedTokens: 0,
        segments: [
          { key: 'conversation', tokens: 420, included: true, source: 'estimated' },
        ],
        excludedSegments: [],
      });

    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true, onError }));

    await waitFor(() => {
      expect(sessionApiGetContextUsageMock).toHaveBeenCalledTimes(1);
    });

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.status',
        properties: {
          sessionID: 'sess-1',
          status: { type: 'compacting', message: 'Compacting context…' },
        },
      });
    });
    const contextButton = await screen.findByRole('button', { name: 'chat.contextUsageTitle' });
    await user.click(contextButton);
    expect(screen.getByText('900')).toBeInTheDocument();

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.error',
        properties: {
          sessionID: 'sess-1',
          error: { message: 'provider unavailable' },
        },
      });
    });

    await waitFor(() => {
      expect(sessionApiGetContextUsageMock).toHaveBeenCalledTimes(2);
    });
    expect(onError).toHaveBeenCalledWith('provider unavailable');

    expect(screen.getByText('420')).toBeInTheDocument();
  });

  it('does not refetch immediately after a pushed context usage snapshot', async () => {
    sessionApiGetContextUsageMock.mockResolvedValueOnce({
      sessionID: 'sess-1',
      usedTokens: 900,
      contextWindow: 1000,
      percent: 90,
      source: 'estimated',
      estimatedTokens: 900,
      compactedTokens: 0,
      segments: [
        { key: 'conversation', tokens: 900, included: true, source: 'estimated' },
      ],
      excludedSegments: [],
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    await waitFor(() => {
      expect(sessionApiGetContextUsageMock).toHaveBeenCalledTimes(1);
    });

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'context.usage.updated',
        properties: {
          sessionID: 'sess-1',
          usedTokens: 420,
          contextWindow: 1000,
          percent: 42,
          source: 'estimated',
          estimatedTokens: 420,
          compactedTokens: 0,
          segments: [
            { key: 'conversation', tokens: 420, included: true, source: 'estimated' },
          ],
          excludedSegments: [],
        },
      });
      useSSEOptionsRef.current.onEvent({
        type: 'session.status',
        properties: {
          sessionID: 'sess-1',
          status: { type: 'idle' },
        },
      });
    });

    expect(sessionApiGetContextUsageMock).toHaveBeenCalledTimes(1);
  });
});

describe('SessionChat compaction divider', () => {
  it('keeps archived history visible before the compressed-context divider', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'old-user',
          role: 'user',
          compacted: true,
          parts: [{ id: 'old-user-part', type: 'text', text: 'old visible request' }] as Message['parts'],
        }),
        makeMessage({
          id: 'summary-1',
          role: 'assistant',
          finish: 'summary',
          parts: [],
        }),
        makeMessage({
          id: 'assistant-1',
          role: 'assistant',
          finish: 'stop',
          parts: [{ id: 'assistant-1-part', type: 'text', text: 'current answer' }] as Message['parts'],
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

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    const dividerLabel = screen.getByText('上下文已压缩');
    expect(dividerLabel).toBeInTheDocument();
    expect(dividerLabel).not.toHaveClass('rounded-full');
    expect(dividerLabel).not.toHaveClass('border');
    expect(dividerLabel).not.toHaveClass('bg-white');
    expect(screen.getByText('old visible request')).toBeInTheDocument();
    expect(screen.getByText('current answer')).toBeInTheDocument();
  });

  it('renders one chronological divider for each summary message', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'old-user',
          role: 'user',
          compacted: true,
          parts: [{ id: 'old-user-part', type: 'text', text: 'first archived turn' }] as Message['parts'],
        }),
        makeMessage({
          id: 'summary-1',
          role: 'assistant',
          finish: 'summary',
          parts: [],
        }),
        makeMessage({
          id: 'middle-user',
          role: 'user',
          compacted: true,
          parts: [{ id: 'middle-user-part', type: 'text', text: 'second archived turn' }] as Message['parts'],
        }),
        makeMessage({
          id: 'summary-2',
          role: 'assistant',
          finish: 'summary',
          parts: [],
        }),
        makeMessage({
          id: 'assistant-1',
          role: 'assistant',
          finish: 'stop',
          parts: [{ id: 'assistant-1-part', type: 'text', text: 'current answer' }] as Message['parts'],
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

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    expect(screen.getByText('first archived turn')).toBeInTheDocument();
    expect(screen.getByText('second archived turn')).toBeInTheDocument();
    expect(screen.getAllByText('上下文已压缩')).toHaveLength(2);
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

describe('streaming activity helpers', () => {
  it('detects pending and running tool parts as active', () => {
    expect(hasActiveToolPart([
      { id: 'tool-1', type: 'tool', state: { status: 'pending' } } as Message['parts'][number],
    ])).toBe(true);
    expect(hasActiveToolPart([
      { id: 'tool-1', type: 'tool', state: { status: 'running' } } as Message['parts'][number],
    ])).toBe(true);
  });

  it('does not treat completed or error tool parts as active', () => {
    expect(hasActiveToolPart([
      { id: 'tool-1', type: 'tool', state: { status: 'completed' } } as Message['parts'][number],
      { id: 'tool-2', type: 'tool', state: { status: 'error' } } as Message['parts'][number],
    ])).toBe(false);
  });

  it('keeps busy, compacting, and retry session statuses active', () => {
    expect(isActiveSessionStatus({ type: 'busy' })).toBe(true);
    expect(isActiveSessionStatus({ type: 'compacting' })).toBe(true);
    expect(isActiveSessionStatus({ type: 'retry' })).toBe(true);
    expect(isActiveSessionStatus({ type: 'idle' })).toBe(false);
    expect(isActiveSessionStatus(undefined)).toBe(false);
  });
});

describe('SessionChat fallback polling', () => {
  it('does not finish streaming while fetched messages still contain a running tool', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const onStreamingDone = vi.fn();
    try {
      useSessionMessagesMock.mockReturnValue({
        messages: [
          makeMessage({
            id: 'assistant-1',
            finish: 'tool-calls',
            parts: [
              { id: 'tool-1', type: 'tool', state: { status: 'running' } } as Message['parts'][number],
            ],
          }),
        ],
        loading: false,
        refetch,
        addMessage: vi.fn(),
        updateMessage: vi.fn(),
        updateMessagePart: vi.fn(),
        replaceMessageText: vi.fn(),
        truncateAfterMessage: vi.fn(),
      });
      clientGetMock.mockResolvedValueOnce({
        data: [
          {
            info: {
              id: 'assistant-1',
              sessionID: 'sess-1',
              role: 'assistant',
              finish: 'tool-calls',
            },
            parts: [
              { id: 'tool-1', type: 'tool', state: { status: 'running' } },
            ],
          },
        ],
      });

      render(React.createElement(SessionChat, {
        sessionId: 'sess-1',
        live: true,
        onStreamingDone,
      }));
      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'session.status',
          properties: { sessionID: 'sess-1', status: { type: 'busy' } },
        });
      });

      await vi.advanceTimersByTimeAsync(5_000);

      expect(refetch).not.toHaveBeenCalled();
      expect(onStreamingDone).not.toHaveBeenCalled();
      expect(clientGetMock).toHaveBeenCalledWith('/api/session/sess-1/message');
    } finally {
      vi.useRealTimers();
    }
  });

  it('finishes streaming when only the local active tool ref is stale', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const onStreamingDone = vi.fn();
    try {
      useSessionMessagesMock.mockReturnValue({
        messages: [
          makeMessage({
            id: 'assistant-1',
            finish: 'stop',
            parts: [
              { id: 'text-1', type: 'text', text: 'done' } as Message['parts'][number],
            ],
          }),
        ],
        loading: false,
        refetch,
        addMessage: vi.fn(),
        updateMessage: vi.fn(),
        updateMessagePart: vi.fn(),
        replaceMessageText: vi.fn(),
        truncateAfterMessage: vi.fn(),
      });
      clientGetMock.mockImplementation((url: string) => {
        if (url === '/api/session/sess-1/message') {
          return Promise.resolve({
            data: [
              {
                info: {
                  id: 'assistant-1',
                  sessionID: 'sess-1',
                  role: 'assistant',
                  finish: 'stop',
                },
                parts: [
                  { id: 'text-1', type: 'text', text: 'done' },
                ],
              },
            ],
          });
        }
        if (url === '/api/session/status') {
          return Promise.resolve({ data: { 'sess-1': { type: 'idle' } } });
        }
        return Promise.resolve({ data: {} });
      });

      render(React.createElement(SessionChat, {
        sessionId: 'sess-1',
        live: true,
        onStreamingDone,
      }));
      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'session.status',
          properties: { sessionID: 'sess-1', status: { type: 'busy' } },
        });
        useSSEOptionsRef.current.onEvent({
          type: 'message.part.updated',
          properties: {
            part: {
              id: 'tool-1',
              messageID: 'assistant-1',
              sessionID: 'sess-1',
              type: 'tool',
              state: { status: 'running' },
            },
          },
        });
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(refetch).toHaveBeenCalled();
      expect(onStreamingDone).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('areChatMessagePartsRenderEqual', () => {
  it('detects streamed text updates even when a later tool part exists', () => {
    const sharedToolPart = {
      id: 'tool-1',
      type: 'tool',
      tool: 'todo',
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
