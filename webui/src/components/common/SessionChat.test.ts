import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Message } from '@/types';

import {
  areChatTimelineItemsRenderEqual,
  buildInstructionDisplayText,
  buildChatTimelineItems,
  buildContextUsageBreakdown,
  buildTodoSummary,
  ChatMessageBubble,
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
  shouldForwardSSEEventToParent,
  shouldRefetchFinishedMessage,
  truncateToolDisplayText,
} from './SessionChat';
import { areChatMessagePartsRenderEqual } from './sessionChatRenderEquality';

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
const sessionApiGetContextUsageMock = vi.fn();
const sessionApiGetMock = vi.fn();
const sessionApiUpdateMock = vi.fn();
const useSessionMessagesMock = vi.fn();
const useSSEOptionsRef = vi.hoisted(() => ({ current: null as any }));
const tMock = (key: string, options?: Record<string, unknown>) => {
  const value = ({
  'chat.placeholder': '请输入消息',
  'chat.emptyText': '暂无消息',
  'chat.sending': '发送中...',
  'chat.thinking': '思考中...',
  'chat.streaming': '继续输出中...',
  'chat.process.title': '查看 {{count}} 个步骤',
  'chat.process.deepThinking': '深度思考',
  'chat.process.reasoningCount': '{{count}} 段思考',
  'chat.process.toolCount': '{{count}} 次工具调用',
  'chat.process.textCount': '{{count}} 段中间回复',
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
  'chat.goal.dismiss': 'Dismiss goal notice',
  'chat.goal.status.active': 'Goal',
  'chat.goal.status.completed': 'Completed',
  'chat.goal.status.blocked': 'Blocked',
  'chat.goal.status.paused': 'Paused',
  'chat.mention.title': '选择 Agent',
  'chat.mention.navigate': '导航',
  'chat.mention.select': '选择',
  'chat.tool.pending': '等待中',
  'chat.tool.running': '执行中',
  'chat.tool.completed': '已完成',
  'chat.tool.error': '失败',
  'chat.tool.loadSkill': '加载技能',
  'chat.tool.actions.readFile': '读取文件',
  'chat.tool.actions.writeFile': '写入文件',
  'chat.tool.actions.editFile': '编辑文件',
  'chat.tool.actions.executeCommand': '执行命令',
  'chat.tool.actions.askQuestion': '向用户提问',
  'chat.tool.actions.installSkill': '安装技能',
  'chat.tool.actions.addProvider': '添加模型服务',
  'chat.tool.todoUpdated': '已更新待办',
  'chat.tool.inputParams': '输入参数',
  'chat.tool.progress.writingFile': '正在写入文件…',
  'chat.tool.progress.editingFile': '正在编辑文件…',
  'chat.tool.progress.working': '正在执行此操作…',
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
  'chat.bash.command': '命令',
  'chat.bash.workdir': '工作目录',
  'chat.bash.duration': '耗时',
  'chat.bash.timeout': '超时',
  'chat.bash.timedOut': '已超时',
  'chat.bash.aborted': '已中止',
  'chat.bash.stdout': '标准输出',
  'chat.bash.stderr': '标准错误',
  'chat.bash.output': '输出',
  'chat.bash.noOutput': '无输出',
  'chat.questionResult.answered': '已回答',
  'chat.questionResult.unanswered': '未回答',
  'chat.questionResult.questionLabel': '问题',
  'chat.questionResult.answerLabel': '回答',
  'chat.questionResult.yes': '是',
  'chat.questionResult.no': '否',
  'question.needsAnswer': '需要你的回答',
  'question.singleSelect': '单选',
  'question.confirm': '确认',
  'question.skip': '跳过',
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

vi.mock('@/features/session-chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/session-chat')>();
  return {
    ...actual,
    usePendingQuestions: () => pendingQuestionsHookMock,
  };
});

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
    get: (...args: unknown[]) => sessionApiGetMock(...args),
    update: (...args: unknown[]) => sessionApiUpdateMock(...args),
    listPromptQueue: (...args: unknown[]) => sessionApiListPromptQueueMock(...args),
    enqueuePrompt: (...args: unknown[]) => sessionApiEnqueuePromptMock(...args),
    updateQueuedPrompt: (...args: unknown[]) => sessionApiUpdateQueuedPromptMock(...args),
    removeQueuedPrompt: (...args: unknown[]) => sessionApiRemoveQueuedPromptMock(...args),
    runQueuedPromptNow: (...args: unknown[]) => sessionApiRunQueuedPromptNowMock(...args),
    updateMessagePart: (...args: unknown[]) => sessionApiUpdateMessagePartMock(...args),
    resendMessage: (...args: unknown[]) => sessionApiResendMessageMock(...args),
    regenerateMessage: (...args: unknown[]) => sessionApiRegenerateMessageMock(...args),
    getContextUsage: (...args: unknown[]) => sessionApiGetContextUsageMock(...args),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  const localStorageData = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      clear: vi.fn(() => localStorageData.clear()),
      getItem: vi.fn((key: string) => localStorageData.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => {
        localStorageData.set(key, String(value));
      }),
      removeItem: vi.fn((key: string) => {
        localStorageData.delete(key);
      }),
    },
  });
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
  sessionApiGetMock.mockResolvedValue({});
  sessionApiUpdateMock.mockResolvedValue({});
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
  pendingQuestionsHookMock.pendingQuestions = {};
  useSSEOptionsRef.current = null;
  useSessionMessagesMock.mockReturnValue({
    messages: [],
    loading: false,
    refetch: vi.fn(),
    addMessage: vi.fn(),
    updateMessage: vi.fn(),
    updateMessagePart: vi.fn(),
    removeMessage: vi.fn(),
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

type FetchedMessageFixture = {
  info: {
    id: string;
    sessionID: string;
    role: 'user' | 'assistant';
    parentID?: string;
    finish?: string | null;
  };
  parts: Message['parts'];
};

function makeFetchedMessage(
  info: Omit<FetchedMessageFixture['info'], 'sessionID'>,
  parts: Message['parts'] = [],
): FetchedMessageFixture {
  return {
    info: { sessionID: 'sess-1', ...info },
    parts,
  };
}

function mockFallbackPolling({
  localMessages,
  fetchedMessages,
  refetch,
  status = { 'sess-1': { type: 'busy' } },
}: {
  localMessages: Message[];
  fetchedMessages: FetchedMessageFixture[];
  refetch: () => unknown;
  status?: Record<string, { type: string }>;
}) {
  useSessionMessagesMock.mockReturnValue({
    messages: localMessages,
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
        data: {
          items: fetchedMessages,
          hasMore: false,
          nextBefore: null,
        },
      });
    }
    if (url === '/api/session/status') {
      return Promise.resolve({ data: status });
    }
    return Promise.resolve({ data: {} });
  });
}

async function startFallbackPolling(onStreamingDone: () => void) {
  render(React.createElement(SessionChat, {
    sessionId: 'sess-1',
    live: true,
    onStreamingDone,
  }));
  await act(async () => {
    await Promise.resolve();
  });
  clientGetMock.mockClear();
  act(() => {
    useSSEOptionsRef.current.onEvent({
      type: 'session.status',
      properties: { sessionID: 'sess-1', status: { type: 'busy' } },
    });
  });
}

