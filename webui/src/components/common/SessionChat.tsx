/**
 * SessionChat — 统一的 Agent Session 对话组件
 *
 * 产品中所有需要 AI 对话能力的地方都应使用此组件：
 * - Session 会话主页面 (compact=false)
 * - 工作流编辑对话面板
 * - 任务执行详情面板
 * - ChatDialog 弹窗
 * - EntitySheet Rex 对话 Tab
 *
 * 功能：
 * - 加载并展示指定 session 的完整对话消息
 * - SSE 实时流式更新
 * - 渲染 text / reasoning / tool 三种 part 类型
 * - 底部追问输入框（可通过 hideInput 隐藏）
 * - 消息复制、时间戳等可选功能
 */

import { useState, useCallback, useRef, useEffect, useMemo, memo } from 'react';
import { Send, Loader2, ChevronDown, Square, Copy, User, Plus, FileText, AlertCircle, X, RefreshCw, Pencil, Save } from 'lucide-react';
import { StreamingMarkdown } from './StreamingMarkdown';
import { useTranslation } from 'react-i18next';
import LoadingSpinner from './LoadingSpinner';
import { QuestionTool } from './QuestionTool';
import DelegateTaskCard, { isDelegateTool } from './DelegateTaskCard';
import CommandDropdown, { parseSlashCommand } from './CommandDropdown';
import { useSessionMessages } from '@/hooks/useSessions';
import { useSSE, type SSEConnectionStatus } from '@/hooks/useSSE';
import { useReasoningToggle } from '@/hooks/useReasoningToggle';
import { usePendingQuestions, type PendingQuestion } from '@/hooks/usePendingQuestions';
import { sessionApi } from '@/api/session';
import client from '@/api/client';
import { commandAPI, type Command } from '@/api/skill';
import { workspaceAPI } from '@/api/workspace';
import { formatSmartTime } from '@/utils/time';
import type { Message, MessagePart, ToolState } from '@/types';

export { formatSmartTime };
export type { SSEConnectionStatus };

// ============================================================================
// Types
// ============================================================================

export type MergedMessage = Message & { _merged?: boolean };

export interface SSEChatEvent {
  type: string;
  properties?: Record<string, any>;
}

/** Node reference shown above the chat input as a dismissible chip */
export interface NodeRef {
  id: string;
  type: string;
  description?: string;
}

/** Display-related options grouped to reduce prop surface. */
export interface SessionChatDisplay {
  /** Compact mode for panels/dialogs (default: true). Set false for full-page. */
  compact?: boolean;
  /** Show copy action on assistant messages */
  showActions?: boolean;
  /** Show timestamp below each message */
  showTimestamp?: boolean;
}

export interface SessionChatProps {
  /** When null/undefined, only welcomeContent + input are rendered (lazy session). */
  sessionId?: string | null;
  /** Subscribe to SSE for live streaming updates */
  live?: boolean;
  /** Placeholder text for the follow-up input */
  placeholder?: string;
  /** Hide the follow-up input box */
  hideInput?: boolean;
  /** Extra class for the outer wrapper (which is a flex-col container) */
  className?: string;
  /** Displayed when there are no messages yet (ignored if welcomeContent is set) */
  emptyText?: string;
  /** Suggested prompts shown above the input before the user sends any message */
  suggestions?: string[];
  /** Node-reference chip above the input */
  nodeRef?: NodeRef | null;
  /** Called when the user dismisses the node chip */
  onNodeRefDismiss?: () => void;
  /** Called once each time the assistant finishes a streaming response */
  onStreamingDone?: () => void;
  /** Auto-send this message on mount via prompt_async */
  initialMessage?: string | null;
  /** Called immediately after initialMessage has been consumed (sent) */
  onInitialMessageConsumed?: () => void;
  /** Agent name to include in prompt_async requests */
  agentName?: string;
  /** Display configuration (compact, showActions, showTimestamp) */
  display?: SessionChatDisplay;
  /** Custom welcome content when no messages. Can be a render prop receiving setInput. */
  welcomeContent?: React.ReactNode | ((setInput: (text: string) => void) => React.ReactNode);
  /** Called when SSE connection status changes */
  onSseStatusChange?: (status: SSEConnectionStatus) => void;
  /** Forward SSE events with properties to parent (global events like session.updated) */
  onSSEEvent?: (event: SSEChatEvent) => void;
  /** Called on session errors from SSE */
  onError?: (message: string) => void;
  /**
   * Called when the user sends a message but sessionId is not yet available.
   * The parent should create a session and update sessionId + initialMessage props.
   */
  onCreateAndSend?: (text: string) => Promise<void> | void;
}

type AttachmentStatus = 'uploading' | 'success' | 'error';

interface ComposerAttachment {
  id: string;
  file: File;
  name: string;
  status: AttachmentStatus;
  workspacePath?: string;
  error?: string;
}

// ============================================================================
// Utilities
// ============================================================================

/**
 * Merge consecutive assistant messages into single display items.
 * Summary messages (finish === 'summary') and compacted messages are kept as-is.
 */
export function mergeConsecutiveAssistantMessages(messages: Message[]): MergedMessage[] {
  const result: MergedMessage[] = [];

  for (const msg of messages) {
    if (msg.finish === 'summary') {
      result.push({ ...msg, parts: [...msg.parts], _merged: false });
      continue;
    }

    if (msg.role !== 'assistant') {
      result.push(msg);
      continue;
    }

    const last = result[result.length - 1];
    if (
      last &&
      last.role === 'assistant' &&
      last._merged &&
      last.finish !== 'summary' &&
      !!last.compacted === !!msg.compacted
    ) {
      last.parts = [...last.parts, ...msg.parts];
      if (msg.finish) last.finish = msg.finish;
    } else {
      result.push({ ...msg, parts: [...msg.parts], _merged: true });
    }
  }

  return result;
}

// ============================================================================
// Main component
// ============================================================================

const ABORT_SSE_SETTLE_DELAY = 2000;
const SCROLL_BOTTOM_THRESHOLD_PX = 80;
const FALLBACK_POLL_MS = 5_000;
const WORKSPACE_UPLOAD_DEST = 'uploads';
const FILE_INPUT_ACCEPT = '.txt,.md,.json,.yaml,.yml,.xml,.csv,.pdf,.doc,.docx';
const ALLOWED_UPLOAD_EXTENSIONS = new Set([
  'txt', 'md', 'json', 'yaml', 'yml', 'xml', 'csv', 'pdf', 'doc', 'docx',
]);

function getFileExtension(filename: string): string {
  const normalized = filename.toLowerCase();
  const idx = normalized.lastIndexOf('.');
  return idx >= 0 ? normalized.slice(idx + 1) : '';
}

function isAllowedUploadFile(file: File): boolean {
  return ALLOWED_UPLOAD_EXTENSIONS.has(getFileExtension(file.name));
}

