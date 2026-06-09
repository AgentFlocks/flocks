import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Message } from '@/types';

import {
  areChatMessagePartsRenderEqual,
  buildTodoSummary,
  ChatToolPart,
  dedupeUploadedDocumentAttachments,
  default as SessionChat,
  getEditingActionBarClassName,
  getMessageBubbleClassName,
  getMessageGroupClassName,
  getRenderableFileUrl,
  getRegenerateTruncateTarget,
  getStandaloneThinkingBubbleClassName,
  getUserAvatarContainerClassName,
  getUserAvatarSpacerClassName,
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
const useSessionMessagesMock = vi.fn();
const tMock = (key: string) => ({
  'chat.placeholder': '请输入消息',
  'chat.emptyText': '暂无消息',
  'chat.sending': '发送中...',
  'chat.thinking': '思考中...',
  'chat.streaming': '继续输出中...',
  'chat.compacting': '压缩中...',
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
  'smartAssistant': '智能助手',
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
    i18n: { language: 'zh-CN' },
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

describe('getRenderableFileUrl', () => {
  it('converts local file URLs to the guarded file download endpoint', () => {
    expect(getRenderableFileUrl('file:///tmp/channel%20image.png')).toBe(
      '/api/file/download?path=%2Ftmp%2Fchannel%20image.png',
    );
  });

  it('leaves browser-readable URLs unchanged', () => {
    expect(getRenderableFileUrl('https://example.com/image.png')).toBe('https://example.com/image.png');
    expect(getRenderableFileUrl('data:image/png;base64,abc')).toBe('data:image/png;base64,abc');
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

    expect(container.textContent).toContain('Progress 1/3 · In progress 1');
    expect(container.textContent).toContain('Todo 阶段');
    expect(container.textContent).toContain('定位 todo 摘要问题中');
    expect(container.textContent).toContain('completed');
    expect(container.textContent).not.toContain('[object Object]');
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