function mockStatefulSessionMessages() {
  useSessionMessagesMock.mockImplementation(() => {
    const [messages, setMessages] = React.useState<Message[]>([]);
    const upsertMessage = (messageInfo: Partial<Message> & { id: string }) => setMessages((prev) => {
      const existingIndex = prev.findIndex((message) => message.id === messageInfo.id);
      if (existingIndex >= 0) {
        const updated = [...prev];
        updated[existingIndex] = {
          ...updated[existingIndex],
          ...messageInfo,
          parts: messageInfo.parts ?? updated[existingIndex].parts,
          finish: messageInfo.finish ?? updated[existingIndex].finish,
        } as Message;
        return updated;
      }
      return [
        ...prev,
        makeMessage({
          id: messageInfo.id,
          sessionID: messageInfo.sessionID,
          role: messageInfo.role ?? 'assistant',
          parts: messageInfo.parts ?? [],
          parentID: messageInfo.parentID,
          finish: messageInfo.finish,
        } as Partial<Message> & { id: string }),
      ];
    });

    return {
      messages,
      loading: false,
      refetch: vi.fn(),
      addMessage: (message: Message) => setMessages((prev) => [...prev, message]),
      updateMessage: upsertMessage,
      updateMessagePart: vi.fn(),
      removeMessage: (messageId: string) => setMessages((prev) => prev.filter(
        (message) => message.id !== messageId,
      )),
      replaceMessageText: vi.fn(),
      markMessageStopped: (messageId: string) => setMessages((prev) => prev.map(
        (message) => (message.id === messageId ? { ...message, finish: 'stop' } : message),
      )),
      truncateAfterMessage: vi.fn(),
    };
  });
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
  // The message column owns the available width, so the inner bubble only
  // controls intrinsic sizing (`w-auto` vs `w-full`). Tests here therefore
  // assert width semantics, not legacy max-width literals.
  it('allows every bubble variant to shrink within the message column', () => {
    for (const compact of [false, true]) {
      for (const isUser of [false, true]) {
        const className = getMessageBubbleClassName({ compact, isUser, isEditing: false });
        expect(className).toContain('min-w-0');
        expect(className).toContain('max-w-full');
      }
    }
  });

  it('keeps non-editing user bubbles auto-sized in full layout', () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('w-auto');
    expect(className.split(' ')).not.toContain('w-full');
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

  it('fills the fixed compact assistant message column', () => {
    const className = getMessageBubbleClassName({
      compact: true,
      isUser: false,
      isEditing: false,
    });

    expect(className).toContain('w-full');
    expect(className).toContain('max-w-full');
  });

  it.each([false, true])('keeps assistant replies in the transparent content flow when compact=%s', (compact) => {
    const className = getMessageBubbleClassName({
      compact,
      isUser: false,
      isEditing: false,
    });

    expect(className).toContain('bg-transparent');
    expect(className).not.toContain('bg-white');
    expect(className).not.toContain('border-zinc-200/90');
    expect(className).not.toContain('shadow-sm');
    expect(className).not.toContain('rounded-[20px]');
    expect(className).not.toContain('rounded-[24px]');
  });

  it('keeps compact user bubbles content-sized when not editing', () => {
    const className = getMessageBubbleClassName({
      compact: true,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('max-w-full');
    expect(className.split(/\s+/)).not.toContain('w-full');
  });

  it.each([false, true])('uses a neutral user bubble background when compact=%s', (compact) => {
    const className = getMessageBubbleClassName({
      compact,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('bg-[#fafaf8]');
    expect(className).toContain('border-black/[0.07]');
    expect(className).not.toContain('bg-sky-50');
    expect(className).not.toContain('border-sky-100');
  });
});

describe('getMessageGroupClassName', () => {
  it('caps full-layout user messages at 88% width', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain('max-w-[88%]');
    expect(className).toContain('w-fit');
  });

  it('expands editing user messages to the full content width', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: true,
      isEditing: true,
    });

    expect(className).toContain('w-full');
    expect(className).toContain('max-w-full');
  });

  it('keeps assistant messages full width in full layout', () => {
    const className = getMessageGroupClassName({
      compact: false,
      isUser: false,
      isEditing: false,
    });

    expect(className).toBe('w-full');
  });

  it('uses the full compact message-list width for assistant messages', () => {
    const className = getMessageGroupClassName({
      compact: true,
      isUser: false,
      isEditing: false,
    });

    expect(className).toBe('w-full max-w-full');
  });
});

describe('SessionChat copy action', () => {
  it('falls back when async clipboard is unavailable', async () => {
    const user = userEvent.setup();
    const execCommand = vi.fn().mockReturnValue(true);

    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    });

    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-copy',
          role: 'assistant',
          parts: [{ id: 'text-1', type: 'text', text: 'copy this result' }],
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
      display: { compact: false, showActions: true },
    }));

    await user.click(screen.getByRole('button', { name: 'chat.copy' }));

    expect(execCommand).toHaveBeenCalledWith('copy');
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

  it('clears the standalone thinking indicator when stopping before Rex creates an assistant message', async () => {
    const user = userEvent.setup();
    mockStatefulSessionMessages();
    clientPostMock.mockImplementation((url: string) => {
      if (url.endsWith('/prompt_async')) {
        return new Promise(() => {});
      }
      if (url.endsWith('/abort')) {
        return new Promise(() => {});
      }
      return Promise.resolve({ data: {} });
    });

    const { container } = render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '接入设备');
    await user.click(container.querySelector('button[class*="bg-sky-500"]')!);

    await waitFor(() => {
      expect(container.querySelectorAll('.animate-bounce').length).toBeGreaterThanOrEqual(3);
    });

    await user.click(screen.getByTitle('chat.stopTitle'));

    await waitFor(() => {
      expect(container.querySelectorAll('.animate-bounce')).toHaveLength(0);
    });

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'message.updated',
        properties: {
          info: {
            id: 'assistant-late',
            sessionID: 'sess-1',
            role: 'assistant',
          },
        },
      });
    });

    await waitFor(() => {
      expect(container.querySelectorAll('.animate-bounce')).toHaveLength(0);
    });
  });

  it('does not show dots again when busy status arrives after abort settles', async () => {
    const user = userEvent.setup();
    mockStatefulSessionMessages();
    clientPostMock.mockImplementation((url: string) => {
      if (url.endsWith('/prompt_async')) {
        return new Promise(() => {});
      }
      return Promise.resolve({ data: {} });
    });

    const { container } = render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '已连接涉笔');
    await user.click(container.querySelector('button[class*="bg-sky-500"]')!);

    await waitFor(() => {
      expect(container.querySelectorAll('.animate-bounce').length).toBeGreaterThanOrEqual(3);
    });

    vi.useFakeTimers();
    try {
      act(() => {
        screen.getByTitle('chat.stopTitle').click();
      });
      await act(async () => {});

      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'message.updated',
          properties: {
            info: {
              id: 'assistant-late',
              sessionID: 'sess-1',
              role: 'assistant',
            },
          },
        });
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_100);
      });

      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'session.status',
          properties: { sessionID: 'sess-1', status: { type: 'busy' } },
        });
      });

      expect(container.querySelectorAll('.animate-bounce')).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not mark the active assistant stopped when abort request fails', async () => {
    const user = userEvent.setup();
    const markMessageStopped = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'user-1',
          role: 'user',
          parts: [{ id: 'user-1-part', type: 'text', text: 'hello' }] as Message['parts'],
        }),
        makeMessage({
          id: 'assistant-1',
          role: 'assistant',
          parts: [{ id: 'assistant-1-part', type: 'text', text: 'partial response' }] as Message['parts'],
          finish: null,
        }),
      ],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      markMessageStopped,
      truncateAfterMessage: vi.fn(),
    });
    clientPostMock.mockImplementation((url: string) => {
      if (url.endsWith('/abort')) {
        return Promise.reject(new Error('abort failed'));
      }
      return Promise.resolve({ data: {} });
    });

    try {
      render(React.createElement(SessionChat, {
        sessionId: 'sess-1',
      }));

      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'session.status',
          properties: { sessionID: 'sess-1', status: { type: 'busy' } },
        });
      });

      await waitFor(() => {
        expect(screen.getByTitle('chat.stopTitle')).toBeInTheDocument();
      });

      await user.click(screen.getByTitle('chat.stopTitle'));

      await waitFor(() => {
        expect(consoleError).toHaveBeenCalled();
      });
      expect(clientPostMock).toHaveBeenCalledWith('/api/session/sess-1/abort');
      expect(markMessageStopped).not.toHaveBeenCalled();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('removes an intermediate assistant when message.removed arrives', () => {
    const removeMessage = vi.fn();
    useSessionMessagesMock.mockReturnValue({
      messages: [makeMessage({ id: 'assistant-failed', role: 'assistant', parts: [] })],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      removeMessage,
      replaceMessageText: vi.fn(),
      markMessageStopped: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'message.removed',
        properties: {
          sessionID: 'sess-1',
          messageID: 'assistant-failed',
        },
      });
    });

    expect(removeMessage).toHaveBeenCalledWith('assistant-failed');
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

  it('sends initialMessage with an instruction display label', async () => {
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      initialMessage: 'Please create a SOC workspace custom page.',
      initialDisplayText: buildInstructionDisplayText('创建 SOC 自定义页面'),
    }));

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({
          displayText: '@@flocks-instruction:创建 SOC 自定义页面',
          parts: expect.any(Array),
        }),
      );
    });
  });
});