export default function SessionChat({
  sessionId,
  live = false,
  placeholder,
  hideInput = false,
  className = '',
  emptyText,
  suggestions,
  nodeRef,
  onNodeRefDismiss,
  onStreamingDone,
  initialMessage,
  agentName,
  display,
  welcomeContent,
  onSseStatusChange,
  onSSEEvent,
  onError,
  onCreateAndSend,
  onInitialMessageConsumed,
}: SessionChatProps) {
  const { t } = useTranslation('session');
  const compact = display?.compact ?? true;
  const showActions = display?.showActions ?? false;
  const showTimestamp = display?.showTimestamp ?? false;
  const effectivePlaceholder = placeholder ?? t('chat.placeholder');
  const effectiveEmptyText = emptyText ?? t('chat.emptyText');
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [isCompacting, setIsCompacting] = useState(false);
  const [compactingMessage, setCompactingMessage] = useState('');
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingPartId, setEditingPartId] = useState<string | null>(null);
  const [editingRole, setEditingRole] = useState<Message['role'] | null>(null);
  const [editingText, setEditingText] = useState('');
  const [actionMessageId, setActionMessageId] = useState<string | null>(null);
  const isCompactingRef = useRef(false);
  const prevStreamingRef = useRef(false);
  // Tracks "sessionId::message" key to prevent double-send in React StrictMode
  const initialMessageSentRef = useRef('');
  const abortingRef = useRef(false);
  // ID of the assistant message that was aborted; used to ignore its finish event
  const abortedMessageIdRef = useRef<string | null>(null);
  const statusCheckedRef = useRef<string | null>(null);
  const {
    pendingQuestions,
    handleQuestionAsked,
    submitAnswer,
    submitReject,
    removeByRequestId,
    fetchPendingQuestions,
    clearAll: clearPendingQuestions,
  } = usePendingQuestions();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);

  // Slash command autocomplete state
  const [commands, setCommands] = useState<Command[]>([]);
  const [showCommandDropdown, setShowCommandDropdown] = useState(false);
  const [commandQuery, setCommandQuery] = useState('');
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const commandsLoadedRef = useRef(false);
  const successfulAttachments = useMemo(
    () => attachments.filter((attachment) => attachment.status === 'success' && attachment.workspacePath),
    [attachments],
  );
  const hasUploadingFiles = attachments.some((attachment) => attachment.status === 'uploading');
  const canSend = !sending && !isStreaming && !hasUploadingFiles && (!!input.trim() || successfulAttachments.length > 0);

  const scrollToBottom = useCallback(() => {
    if (!isAtBottomRef.current) return;
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
    });
  }, []);

  const rafScheduledRef = useRef(false);
  const handleScroll = useCallback(() => {
    if (rafScheduledRef.current) return;
    rafScheduledRef.current = true;
    requestAnimationFrame(() => {
      const el = scrollContainerRef.current;
      if (el) {
        isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
      }
      rafScheduledRef.current = false;
    });
  }, []);

  const {
    messages,
    loading,
    refetch,
    addMessage,
    updateMessage,
    updateMessagePart,
    replaceMessageText,
    truncateAfterMessage,
  } =
    useSessionMessages(sessionId || undefined);

  // Keep a ref to latest messages so handleAbort can read it without stale closure
  const messagesRef = useRef(messages);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const hasUserMessage = useMemo(() => messages.some((m) => m.role === 'user'), [messages]);

  const sseEnabled = live || isStreaming || !hideInput;

  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      const { type, properties } = event;

      // Forward events with payload to parent (e.g. session.updated, workflow.updated).
      // Skip empty events like heartbeats to avoid noisy callbacks.
      if (properties) onSSEEvent?.(event);

      if (!properties || !sessionId) return;

      if (type === 'message.updated' && properties.info?.sessionID === sessionId) {
        updateMessage(properties.info);
        if (properties.info.finish || properties.info.time?.completed) {
          refetch();
          // If this is the message we aborted, don't stop streaming — the user may have
          // already sent a new message whose response is now arriving.
          if (abortedMessageIdRef.current && abortedMessageIdRef.current === properties.info.id) {
            abortedMessageIdRef.current = null;
            abortingRef.current = false;
          } else {
            setIsStreaming(false);
            abortingRef.current = false;
            abortedMessageIdRef.current = null;
          }
        } else if (
          properties.info.role === 'assistant' &&
          !properties.info.finish &&
          !abortingRef.current
        ) {
          setIsStreaming(true);
        }
      } else if (type === 'message.part.updated' && properties.part?.sessionID === sessionId) {
        updateMessagePart(properties.part, properties.delta);
        scrollToBottom();
      } else if (type === 'question.asked' && properties.sessionID === sessionId) {
        const callID: string | undefined = properties.tool?.callID;
        const requestId: string | undefined = properties.id;
        if (callID && requestId) {
          handleQuestionAsked(callID, requestId, properties.questions || []);
          scrollToBottom();
        }
      } else if (
        (type === 'question.replied' || type === 'question.rejected') &&
        properties.sessionID === sessionId
      ) {
        const requestId: string | undefined = properties.requestID;
        if (requestId) {
          removeByRequestId(requestId);
        }
      } else if (type === 'session.status' && properties.sessionID === sessionId) {
        if (properties.status?.type === 'compacting') {
          setIsCompacting(true);
          isCompactingRef.current = true;
          setCompactingMessage(properties.status.message || t('chat.compacting'));
        } else {
          const wasCompacting = isCompactingRef.current;
          setIsCompacting(false);
          isCompactingRef.current = false;
          setCompactingMessage('');
          if (wasCompacting) refetch();
        }
      } else if (type === 'session.error' && properties.sessionID === sessionId) {
        setIsStreaming(false);
        setIsCompacting(false);
        abortingRef.current = false;
        onError?.(properties.error?.message || t('chat.placeholder'));
      }
    },
    [
      sessionId,
      updateMessage,
      updateMessagePart,
      refetch,
      handleQuestionAsked,
      removeByRequestId,
      onSSEEvent,
      onError,
      scrollToBottom,
    ],
  );

  const handleQuestionAnswer = useCallback(
    async (callID: string, requestId: string, answers: string[][]) => {
      try {
        await submitAnswer(callID, requestId, answers);
      } catch (err: unknown) {
        alert(`Submit failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [submitAnswer],
  );

  const handleQuestionReject = useCallback(
    async (callID: string, requestId: string) => {
      try {
        await submitReject(callID, requestId);
      } catch (err: unknown) {
        alert(`Cancel failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [submitReject],
  );

  const { status: sseStatus } = useSSE({
    url: `${import.meta.env.VITE_API_BASE_URL || ''}/api/event`,
    onEvent: handleSSEEvent,
    onReconnect: () => {
      if (!sessionId) return;
      refetch();
      fetchPendingQuestions(sessionId).catch((err) => {
        console.warn('[SessionChat] Failed to recover pending questions after reconnect:', err);
      });
    },
    enabled: sseEnabled,
    reconnect: { enabled: true, maxRetries: 5, initialDelay: 1000, maxDelay: 10000 },
  });

  // Forward SSE connection status to parent
  useEffect(() => {
    onSseStatusChange?.(sseStatus);
  }, [sseStatus, onSseStatusChange]);

  // Auto-scroll when messages update
  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Auto-resize textarea
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, compact ? 96 : 200)}px`;
  }, [compact]);
  useEffect(() => { autoResize(); }, [input, autoResize]);

  // Reset state on session change
  useEffect(() => {
    setIsStreaming(false);
    setAttachments([]);
    setIsDragOver(false);
    setIsCompacting(false);
    setCompactingMessage('');
    abortingRef.current = false;
    abortedMessageIdRef.current = null;
    statusCheckedRef.current = null;
    isAtBottomRef.current = true;
    clearPendingQuestions();
  }, [sessionId, clearPendingQuestions]);

  // Recover streaming state after page refresh / session switch
  useEffect(() => {
    if (!sessionId || loading) return;
    if (statusCheckedRef.current === sessionId) return;
    statusCheckedRef.current = sessionId;

    const checkStatus = async () => {
      try {
        const res = await client.get('/api/session/status');
        const status = res.data[sessionId];
        if (status?.type === 'busy') {
          setIsStreaming(true);
        } else if (status?.type === 'compacting') {
          setIsStreaming(true);
          setIsCompacting(true);
          isCompactingRef.current = true;
          setCompactingMessage(status.message || t('chat.compacting'));
        }
      } catch {
        if (messages.length > 0) {
          const lastMsg = messages[messages.length - 1];
          if (lastMsg.role === 'assistant' && !lastMsg.finish) {
            setIsStreaming(true);
          }
        }
      }

      try {
        await fetchPendingQuestions(sessionId);
      } catch (err) {
        console.warn('[SessionChat] Failed to recover pending questions:', err);
      }
    };
    checkStatus();
  }, [sessionId, loading, messages, fetchPendingQuestions]);

  // Refetch when page becomes visible again
  useEffect(() => {
    if (!sessionId) return;
    const handler = () => {
      if (document.visibilityState === 'visible') refetch();
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, [sessionId, refetch]);

  // Backup refetch when compaction ends — covers SSE reconnect scenarios
  // where the session.status event may have been missed.
  const prevIsCompactingRef = useRef(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (prevIsCompactingRef.current && !isCompacting && sessionId) {
      refetch();
      // Delayed safety-net: refetch once more in case the immediate fetch
      // returned stale data (e.g. compacted flag not yet persisted).
      timer = setTimeout(() => refetch(), 1500);
    }
    prevIsCompactingRef.current = isCompacting;
    return () => { if (timer) clearTimeout(timer); };
  }, [isCompacting, sessionId, refetch]);

  /** Lazily load slash commands on first use (for autocomplete dropdown). */
  const loadCommandsIfNeeded = useCallback(async (): Promise<void> => {
    if (commandsLoadedRef.current) return;
    commandsLoadedRef.current = true; // Optimistic: prevent concurrent fetches
    try {
      const res = await commandAPI.list();
      setCommands(res.data ?? []);
    } catch {
      commandsLoadedRef.current = false; // Allow retry on failure
    }
  }, []);

  const buildAttachmentBlock = useCallback((items: ComposerAttachment[]) => {
    if (items.length === 0) return '';
    const lines = items
      .map((attachment) => attachment.workspacePath)
      .filter((path): path is string => Boolean(path))
      .map((path) => `- ${path}`);
    if (lines.length === 0) return '';
    return `Attached files:\n${lines.join('\n')}`;
  }, []);

  const buildMessageText = useCallback((rawText: string, items: ComposerAttachment[]) => {
    const attachmentBlock = buildAttachmentBlock(items);
    const content = rawText
      ? attachmentBlock
        ? `${rawText}\n\n${attachmentBlock}`
        : rawText
      : attachmentBlock;

    if (!content) return '';
    return nodeRef
      ? `@@node:${nodeRef.id}|${nodeRef.type}\n${content}`
      : content;
  }, [buildAttachmentBlock, nodeRef]);

  const updateAttachment = useCallback((id: string, updater: (attachment: ComposerAttachment) => ComposerAttachment) => {
    setAttachments((prev) => prev.map((attachment) => (
      attachment.id === id ? updater(attachment) : attachment
    )));
  }, []);

  const uploadSelectedFiles = useCallback(async (entries: Array<{ id: string; file: File }>) => {
    if (entries.length === 0) return;
    try {
      const response = await workspaceAPI.upload(
        entries.map((entry) => entry.file),
        WORKSPACE_UPLOAD_DEST,
        'chat',
      );
      const uploaded = response.data.uploaded ?? [];
      setAttachments((prev) => prev.map((attachment) => {
        const entryIndex = entries.findIndex((entry) => entry.id === attachment.id);
        if (entryIndex < 0) return attachment;
        const result = uploaded[entryIndex];
        if (!result || result.error || !result.path) {
          return {
            ...attachment,
            status: 'error',
            error: result?.error || t('chat.upload.errorGeneric'),
          };
        }
        return {
          ...attachment,
          name: result.name || attachment.name,
          status: 'success',
          workspacePath: result.path,
          error: undefined,
        };
      }));
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message ?? t('chat.upload.errorGeneric');
      setAttachments((prev) => prev.map((attachment) => (
        entries.some((entry) => entry.id === attachment.id)
          ? { ...attachment, status: 'error', error: detail }
          : attachment
      )));
    }
  }, [t]);

  const queueFilesForUpload = useCallback((files: File[]) => {
    if (files.length === 0) return;
    const validEntries: Array<{ id: string; file: File }> = [];
    const invalidAttachments: ComposerAttachment[] = [];

    files.forEach((file, index) => {
      const id = `attachment-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`;
      if (!isAllowedUploadFile(file)) {
        invalidAttachments.push({
          id,
          file,
          name: file.name,
          status: 'error',
          error: t('chat.upload.invalidType'),
        });
        return;
      }
      validEntries.push({ id, file });
    });

    if (invalidAttachments.length > 0) {
      setAttachments((prev) => [...prev, ...invalidAttachments]);
    }

    if (validEntries.length === 0) return;

    setAttachments((prev) => [
      ...prev,
      ...validEntries.map(({ id, file }) => ({
        id,
        file,
        name: file.name,
        status: 'uploading' as const,
      })),
    ]);

    void uploadSelectedFiles(validEntries);
  }, [t, uploadSelectedFiles]);

  const handleFileSelection = useCallback((fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    queueFilesForUpload(Array.from(fileList));
  }, [queueFilesForUpload]);

  const handleRetryAttachment = useCallback((attachmentId: string) => {
    const attachment = attachments.find((item) => item.id === attachmentId);
    if (!attachment) return;
    updateAttachment(attachmentId, (current) => ({
      ...current,
      status: 'uploading',
      error: undefined,
    }));
    void uploadSelectedFiles([{ id: attachment.id, file: attachment.file }]);
  }, [attachments, updateAttachment, uploadSelectedFiles]);

  const handleRemoveAttachment = useCallback((attachmentId: string) => {
    setAttachments((prev) => prev.filter((attachment) => attachment.id !== attachmentId));
  }, []);

  const handleComposerPaste = useCallback((event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length === 0) return;
    event.preventDefault();
    queueFilesForUpload(files);
  }, [queueFilesForUpload]);

  const handleComposerDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!Array.from(event.dataTransfer?.types ?? []).includes('Files')) return;
    event.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleComposerDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setIsDragOver(false);
    }
  }, []);

  const handleComposerDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    setIsDragOver(false);
    handleFileSelection(event.dataTransfer.files);
  }, [handleFileSelection]);

  /**
   * Execute a slash command via the dedicated command API.
   *
   * The backend creates the user message (showing "/tools"), handles the command
   * directly if possible (no LLM), and pushes the response via SSE.
   * A temporary user message is added immediately for instant feedback;
   * the SSE "message.updated" event replaces it with the persisted message.
   */
  const sendCommand = async (command: string, args: string) => {
    if (!sessionId) return;

    abortingRef.current = false;
    isAtBottomRef.current = true;
    setSending(true);
    setIsStreaming(true);

    const displayText = args ? `/${command} ${args}` : `/${command}`;
    const tempId = `temp-${Date.now()}`;
    addMessage({
      id: tempId,
      sessionID: sessionId,
      role: 'user',
      parts: [{ id: `${tempId}-part`, type: 'text', text: displayText }],
      timestamp: Date.now(),
    } as Message);

    try {
      await client.post(`/api/session/${sessionId}/command`, {
        command,
        arguments: args,
        agent: agentName,
      });
    } catch (err: unknown) {
      setIsStreaming(false);
      const axiosErr = err as any;
      if (axiosErr?.response?.status === 404) {
        onError?.('Session not found. Please start a new session.');
      } else {
        alert(`Command failed: ${err instanceof Error ? err.message : String(err)}`);
      }
      throw err;
    } finally {
      setSending(false);
    }
  };

  /** Core send logic */
  const sendText = async (text: string) => {
    if (!sessionId) return;
    // Clear abort state immediately so SSE events for the new stream are not suppressed
    abortingRef.current = false;
    // Force scroll to bottom when user sends a new message
    isAtBottomRef.current = true;
    setSending(true);
    setIsStreaming(true);

    const tempId = `temp-${Date.now()}`;
    addMessage({
      id: tempId,
      sessionID: sessionId,
      role: 'user',
      parts: [{ id: `${tempId}-part`, type: 'text', text }],
      timestamp: Date.now(),
    } as Message);

    try {
      const payload: Record<string, unknown> = {
        parts: [{ type: 'text', text }],
      };
      if (agentName) payload.agent = agentName;

      await client.post(`/api/session/${sessionId}/prompt_async`, payload);
    } catch (err: unknown) {
      setIsStreaming(false);
      const axiosErr = err as any;
      if (axiosErr?.response?.status === 404) {
        onError?.(`Session not found. Please start a new session.`);
      } else {
        alert(`Send failed: ${err instanceof Error ? err.message : String(err)}`);
      }
      throw err;
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    if (!canSend) return;
    const rawText = input.trim();
    const attachmentsToSend = [...successfulAttachments];
    const text = buildMessageText(rawText, attachmentsToSend);
    if (!text) return;

    setInput('');
    setShowCommandDropdown(false);

    // Route slash commands through the command API (requires an active session)
    const parsed = attachmentsToSend.length === 0 ? parseSlashCommand(rawText) : null;
    if (parsed) {
      if (!sessionId) {
        // Slash commands need an existing session; restore input and do nothing
        setInput(rawText);
        return;
      }
      try {
        await sendCommand(parsed.command, parsed.args);
      } catch {
        setInput(rawText);
      }
      return;
    }

    if (!sessionId) {
      if (onCreateAndSend) {
        setSending(true);
        try {
          await onCreateAndSend(text);
          setAttachments([]);
        } catch {
          setInput(rawText);
        } finally {
          setSending(false);
        }
      }
      return;
    }

    try {
      await sendText(text);
      setAttachments([]);
    } catch {
      setInput(rawText);
    }
  };

  // Auto-send initialMessage (reactive to prop changes; waits for sessionId).
  // Uses a composite key to guard against React StrictMode double-mount sends.
  // Immediately notifies parent so the message won't re-send if selectedSessionId changes.
  useEffect(() => {
    if (!initialMessage || !sessionId) return;
    const sentKey = `${sessionId}::${initialMessage}`;
    if (initialMessageSentRef.current === sentKey) return;
    initialMessageSentRef.current = sentKey;
    sendText(initialMessage).catch(() => {});
    onInitialMessageConsumed?.();
  }, [initialMessage, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showCommandDropdown) {
      const filtered = commands.filter(
        (cmd) => !cmd.hidden && (commandQuery === '' || cmd.name.toLowerCase().startsWith(commandQuery.toLowerCase()))
      );
      const filteredCount = filtered.length;

      if (e.key === 'Escape') {
        e.preventDefault();
        setShowCommandDropdown(false);
        return;
      }

      if (filteredCount === 0) {
        // No candidates — let Enter/Tab fall through to normal behavior
        if (e.key === 'Tab') { e.preventDefault(); }
      } else {
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedCommandIndex((i) => (i - 1 + filteredCount) % filteredCount);
          return;
        }
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedCommandIndex((i) => (i + 1) % filteredCount);
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current)) {
          e.preventDefault();
          const chosen = filtered[selectedCommandIndex] ?? filtered[0];
          if (chosen) {
            setInput(`/${chosen.name} `);
            setShowCommandDropdown(false);
          }
          return;
        }
      }
    }

    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleAbort = useCallback(async () => {
    if (!sessionId) return;
    try {
      // Record the ID of the message being aborted so we can ignore its finish event later
      const lastAsstMsg = [...messagesRef.current].reverse().find(
        (m) => m.role === 'assistant' && !m.finish,
      );
      abortedMessageIdRef.current = lastAsstMsg?.id || null;
      abortingRef.current = true;
      await client.post(`/api/session/${sessionId}/abort`);
      setIsStreaming(false);
      setTimeout(() => { abortingRef.current = false; }, ABORT_SSE_SETTLE_DELAY);
    } catch (err) {
      console.error('[SessionChat] Abort failed:', err);
      abortingRef.current = false;
      abortedMessageIdRef.current = null;
    }
  }, [sessionId]);

  // Fire onStreamingDone when isStreaming transitions true → false
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      onStreamingDone?.();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, onStreamingDone]);

  // Fallback polling to detect completion when SSE events are missed
  useEffect(() => {
    if (!isStreaming || !sessionId) return;
    const timer = setInterval(async () => {
      try {
        const res = await client.get(`/api/session/${sessionId}/message`);
        const msgs: any[] = res.data || [];
        const lastMsg = msgs[msgs.length - 1];
        if (lastMsg?.info?.role === 'assistant' && (lastMsg.info.finish || lastMsg.info.time?.completed)) {
          refetch();
          setIsStreaming(false);
        }
      } catch { /* ignore */ }
    }, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [isStreaming, sessionId, refetch]);

  // Copy text to clipboard
  const handleCopy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
  }, []);

  const resetEditingState = useCallback(() => {
    setEditingMessageId(null);
    setEditingPartId(null);
    setEditingRole(null);
    setEditingText('');
    setActionMessageId(null);
  }, []);

  const reportActionError = useCallback((fallback: string, err: unknown) => {
    const message = err instanceof Error ? err.message : fallback;
    onError?.(message);
    if (!onError) {
      alert(message);
    }
  }, [onError]);

  const beginMessageEdit = useCallback((
    targetMessageId: string,
    targetPartId: string,
    role: Message['role'],
    rawText: string,
  ) => {
    setEditingMessageId(targetMessageId);
    setEditingPartId(targetPartId);
    setEditingRole(role);
    setEditingText(rawText);
    setActionMessageId(null);
  }, []);

  const handleSaveEditedMessage = useCallback(async () => {
    if (!sessionId || !editingMessageId || !editingPartId || !editingRole) return;
    const text = editingText.trim();
    if (!text) return;

    setActionMessageId(editingMessageId);
    try {
      await sessionApi.updateMessagePart(sessionId, editingMessageId, editingPartId, {
        id: editingPartId,
        messageID: editingMessageId,
        sessionID: sessionId,
        type: 'text',
        text,
      });
      replaceMessageText(editingMessageId, editingPartId, text);
      resetEditingState();
    } catch (err) {
      reportActionError(t('chat.errors.saveFailed'), err);
    } finally {
      setActionMessageId(null);
    }
  }, [
    editingMessageId,
    editingPartId,
    editingRole,
    editingText,
    replaceMessageText,
    reportActionError,
    resetEditingState,
    sessionId,
    t,
  ]);

  const handleSendEditedUserMessage = useCallback(async () => {
    if (!sessionId || !editingMessageId || !editingPartId || editingRole !== 'user') return;
    const text = editingText.trim();
    if (!text) return;

    abortingRef.current = false;
    isAtBottomRef.current = true;
    setActionMessageId(editingMessageId);
    try {
      await sessionApi.resendMessage(sessionId, editingMessageId, editingPartId, text);
      replaceMessageText(editingMessageId, editingPartId, text);
      truncateAfterMessage(editingMessageId);
      setIsStreaming(true);
      resetEditingState();
    } catch (err) {
      reportActionError(t('chat.errors.resendFailed'), err);
    } finally {
      setActionMessageId(null);
    }
  }, [
    editingMessageId,
    editingPartId,
    editingRole,
    editingText,
    replaceMessageText,
    reportActionError,
    resetEditingState,
    sessionId,
    t,
    truncateAfterMessage,
  ]);

  const handleRegenerateMessage = useCallback(async (messageId: string) => {
    if (!sessionId) return;

    abortingRef.current = false;
    isAtBottomRef.current = true;
    setActionMessageId(messageId);
    try {
      await sessionApi.regenerateMessage(sessionId, messageId);
      truncateAfterMessage(messageId, { includeTarget: true });
      setIsStreaming(true);
      if (editingMessageId === messageId) {
        resetEditingState();
      }
    } catch (err) {
      reportActionError(t('chat.errors.regenerateFailed'), err);
    } finally {
      setActionMessageId(null);
    }
  }, [editingMessageId, reportActionError, resetEditingState, sessionId, t, truncateAfterMessage]);

  useEffect(() => {
    if (!editingMessageId) return;
    if (!messages.some((message) => message.id === editingMessageId)) {
      resetEditingState();
    }
  }, [editingMessageId, messages, resetEditingState]);

  // ── Merged messages with compaction grouping ──
  // The compaction divider is rendered at the position of the FIRST
  // compacted message (not the summary), so it appears before the
  // preserved messages rather than after them.
  const { merged, compactedGroupMap, summaryRedirectMap, skipIndices } = useMemo(() => {
    const merged = mergeConsecutiveAssistantMessages(messages);
    const compactedGroupMap = new Map<number, MergedMessage[]>();
    // Maps: first-compacted-index → summary-message-index, so we can
    // render the summary message at the earlier position.
    const summaryRedirectMap = new Map<number, number>();
    const compactedBuffer: MergedMessage[] = [];
    let firstCompactedIdx = -1;
    const skipIndices = new Set<number>();

    for (let idx = 0; idx < merged.length; idx++) {
      const msg = merged[idx];
      if (msg.parts.length > 0 && msg.parts.every(p => p.synthetic)) {
        skipIndices.add(idx);
        continue;
      }
      if (msg.compacted) {
        if (compactedBuffer.length === 0) firstCompactedIdx = idx;
        compactedBuffer.push(msg);
        skipIndices.add(idx);
      } else if (msg.finish === 'summary' && compactedBuffer.length > 0) {
        // Render the divider at the first compacted message's position
        skipIndices.delete(firstCompactedIdx);
        compactedGroupMap.set(firstCompactedIdx, [...compactedBuffer]);
        summaryRedirectMap.set(firstCompactedIdx, idx);
        // Skip the summary at its natural (later) position
        skipIndices.add(idx);
        compactedBuffer.length = 0;
        firstCompactedIdx = -1;
      }
    }

    // Orphaned compacted messages (no summary found yet — e.g. compaction
    // still in progress or summary missed during SSE race).  Un-skip them
    // so they remain visible rather than silently disappearing.
    if (compactedBuffer.length > 0) {
      for (const orphan of compactedBuffer) {
        const orphanIdx = merged.indexOf(orphan);
        if (orphanIdx >= 0) skipIndices.delete(orphanIdx);
      }
      compactedBuffer.length = 0;
    }

    return { merged, compactedGroupMap, summaryRedirectMap, skipIndices };
  }, [messages]);

  // ── Styling based on compact mode ──
  const msgAreaClass = compact
    ? 'flex-1 min-h-0 overflow-y-auto bg-gray-50 px-4 py-4 space-y-3'
    : 'flex-1 min-h-0 overflow-y-auto bg-gray-50 py-6';

  const msgListClass = compact ? '' : 'space-y-6 max-w-3xl mx-auto w-full px-4';

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      {/* Messages area */}
      <div ref={scrollContainerRef} className={msgAreaClass} onScroll={handleScroll}>
        {loading && messages.length === 0 ? (
          <div className="flex justify-center py-8">
            <LoadingSpinner />
          </div>
        ) : messages.length === 0 ? (
          welcomeContent ? (
            typeof welcomeContent === 'function' ? (
              <div className="flex items-center justify-center" style={{ minHeight: '100%' }}>
                {welcomeContent((text) => { setInput(text); textareaRef.current?.focus(); })}
              </div>
            ) : (
              <div className="flex items-center justify-center" style={{ minHeight: '100%' }}>
                {welcomeContent}
              </div>
            )
          ) : (
            <div className="text-center py-8 text-gray-400 text-sm">{effectiveEmptyText}</div>
          )
        ) : (
          <div className={msgListClass}>
            {merged.map((msg, i) => {
              if (skipIndices.has(i)) return null;
              // If this position is a redirect, render the summary message here
              const redirectIdx = summaryRedirectMap.get(i);
              const messageToRender = redirectIdx !== undefined ? merged[redirectIdx] : msg;
              return (
                <ChatMessageBubble
                  key={messageToRender.id}
                  message={messageToRender}
                  isActive={
                    isStreaming &&
                    i === merged.length - 1 &&
                    messageToRender.role === 'assistant' &&
                    !messageToRender.finish
                  }
                  pendingQuestions={pendingQuestions}
                  onQuestionAnswer={handleQuestionAnswer}
                  onQuestionReject={handleQuestionReject}
                  showActions={showActions}
                  showTimestamp={showTimestamp}
                  compact={compact}
                  onCopy={handleCopy}
                  editingMessageId={editingMessageId}
                  editingText={editingText}
                  actionsDisabled={sending || isStreaming}
                  actionMessageId={actionMessageId}
                  onEditStart={beginMessageEdit}
                  onEditChange={setEditingText}
                  onEditCancel={resetEditingState}
                  onEditSave={handleSaveEditedMessage}
                  onEditSend={handleSendEditedUserMessage}
                  onRegenerate={handleRegenerateMessage}
                  compactedMessages={compactedGroupMap.get(i)}
                />
              );
            })}

            {/* Compacting indicator */}
            {isCompacting && (
              <div className={`flex justify-start ${!compact ? 'group w-full' : ''}`}>
                <div className={`${compact ? 'max-w-[90%] px-4 py-3 rounded-xl' : 'max-w-2xl w-full px-6 py-4 rounded-2xl'} shadow-sm bg-amber-50 border border-amber-200 text-sm`}>
                  <div className="flex items-center gap-2 text-sm text-amber-700">
                    <Loader2 className="w-4 h-4 animate-spin text-amber-500" />
                    <span>{compactingMessage || t('chat.compacting')}</span>
                  </div>
                </div>
              </div>
            )}

            {/* Standalone thinking indicator when no incomplete message exists */}
            {(isStreaming || sending) && !isCompacting && !(messages.length > 0 && messages[messages.length - 1].role === 'assistant' && !messages[messages.length - 1].finish) && (
              <div className={`flex justify-start ${!compact ? 'group w-full' : ''}`}>
                <div className={`${compact ? 'max-w-[90%] px-4 py-3 rounded-xl' : 'max-w-2xl w-full px-6 py-4 rounded-2xl'} shadow-sm bg-white border border-gray-200 text-sm`}>
                  <div className="text-xs font-medium mb-1.5 opacity-70 flex items-center gap-1.5">
                    <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-red-600 text-white text-[9px] font-bold">R</span>
                    Rex
                  </div>
                  <div className="flex items-center gap-2 text-sm text-gray-500">
                    <div className="flex gap-0.5">
                      <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                      <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                      <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span>{t('chat.thinking')}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
        <div ref={messagesEndRef} className="h-0" />
      </div>

      {/* Suggestions — shown before user sends any message */}
      {suggestions && suggestions.length > 0 && !hasUserMessage && !hideInput && (
        <div className="flex-shrink-0 px-3 pt-2.5 pb-2 border-t border-gray-100 bg-white">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-xs font-medium text-gray-400">{t('chat.suggestions')}</span>
          </div>
          <div className="flex flex-col gap-1.5 max-h-36 overflow-y-auto">
            {suggestions.map((q, i) => (
              <button
                key={i}
                onClick={() => setInput(q)}
                disabled={sending}
                className="text-left text-xs text-gray-600 bg-gray-50 hover:bg-gray-100 hover:text-gray-900 border border-gray-200 hover:border-gray-300 rounded-lg px-2.5 py-2 transition-colors line-clamp-2 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Follow-up input */}
      {!hideInput && (
        <div className={`flex-shrink-0 border-t border-gray-200 bg-white ${compact ? 'px-4 py-3' : 'px-6 py-4'}`}>
          <div className={`flex items-end gap-2 ${!compact ? 'max-w-3xl mx-auto w-full gap-3' : ''}`}>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept={FILE_INPUT_ACCEPT}
              multiple
              onChange={(event) => {
                handleFileSelection(event.target.files);
                event.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={sending || isStreaming}
              title={t('chat.upload.select')}
              className={`flex-shrink-0 rounded-lg border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 hover:text-gray-900 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${
                compact ? 'w-10 h-[40px]' : 'w-12 h-[52px] rounded-xl'
              } inline-flex items-center justify-center`}
            >
              <Plus className="w-4 h-4" />
            </button>
            <div className="relative flex-1">
              <CommandDropdown
                visible={showCommandDropdown}
                query={commandQuery}
                commands={commands}
                selectedIndex={selectedCommandIndex}
                onSelect={(cmd) => {
                  setInput(`/${cmd.name} `);
                  setShowCommandDropdown(false);
                  textareaRef.current?.focus();
                }}
              />
              <div
                onDragOver={handleComposerDragOver}
                onDragLeave={handleComposerDragLeave}
                onDrop={handleComposerDrop}
                className={`border rounded-lg focus-within:border-gray-400 focus-within:ring-2 focus-within:ring-gray-100 transition-all bg-white overflow-hidden ${
                  isCompacting
                    ? 'border-amber-200 bg-amber-50/30'
                    : isDragOver
                      ? 'border-sky-400 bg-sky-50/70 ring-2 ring-sky-100'
                      : isStreaming
                        ? 'border-gray-200 bg-gray-50'
                        : 'border-gray-300'
                } ${!compact ? 'border-2 rounded-xl focus-within:ring-4' : ''}`}
              >
                {/* Node reference chip */}
                {nodeRef && (
                  <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-400 flex-shrink-0" />
                    <code className="text-[11px] font-mono font-semibold text-slate-700 truncate flex-1">{nodeRef.id}</code>
                    <span className="text-[10px] text-slate-400 flex-shrink-0">{nodeRef.type}</span>
                    {onNodeRefDismiss && (
                      <button
                        onClick={onNodeRefDismiss}
                        className="ml-1 text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0"
                        title={t('chat.removeNodeRef')}
                      >
                        <svg className="w-3 h-3" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5">
                          <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
                        </svg>
                      </button>
                    )}
                  </div>
                )}
                {attachments.length > 0 && (
                  <div className={`flex flex-wrap gap-2 px-3 ${nodeRef ? 'pb-2' : 'pt-2'} ${attachments.length > 0 ? '' : 'hidden'}`}>
                    {attachments.map((attachment) => {
                      const isUploading = attachment.status === 'uploading';
                      const isError = attachment.status === 'error';
                      const attachmentPath = attachment.workspacePath ?? null;
                      return (
                        <div
                          key={attachment.id}
                          className={`inline-flex max-w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${
                            isError
                              ? 'border-red-200 bg-red-50 text-red-700'
                              : isUploading
                                ? 'border-sky-200 bg-sky-50 text-sky-700'
                                : 'border-gray-200 bg-gray-50 text-gray-700'
                          }`}
                        >
                          {isUploading ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0" />
                          ) : isError ? (
                            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
                          ) : (
                            <FileText className="w-3.5 h-3.5 flex-shrink-0" />
                          )}
                          <div className="min-w-0">
                            <div className="truncate font-medium">{attachment.name}</div>
                            {attachmentPath && (
                              <div className="truncate text-[11px] opacity-70">{attachmentPath}</div>
                            )}
                            {attachment.error && (
                              <div className="truncate text-[11px]">{attachment.error}</div>
                            )}
                          </div>
                          {isError && (
                            <button
                              type="button"
                              onClick={() => handleRetryAttachment(attachment.id)}
                              className="rounded p-0.5 hover:bg-white/70 transition-colors"
                              title={t('chat.upload.retry')}
                            >
                              <RefreshCw className="w-3.5 h-3.5" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => handleRemoveAttachment(attachment.id)}
                            className="rounded p-0.5 hover:bg-white/70 transition-colors"
                            title={t('chat.upload.remove')}
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
                {isDragOver && (
                  <div className="px-3 pb-1 text-[11px] text-sky-600">
                    {t('chat.upload.dropHint')}
                  </div>
                )}
                <div className={nodeRef || attachments.length > 0 ? 'px-3 pb-2.5' : `px-3 ${compact ? 'py-2' : 'py-3'}`}>
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={(e) => {
                      const val = e.target.value;
                      setInput(val);
                      const trimmed = val.trimStart();
                      if (trimmed.startsWith('/') && !trimmed.includes(' ') && successfulAttachments.length === 0) {
                        void loadCommandsIfNeeded();
                        const q = trimmed.slice(1);
                        setCommandQuery(q);
                        setSelectedCommandIndex(0);
                        setShowCommandDropdown(true);
                      } else {
                        setShowCommandDropdown(false);
                      }
                    }}
                    onBlur={() => { setTimeout(() => setShowCommandDropdown(false), 100); }}
                    onCompositionStart={() => { isComposingRef.current = true; }}
                    onCompositionEnd={() => { isComposingRef.current = false; }}
                    onPaste={handleComposerPaste}
                    onKeyDown={handleKeyDown}
                    placeholder={
                      isCompacting
                        ? t('chat.placeholderCompacting')
                        : isStreaming
                          ? t('chat.placeholderStreaming')
                          : nodeRef
                            ? t('chat.placeholderNodeRef', { nodeId: nodeRef.id })
                            : effectivePlaceholder
                    }
                    className={`w-full resize-none outline-none text-sm placeholder-gray-400 ${
                      isStreaming ? 'text-gray-400 cursor-not-allowed' : 'text-gray-900'
                    } ${!compact ? 'bg-transparent' : ''}`}
                    style={{ minHeight: '24px', maxHeight: compact ? '96px' : '200px' }}
                    disabled={sending || isStreaming}
                    rows={1}
                  />
                </div>
              </div>
            </div>
            <button
              onClick={handleSend}
              disabled={!canSend}
              className={`flex-shrink-0 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 text-sm transition-colors ${
                compact ? 'px-3 py-2 h-[40px]' : 'px-6 py-3 h-[52px] rounded-xl shadow-md hover:shadow-lg'
              }`}
              title={hasUploadingFiles ? t('chat.upload.waiting') : undefined}
            >
              {sending || hasUploadingFiles ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
            {isStreaming && (
              <button
                onClick={handleAbort}
                className={`flex-shrink-0 bg-gradient-to-r from-red-600 to-red-500 text-white rounded-lg hover:from-red-700 hover:to-red-600 flex items-center gap-1 text-sm transition-all shadow ${
                  compact ? 'px-3 py-2 h-[40px]' : 'px-4 py-3 h-[52px] rounded-xl'
                }`}
                title={t('chat.stopTitle')}
              >
                <Square className="w-4 h-4 fill-current" />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// ChatMessageBubble
// ============================================================================

export interface ChatMessageBubbleProps {
  message: MergedMessage;
  isActive?: boolean;
  pendingQuestions?: Record<string, PendingQuestion>;
  onQuestionAnswer?: (callID: string, requestId: string, answers: string[][]) => Promise<void>;
  onQuestionReject?: (callID: string, requestId: string) => Promise<void>;
  showActions?: boolean;
  showTimestamp?: boolean;
  compact?: boolean;
  onCopy?: (text: string) => void;
  editingMessageId?: string | null;
  editingText?: string;
  actionsDisabled?: boolean;
  actionMessageId?: string | null;
  onEditStart?: (messageId: string, partId: string, role: Message['role'], rawText: string) => void;
  onEditChange?: (text: string) => void;
  onEditCancel?: () => void;
  onEditSave?: () => Promise<void>;
  onEditSend?: () => Promise<void>;
  onRegenerate?: (messageId: string) => Promise<void>;
  /** Compacted messages that precede this summary message */
  compactedMessages?: MergedMessage[];
}

function ChatMessageBubbleInner({
  message,
  isActive = false,
  pendingQuestions,
  onQuestionAnswer,
  onQuestionReject,
  showActions = false,
  showTimestamp = false,
  compact = true,
  onCopy,
  editingMessageId,
  editingText = '',
  actionsDisabled = false,
  actionMessageId,
  onEditStart,
  onEditChange,
  onEditCancel,
  onEditSave,
  onEditSend,
  onRegenerate,
  compactedMessages,
}: ChatMessageBubbleProps) {
  const { t } = useTranslation('session');
  const isUser = message.role === 'user';
  const parts: MessagePart[] = Array.isArray(message.parts) ? message.parts : [];
  const { getPartExpanded, togglePart, isReasoningDone } = useReasoningToggle(parts, message.finish);
  if (message.finish === 'summary') {
    const hasArchived = compactedMessages && compactedMessages.length > 0;
    return (
      <div className="my-3 px-1">
        {/* Archived messages shown inline without collapse */}
        {hasArchived && (
          <div className="mb-3 space-y-3">
            {compactedMessages!.map((cMsg) => (
              <ChatMessageBubble
                key={cMsg.id}
                message={cMsg}
                showTimestamp={showTimestamp}
                compact={compact}
                onCopy={onCopy}
                editingMessageId={editingMessageId}
                editingText={editingText}
                actionsDisabled={actionsDisabled}
                actionMessageId={actionMessageId}
                onEditStart={onEditStart}
                onEditChange={onEditChange}
                onEditCancel={onEditCancel}
                onEditSave={onEditSave}
                onEditSend={onEditSend}
                onRegenerate={onRegenerate}
              />
            ))}
          </div>
        )}
      </div>
    );
  }
  const rawAgentName = message.agent || 'rex';
  const agentName = rawAgentName.charAt(0).toUpperCase() + rawAgentName.slice(1);

  const getTextContent = () =>
    parts
      .filter((p) => p.type === 'text' && p.text)
      .map((p) => p.text)
      .join('\n\n');

  const editableTextParts = parts.filter((part): part is MessagePart & { text: string } =>
    part.type === 'text' && typeof part.text === 'string',
  );
  const latestEditablePart = editableTextParts.length > 0 ? editableTextParts[editableTextParts.length - 1] : null;
  const targetMessageId = String((latestEditablePart as any)?.messageID || message.id);
  const targetPartId = latestEditablePart?.id || null;
  const editableRawText = latestEditablePart?.text || '';
  const isEditing = !!targetPartId && editingMessageId === targetMessageId;
  const isActionPending = actionMessageId === targetMessageId;

  const bubbleClass = compact
    ? `max-w-[90%] px-4 py-3 rounded-xl text-sm break-words ${
        isUser
          ? 'bg-gradient-to-br from-slate-50 to-gray-100 border border-slate-200 text-gray-900 shadow-sm'
          : 'bg-white border border-gray-200 shadow-sm'
      }`
    : `${isUser ? 'max-w-2xl w-auto' : 'max-w-2xl w-full'} px-6 py-4 rounded-2xl text-sm break-words ${
        isUser
          ? 'bg-gradient-to-br from-slate-50 to-gray-100 border border-slate-200 text-gray-900 shadow-sm'
          : 'bg-white border border-gray-200 shadow-sm hover:shadow-md transition-shadow duration-200'
      }`;
  const actionBarClass = `absolute bottom-0 z-10 flex items-center gap-1.5 transition-all duration-150 ${
    isUser ? 'right-3 translate-x-0.5 translate-y-1/2' : 'left-3 -translate-x-0.5 translate-y-1/2'
  } ${
    isEditing
      ? 'opacity-100 pointer-events-auto'
      : 'opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto'
  }`;
  const iconButtonClass = 'group/action relative inline-flex h-6 w-6 items-center justify-center rounded-full border border-gray-200/90 bg-white text-gray-500 shadow-[0_6px_18px_rgba(15,23,42,0.08)] backdrop-blur-sm transition-all duration-150 hover:-translate-y-px hover:border-gray-300 hover:text-gray-900 disabled:opacity-40 disabled:cursor-not-allowed';
  const tooltipClass = 'pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity duration-150 group-hover/action:opacity-100';

  return (
    <div className={`group relative flex ${isUser ? 'justify-end' : 'justify-start'} ${!compact ? 'w-full' : ''}`}>
      <div className={`${bubbleClass} relative`} style={{ overflowWrap: 'anywhere' }}>
        {/* Role badge */}
        <div className={`text-xs font-medium mb-2 flex items-center ${compact ? 'gap-1.5' : 'gap-2'}`}>
          {isUser ? (
            <span className="flex items-center gap-1.5">
              <span className={`inline-flex items-center justify-center rounded-full bg-slate-500 text-white flex-shrink-0 ${
                compact ? 'w-5 h-5' : 'w-6 h-6'
              }`}>
                <User className={compact ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
              </span>
              <span className="font-semibold text-gray-700">{t('chat.you')}</span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5">
              <span className={`inline-flex items-center justify-center rounded-full bg-red-600 text-white font-bold flex-shrink-0 ${
                compact ? 'w-5 h-5 text-[9px]' : 'w-6 h-6 text-xs'
              }`}>
                {agentName.charAt(0)}
              </span>
              <span className="font-semibold text-gray-800">{agentName}</span>
            </span>
          )}
        </div>

        {/* Empty / loading state */}
        {parts.length === 0 && (
          <div className="flex items-center gap-2 opacity-60">
            <div className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
            {isUser ? t('chat.sending') : t('chat.thinking')}
          </div>
        )}

        {/* Parts */}
        {isEditing ? (
          <div className="space-y-3">
            <textarea
              value={editingText}
              onChange={(event) => onEditChange?.(event.target.value)}
              rows={Math.min(12, Math.max(4, editingText.split('\n').length + 1))}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-100"
            />
          </div>
        ) : (
          parts.map((part: MessagePart, i: number) => (
            <div key={part.id || i}>
              {/* Text */}
              {part.type === 'text' && part.text && (() => {
                const nodeRefMatch = isUser
                  ? part.text.match(/^@@node:([^|\n]+)\|([^\n]+)\n([\s\S]*)$/)
                  : null;
                const displayText = nodeRefMatch ? nodeRefMatch[3] : part.text;
                return (
                  <>
                    {nodeRefMatch && (
                      <div className="flex items-center gap-1.5 mb-2 bg-gray-100 border border-gray-200 rounded-md px-2 py-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-gray-400 flex-shrink-0" />
                        <code className="text-[10px] font-mono font-semibold text-gray-700 truncate">{nodeRefMatch[1]}</code>
                        <span className="text-[9px] text-gray-500 flex-shrink-0">{nodeRefMatch[2]}</span>
                      </div>
                    )}
                    <StreamingMarkdown
                      content={displayText}
                      isStreaming={isActive && !isUser}
                    />
                  </>
                );
              })()}

              {/* Tool call */}
              {part.type === 'tool' && (
                <ChatToolPart
                  part={part}
                  pendingQuestion={part.callID ? pendingQuestions?.[part.callID] : undefined}
                  onAnswer={onQuestionAnswer && part.callID
                    ? (answers) => onQuestionAnswer(part.callID!, pendingQuestions![part.callID!].requestId, answers)
                    : undefined}
                  onReject={onQuestionReject && part.callID
                    ? () => onQuestionReject(part.callID!, pendingQuestions![part.callID!].requestId)
                    : undefined}
                />
              )}

              {/* Reasoning / thinking */}
              {(part.type === 'reasoning' || part.type === 'thinking') && (part.text || part.thinking) && (() => {
                const thinkingText = part.text || part.thinking || '';
                const partKey = part.id || `reasoning-${i}`;
                const isExpanded = getPartExpanded(partKey);
                const isThinking = !isReasoningDone;
                const partLabel = isThinking
                  ? `💭 ${t('chat.thinking')}`
                  : `💭 ${thinkingText.slice(0, 60)}${thinkingText.length > 60 ? '...' : ''}`;
                return (
                  <div className="mt-2">
                    <button
                      onClick={() => togglePart(partKey)}
                      disabled={isThinking}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded-md border text-xs font-medium transition-colors w-full text-left
                        ${isThinking
                          ? 'bg-purple-50 border-purple-200 text-purple-700 cursor-default'
                          : 'bg-purple-50 hover:bg-purple-100 border-purple-200 text-purple-900 cursor-pointer'
                        }`}
                    >
                      {isThinking && (
                        <span className="flex gap-0.5 mr-1">
                          <span className="w-1 h-1 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                          <span className="w-1 h-1 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                          <span className="w-1 h-1 bg-purple-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                        </span>
                      )}
                      {partLabel}
                      {!isThinking && (
                        <ChevronDown
                          className={`w-3 h-3 ml-auto text-purple-600 transition-transform ${isExpanded ? '' : '-rotate-90'}`}
                        />
                      )}
                    </button>
                    {isExpanded && (
                      <div className="mt-1 p-2 bg-purple-50/50 rounded-md border border-purple-100 text-xs text-purple-800 whitespace-pre-wrap font-mono leading-relaxed">
                        {thinkingText}
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          ))
        )}

        {/* Streaming indicator */}
        {isActive && !isUser && parts.length > 0 && (() => {
          const lastPart = parts[parts.length - 1];
          const isDelegating = lastPart?.type === 'tool'
            && isDelegateTool(lastPart.tool || '')
            && lastPart.state?.status === 'running';
          if (isDelegating) return null;
          return (
            <div className="flex items-center gap-2 mt-2.5 pt-2 border-t border-gray-100 text-xs text-gray-400">
              <div className="flex gap-0.5">
                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span>{t('chat.streaming')}</span>
            </div>
          );
        })()}

        {/* Timestamp */}
        {showTimestamp && message.timestamp && (
          <div className={`text-xs mt-2 ${isUser ? 'opacity-60' : 'opacity-40'}`}>
            {formatSmartTime(message.timestamp)}
          </div>
        )}

        {/* Actions */}
        {showActions && parts.length > 0 && (
          <div className={actionBarClass}>
            {isEditing ? (
              <>
                <button
                  onClick={() => void onEditSave?.()}
                  disabled={actionsDisabled || isActionPending || !editingText.trim()}
                  className={iconButtonClass}
                  aria-label={t('chat.save')}
                >
                  <Save className="w-3 h-3" />
                  <span className={tooltipClass}>{t('chat.save')}</span>
                </button>
                {isUser && (
                  <button
                    onClick={() => void onEditSend?.()}
                    disabled={actionsDisabled || isActionPending || !editingText.trim()}
                    className={iconButtonClass}
                    aria-label={t('chat.sendEdited')}
                  >
                    <Send className="w-3 h-3" />
                    <span className={tooltipClass}>{t('chat.sendEdited')}</span>
                  </button>
                )}
                <button
                  onClick={onEditCancel}
                  disabled={isActionPending}
                  className={iconButtonClass}
                  aria-label={t('chat.cancel')}
                >
                  <X className="w-3 h-3" />
                  <span className={tooltipClass}>{t('chat.cancel')}</span>
                </button>
              </>
            ) : (
              <>
                {targetPartId && editableRawText && (
                  <button
                    onClick={() => onEditStart?.(targetMessageId, targetPartId, message.role, editableRawText)}
                    disabled={actionsDisabled || isActionPending}
                    className={iconButtonClass}
                    aria-label={isUser ? t('chat.edit') : t('chat.editRawTitle')}
                  >
                    <Pencil className="w-3 h-3" />
                    <span className={tooltipClass}>{isUser ? t('chat.edit') : t('chat.editRawTitle')}</span>
                  </button>
                )}
                <button
                  onClick={() => onCopy?.(getTextContent())}
                  disabled={isActionPending}
                  className={iconButtonClass}
                  aria-label={t('chat.copy')}
                >
                  <Copy className="w-3 h-3" />
                  <span className={tooltipClass}>{t('chat.copy')}</span>
                </button>
                {!isUser && (
                  <button
                    onClick={() => void onRegenerate?.(targetMessageId)}
                    disabled={actionsDisabled || isActionPending}
                    className={iconButtonClass}
                    aria-label={t('chat.regenerate')}
                  >
                    <RefreshCw className={`w-3 h-3 ${isActionPending ? 'animate-spin' : ''}`} />
                    <span className={tooltipClass}>{t('chat.regenerate')}</span>
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// ChatToolPart — collapsible tool call card
// ============================================================================

export interface ChatToolPartProps {
  part: MessagePart;
  pendingQuestion?: PendingQuestion;
  onAnswer?: (answers: string[][]) => Promise<void>;
  onReject?: () => Promise<void>;
}

export function ChatToolPart({ part, pendingQuestion, onAnswer, onReject }: ChatToolPartProps) {
  const { t } = useTranslation('session');
  const toolName = part.tool || 'unknown';

  // Primary check: tool name is a known delegate tool.
  // Fallback: state.input contains delegate-specific fields (handles cases where
  // part.tool may be missing or unrecognized after reload).
  const stateInput = part.state?.input as Record<string, any> | undefined;
  if (
    isDelegateTool(toolName) ||
    (stateInput && ('subagent_type' in stateInput || 'category' in stateInput))
  ) {
    return <DelegateTaskCard part={part} />;
  }

  const state: Partial<ToolState> = part.state || {};
  const status = state.status || 'pending';

  // Some tools block on an internal `question` call (for example safety
  // confirmation inside `ssh_host_cmd`), so render the question UI whenever
  // this running tool part has a pending question attached to it.
  const isWaitingForAnswer = status === 'running' && !!pendingQuestion;

  const statusConfig: Record<string, { icon: string; bg: string; border: string; text: string; label: string }> = {
    pending:   { icon: '⏳', bg: 'bg-yellow-50', border: 'border-yellow-200', text: 'text-yellow-800', label: t('chat.tool.pending') },
    running:   { icon: '🔄', bg: 'bg-sky-50',   border: 'border-sky-200',    text: 'text-sky-800', label: t('chat.tool.running') },
    completed: { icon: '✅', bg: 'bg-green-50',  border: 'border-green-200',  text: 'text-green-800', label: t('chat.tool.completed') },
    error:     { icon: '❌', bg: 'bg-red-50',    border: 'border-red-200',    text: 'text-red-800', label: t('chat.tool.error') },
  };
  const config = statusConfig[status] ?? statusConfig.pending;

  const formatOutput = (output: unknown): string => {
    if (typeof output === 'string') {
      try { return JSON.stringify(JSON.parse(output), null, 2); } catch { return output; }
    }
    return JSON.stringify(output, null, 2);
  };

  const inputSummary = state.input
    ? Object.entries(state.input).map(([k, v]) => `${k}=${v}`).join(', ')
    : '';

  if (isWaitingForAnswer) {
    return (
      <div className="mt-2">
        <QuestionTool
          questions={pendingQuestion!.questions}
          onAnswer={onAnswer!}
          onReject={onReject}
          compact
        />
      </div>
    );
  }

  return (
    <details className={`mt-1.5 rounded-md border ${config.bg} ${config.border} overflow-hidden`}>
      <summary
        className={`px-2 py-1.5 cursor-pointer flex items-center gap-1.5 text-xs font-medium ${config.text} hover:opacity-80`}
      >
        <span>{config.icon}</span>
        <span className="truncate">{toolName.replace(/_/g, ' ')}</span>
        {inputSummary && (
          <span className="text-[10px] opacity-50 truncate max-w-[120px]">({inputSummary})</span>
        )}
        {state.title && <span className="ml-1 opacity-70 text-[10px]">{state.title}</span>}
        <span className="ml-auto opacity-70">{config.label}</span>
      </summary>

      <div className="px-2 pb-2 space-y-1 text-xs">
        {state.input && (
          <details className="bg-white/50 rounded p-1.5">
            <summary className="cursor-pointer font-medium text-gray-600 text-[11px]">📥 {t('chat.tool.inputParams')}</summary>
            <pre className="mt-1 p-1.5 bg-gray-800 text-gray-100 rounded text-[11px] overflow-x-auto font-mono">
              {JSON.stringify(state.input, null, 2)}
            </pre>
          </details>
        )}

        {status === 'completed' && state.output !== undefined && (
          <details className="bg-white/50 rounded p-1.5" open>
            <summary className="cursor-pointer font-medium text-gray-600 text-[11px]">📤 {t('chat.tool.outputResult')}</summary>
            <pre className="mt-1 p-1.5 bg-gray-800 text-green-300 rounded text-[11px] overflow-x-auto max-h-48 overflow-y-auto font-mono">
              {formatOutput(state.output)}
            </pre>
          </details>
        )}

        {status === 'error' && state.error && (
          <div className="bg-red-100 rounded p-1.5 text-red-700 text-[11px]">
            {t('chat.tool.errorLabel')}: {state.error}
          </div>
        )}

        {state.time?.start && state.time?.end && (
          <div className="text-gray-400 text-right text-[10px]">
            {t('chat.tool.elapsed')}: {((state.time.end - state.time.start) / 1000).toFixed(2)}s
          </div>
        )}
      </div>
    </details>
  );
}

/**
 * Memoized export of ChatMessageBubble.
 *
 * Fast path (O(1) field checks, aligned with Open WebUI's approach):
 * - structural props: isActive, role, finish, parts.length
 * - content probe: last part's text/thinking field
 *
 * Only triggers a re-render when something actually visible has changed,
 * avoiding unnecessary reconciliation during high-frequency streaming.
 */
export const ChatMessageBubble = memo(ChatMessageBubbleInner, (prev, next) => {
  if (prev.isActive !== next.isActive) return false;
  if (prev.showActions !== next.showActions) return false;
  if (prev.editingMessageId !== next.editingMessageId) return false;
  if (prev.editingText !== next.editingText) return false;
  if (prev.actionsDisabled !== next.actionsDisabled) return false;
  if (prev.actionMessageId !== next.actionMessageId) return false;
  if (prev.message.finish !== next.message.finish) return false;
  const prevParts = prev.message.parts as any[] | undefined;
  const nextParts = next.message.parts as any[] | undefined;
  if ((prevParts?.length ?? 0) !== (nextParts?.length ?? 0)) return false;
  if (prev.pendingQuestions !== next.pendingQuestions) return false;
  // O(1) content probe on the last part — covers the streaming delta case
  const prevLast = prevParts?.[prevParts.length - 1];
  const nextLast = nextParts?.[nextParts.length - 1];
  return (
    prevLast?.text === nextLast?.text &&
    prevLast?.thinking === nextLast?.thinking &&
    prevLast?.state?.status === nextLast?.state?.status
  );
});