describe('SessionChat composer controls', () => {
  it('enables Auto on an existing session before sending without a model override', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      modelAuto: true,
      model: null,
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), 'continue{enter}');

    await waitFor(() => {
      expect(sessionApiUpdateMock).toHaveBeenCalledWith('sess-1', {
        model_auto: true,
        model_pinned: false,
      });
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({
          messageID: expect.any(String),
          parts: [{ type: 'text', text: 'continue' }],
        }),
      );
    });
    expect(sessionApiUpdateMock.mock.invocationCallOrder[0]).toBeLessThan(
      clientPostMock.mock.invocationCallOrder[0],
    );
  });

  it('keeps the disabled send button visible in dark mode', () => {
    const { container } = render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    const disabledButtons = Array.from(container.querySelectorAll('button:disabled'));
    const sendButton = disabledButtons.find((button) => button.querySelector('svg'));

    expect(sendButton?.className).toContain('dark:bg-[#46515e]');
    expect(sendButton?.className).toContain('dark:text-[#b8c2cc]');
    expect(sendButton?.className).toContain('dark:border-[#5a6573]');
  });
});

describe('shouldRenderMessage', () => {
  it('keeps active empty assistant messages eligible for the thinking indicator', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-active',
      role: 'assistant',
      parts: [],
      finish: null,
    }), { isActive: true })).toBe(true);
  });

  it('hides inactive empty assistant messages', () => {
    expect(shouldRenderMessage(makeMessage({
      id: 'assistant-inactive',
      role: 'assistant',
      parts: [],
      finish: null,
    }))).toBe(false);
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

describe('ChatMessageBubble reasoning streaming', () => {
  it.each(['reasoning', 'thinking'] as const)(
    'paces an active %s part after a tool and flushes the completed text',
    (partType) => {
      type RafCallback = (time: number) => void;
      const callbacks = new Map<number, RafCallback>();
      let nextRafId = 0;
      vi.stubGlobal('requestAnimationFrame', (callback: RafCallback) => {
        const id = ++nextRafId;
        callbacks.set(id, callback);
        return id;
      });
      vi.stubGlobal('cancelAnimationFrame', (id: number) => {
        callbacks.delete(id);
      });

      const makeReasoningMessage = (text: string, finish?: Message['finish']) => makeMessage({
        id: 'assistant-reasoning-stream',
        role: 'assistant',
        finish,
        parts: [
          {
            id: 'tool-before-reasoning',
            messageID: 'assistant-reasoning-stream',
            sessionID: 'sess-1',
            type: 'tool',
            tool: 'read',
            state: { status: 'completed', output: 'done' },
          } as any,
          {
            id: 'reasoning-stream',
            messageID: 'assistant-reasoning-stream',
            sessionID: 'sess-1',
            type: partType,
            text,
          } as any,
        ],
      });

      let unmount = () => {};
      try {
        const rendered = render(React.createElement(ChatMessageBubble, {
          message: makeReasoningMessage('思'),
          isActive: true,
        }));
        unmount = rendered.unmount;

        rendered.rerender(React.createElement(ChatMessageBubble, {
          message: makeReasoningMessage('思考过程'),
          isActive: true,
        }));

        expect(screen.getByText('思考中...')).toBeInTheDocument();
        expect(screen.getByText('思')).toBeInTheDocument();
        expect(screen.queryByText('思考过程')).not.toBeInTheDocument();

        act(() => {
          const pending = [...callbacks.values()];
          callbacks.clear();
          pending.forEach(callback => callback(1000 / 60));
        });
        expect(screen.getByText('思考')).toBeInTheDocument();

        rendered.rerender(React.createElement(ChatMessageBubble, {
          message: makeReasoningMessage('思考过程', 'stop'),
          isActive: false,
        }));
        expect(screen.getByText('思考过程')).toBeInTheDocument();
      } finally {
        unmount();
        vi.unstubAllGlobals();
      }
    },
  );

  it('does not animate reasoning while its process group is closed', () => {
    let nextRafId = 0;
    const requestAnimationFrameSpy = vi.fn(() => ++nextRafId);
    vi.stubGlobal('requestAnimationFrame', requestAnimationFrameSpy);
    vi.stubGlobal('cancelAnimationFrame', vi.fn());

    const messageId = 'assistant-hidden-reasoning';
    const processGroupKey = `${messageId}:process:0`;
    const makeHiddenReasoningMessage = (text: string) => makeMessage({
      id: messageId,
      role: 'assistant',
      parts: [
        {
          id: 'tool-before-hidden-reasoning',
          messageID: messageId,
          sessionID: 'sess-1',
          type: 'tool',
          tool: 'read',
          state: { status: 'completed', output: 'done' },
        } as any,
        {
          id: 'hidden-reasoning',
          messageID: messageId,
          sessionID: 'sess-1',
          type: 'reasoning',
          text,
        } as any,
      ],
    });

    let unmount = () => {};
    try {
      const rendered = render(React.createElement(ChatMessageBubble, {
        message: makeHiddenReasoningMessage('隐藏'),
        isActive: true,
        collapseIntermediateSteps: true,
        processGroupsOpenWhileActive: true,
        processGroupOpenState: { [processGroupKey]: false },
      }));
      unmount = rendered.unmount;

      rendered.rerender(React.createElement(ChatMessageBubble, {
        message: makeHiddenReasoningMessage('隐藏更新'),
        isActive: true,
        collapseIntermediateSteps: true,
        processGroupsOpenWhileActive: true,
        processGroupOpenState: { [processGroupKey]: false },
      }));
      expect(requestAnimationFrameSpy).not.toHaveBeenCalled();

      rendered.rerender(React.createElement(ChatMessageBubble, {
        message: makeHiddenReasoningMessage('隐藏更新'),
        isActive: true,
        collapseIntermediateSteps: true,
        processGroupsOpenWhileActive: true,
        processGroupOpenState: { [processGroupKey]: true },
      }));
      expect(requestAnimationFrameSpy).not.toHaveBeenCalled();

      rendered.rerender(React.createElement(ChatMessageBubble, {
        message: makeHiddenReasoningMessage('隐藏更新继续'),
        isActive: true,
        collapseIntermediateSteps: true,
        processGroupsOpenWhileActive: true,
        processGroupOpenState: { [processGroupKey]: true },
      }));
      expect(requestAnimationFrameSpy).toHaveBeenCalledTimes(1);
    } finally {
      unmount();
      vi.unstubAllGlobals();
    }
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

describe('shouldForwardSSEEventToParent', () => {
  it('forwards global workflow, task, and session update events', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'workflow.updated',
      properties: { id: 'workflow-1' },
    }, 'sess-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'task.updated',
      properties: { executionID: 'task-1' },
    }, 'sess-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'session.updated',
      properties: { id: 'other-session' },
    }, 'sess-1')).toBe(true);
  });

  it('forwards chat events only for the current session', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'message.part.updated',
      properties: { part: { sessionID: 'sess-1' } },
    }, 'sess-1')).toBe(true);
    expect(shouldForwardSSEEventToParent({
      type: 'message.part.updated',
      properties: { part: { sessionID: 'other-session' } },
    }, 'sess-1')).toBe(false);
    expect(shouldForwardSSEEventToParent({
      type: 'context.usage.updated',
      properties: { sessionID: 'other-session' },
    }, 'sess-1')).toBe(false);
  });

  it('skips heartbeat-style events without payloads', () => {
    expect(shouldForwardSSEEventToParent({
      type: 'server.heartbeat',
    }, 'sess-1')).toBe(false);
  });
});

describe('buildChatTimelineItems', () => {
  it('filters skipped and non-renderable messages while marking the active assistant', () => {
    const messages = [
      makeMessage({
        id: 'user-1',
        role: 'user',
        parts: [{ id: 'user-part', type: 'text', text: 'hello' }] as Message['parts'],
      }),
      makeMessage({
        id: 'synthetic-1',
        role: 'assistant',
        parts: [{ id: 'synthetic-part', type: 'text', text: '', synthetic: true }] as Message['parts'],
      }),
      makeMessage({
        id: 'assistant-empty',
        role: 'assistant',
        parts: [],
        finish: null,
      }),
      makeMessage({
        id: 'assistant-active',
        role: 'assistant',
        parts: [],
        finish: null,
      }),
    ];

    const items = buildChatTimelineItems({
      messages,
      skipIndices: new Set([1]),
      isStreaming: true,
    });

    expect(items.map((item) => item.message.id)).toEqual(['user-1', 'assistant-active']);
    expect(items.map((item) => item.isActive)).toEqual([false, true]);
  });

  it('keeps the same visible set when not streaming', () => {
    const messages = [
      makeMessage({
        id: 'assistant-empty',
        role: 'assistant',
        parts: [],
        finish: null,
      }),
      makeMessage({
        id: 'assistant-text',
        role: 'assistant',
        parts: [{ id: 'text-part', type: 'text', text: 'done' }] as Message['parts'],
        finish: 'stop',
      }),
    ];

    const items = buildChatTimelineItems({
      messages,
      skipIndices: new Set(),
      isStreaming: false,
    });

    expect(items.map((item) => item.message.id)).toEqual(['assistant-text']);
    expect(items[0].isActive).toBe(false);
  });
});

describe('areChatTimelineItemsRenderEqual', () => {
  it('treats cloned assistant messages with identical visible parts as equal', () => {
    const prevMessage = makeMessage({
      id: 'assistant-1',
      role: 'assistant',
      agent: 'rex',
      parts: [{ id: 'text-1', type: 'text', text: 'hello' }] as Message['parts'],
      finish: 'stop',
    });
    const nextMessage = {
      ...prevMessage,
      parts: [{ id: 'text-1', type: 'text', text: 'hello' }] as Message['parts'],
    };

    expect(areChatTimelineItemsRenderEqual(
      [{ message: prevMessage as any, isActive: false }],
      [{ message: nextMessage as any, isActive: false }],
    )).toBe(true);
  });

  it('detects visible text changes in otherwise stable timeline items', () => {
    const prevMessage = makeMessage({
      id: 'assistant-1',
      role: 'assistant',
      parts: [{ id: 'text-1', type: 'text', text: 'hello' }] as Message['parts'],
    });
    const nextMessage = {
      ...prevMessage,
      parts: [{ id: 'text-1', type: 'text', text: 'hello world' }] as Message['parts'],
    };

    expect(areChatTimelineItemsRenderEqual(
      [{ message: prevMessage as any, isActive: false }],
      [{ message: nextMessage as any, isActive: false }],
    )).toBe(false);
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

  it('renders assistant error messages when the only part is blank text', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-error-with-blank-text',
          role: 'assistant',
          parts: [{ id: 'blank-text', type: 'text', text: '' }] as Message['parts'],
          finish: 'error',
          error: {
            name: 'EmptyResponseError',
            data: { message: 'Model returned an empty response.' },
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

    expect(screen.getByText('Model returned an empty response.')).toBeInTheDocument();
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
    expect(screen.getByText('查看 2 个步骤')).toBeInTheDocument();
    expect(processGroup.querySelector('summary')).toHaveClass('text-sm');
    expect(processGroup.className).not.toContain('rounded-lg');
    expect(processGroup.closest('[data-process-output="true"]')?.className).not.toContain('bg-white');
    expect(screen.getByText('已读取当前 workflow.md。')).toBeInTheDocument();

    await user.click(screen.getByText('查看 2 个步骤'));

    expect(processGroup.open).toBe(true);
    expect(screen.getByTestId('chat-process-timeline')).toBeInTheDocument();
    expect(screen.getByTestId('chat-process-reasoning-step')).toHaveTextContent('深度思考');
    expect(screen.getByTestId('chat-process-reasoning-step').querySelector('button')).toHaveClass('text-sm');
    expect(screen.getByTestId('chat-process-tool-step')).toHaveTextContent('读取文件');
  });

  it('opens process groups while an assistant message is active and collapses after completion', () => {
    const activeMessage = makeMessage({
      id: 'assistant-active-process',
      role: 'assistant',
      parts: [
        {
          id: 'reason-active',
          messageID: 'assistant-active-process',
          sessionID: 'sess-1',
          type: 'reasoning',
          text: '先检查当前配置',
        } as any,
        {
          id: 'tool-active',
          messageID: 'assistant-active-process',
          sessionID: 'sess-1',
          type: 'tool',
          tool: 'read',
          callID: 'call-active',
          state: {
            status: 'running',
            input: { filePath: 'workflow.md' },
          },
        } as any,
      ],
    });

    const { rerender } = render(React.createElement(ChatMessageBubble, {
      message: activeMessage,
      isActive: true,
      collapseIntermediateSteps: true,
      processGroupsOpenWhileActive: true,
    }));

    const processGroup = screen.getByTestId('chat-process-group') as HTMLDetailsElement;
    expect(processGroup.open).toBe(true);

    rerender(React.createElement(ChatMessageBubble, {
      message: { ...activeMessage, finish: 'stop' } as Message,
      isActive: false,
      collapseIntermediateSteps: true,
      processGroupsOpenWhileActive: true,
    }));

    expect(processGroup.open).toBe(false);
  });

  it('uses one global process group and leaves only the final reply visible', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-global-process',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'reason-global-1',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'reasoning',
              text: '先分析需求',
            } as any,
            {
              id: 'tool-global-1',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'read',
              callID: 'call-global-1',
              state: {
                status: 'completed',
                input: { filePath: 'workflow.md' },
                output: 'workflow content',
              },
            } as any,
            {
              id: 'text-global-middle',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'text',
              text: '已经读取文件，继续检查相关配置。',
            } as any,
            {
              id: 'reason-global-2',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'thinking',
              text: '再整理最终结论',
            } as any,
            {
              id: 'tool-global-2',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'write',
              callID: 'call-global-2',
              state: {
                status: 'completed',
                input: { filePath: 'workflow.json' },
                output: 'ok',
              },
            } as any,
            {
              id: 'text-global-final',
              messageID: 'assistant-global-process',
              sessionID: 'sess-1',
              type: 'text',
              text: '最终结果已生成。',
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
    expect(screen.getAllByTestId('chat-process-group')).toHaveLength(1);
    expect(processGroup.open).toBe(false);
    expect(screen.getByText('查看 5 个步骤')).toBeInTheDocument();
    expect(screen.getByText('最终结果已生成。')).toBeVisible();
  });

  it('keeps a user-opened process group open when new tool parts arrive', async () => {
    const user = userEvent.setup();
    const makeProcessMessage = (includeNewTool = false) => makeMessage({
      id: 'assistant-live-process',
      role: 'assistant',
      parts: [
        {
          id: 'reason-live',
          messageID: 'assistant-live-process',
          sessionID: 'sess-1',
          type: 'reasoning',
          text: '先检查上下文',
        } as any,
        {
          id: 'tool-live-1',
          messageID: 'assistant-live-process',
          sessionID: 'sess-1',
          type: 'tool',
          tool: 'read',
          callID: 'call-live-1',
          state: {
            status: 'completed',
            input: { filePath: 'workflow.md' },
            output: 'workflow content',
          },
        } as any,
        ...(
          includeNewTool
            ? [{
                id: 'tool-live-2',
                messageID: 'assistant-live-process',
                sessionID: 'sess-1',
                type: 'tool',
                tool: 'write',
                callID: 'call-live-2',
                state: {
                  status: 'running',
                  input: { filePath: 'workflow.json' },
                },
              } as any]
            : []
        ),
      ],
    });

    useSessionMessagesMock.mockReturnValue({
      messages: [makeProcessMessage()],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    const { rerender } = render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    const processGroup = screen.getByTestId('chat-process-group') as HTMLDetailsElement;
    await user.click(screen.getByText('查看 2 个步骤'));
    expect(processGroup.open).toBe(true);

    useSessionMessagesMock.mockReturnValue({
      messages: [makeProcessMessage(true)],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    rerender(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    expect(screen.getByText('查看 3 个步骤')).toBeInTheDocument();
    expect(screen.getByTestId('chat-process-group')).toBe(processGroup);
    expect(processGroup.open).toBe(true);
    expect(screen.getAllByTestId('chat-process-tool-step')).toHaveLength(2);
  });

  it('restores a user-opened process group after switching away from a keyed session chat', async () => {
    const user = userEvent.setup();
    const processMessage = makeMessage({
      id: 'assistant-switch-process',
      role: 'assistant',
      finish: 'stop',
      parts: [
        {
          id: 'reason-switch',
          messageID: 'assistant-switch-process',
          sessionID: 'sess-1',
          type: 'reasoning',
          text: '先检查上下文',
        } as any,
        {
          id: 'tool-switch',
          messageID: 'assistant-switch-process',
          sessionID: 'sess-1',
          type: 'tool',
          tool: 'read',
          callID: 'call-switch',
          state: {
            status: 'completed',
            input: { filePath: 'workflow.md' },
            output: 'workflow content',
          },
        } as any,
      ],
    });
    const otherSessionMessage = makeMessage({
      id: 'assistant-other-session',
      sessionID: 'sess-2',
      role: 'assistant',
      finish: 'stop',
      parts: [
        {
          id: 'text-other-session',
          messageID: 'assistant-other-session',
          sessionID: 'sess-2',
          type: 'text',
          text: '另一个 session。',
        } as any,
      ],
    });
    useSessionMessagesMock.mockImplementation((currentSessionId: string | null) => ({
      messages: currentSessionId === 'sess-1' ? [processMessage] : [otherSessionMessage],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    }));

    const renderKeyedChat = (currentSessionId: string) => React.createElement(SessionChat, {
      key: currentSessionId,
      sessionId: currentSessionId,
      display: { collapseIntermediateSteps: true },
    });
    const { rerender } = render(renderKeyedChat('sess-1'));

    await user.click(screen.getByText('查看 2 个步骤'));
    expect(screen.getByTestId('chat-process-group')).toHaveProperty('open', true);

    rerender(renderKeyedChat('sess-2'));
    expect(screen.getByText('另一个 session。')).toBeVisible();

    rerender(renderKeyedChat('sess-1'));

    const restoredProcessGroup = screen.getByTestId('chat-process-group') as HTMLDetailsElement;
    expect(restoredProcessGroup.open).toBe(true);
    expect(screen.getByTestId('chat-process-tool-step').querySelector('summary')).toBeVisible();
  });

  it('keeps pending questions visible and folds answered questions into the process group', async () => {
    const user = userEvent.setup();
    const makeQuestionMessage = (status: 'running' | 'completed', includeFinalText = false) => makeMessage({
      id: 'assistant-question-process',
      role: 'assistant',
      finish: status === 'completed' ? 'stop' : undefined,
      parts: [
        {
          id: 'reason-before-question',
          messageID: 'assistant-question-process',
          sessionID: 'sess-1',
          type: 'reasoning',
          text: '需要先询问用户范围',
        } as any,
        {
          id: 'tool-question',
          messageID: 'assistant-question-process',
          sessionID: 'sess-1',
          type: 'tool',
          tool: 'question',
          callID: 'call-question',
          state: {
            status,
            input: {
              questions: [
                {
                  question: '选择范围',
                  header: '测试范围',
                  type: 'confirm',
                },
              ],
            },
            output: status === 'completed'
              ? 'User has answered your questions: "选择范围"="yes". You can now continue with the user\'s answers in mind.'
              : undefined,
            metadata: status === 'completed' ? { answers: [['yes']] } : {},
          },
        } as any,
        ...(
          includeFinalText
            ? [{
                id: 'text-after-question',
                messageID: 'assistant-question-process',
                sessionID: 'sess-1',
                type: 'text',
                text: '已按你的选择继续处理。',
              } as any]
            : []
        ),
      ],
    });

    pendingQuestionsHookMock.pendingQuestions = {
      'call-question': {
        requestId: 'req-question',
        questions: [
          {
            id: 'scope',
            type: 'choice',
            question: '选择范围',
            options: [
              { label: '全部', description: '检查全部内容' },
            ],
          },
        ],
      },
    };
    useSessionMessagesMock.mockReturnValue({
      messages: [makeQuestionMessage('running')],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    const { rerender } = render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    expect(screen.getByText('需要你的回答')).toBeVisible();
    expect(screen.getByText('选择范围')).toBeVisible();
    expect(screen.getByText('查看 1 个步骤')).toBeInTheDocument();

    pendingQuestionsHookMock.pendingQuestions = {};
    useSessionMessagesMock.mockReturnValue({
      messages: [makeQuestionMessage('completed')],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    rerender(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    expect(screen.queryByText('需要你的回答')).not.toBeInTheDocument();
    expect(screen.getByText('查看 2 个步骤')).toBeInTheDocument();
    expect(screen.getByText('向用户提问')).not.toBeVisible();
    expect(screen.queryByText('输入参数')).not.toBeInTheDocument();
    expect(screen.queryByText('输出结果')).not.toBeInTheDocument();

    await user.click(screen.getByText('查看 2 个步骤'));

    expect(screen.getByText('向用户提问')).toBeVisible();
    expect(screen.getByText('问题')).not.toBeVisible();

    await user.click(screen.getByText('向用户提问'));

    expect(screen.getByText('问题')).toBeVisible();
    expect(screen.getByText('回答')).toBeVisible();
    expect(screen.getAllByText('测试范围').length).toBeGreaterThan(0);
    expect(screen.getByText('是')).toBeVisible();

    await user.click(screen.getByText('查看 2 个步骤'));

    useSessionMessagesMock.mockReturnValue({
      messages: [makeQuestionMessage('completed', true)],
      loading: false,
      refetch: vi.fn(),
      addMessage: vi.fn(),
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    rerender(React.createElement(SessionChat, {
      sessionId: 'sess-1',
      display: { collapseIntermediateSteps: true },
    }));

    expect(screen.getByText('已按你的选择继续处理。')).toBeVisible();
    expect(screen.getByText('查看 2 个步骤')).toBeInTheDocument();
    expect(screen.getByText('向用户提问')).not.toBeVisible();
  });

  it('renders collapsed process groups inside the full compact assistant column', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-process-width',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'reason-width',
              messageID: 'assistant-process-width',
              sessionID: 'sess-1',
              type: 'reasoning',
              text: '需要先读取当前工作流',
            } as any,
            {
              id: 'tool-width',
              messageID: 'assistant-process-width',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'read',
              callID: 'call-width',
              state: {
                status: 'running',
                input: { filePath: 'workflow.md' },
              },
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

    const processGroup = screen.getByTestId('chat-process-group');
    expect(processGroup.closest('.w-full.max-w-full')).not.toBeNull();
  });

  it('can default grouped process details open without locking user toggles', async () => {
    const user = userEvent.setup();
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-process-open',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'reason-open',
              messageID: 'assistant-process-open',
              sessionID: 'sess-1',
              type: 'reasoning',
              text: '先分析当前会话',
            } as any,
            {
              id: 'tool-open',
              messageID: 'assistant-process-open',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'read',
              callID: 'call-open',
              state: {
                status: 'completed',
                input: { filePath: 'session.json' },
                output: 'ok',
              },
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
      display: { collapseIntermediateSteps: true, processGroupsDefaultOpen: true },
    }));

    const processGroup = screen.getByTestId('chat-process-group') as HTMLDetailsElement;
    expect(processGroup.open).toBe(true);

    await user.click(screen.getByText('查看 2 个步骤'));

    expect(processGroup.open).toBe(false);
  });

  it('does not split collapsed process groups on invisible step markers', () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'assistant-process-1',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'reason-1',
              messageID: 'assistant-process-1',
              sessionID: 'sess-1',
              type: 'reasoning',
              text: '先读取 workflow.md',
            } as any,
            {
              id: 'tool-1',
              messageID: 'assistant-process-1',
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
              id: 'empty-text-1',
              messageID: 'assistant-process-1',
              sessionID: 'sess-1',
              type: 'text',
              text: '',
            } as any,
          ],
        }),
        makeMessage({
          id: 'assistant-process-2',
          role: 'assistant',
          finish: 'stop',
          parts: [
            {
              id: 'step-start-1',
              messageID: 'assistant-process-2',
              sessionID: 'sess-1',
              type: 'step-start',
            } as any,
            {
              id: 'reason-2',
              messageID: 'assistant-process-2',
              sessionID: 'sess-1',
              type: 'thinking',
              text: '再生成 workflow.json',
            } as any,
            {
              id: 'tool-2',
              messageID: 'assistant-process-2',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'write',
              callID: 'call-2',
              state: {
                status: 'completed',
                input: { filePath: 'workflow.json' },
                output: 'ok',
              },
            } as any,
            {
              id: 'step-finish-1',
              messageID: 'assistant-process-2',
              sessionID: 'sess-1',
              type: 'step-finish',
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

    expect(screen.getAllByTestId('chat-process-group')).toHaveLength(1);
    expect(screen.getByText('查看 4 个步骤')).toBeInTheDocument();
  });

  it('keeps the compact compaction bubble at the full assistant column width', async () => {
    useSessionMessagesMock.mockReturnValue({
      messages: [
        makeMessage({
          id: 'user-before-compaction',
          role: 'user',
          finish: 'stop',
          parts: [
            {
              id: 'user-text',
              messageID: 'user-before-compaction',
              sessionID: 'sess-1',
              type: 'text',
              text: '继续优化工作流',
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
      live: true,
      display: { collapseIntermediateSteps: true },
    }));

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.status',
        properties: {
          sessionID: 'sess-1',
          status: { type: 'compacting', message: '正在压缩上下文...' },
        },
      });
    });

    const compactionText = await screen.findByText('正在压缩上下文...');
    expect(compactionText.closest('.w-full.max-w-full')).not.toBeNull();
  });
});

describe('SessionChat optimistic message identity', () => {
  it('uses the optimistic user message ID for the persisted prompt', async () => {
    const user = userEvent.setup();
    const addMessage = vi.fn();
    useSessionMessagesMock.mockReturnValue({
      messages: [],
      loading: false,
      refetch: vi.fn(),
      addMessage,
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '测试 question 工具{enter}');

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({ messageID: expect.stringMatching(/^msg_/) }),
      );
    });

    expect(addMessage).toHaveBeenCalledTimes(1);
    expect(clientPostMock.mock.calls.filter(
      ([url]) => url === '/api/session/sess-1/prompt_async',
    )).toHaveLength(1);
    const optimisticMessage = addMessage.mock.calls[0][0] as Message;
    const promptCall = clientPostMock.mock.calls.find(
      ([url]) => url === '/api/session/sess-1/prompt_async',
    );
    const payload = promptCall?.[1] as { messageID?: string } | undefined;
    expect(payload?.messageID).toBe(optimisticMessage.id);
  });

  it('uses the optimistic user message ID for slash commands', async () => {
    const user = userEvent.setup();
    const addMessage = vi.fn();
    useSessionMessagesMock.mockReturnValue({
      messages: [],
      loading: false,
      refetch: vi.fn(),
      addMessage,
      updateMessage: vi.fn(),
      updateMessagePart: vi.fn(),
      replaceMessageText: vi.fn(),
      truncateAfterMessage: vi.fn(),
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1' }));

    await user.type(screen.getByPlaceholderText('请输入消息'), '/tools{enter}');

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/command',
        expect.objectContaining({ messageID: expect.stringMatching(/^msg_/) }),
      );
    });

    expect(addMessage).toHaveBeenCalledTimes(1);
    expect(clientPostMock.mock.calls.filter(
      ([url]) => url === '/api/session/sess-1/command',
    )).toHaveLength(1);
    const optimisticMessage = addMessage.mock.calls[0][0] as Message;
    const commandCall = clientPostMock.mock.calls.find(
      ([url]) => url === '/api/session/sess-1/command',
    );
    const payload = commandCall?.[1] as { messageID?: string } | undefined;
    expect(payload?.messageID).toBe(optimisticMessage.id);
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

  it('uses the selected agent when creating a session from the first message', async () => {
    const user = userEvent.setup();
    const onCreateAndSend = vi.fn().mockResolvedValue('sess-created');
    render(React.createElement(SessionChat, {
      sessionId: null,
      agentName: 'explore',
      mentionAgents,
      onCreateAndSend,
    }));

    await user.type(screen.getByPlaceholderText('请输入消息'), 'summarize this file{enter}');

    await waitFor(() => {
      expect(onCreateAndSend).toHaveBeenCalledWith(
        'summarize this file',
        [],
        'explore',
        undefined,
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

describe('SessionChat slash command routing', () => {
  it('sends absolute filesystem paths as normal prompts instead of slash commands', async () => {
    render(React.createElement(SessionChat, {
      sessionId: 'sess-1',
    }));

    const text = '/tmp/stream_alert_denoise/rex_integration_guide.md\n\nuse this file';
    const textarea = screen.getByPlaceholderText('请输入消息') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: text } });
    fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(clientPostMock).toHaveBeenCalledWith(
        '/api/session/sess-1/prompt_async',
        expect.objectContaining({
          parts: [{ type: 'text', text }],
        }),
      );
    });
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

describe('ChatToolPart delegate rendering', () => {
  it('keeps the specialized delegate view inside a process timeline', () => {
    render(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: {
          id: 'delegate-process-step',
          type: 'tool',
          tool: 'delegate_task',
          state: {
            status: 'running',
            input: {
              subagent_type: 'explore',
              description: '排查会话页面',
            },
          },
        } as any,
      }),
    );

    expect(screen.getByTestId('chat-process-delegate-step')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-process-tool-step')).not.toBeInTheDocument();
  });
});

describe('ChatToolPart load skill rendering', () => {
  const part = {
    id: 'load-skill-part',
    type: 'tool',
    tool: 'skill_load',
    callID: 'call-load-skill',
    state: {
      status: 'completed',
      input: {
        name: 'agent-builder',
      },
      title: 'Loaded skill: agent-builder',
      output: 'Skill loaded',
    },
  } as any;

  it('uses the localized action name in the process timeline', () => {
    render(React.createElement(ChatToolPart, { part, processStep: true }));

    const processStep = screen.getByTestId('chat-process-tool-step');
    expect(processStep.querySelector('summary')).toHaveTextContent('加载技能');
    expect(processStep.querySelector('summary')).toHaveTextContent('agent-builder');
    expect(processStep.querySelector('summary')).not.toHaveTextContent('Loaded skill');
    expect(processStep.querySelector('summary')).not.toHaveTextContent('已完成');
  });

  it('uses the localized action name in the expanded tool card', () => {
    const { container } = render(React.createElement(ChatToolPart, { part }));

    expect(container.querySelector('summary')).toHaveTextContent('加载技能');
    expect(container.querySelector('summary')).not.toHaveTextContent('load skill');
  });
});

describe('ChatToolPart semantic tool presentation', () => {
  it('shows the tool immediately and fills in parameters when they arrive', () => {
    const initialPart = {
      id: 'streaming-write-part',
      type: 'tool',
      tool: 'write',
      state: {
        status: 'pending',
        input: {},
      },
    } as any;
    const { rerender } = render(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: initialPart,
      }),
    );

    let summary = screen.getByTestId('chat-process-tool-step').querySelector('summary');
    expect(summary).toHaveTextContent('写入文件');
    expect(summary).not.toHaveTextContent('filePath');
    expect(screen.queryByText('输入参数')).not.toBeInTheDocument();
    expect(screen.getByTestId('chat-tool-action-progress')).toHaveTextContent(
      '正在写入文件…',
    );

    rerender(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: {
          ...initialPart,
          state: {
            status: 'running',
            input: {
              filePath: '/repo/report.md',
              content: '# Report',
            },
          },
        },
      }),
    );

    summary = screen.getByTestId('chat-process-tool-step').querySelector('summary');
    expect(summary).toHaveTextContent('写入文件');
    expect(summary).toHaveTextContent('/repo/report.md');
    expect(screen.getByText('输入参数')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-tool-action-progress')).not.toBeInTheDocument();
  });

  it.each(['edit', 'apply_patch'])('describes a pending %s tool as editing the file', (tool) => {
    render(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: {
          id: `streaming-${tool}-part`,
          type: 'tool',
          tool,
          state: {
            status: 'pending',
            input: {},
          },
        } as any,
      }),
    );

    expect(screen.getByTestId('chat-tool-action-progress')).toHaveTextContent(
      '正在编辑文件…',
    );
  });

  it('shows a localized action and concise target in the process timeline', () => {
    render(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: {
          id: 'read-file-part',
          type: 'tool',
          tool: 'read',
          state: {
            status: 'completed',
            input: {
              filePath: '/repo/webui/src/components/common/SessionChat.tsx',
              offset: 5200,
              limit: 200,
            },
          },
        } as any,
      }),
    );

    const summary = screen.getByTestId('chat-process-tool-step').querySelector('summary');
    expect(summary).toHaveTextContent('读取文件');
    expect(summary).toHaveTextContent('/repo/webui/src/components/common/SessionChat.tsx · 5200');
    expect(summary).not.toHaveTextContent('已完成');
    expect(summary).not.toHaveTextContent('filePath=');
  });

  it('uses a subcommand-specific action for skill management', () => {
    render(
      React.createElement(ChatToolPart, {
        processStep: true,
        part: {
          id: 'install-skill-part',
          type: 'tool',
          tool: 'flocks_skills',
          state: {
            status: 'running',
            input: {
              subcommand: 'install',
              args: 'agent-builder',
            },
          },
        } as any,
      }),
    );

    const summary = screen.getByTestId('chat-process-tool-step').querySelector('summary');
    expect(summary).toHaveTextContent('安装技能');
    expect(summary).toHaveTextContent('agent-builder');
    expect(summary).not.toHaveTextContent('执行中');
  });

  it('redacts sensitive fields in expanded input parameters', () => {
    const { container } = render(
      React.createElement(ChatToolPart, {
        part: {
          id: 'add-provider-part',
          type: 'tool',
          tool: 'add_provider',
          state: {
            status: 'completed',
            input: {
              name: 'Internal LLM',
              api_key: 'super-secret-key',
              config: {
                password: 'super-secret-password',
                base_url: 'https://models.example.com/v1',
              },
            },
          },
        } as any,
      }),
    );

    expect(container.textContent).toContain('添加模型服务');
    const payload = container.querySelector('pre')?.textContent || '';
    expect(payload).toContain('••••••');
    expect(payload).toContain('https://models.example.com/v1');
    expect(payload).not.toContain('super-secret-key');
    expect(payload).not.toContain('super-secret-password');
  });
});

describe('ChatMessageBubble session typography', () => {
  it('uses the outer navigation font size for the agent label', () => {
    render(React.createElement(ChatMessageBubble, {
      message: makeMessage({
        id: 'assistant-agent-label',
        role: 'assistant',
        parts: [{
          id: 'assistant-text',
          type: 'text',
          text: '已加载技能。',
        } as any],
      }),
    }));

    expect(screen.getByText('Rex')).toHaveClass('text-sm');
  });
});

describe('ChatMessageBubble footer layout', () => {
  it('places assistant actions immediately before the timestamp', () => {
    render(React.createElement(ChatMessageBubble, {
      message: makeMessage({
        id: 'assistant-footer',
        role: 'assistant',
        timestamp: Date.now(),
        parts: [{
          id: 'assistant-text',
          type: 'text',
          text: '任务已完成。',
        } as any],
      }),
      compact: false,
      showActions: true,
      showTimestamp: true,
    }));

    const regenerateButton = screen.getByRole('button', { name: 'chat.regenerate' });
    const actionGroup = regenerateButton.parentElement;
    const footer = actionGroup?.parentElement;

    expect(footer).toHaveClass('justify-start', 'gap-1.5');
    expect(footer?.children[0]).toBe(actionGroup);
    expect(footer?.children[1]).toHaveClass('text-[11px]');
    expect(regenerateButton).toHaveClass(
      'border-transparent',
      'bg-transparent',
      'hover:bg-white',
      'active:bg-white',
      'focus-visible:bg-white',
    );
    expect(regenerateButton).not.toHaveClass('border-gray-200/80', 'bg-white/80');
  });

  it('keeps the user timestamp before the action group', () => {
    render(React.createElement(ChatMessageBubble, {
      message: makeMessage({
        id: 'user-footer',
        role: 'user',
        timestamp: Date.now(),
        parts: [{
          id: 'user-text',
          type: 'text',
          text: '继续处理。',
        } as any],
      }),
      compact: false,
      showActions: true,
      showTimestamp: true,
    }));

    const copyButton = screen.getByRole('button', { name: 'chat.copy' });
    const actionGroup = copyButton.parentElement;
    const footer = actionGroup?.parentElement;

    expect(footer).toHaveClass('justify-between');
    expect(footer?.children[0]).toHaveClass('text-[11px]');
    expect(footer?.children[1]).toBe(actionGroup);
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

describe('ChatToolPart bash rendering', () => {
  it('renders command metadata and streams without generic JSON sections', () => {
    const { container } = render(
      React.createElement(ChatToolPart, {
        part: {
          id: 'bash-part',
          type: 'tool',
          tool: 'bash',
          callID: 'call-bash',
          state: {
            status: 'completed',
            input: {
              command: 'npm run test:run -- SessionChat.test.ts',
              description: '运行会话组件测试',
              workdir: '/repo',
              timeout: 120000,
            },
            output: {
              stdout: 'tests passed\n',
              stderr: 'warning: slow test\n',
              exit_code: 0,
            },
            time: { start: 1000, end: 2500 },
          },
        } as any,
      }),
    );

    const text = container.textContent || '';
    expect(text).toContain('执行命令');
    expect(text).toContain('运行会话组件测试');
    expect(text).toContain('npm run test:run -- SessionChat.test.ts');
    expect(text).toContain('命令');
    expect(text).toContain('工作目录');
    expect(text).toContain('/repo');
    expect(text).toContain('耗时 1.50s');
    expect(text).toContain('超时 120000ms');
    expect(text).toContain('标准输出');
    expect(text).toContain('tests passed');
    expect(text).toContain('标准错误');
    expect(text).toContain('warning: slow test');
    expect(text).not.toContain('输入参数');
    expect(text).not.toContain('输出结果');
    expect(text).not.toContain('退出码');
    expect(text).not.toContain('exit_code');
    expect(screen.getByText('$')).toHaveClass('text-zinc-500');
    expect(screen.getByText('$').closest('pre')).toHaveClass('bg-zinc-950');
    expect(screen.getByText('$').closest('pre')).toHaveClass('max-h-64');
    expect(screen.getByText('tests passed').closest('pre')).toHaveClass('max-h-64');
  });
});

describe('ChatToolPart question result rendering', () => {
  it('uses error styling for unanswered failed questions', () => {
    const { container } = render(
      React.createElement(ChatToolPart, {
        part: {
          id: 'question-error-part',
          type: 'tool',
          tool: 'question',
          callID: 'call-question-error',
          state: {
            status: 'error',
            input: {
              questions: [
                {
                  question: '是否继续发布？',
                  header: '发布确认',
                  type: 'confirm',
                },
              ],
            },
            error: 'Question timed out',
          },
        } as any,
      }),
    );

    const questionDetails = container.querySelector('details') as HTMLDetailsElement;
    expect(questionDetails).not.toBeNull();
    expect(questionDetails.open).toBe(false);
    expect(screen.getByText('失败')).toHaveClass('text-red-500');
    expect(screen.getByText('回答')).toHaveClass('text-red-500');
    expect(screen.getByText('未回答')).toHaveClass('border-red-200');
    expect(screen.getByText('未回答')).toHaveClass('bg-red-50');
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

describe('SessionChat goal banner', () => {
  it('hydrates a persisted goal banner when the session loads', async () => {
    sessionApiGetMock.mockResolvedValue({
      id: 'sess-1',
      goal: {
        status: 'active',
        objective: 'List built-in tools',
      },
    });

    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    expect(await screen.findByText('Goal')).toBeInTheDocument();
    expect(screen.getByText('List built-in tools')).toBeInTheDocument();
    expect(sessionApiGetMock).toHaveBeenCalledWith('sess-1');
  });

  it('shows goal status updates and lets the user dismiss the current notice', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.goal.updated',
        properties: {
          sessionID: 'sess-1',
          status: 'active',
          objective: 'List built-in tools',
        },
      });
    });

    expect(await screen.findByText('Goal')).toBeInTheDocument();
    expect(screen.getByText('List built-in tools')).toBeInTheDocument();

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.goal.updated',
        properties: {
          sessionID: 'sess-1',
          status: 'completed',
          objective: 'List built-in tools',
          reason: 'Goal complete: tools listed',
        },
      });
    });

    expect(await screen.findByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('List built-in tools')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Dismiss goal notice' }));

    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
    expect(screen.queryByText('List built-in tools')).not.toBeInTheDocument();
  });

  it('keeps a dismissed persisted goal hidden after remount', async () => {
    const user = userEvent.setup();
    sessionApiGetMock.mockResolvedValue({
      id: 'sess-1',
      goal: {
        status: 'completed',
        objective: 'List built-in tools',
        reason: 'Goal complete: tools listed',
      },
    });

    const view = render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    expect(await screen.findByText('Completed')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Dismiss goal notice' }));
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
    expect(window.localStorage.getItem('flocks:session:sess-1:dismissedGoal')).toBe(
      'completed:List built-in tools',
    );

    view.unmount();
    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    await waitFor(() => {
      expect(sessionApiGetMock).toHaveBeenCalledTimes(2);
      expect(screen.queryByText('Completed')).not.toBeInTheDocument();
    });
    expect(screen.queryByText('List built-in tools')).not.toBeInTheDocument();
  });

  it('shows a new goal even when a previous goal was dismissed', async () => {
    const user = userEvent.setup();
    render(React.createElement(SessionChat, { sessionId: 'sess-1', live: true }));

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.goal.updated',
        properties: {
          sessionID: 'sess-1',
          status: 'completed',
          objective: 'List built-in tools',
        },
      });
    });
    expect(await screen.findByText('Completed')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Dismiss goal notice' }));

    act(() => {
      useSSEOptionsRef.current.onEvent({
        type: 'session.goal.updated',
        properties: {
          sessionID: 'sess-1',
          status: 'active',
          objective: 'Calculate 4+87',
        },
      });
    });

    expect(await screen.findByText('Goal')).toBeInTheDocument();
    expect(screen.getByText('Calculate 4+87')).toBeInTheDocument();
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
  it('reconciles pending questions while the session is busy', async () => {
    vi.useFakeTimers();
    try {
      render(React.createElement(SessionChat, {
        sessionId: 'sess-1',
        live: true,
      }));

      await act(async () => {
        await Promise.resolve();
      });
      pendingQuestionsHookMock.fetchPendingQuestions.mockClear();

      act(() => {
        useSSEOptionsRef.current.onEvent({
          type: 'session.status',
          properties: { sessionID: 'sess-1', status: { type: 'busy' } },
        });
      });
      pendingQuestionsHookMock.fetchPendingQuestions.mockClear();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });

      expect(pendingQuestionsHookMock.fetchPendingQuestions).toHaveBeenCalledWith('sess-1');
    } finally {
      vi.useRealTimers();
    }
  });

  it('refetches when polling finds a pending question missing from local messages', async () => {
    vi.useFakeTimers();
    let resolveRefetch: (() => void) | undefined;
    const refetch = vi.fn(() => new Promise<void>((resolve) => {
      resolveRefetch = resolve;
    }));
    const onStreamingDone = vi.fn();
    try {
      pendingQuestionsHookMock.pendingQuestions = {
        'call-question-1': {
          requestId: 'request-question-1',
          questions: [{ question: 'Continue?' }],
        },
      };
      mockFallbackPolling({
        localMessages: [
          makeMessage({ id: 'user-1', role: 'user' }),
          makeMessage({ id: 'assistant-1', parentID: 'user-1' }),
        ],
        fetchedMessages: [
          makeFetchedMessage({ id: 'user-1', role: 'user' }),
          makeFetchedMessage(
            { id: 'assistant-1', role: 'assistant', parentID: 'user-1', finish: 'tool-calls' },
            [{
              id: 'question-tool-1',
              messageID: 'assistant-1',
              sessionID: 'sess-1',
              type: 'tool',
              tool: 'question',
              callID: 'call-question-1',
              state: {
                status: 'completed',
                input: { questions: [{ question: 'Continue?' }] },
              },
            } as Message['parts'][number]],
          ),
        ],
        refetch,
      });
      await startFallbackPolling(onStreamingDone);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(refetch).toHaveBeenCalledTimes(1);
      expect(onStreamingDone).not.toHaveBeenCalled();
      expect(clientGetMock).not.toHaveBeenCalledWith('/api/session/status');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(refetch).toHaveBeenCalledTimes(1);
      expect(clientGetMock.mock.calls.filter(
        ([url]) => url === '/api/session/sess-1/message',
      )).toHaveLength(2);

      await act(async () => {
        resolveRefetch?.();
        await Promise.resolve();
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it('ignores a missing running question from a historical turn while the session is busy', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const onStreamingDone = vi.fn();
    try {
      const localMessages = [
        makeMessage({ id: 'old-user', role: 'user' }),
        makeMessage({ id: 'old-assistant', parentID: 'old-user', finish: 'tool-calls' }),
        makeMessage({ id: 'current-user', role: 'user' }),
        makeMessage({ id: 'current-assistant', parentID: 'current-user' }),
      ];
      mockFallbackPolling({
        localMessages,
        fetchedMessages: [
          makeFetchedMessage({ id: 'old-user', role: 'user' }),
          makeFetchedMessage(
            {
              id: 'old-assistant',
              role: 'assistant',
              parentID: 'old-user',
              finish: 'tool-calls',
            },
            [{
              id: 'old-tool',
              type: 'tool',
              tool: 'question',
              state: { status: 'running' },
            } as Message['parts'][number]],
          ),
          makeFetchedMessage({ id: 'current-user', role: 'user' }),
          makeFetchedMessage({
            id: 'current-assistant',
            role: 'assistant',
            parentID: 'current-user',
            finish: null,
          }),
        ],
        refetch,
      });
      await startFallbackPolling(onStreamingDone);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(clientGetMock).toHaveBeenCalledWith('/api/session/sess-1/message', {
        params: { page: true, limit: 50, include_archived: true },
      });
      expect(clientGetMock).not.toHaveBeenCalledWith('/api/session/status');
      expect(refetch).not.toHaveBeenCalled();
      expect(onStreamingDone).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('finishes streaming when only a historical turn has a running question', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const onStreamingDone = vi.fn();
    try {
      const localMessages = [
        makeMessage({ id: 'old-user', role: 'user' }),
        makeMessage({ id: 'old-assistant', parentID: 'old-user', finish: 'tool-calls' }),
        makeMessage({ id: 'current-user', role: 'user' }),
        makeMessage({ id: 'current-assistant', parentID: 'current-user', finish: 'stop' }),
      ];
      mockFallbackPolling({
        localMessages,
        fetchedMessages: [
          makeFetchedMessage({ id: 'old-user', role: 'user' }),
          makeFetchedMessage(
            {
              id: 'old-assistant',
              role: 'assistant',
              parentID: 'old-user',
              finish: 'tool-calls',
            },
            [{
              id: 'old-tool',
              type: 'tool',
              tool: 'question',
              state: { status: 'running' },
            } as Message['parts'][number]],
          ),
          makeFetchedMessage({ id: 'current-user', role: 'user' }),
          makeFetchedMessage(
            {
              id: 'current-assistant',
              role: 'assistant',
              parentID: 'current-user',
              finish: 'stop',
            },
            [{ id: 'current-text', type: 'text', text: 'done' } as Message['parts'][number]],
          ),
        ],
        refetch,
        status: {},
      });
      await startFallbackPolling(onStreamingDone);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(clientGetMock).toHaveBeenCalledWith('/api/session/status');
      expect(refetch).toHaveBeenCalledTimes(1);
      expect(onStreamingDone).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(refetch).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not refetch a missing non-question tool while it is still running', async () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const onStreamingDone = vi.fn();
    try {
      mockFallbackPolling({
        localMessages: [
          makeMessage({ id: 'user-1', role: 'user' }),
          makeMessage({ id: 'assistant-1', parentID: 'user-1', finish: 'tool-calls' }),
        ],
        fetchedMessages: [
          makeFetchedMessage({ id: 'user-1', role: 'user' }),
          makeFetchedMessage(
            {
              id: 'assistant-1',
              role: 'assistant',
              parentID: 'user-1',
              finish: 'tool-calls',
            },
            [{
              id: 'tool-1',
              type: 'tool',
              tool: 'bash',
              state: { status: 'running' },
            } as Message['parts'][number]],
          ),
        ],
        refetch,
      });
      await startFallbackPolling(onStreamingDone);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      expect(refetch).not.toHaveBeenCalled();
      expect(onStreamingDone).not.toHaveBeenCalled();
      expect(clientGetMock).not.toHaveBeenCalledWith('/api/session/status');
      expect(clientGetMock).toHaveBeenCalledWith('/api/session/sess-1/message', {
        params: { page: true, limit: 50, include_archived: true },
      });
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
            data: {
              items: [
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
              hasMore: false,
              nextBefore: null,
            },
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
