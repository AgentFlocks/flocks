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
import { Send, Loader2, ChevronDown, Square, Copy, User, FileText, AlertCircle, X, RefreshCw, Pencil, Save, ImageIcon, Paperclip, ArrowUp, Clock, CheckCircle2, XCircle, Brain } from 'lucide-react';
import { StreamingMarkdown } from './StreamingMarkdown';
import { useTranslation } from 'react-i18next';
import LoadingSpinner from './LoadingSpinner';
import { QuestionTool } from './QuestionTool';
import DelegateTaskCard, { isDelegateTool, shouldRenderDelegateTaskCard } from './DelegateTaskCard';
import CommandDropdown, { parseSlashCommand } from './CommandDropdown';
import ImageLightbox from './ImageLightbox';
import { useSessionMessages } from '@/hooks/useSessions';
import { useSSE, type SSEConnectionStatus } from '@/hooks/useSSE';
import { useReasoningToggle } from '@/hooks/useReasoningToggle';
import { usePendingQuestions, type PendingQuestion } from '@/hooks/usePendingQuestions';
import { sessionApi } from '@/api/session';
import client, { getApiBase } from '@/api/client';
import { commandAPI, type Command } from '@/api/skill';
import { useToast } from './Toast';
import { workspaceAPI } from '@/api/workspace';
import { formatSmartTime } from '@/utils/time';
import {
  FILE_INPUT_ACCEPT_IMAGES,
  batchCompressOptions,
  buildPromptParts,
  compressImageFile,
  getFileExtension,
  isImageFile,
  readFileAsDataUrl,
  type ImagePartData,
} from '@/utils/imageUpload';
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
  /** Extra content injected into the composer toolbar (left of send button, after divider) */
  toolbarSlot?: React.ReactNode;
  /**
   * Called when the user sends a message but sessionId is not yet available.
   * The parent should create a session and dispatch the prompt (with the
   * provided text and any image attachments) to the new session.
   *
   * `imageParts` carries inline image data URLs — parents that don't yet
   * support image input can ignore the second argument.
   *
   * The return value is intentionally typed as ``unknown`` so callers can
   * pass ``useSessionChat().createAndSend`` (which resolves to the new
   * session id) directly without an empty ``async (..) => { await ... }``
   * shim.
   */
  onCreateAndSend?: (text: string, imageParts?: ImagePartData[]) => Promise<unknown> | unknown;
  /** Called when the user sends "/new" to create a new session */
  onCreateNewSession?: () => Promise<void> | void;
  /**
   * Whether the current model supports vision/image analysis.
   * true = allow images; false = block images with a UI warning; null/undefined = allow (unknown).
   */
  supportsVision?: boolean | null;
}

type AttachmentStatus = 'uploading' | 'success' | 'error';

interface ComposerAttachment {
  id: string;
  file: File;
  name: string;
  status: AttachmentStatus;
  /** For document attachments: the workspace-relative path after upload */
  workspacePath?: string;
  /** For image attachments: the base64 data URL (no server upload needed) */
  dataUrl?: string;
  /** True if this attachment is an image file */
  isImage?: boolean;
  error?: string;
}

// Composer drafts are persisted to ``localStorage`` so navigating away from
// the page (e.g. clicking the sidebar to open Agents / Workflows) and coming
// back doesn't lose the half-typed message. Keyed per session so two sessions
// don't share a draft, and namespaced to avoid colliding with other features.
import { readChatDraft, writeChatDraft } from '@/utils/chatDraft';

// Backend stages emitted by ``SessionCompaction.process`` /
// ``summarize_chunked`` via the ``session.compaction_progress`` SSE event.
// Keep in sync with ``flocks/session/lifecycle/compaction/{compaction,summary}.py``.
type CompactionStage =
  | 'load'
  | 'strategy'
  | 'chunk_done'
  | 'merge_started'
  | 'merge_done'
  | 'summarize_done'
  | 'complete';

interface CompactionStageEntry {
  stage: CompactionStage;
  data: Record<string, unknown>;
  ts: number;
}

/**
 * Render a single human-readable line for one compaction stage event.
 *
 * Kept i18n-aware (caller passes ``t``) and total-aware so e.g.
 * ``chunk_done`` shows ``2 / 5``.  Numbers are rendered defensively —
 * the SSE payload is untyped JSON, so we type-narrow before formatting.
 *
 * Returns ``null`` if the stage is unknown so the caller can ``filter
 * Boolean`` the list without printing raw event names to end users.
 */
function describeCompactionStage(
  entry: CompactionStageEntry,
  t: (key: string, options?: Record<string, unknown>) => string,
): string | null {
  const data = entry.data;
  const num = (k: string): number | undefined =>
    typeof data[k] === 'number' ? (data[k] as number) : undefined;
  switch (entry.stage) {
    case 'load': {
      const count = num('message_count');
      return t('chat.compactionStage.load', { count: count ?? '?' });
    }
    case 'strategy': {
      const decision = typeof data.decision === 'string' ? data.decision : 'single_pass';
      const chunks = num('chunks');
      if (chunks && chunks > 1) {
        return t('chat.compactionStage.strategyChunked', { count: chunks });
      }
      return t(`chat.compactionStage.strategy_${decision}`, {
        defaultValue: t('chat.compactionStage.strategyGeneric'),
      });
    }
    case 'chunk_done':
      // Per-chunk events drive the percentage bar but are intentionally
      // hidden from the milestone list — users asked for a single
      // overall progress signal rather than N noisy "chunk X/N done"
      // lines that arrive out of order under ``asyncio.gather``.
      return null;
    case 'merge_started':
      return t('chat.compactionStage.mergeStarted', { count: num('chunks_merged') ?? '?' });
    case 'merge_done': {
      const ok = data.ok !== false;
      const ms = num('duration_ms');
      return ok
        ? t('chat.compactionStage.mergeDone', {
            seconds: ms !== undefined ? (ms / 1000).toFixed(1) : '?',
          })
        : t('chat.compactionStage.mergeFailed');
    }
    case 'summarize_done':
      return t('chat.compactionStage.summarizeDone', { chars: num('summary_chars') ?? 0 });
    case 'complete':
      return t('chat.compactionStage.complete');
    default:
      return null;
  }
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

export function getMessageBubbleClassName({
  compact,
  isUser,
  isEditing,
}: {
  compact: boolean;
  isUser: boolean;
  isEditing: boolean;
}): string {
  if (compact) {
    return `max-w-[90%] px-4 py-3 rounded-[20px] text-sm break-words shadow-sm ${
      isUser
        ? 'bg-sky-50 border border-sky-100 text-zinc-900'
        : 'bg-white border border-zinc-200/90'
    }`;
  }

  const widthClass = isUser
    ? (isEditing ? 'w-full' : 'w-auto')
    : 'w-full';

  return `${widthClass} px-5 py-4 rounded-[24px] text-sm break-words shadow-sm ${
    isUser
      ? 'bg-sky-50 border border-sky-100 text-zinc-900'
      : 'bg-white border border-zinc-200/90'
  }`;
}

export function getMessageGroupClassName({
  compact,
  isUser,
  isEditing,
}: {
  compact: boolean;
  isUser: boolean;
  isEditing: boolean;
}): string {
  if (!isUser) {
    return compact ? 'max-w-[90%]' : 'w-full';
  }

  if (compact) {
    return isEditing ? 'w-full max-w-[90%]' : 'w-fit max-w-[90%]';
  }

  return isEditing ? 'w-[80%] max-w-[80%]' : 'w-fit max-w-[80%]';
}

export function getRegenerateTruncateTarget(
  messages: Message[],
  messageId: string,
): { messageId: string; includeTarget?: boolean } {
  const targetMessage = messages.find((message) => message.id === messageId);
  if (targetMessage?.role === 'assistant' && targetMessage.parentID) {
    return { messageId: targetMessage.parentID };
  }
  return { messageId, includeTarget: true };
}

export function shouldRefetchFinishedMessage({
  finishedMessageId,
  abortedMessageId,
}: {
  finishedMessageId?: string | null;
  abortedMessageId?: string | null;
}): boolean {
  return !finishedMessageId || !abortedMessageId || finishedMessageId !== abortedMessageId;
}

export function getEditingActionBarClassName(): string {
  return 'mt-3 flex w-full items-center justify-end gap-1.5';
}

export function getStandaloneThinkingBubbleClassName(compact: boolean): string {
  return getMessageBubbleClassName({ compact, isUser: false, isEditing: false });
}

export function getUserAvatarContainerClassName(compact: boolean): string {
  return `pointer-events-none absolute left-full top-0 ml-2.5 translate-y-1/2 flex items-center justify-end ${
    compact ? 'h-7' : 'h-8'
  }`;
}

export function getUserAvatarSpacerClassName(compact: boolean): string {
  return compact ? 'h-3.5' : 'h-4';
}


// ============================================================================
// Main component
// ============================================================================

const ABORT_SSE_SETTLE_DELAY = 2000;
const SCROLL_BOTTOM_THRESHOLD_PX = 80;
const FALLBACK_POLL_MS = 5_000;
const WORKSPACE_UPLOAD_DEST = 'uploads';
const FILE_INPUT_ACCEPT_DOCS = '.txt,.md,.json,.yaml,.yml,.xml,.csv,.pdf,.doc,.docx,.html,.htm,.ppt,.pptx,.xls,.xlsx';
const FILE_INPUT_ACCEPT_ALL = `${FILE_INPUT_ACCEPT_DOCS},${FILE_INPUT_ACCEPT_IMAGES}`;
const ALLOWED_UPLOAD_EXTENSIONS = new Set([
  'txt', 'md', 'json', 'yaml', 'yml', 'xml', 'csv', 'pdf', 'doc', 'docx',
  'html', 'htm', 'ppt', 'pptx', 'xls', 'xlsx',
]);

function isAllowedUploadFile(file: File): boolean {
  return ALLOWED_UPLOAD_EXTENSIONS.has(getFileExtension(file.name));
}

function isUploadedDocumentAttachment<T extends {
  status: string;
  workspacePath?: string;
  isImage?: boolean;
}>(
  attachment: T,
): attachment is T & { workspacePath: string } {
  return attachment.status === 'success' && !attachment.isImage && Boolean(attachment.workspacePath);
}

export function dedupeUploadedDocumentAttachments<T extends {
  status: string;
  workspacePath?: string;
  isImage?: boolean;
}>(items: T[]): T[] {
  const latestIndexByPath = new Map<string, number>();
  items.forEach((item, index) => {
    if (isUploadedDocumentAttachment(item)) {
      latestIndexByPath.set(item.workspacePath, index);
    }
  });
  return items.filter((item, index) => (
    !isUploadedDocumentAttachment(item) || latestIndexByPath.get(item.workspacePath) === index
  ));
}

export function listUploadedDocumentPaths<T extends {
  status: string;
  workspacePath?: string;
  isImage?: boolean;
}>(items: T[]): string[] {
  return dedupeUploadedDocumentAttachments(items)
    .filter(isUploadedDocumentAttachment)
    .map((item) => item.workspacePath);
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
  onCreateNewSession,
  onInitialMessageConsumed,
  supportsVision,
  toolbarSlot,
}: SessionChatProps) {
  const { t } = useTranslation('session');
  const toast = useToast();
  const compact = display?.compact ?? true;
  const showActions = display?.showActions ?? false;
  const showTimestamp = display?.showTimestamp ?? false;
  const effectivePlaceholder = placeholder ?? t('chat.placeholder');
  const effectiveEmptyText = emptyText ?? t('chat.emptyText');
  // Restore any persisted draft on first mount so navigating away (e.g.
  // sidebar → Agents → back to Sessions) doesn't wipe the user's half-typed
  // message. Subsequent session changes are re-hydrated by the effect below.
  const [input, setInput] = useState<string>(() => readChatDraft(sessionId));
  const [sending, setSending] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  // Lightbox preview for composer thumbnails. Shares the same overlay
  // component used by message bubbles so the click-to-enlarge gesture is
  // consistent across the upload tray and the rendered chat history.
  const [composerPreview, setComposerPreview] = useState<{ url: string; alt?: string } | null>(null);
  const [isCompacting, setIsCompacting] = useState(false);
  const [compactingMessage, setCompactingMessage] = useState('');
  // Live compaction progress, populated by ``session.compaction_progress`` SSE
  // events emitted by the backend. ``chunk_done`` arrivals are non-deterministic
  // (parallel ``asyncio.gather``) so we deduplicate by ``data.chunk`` index.
  // The chunk progress bar (``done/total``) is *derived* from this single
  // source via useMemo below — keeping a parallel state would risk drift if
  // either updater missed an event (and earlier did: a stale closure read
  // froze ``done`` at 1 for multi-chunk runs).
  const [compactionStages, setCompactionStages] = useState<CompactionStageEntry[]>([]);
  // Single weighted progress percentage (0–100) covering the whole
  // compaction pipeline. Per-chunk events drive the parallel-summary
  // band (10–70%); merge owns 70–95%; summary write + completion
  // close the last 5%. Single-pass runs skip the chunk band entirely
  // and jump strategy → summarize_done (20% → 95%).
  //
  // Why fixed weights instead of timing-based progress:
  //  - Chunks finish in non-deterministic order so a time-linear bar
  //    would jitter or stall whenever the slowest chunk dominates.
  //  - The user only needs "where am I in the pipeline", not real-time
  //    estimation; phase advancement gives a credible signal of life.
  const compactionPercent = useMemo<number | null>(() => {
    if (compactionStages.length === 0) return null;
    const seenStage = new Set(compactionStages.map((e) => e.stage));
    if (seenStage.has('complete')) return 100;

    const strategyEvent = compactionStages.find((e) => e.stage === 'strategy');
    const useChunked = strategyEvent
      ? Boolean((strategyEvent.data as { use_chunked?: boolean }).use_chunked)
      : false;

    if (useChunked) {
      if (seenStage.has('summarize_done')) return 97;
      if (seenStage.has('merge_done')) return 95;
      if (seenStage.has('merge_started')) return 75;
      let total = 0;
      const seenChunks = new Set<number>();
      for (const entry of compactionStages) {
        if (entry.stage !== 'chunk_done') continue;
        const d = entry.data as { chunk?: number; total?: number };
        if (typeof d.chunk === 'number') seenChunks.add(d.chunk);
        if (typeof d.total === 'number' && d.total > total) total = d.total;
      }
      if (total > 0) {
        return Math.min(70, 10 + Math.round((seenChunks.size / total) * 60));
      }
      if (seenStage.has('strategy')) return 10;
      if (seenStage.has('load')) return 5;
      return 1;
    }

    if (seenStage.has('summarize_done')) return 95;
    if (seenStage.has('strategy')) return 20;
    if (seenStage.has('load')) return 10;
    return 1;
  }, [compactionStages]);
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
  const successfulDocAttachments = useMemo(
    () => attachments.filter((a) => a.status === 'success' && a.workspacePath && !a.isImage),
    [attachments],
  );
  const successfulImageAttachments = useMemo(
    () => attachments.filter((a) => a.status === 'success' && a.isImage && a.dataUrl),
    [attachments],
  );
  // Keep backward-compat alias (used in slash-command guard)
  const successfulAttachments = useMemo(
    () => [...successfulDocAttachments, ...successfulImageAttachments],
    [successfulDocAttachments, successfulImageAttachments],
  );
  const hasUploadingFiles = attachments.some((attachment) => attachment.status === 'uploading');
  const canSend = !sending && !isStreaming && !hasUploadingFiles &&
    (!!input.trim() || successfulDocAttachments.length > 0 || successfulImageAttachments.length > 0);

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
    markMessageStopped,
    truncateAfterMessage,
  } =
    useSessionMessages(sessionId || undefined);

  // Keep a ref to latest messages so handleAbort can read it without stale closure
  const messagesRef = useRef(messages);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const hasUserMessage = useMemo(() => messages.some((m) => m.role === 'user'), [messages]);

  const sseEnabled = Boolean(sessionId) && (live || isStreaming || !hideInput);

  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      const { type, properties } = event;

      // Forward events with payload to parent (e.g. session.updated, workflow.updated).
      // Skip empty events like heartbeats to avoid noisy callbacks.
      if (properties) onSSEEvent?.(event);

      if (!properties || !sessionId) return;

      if (type === 'session.updated' && properties.id === sessionId && properties.status === 'idle') {
        setIsStreaming(false);
        const lastAsstMsg = [...messagesRef.current].reverse().find(
          (message) => message.role === 'assistant' && !message.finish,
        );
        if (lastAsstMsg?.parts?.length) {
          markMessageStopped(lastAsstMsg.id);
        }
      } else if (type === 'message.updated' && properties.info?.sessionID === sessionId) {
        updateMessage(properties.info);
        if (properties.info.finish || properties.info.time?.completed) {
          const shouldRefetch = shouldRefetchFinishedMessage({
            finishedMessageId: properties.info.id,
            abortedMessageId: abortedMessageIdRef.current,
          });
          // Preserve locally streamed partial text when the user aborts. The
          // backend never persists in-flight text chunks, so refetching here
          // would replace the visible partial response with an empty message.
          if (shouldRefetch) {
            refetch();
            setIsStreaming(false);
          }
          abortingRef.current = false;
          abortedMessageIdRef.current = null;
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
          // Reset progress state on each new compaction cycle so a stale
          // run's stages do not leak into a fresh "Compacting..." panel.
          setCompactionStages([]);
        } else {
          const wasCompacting = isCompactingRef.current;
          setIsCompacting(false);
          isCompactingRef.current = false;
          setCompactingMessage('');
          setCompactionStages([]);
          if (wasCompacting) refetch();
        }
      } else if (type === 'session.compaction_progress' && properties.sessionID === sessionId) {
        const stage = properties.stage as CompactionStage | undefined;
        const data = (properties.data ?? {}) as Record<string, unknown>;
        if (!stage) return;
        // Single source of truth: append into ``compactionStages`` and let
        // the progress bar derive ``done/total`` from it via useMemo.
        // ``chunk_done`` arrives in non-deterministic order under
        // ``asyncio.gather``; deduplicate by chunk index here so SSE
        // reconnects / accidental re-deliveries are idempotent.
        setCompactionStages((prev) => {
          if (stage === 'chunk_done') {
            const chunkIdx = typeof data.chunk === 'number' ? data.chunk : undefined;
            if (chunkIdx !== undefined && prev.some(
              (e) => e.stage === 'chunk_done' && (e.data as { chunk?: number }).chunk === chunkIdx,
            )) {
              return prev;
            }
          }
          return [...prev, { stage, data, ts: Date.now() }];
        });
      } else if (type === 'session.error' && properties.sessionID === sessionId) {
        setIsStreaming(false);
        setIsCompacting(false);
        setCompactionStages([]);
        abortingRef.current = false;
        onError?.(properties.error?.message || t('chat.placeholder'));
      }
    },
    [
      sessionId,
      updateMessage,
      updateMessagePart,
      markMessageStopped,
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
    url: `${getApiBase()}/api/event`,
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
    setCompactionStages([]);
    abortingRef.current = false;
    abortedMessageIdRef.current = null;
    statusCheckedRef.current = null;
    isAtBottomRef.current = true;
    clearPendingQuestions();
    // Swap the draft when the session changes — needed for callers that
    // don't force a remount (Session/index.tsx does, but other consumers
    // such as WorkflowDetail/ChatTab may swap sessionId without a remount).
    setInput(readChatDraft(sessionId));
  }, [sessionId, clearPendingQuestions]);

  // Persist the draft on every keystroke. localStorage writes are synchronous
  // and cheap, so debouncing isn't worth the added latency on send (which
  // depends on the draft being flushed). Drafts are removed when ``input``
  // becomes empty (e.g. after a successful send).
  useEffect(() => {
    writeChatDraft(sessionId, input);
  }, [sessionId, input]);

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
      const serverCommands = res.data ?? [];
      // Merge client-side /new command into the autocomplete list
      setCommands([
        {
          name: 'new',
          canonical_name: 'new',
          description: 'Create a new session',
          template: '',
          hidden: false,
          aliases: [],
          visible_surfaces: [],
          execution_kind: 'session_control',
          allow_attachments: false,
          requires_existing_session: false,
          channel_safe: false,
        } satisfies Command,
        ...serverCommands,
      ]);
    } catch {
      commandsLoadedRef.current = false; // Allow retry on failure
    }
  }, []);

  const buildAttachmentBlock = useCallback((items: ComposerAttachment[]) => {
    if (items.length === 0) return '';
    const lines = listUploadedDocumentPaths(items).map((path) => `- ${path}`);
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
      setAttachments((prev) => dedupeUploadedDocumentAttachments(prev.map((attachment) => {
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
          workspacePath: result.abs_path ?? result.path,
          error: undefined,
        };
      })));
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message ?? t('chat.upload.errorGeneric');
      setAttachments((prev) => prev.map((attachment) => (
        entries.some((entry) => entry.id === attachment.id)
          ? { ...attachment, status: 'error', error: detail }
          : attachment
      )));
    }
  }, [t]);

  const queueFilesForUpload = useCallback((files: File[], { imageBlocked = false }: { imageBlocked?: boolean } = {}) => {
    if (files.length === 0) return;
    const validDocEntries: Array<{ id: string; file: File }> = [];
    const validImageFiles: Array<{ id: string; file: File }> = [];
    const invalidAttachments: ComposerAttachment[] = [];
    let imageRejectedToastShown = false;

    files.forEach((file, index) => {
      const id = `attachment-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`;

      if (isImageFile(file)) {
        if (imageBlocked || supportsVision === false) {
          // Show a toast once for the whole batch of rejected images
          if (!imageRejectedToastShown) {
            imageRejectedToastShown = true;
            toast.error(t('chat.upload.imageNotSupported'));
          }
        } else {
          validImageFiles.push({ id, file });
        }
        return;
      }

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
      validDocEntries.push({ id, file });
    });

    if (invalidAttachments.length > 0) {
      setAttachments((prev) => [...prev, ...invalidAttachments]);
    }

    // Handle document uploads (server upload)
    if (validDocEntries.length > 0) {
      setAttachments((prev) => [
        ...prev,
        ...validDocEntries.map(({ id, file }) => ({
          id,
          file,
          name: file.name,
          status: 'uploading' as const,
        })),
      ]);
      void uploadSelectedFiles(validDocEntries);
    }

    // Handle image files (read as base64, no server upload)
    if (validImageFiles.length > 0) {
      setAttachments((prev) => [
        ...prev,
        ...validImageFiles.map(({ id, file }) => ({
          id,
          file,
          name: file.name,
          status: 'uploading' as const,
          isImage: true,
        })),
      ]);
      // Pick compression aggressiveness from how many images are arriving
      // together. A 4-image drop gets a tighter cap than a single image so
      // the combined base64 body still fits inside upstream gateway limits.
      const batchOpts = batchCompressOptions(validImageFiles.length);
      validImageFiles.forEach(({ id, file }) => {
        compressImageFile(file, batchOpts)
          .then((compressed) => readFileAsDataUrl(compressed).then((dataUrl) => ({ compressed, dataUrl })))
          .then(({ compressed, dataUrl }) => {
            setAttachments((prev) => prev.map((a) =>
              a.id === id
                ? { ...a, file: compressed, name: compressed.name, status: 'success' as const, dataUrl, isImage: true }
                : a
            ));
          })
          .catch(() => {
            setAttachments((prev) => prev.map((a) =>
              a.id === id
                ? { ...a, status: 'error' as const, error: t('chat.upload.errorGeneric') }
                : a
            ));
          });
      });
    }
  }, [t, toast, uploadSelectedFiles, supportsVision]);

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
    if (attachment.isImage) {
      compressImageFile(attachment.file)
        .then((compressed) => readFileAsDataUrl(compressed).then((dataUrl) => ({ compressed, dataUrl })))
        .then(({ compressed, dataUrl }) => {
          setAttachments((prev) => prev.map((a) =>
            a.id === attachmentId
              ? { ...a, file: compressed, name: compressed.name, status: 'success' as const, dataUrl, error: undefined }
              : a
          ));
        })
        .catch(() => {
          setAttachments((prev) => prev.map((a) =>
            a.id === attachmentId
              ? { ...a, status: 'error' as const, error: t('chat.upload.errorGeneric') }
              : a
          ));
        });
    } else {
      void uploadSelectedFiles([{ id: attachment.id, file: attachment.file }]);
    }
  }, [attachments, updateAttachment, uploadSelectedFiles, t]);

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
  const sendText = async (text: string, imageParts: ImagePartData[] = []) => {
    if (!sessionId) return;
    // Clear abort state immediately so SSE events for the new stream are not suppressed
    abortingRef.current = false;
    // Force scroll to bottom when user sends a new message
    isAtBottomRef.current = true;
    setSending(true);
    setIsStreaming(true);

    const tempId = `temp-${Date.now()}`;
    const tempParts: MessagePart[] = [];
    if (text) tempParts.push({ id: `${tempId}-text`, type: 'text', text });
    imageParts.forEach((img, i) => {
      tempParts.push({ id: `${tempId}-img-${i}`, type: 'file', url: img.url, mime: img.mime, filename: img.filename });
    });

    addMessage({
      id: tempId,
      sessionID: sessionId,
      role: 'user',
      parts: tempParts.length > 0 ? tempParts : [{ id: `${tempId}-part`, type: 'text', text }],
      timestamp: Date.now(),
    } as Message);

    try {
      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
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
    const docAttachmentsToSend = [...successfulDocAttachments];
    const imageAttachmentsToSend = [...successfulImageAttachments];
    const text = buildMessageText(rawText, docAttachmentsToSend);

    // Need either text content or image attachments
    if (!text && imageAttachmentsToSend.length === 0) return;

    setInput('');
    setShowCommandDropdown(false);

    const imageParts: ImagePartData[] = imageAttachmentsToSend.map((a) => ({
      url: a.dataUrl!,
      mime: a.file.type,
      filename: a.name,
    }));

    // Route slash commands through the command API (requires an active session, no images)
    const parsed = docAttachmentsToSend.length === 0 && imageAttachmentsToSend.length === 0
      ? parseSlashCommand(rawText) : null;
    if (parsed) {
      // Handle /new command locally: create a new session
      if (parsed.command === 'new') {
        if (onCreateNewSession) {
          await onCreateNewSession();
        }
        return;
      }

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
          await onCreateAndSend(text, imageParts);
          setAttachments([]);
        } catch {
          // Restore both the text and the attachment list so the user can
          // retry without re-uploading images. Image data URLs are already
          // in memory, so restoring the array is safe and cheap.
          setInput(rawText);
          setAttachments(imageAttachmentsToSend);
        } finally {
          setSending(false);
        }
      }
      return;
    }

    try {
      await sendText(text, imageParts);
      setAttachments([]);
    } catch {
      setInput(rawText);
      setAttachments(imageAttachmentsToSend);
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
      if (lastAsstMsg?.id) {
        markMessageStopped(lastAsstMsg.id);
      }
      setIsStreaming(false);
      setTimeout(() => { abortingRef.current = false; }, ABORT_SSE_SETTLE_DELAY);
    } catch (err) {
      console.error('[SessionChat] Abort failed:', err);
      abortingRef.current = false;
      abortedMessageIdRef.current = null;
    }
  }, [markMessageStopped, sessionId]);

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
      const truncateTarget = getRegenerateTruncateTarget(messagesRef.current, messageId);
      truncateAfterMessage(
        truncateTarget.messageId,
        truncateTarget.includeTarget ? { includeTarget: true } : undefined,
      );
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

  const msgListClass = compact ? '' : 'space-y-5 w-[min(76%,64rem)] mx-auto pl-4 pr-8';

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      {/* Messages area */}
      <div
        ref={scrollContainerRef}
        className={msgAreaClass}
        onScroll={handleScroll}
        style={{ scrollbarGutter: 'stable' }}
      >
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

            {/* Compacting indicator with live progress stages */}
            {isCompacting && (
              <div className={`flex justify-start ${!compact ? 'group w-full' : ''}`}>
                <div className={`${compact ? 'max-w-[90%] px-4 py-3 rounded-xl' : 'max-w-2xl w-full px-6 py-4 rounded-2xl'} shadow-sm bg-amber-50 border border-amber-200 text-sm`}>
                  <div className="flex items-center gap-2 text-sm text-amber-700">
                    <Loader2 className="w-4 h-4 animate-spin text-amber-500" />
                    <span>{compactingMessage || t('chat.compacting')}</span>
                  </div>
                  {compactionPercent !== null && (
                    <div className="mt-2">
                      <div className="flex items-center justify-between text-[11px] text-amber-700/80 mb-1">
                        <span>{t('chat.compactionStage.overallProgressLabel')}</span>
                        <span>{compactionPercent}%</span>
                      </div>
                      <div className="h-1 w-full rounded-full bg-amber-100 overflow-hidden">
                        <div
                          className="h-full bg-amber-500 transition-all duration-300"
                          style={{ width: `${compactionPercent}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {compactionStages.length > 0 && (
                    <ul className="mt-2 space-y-0.5 text-[11px] text-amber-700/80 max-h-32 overflow-y-auto">
                      {compactionStages
                        .map((entry, idx) => {
                          const text = describeCompactionStage(entry, t);
                          if (!text) return null;
                          return (
                            <li key={`${entry.stage}-${idx}-${entry.ts}`} className="flex gap-1.5">
                              <span className="text-amber-400">·</span>
                              <span>{text}</span>
                            </li>
                          );
                        })
                        .filter(Boolean)}
                    </ul>
                  )}
                </div>
              </div>
            )}

            {/* Standalone thinking indicator when no incomplete message exists */}
            {(isStreaming || sending) && !isCompacting && !(messages.length > 0 && messages[messages.length - 1].role === 'assistant' && !messages[messages.length - 1].finish) && (
              <div className={`group relative ${!compact ? 'w-full' : ''} flex`}>
                <div className={`flex gap-2.5 ${getMessageGroupClassName({ compact, isUser: false, isEditing: false })}`}>
                  <span
                    className={`inline-flex items-center justify-center rounded-full bg-red-500 text-white font-bold shadow-sm ring-2 ring-white flex-shrink-0 ${
                      compact ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm'
                    }`}
                  >
                    R
                  </span>
                  <div className="flex flex-col items-start flex-1 min-w-0">
                    <div className={`flex items-center gap-2 ${compact ? 'h-7' : 'h-8'}`}>
                      <span className="text-xs font-semibold text-zinc-700">Rex</span>
                    </div>
                    <div className="flex flex-col min-w-0 w-full">
                      <div className={getStandaloneThinkingBubbleClassName(compact)}>
                        <div className="flex items-center gap-2 text-sm text-gray-500">
                          <div className="flex gap-0.5">
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                          </div>
                        </div>
                      </div>
                    </div>
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
        <div className={`flex-shrink-0 bg-white ${compact ? 'px-4 py-3' : 'py-4'}`}>
          <div className={`relative min-w-0 ${!compact ? 'w-[min(76%,64rem)] mx-auto pr-8 pl-[58px]' : ''}`}>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept={FILE_INPUT_ACCEPT_ALL}
              multiple
              onChange={(event) => {
                handleFileSelection(event.target.files);
                event.target.value = '';
              }}
            />
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
              className={`rounded-2xl border transition-all ${
                isCompacting
                  ? 'border-amber-200 bg-amber-50/30'
                  : isDragOver
                    ? 'border-sky-300 bg-sky-50/60 ring-4 ring-sky-100'
                    : isStreaming
                      ? 'border-zinc-200 bg-zinc-50'
                      : 'border-zinc-200 bg-zinc-50 hover:border-zinc-300 focus-within:border-zinc-300 focus-within:bg-white focus-within:ring-4 focus-within:ring-zinc-100'
              }`}
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

                      // Image thumbnail display
                      if (attachment.isImage && attachment.dataUrl && !isError) {
                        return (
                          <div
                            key={attachment.id}
                            className={`relative flex-shrink-0 rounded-lg border overflow-hidden ${
                              isUploading ? 'border-sky-200 bg-sky-50' : 'border-gray-200 bg-gray-50'
                            }`}
                          >
                            {isUploading ? (
                              <div className="w-16 h-16 flex items-center justify-center">
                                <Loader2 className="w-5 h-5 animate-spin text-sky-500" />
                              </div>
                            ) : (
                              <img
                                src={attachment.dataUrl}
                                alt={attachment.name}
                                className="w-16 h-16 object-cover cursor-zoom-in"
                                title={attachment.name}
                                onClick={() =>
                                  setComposerPreview({ url: attachment.dataUrl!, alt: attachment.name })
                                }
                              />
                            )}
                            <button
                              type="button"
                              onClick={() => handleRemoveAttachment(attachment.id)}
                              className="absolute top-0.5 right-0.5 rounded-full bg-black/50 p-0.5 text-white hover:bg-black/70 transition-colors"
                              title={t('chat.upload.remove')}
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </div>
                        );
                      }

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
                          ) : attachment.isImage ? (
                            <ImageIcon className="w-3.5 h-3.5 flex-shrink-0" />
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
                          {isError && !attachment.isImage && (
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
                  <div className="px-4 pb-1 text-[11px] text-sky-600">
                    {t('chat.upload.dropHint')}
                  </div>
                )}
                <div className="px-4 pt-3 pb-1">
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
                    className={`w-full resize-none outline-none bg-transparent text-sm placeholder-zinc-400 ${
                      isStreaming ? 'text-zinc-400 cursor-not-allowed' : 'text-zinc-900'
                    }`}
                    style={{ minHeight: '24px', maxHeight: compact ? '120px' : '240px' }}
                    disabled={sending || isStreaming}
                    rows={1}
                  />
                </div>

                {/* Bottom toolbar inside the composer card */}
                <div className="flex items-center gap-1 px-2 pb-2">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={sending || isStreaming}
                    title={t('chat.upload.selectWithImage')}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 hover:text-zinc-800 hover:bg-zinc-200/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    <Paperclip className="w-4 h-4" />
                  </button>

                  {/* divider + injected slot (e.g. agent selector) */}
                  {toolbarSlot && (
                    <>
                      <div className="w-px h-4 bg-zinc-200 mx-1 flex-shrink-0" />
                      {toolbarSlot}
                    </>
                  )}

                  <div className="flex-1" />

                  {isStreaming ? (
                    <button
                      onClick={handleAbort}
                      title={t('chat.stopTitle')}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-zinc-800 text-white hover:bg-zinc-900 shadow-sm transition-all"
                    >
                      <Square className="w-3 h-3 fill-current" />
                    </button>
                  ) : (
                    <button
                      onClick={handleSend}
                      disabled={!canSend}
                      title={hasUploadingFiles ? t('chat.upload.waiting') : undefined}
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-full transition-all ${
                        canSend
                          ? 'bg-sky-500 text-white hover:bg-sky-600 shadow-sm hover:shadow'
                          : 'bg-zinc-200 text-zinc-400 cursor-not-allowed'
                      }`}
                    >
                      {sending || hasUploadingFiles ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUp className="w-4 h-4" strokeWidth={2.5} />}
                    </button>
                  )}
                </div>
            </div>
          </div>
        </div>
      )}
      {composerPreview && (
        <ImageLightbox
          src={composerPreview.url}
          alt={composerPreview.alt}
          onClose={() => setComposerPreview(null)}
        />
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
  // Lightbox state for inline image previews. Browsers block top-level
  // navigation to ``data:`` URLs (the format we send for chat images), so a
  // ``window.open`` would land on a blank page. We open an in-app overlay
  // instead — same UX, no popup blocker / data-URL restriction headaches.
  const [previewImage, setPreviewImage] = useState<{ url: string; alt?: string } | null>(null);
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

  const bubbleClass = getMessageBubbleClassName({ compact, isUser, isEditing });
  const messageGroupClass = getMessageGroupClassName({ compact, isUser, isEditing });
  const actionBarClass = `flex items-center gap-1.5`;
  const editingActionBarClass = getEditingActionBarClassName();
  const iconButtonClass = 'group/action relative inline-flex h-6 w-6 items-center justify-center rounded-full border border-gray-200/80 bg-white/80 text-gray-400 transition-colors duration-150 hover:border-gray-300 hover:text-gray-700 disabled:opacity-40 disabled:cursor-not-allowed';
  const tooltipClass = 'pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity duration-150 group-hover/action:opacity-100';

  const avatarSize = compact ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm';

  const avatar = isUser ? (
    <span className={`inline-flex items-center justify-center rounded-full bg-gradient-to-b from-sky-400 to-blue-500 text-white shadow-sm ring-2 ring-white flex-shrink-0 ${avatarSize}`}>
      <User className={compact ? 'w-3 h-3' : 'w-3.5 h-3.5'} />
    </span>
  ) : (
    <span className={`inline-flex items-center justify-center rounded-full bg-red-500 text-white font-bold shadow-sm ring-2 ring-white flex-shrink-0 ${avatarSize}`}>
      {agentName.charAt(0).toUpperCase()}
    </span>
  );

  const headerHeight = compact ? 'h-7' : 'h-8';
  const bubble = (
    <div className={`${bubbleClass} relative`} style={{ overflowWrap: 'anywhere' }}>

      {/* Empty / loading state */}
      {parts.length === 0 && (
        isUser ? (
          <div className="flex items-center gap-2 opacity-60">
            <div className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
            {t('chat.sending')}
          </div>
        ) : (
          <div className="flex items-center gap-1 py-1" aria-label={t('chat.thinking')}>
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:-0.3s]" />
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:-0.15s]" />
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" />
          </div>
        )
      )}

      {/* Parts */}
      {isEditing ? (
        <div className="space-y-3">
          <textarea
            value={editingText}
            onChange={(event) => onEditChange?.(event.target.value)}
            rows={Math.min(12, Math.max(4, editingText.split('\n').length + 1))}
            className="w-full rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-gray-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </div>
      ) : (
        (() => {
          // Render attachments (file/image parts) first so the bubble shows
          // image previews above the textual prompt — matches typical chat
          // UX for "look at this image and …" style messages.
          const fileParts = parts.filter((p) => p.type === 'file' && p.url);
          const otherParts = parts.filter((p) => !(p.type === 'file' && p.url));
          return (
            <>
              {fileParts.length > 0 && (
                <div className="mb-2 flex flex-row flex-wrap items-center gap-2">
                  {fileParts.map((part, i) => {
                    const isImage = (part.mime || '').startsWith('image/');
                    if (isImage && part.url) {
                      return (
                        <img
                          key={part.id || `file-${i}`}
                          src={part.url}
                          alt={part.filename || ''}
                          className="h-24 w-24 flex-shrink-0 rounded-lg border border-gray-200 object-cover bg-gray-50 cursor-zoom-in transition-transform hover:scale-[1.02]"
                          onClick={() => setPreviewImage({ url: part.url!, alt: part.filename })}
                        />
                      );
                    }
                    return (
                      <div
                        key={part.id || `file-${i}`}
                        className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-xs text-gray-700"
                      >
                        <FileText className="w-3.5 h-3.5 flex-shrink-0" />
                        <span className="truncate max-w-[240px]">{part.filename || 'file'}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {otherParts.map((part: MessagePart, i: number) => (
                // Spacing between consecutive parts is owned by this wrapper,
                // not by individual part components. Each part used to set its
                // own `mt-2 first:mt-0`, but since every part lives in its own
                // wrapper div, `first:` always matched and the gap collapsed
                // to zero between, e.g., a tool card and the next thinking
                // block, making them look glued together.
                <div key={part.id || i} className="mt-2 first:mt-0">
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
                    return (
                      // Vertical spacing is provided by the parent part wrapper
                      // (see `otherParts.map` above); keep this container neutral
                      // so wrapper-level `mt-2 first:mt-0` is the single source of
                      // truth for inter-part gaps.
                      <div>
                        <button
                          onClick={() => togglePart(partKey)}
                          disabled={isThinking}
                          className="group/think w-full text-left"
                        >
                          <div className={`flex items-center gap-2 px-2.5 py-1.5 rounded-md border text-xs transition-colors ${
                            isThinking
                              ? 'bg-sky-50 border-sky-100'
                              : 'bg-zinc-50 border-zinc-200 hover:bg-zinc-100'
                          }`}>
                            {isThinking ? (
                              <>
                                <Brain className="w-3.5 h-3.5 flex-shrink-0 text-violet-500" />
                                <span className="text-violet-600">{t('chat.thinking')}</span>
                              </>
                            ) : (
                              <>
                                <Brain className="w-3.5 h-3.5 flex-shrink-0 text-violet-500" />
                                <span className="text-zinc-500 truncate min-w-0">
                                  {thinkingText.slice(0, 80)}{thinkingText.length > 80 ? '…' : ''}
                                </span>
                                <ChevronDown className={`w-3 h-3 ml-auto text-zinc-400 flex-shrink-0 transition-transform ${isExpanded ? '' : '-rotate-90'}`} />
                              </>
                            )}
                          </div>
                        </button>
                        {isExpanded && (
                          <div className="mt-1 px-2.5 py-2 bg-zinc-50 rounded-md border border-zinc-200 text-[11px] text-zinc-500 whitespace-pre-wrap font-mono leading-relaxed max-h-52 overflow-y-auto">
                            {thinkingText}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              ))}
            </>
          );
        })()
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


      {/* Actions — rendered inside bubble only while editing */}
      {showActions && parts.length > 0 && isEditing && (
        <div className={editingActionBarClass}>
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
        </div>
      )}
    </div>
  );
  const footer = !compact && showActions && parts.length > 0 && !isEditing ? (
    <div className="flex items-center justify-between mt-1.5">
      {showTimestamp && message.timestamp
        ? <span className="text-[11px] text-zinc-400 select-none">{formatSmartTime(message.timestamp)}</span>
        : <span />}
      <div className={actionBarClass}>
        {isUser ? (
          <>
            {targetPartId && editableRawText && (
              <button
                onClick={() => onEditStart?.(targetMessageId, targetPartId, message.role, editableRawText)}
                disabled={actionsDisabled || isActionPending}
                className={iconButtonClass}
                aria-label={t('chat.edit')}
              >
                <Pencil className="w-3 h-3" />
                <span className={tooltipClass}>{t('chat.edit')}</span>
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
          </>
        ) : (
          <>
            <button
              onClick={() => void onRegenerate?.(targetMessageId)}
              disabled={actionsDisabled || isActionPending}
              className={iconButtonClass}
              aria-label={t('chat.regenerate')}
            >
              <RefreshCw className={`w-3 h-3 ${isActionPending ? 'animate-spin' : ''}`} />
              <span className={tooltipClass}>{t('chat.regenerate')}</span>
            </button>
            <button
              onClick={() => onCopy?.(getTextContent())}
              disabled={isActionPending}
              className={iconButtonClass}
              aria-label={t('chat.copy')}
            >
              <Copy className="w-3 h-3" />
              <span className={tooltipClass}>{t('chat.copy')}</span>
            </button>
          </>
        )}
      </div>
    </div>
  ) : null;

  if (isUser) {
    return (
      <div className={`group relative ${!compact ? 'w-full' : ''} flex justify-end`}>
        <div className={`relative flex flex-col items-end gap-2 ${messageGroupClass}`}>
          <div className={getUserAvatarContainerClassName(compact)}>
            {avatar}
          </div>
          <div aria-hidden="true" className={getUserAvatarSpacerClassName(compact)} />
          <div className={`flex flex-col min-w-0 ${isEditing ? 'w-full' : 'w-fit max-w-full'}`}>
            {bubble}
            {footer}
          </div>
        </div>
        {previewImage && (
          <ImageLightbox
            src={previewImage.url}
            alt={previewImage.alt}
            onClose={() => setPreviewImage(null)}
          />
        )}
      </div>
    );
  }

  return (
    <div className={`group relative ${!compact ? 'w-full' : ''} flex`}>
      <div className={`flex gap-2.5 ${messageGroupClass}`}>
        {avatar}
        <div className="flex flex-col items-start flex-1 min-w-0">
          <div className={`flex items-center gap-2 ${headerHeight}`}>
            <span className="text-xs font-semibold text-zinc-700">
              {agentName}
            </span>
          </div>
          <div className="flex flex-col min-w-0 w-full">
            {bubble}
            {footer}
          </div>
        </div>
      </div>
      {previewImage && (
        <ImageLightbox
          src={previewImage.url}
          alt={previewImage.alt}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </div>
  );
}

// ============================================================================
// ChatToolPart — collapsible tool call card
// ============================================================================

const TOOL_DISPLAY_MAX_LEN = 120;
/** Truncate long tool titles / param summaries shown in the card header. */
export function truncateToolDisplayText(text: string, maxLen = TOOL_DISPLAY_MAX_LEN): string {
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

function buildToolInputSummary(input: Record<string, unknown>): string {
  return Object.entries(input)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(', ');
}

type TodoSummaryEntry = {
  id?: string;
  content: string;
  status?: string;
  activeForm?: string;
};

function isTodoSummaryEntry(value: unknown): value is TodoSummaryEntry {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.content === 'string';
}

function readTodoEntries(value: unknown): TodoSummaryEntry[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(isTodoSummaryEntry)
    .map((todo) => ({
      id: typeof todo.id === 'string' ? todo.id : undefined,
      content: todo.content.trim(),
      status: typeof todo.status === 'string' ? todo.status : undefined,
      activeForm: typeof todo.activeForm === 'string' ? todo.activeForm : undefined,
    }))
    .filter((todo) => todo.content.length > 0);
}

function pickTodoEntries(...candidates: unknown[]): TodoSummaryEntry[] {
  for (const candidate of candidates) {
    const todos = readTodoEntries(candidate);
    if (todos.length > 0) return todos;
  }
  return [];
}

export function buildTodoWriteSummary(state: Partial<ToolState>): string {
  const metadata = state.metadata ?? {};
  const currentTodos = pickTodoEntries(metadata.newTodos, metadata.todos, state.input?.todos);
  if (currentTodos.length === 0) return '';
  const totalCount = currentTodos.length;
  const terminalCount = currentTodos.filter(
    (todo) => todo.status === 'completed' || todo.status === 'cancelled',
  ).length;
  const inProgressCount = currentTodos.filter((todo) => todo.status === 'in_progress').length;
  const hasCancelled = currentTodos.some((todo) => todo.status === 'cancelled');

  let summary =
    terminalCount === totalCount
      ? hasCancelled
        ? `Done ${terminalCount}/${totalCount}`
        : `Completed ${terminalCount}/${totalCount}`
      : `Progress ${terminalCount}/${totalCount}`;

  if (inProgressCount > 0 && terminalCount < totalCount) {
    summary += ` · In progress ${inProgressCount}`;
  }

  return summary;
}

export interface ChatToolPartProps {
  part: MessagePart;
  pendingQuestion?: PendingQuestion;
  onAnswer?: (answers: string[][]) => Promise<void>;
  onReject?: () => Promise<void>;
}

export function ChatToolPart({ part, pendingQuestion, onAnswer, onReject }: ChatToolPartProps) {
  const { t } = useTranslation('session');
  const toolName = part.tool || 'unknown';

  // Keep the delegate fallback narrow: many MCP tools also carry a generic
  // `category` field (for example wecom_mcp category="doc").
  if (shouldRenderDelegateTaskCard(part)) {
    return <DelegateTaskCard part={part} />;
  }

  const state: Partial<ToolState> = part.state || {};
  const status = state.status || 'pending';

  // Some tools block on an internal `question` call (for example safety
  // confirmation inside `ssh_host_cmd`), so render the question UI whenever
  // this running tool part has a pending question attached to it.
  const isWaitingForAnswer = status === 'running' && !!pendingQuestion;

  type StatusCfg = {
    icon: React.ReactNode;
    iconColor: string;
    pill: string;      // 状态 pill 样式
    label: string;
  };
  const statusConfig: Record<string, StatusCfg> = {
    pending:   {
      icon: <Clock className="w-3.5 h-3.5 flex-shrink-0" />,
      iconColor: 'text-zinc-400',
      pill: 'bg-zinc-100 text-zinc-500',
      label: t('chat.tool.pending'),
    },
    running:   {
      icon: <Loader2 className="w-3.5 h-3.5 flex-shrink-0 animate-spin" />,
      iconColor: 'text-sky-500',
      pill: 'bg-sky-50 text-sky-600',
      label: t('chat.tool.running'),
    },
    completed: {
      icon: <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />,
      iconColor: 'text-green-500',
      pill: 'bg-green-50 text-green-600',
      label: t('chat.tool.completed'),
    },
    error:     {
      icon: <XCircle className="w-3.5 h-3.5 flex-shrink-0" />,
      iconColor: 'text-red-400',
      pill: 'bg-red-50 text-red-500',
      label: t('chat.tool.error'),
    },
  };
  const config = statusConfig[status] ?? statusConfig.pending;

  const formatOutput = (output: unknown): string => {
    if (typeof output === 'string') {
      try { return JSON.stringify(JSON.parse(output), null, 2); } catch { return output; }
    }
    return JSON.stringify(output, null, 2);
  };

  // Reuse the shared helpers so the truncation rules stay in sync with the
  // delegate-task card and any other places that render tool input previews.
  const inputSummary = state.input
    ? truncateToolDisplayText(
        toolName === 'todowrite'
          ? (buildTodoWriteSummary(state) || buildToolInputSummary(state.input))
          : buildToolInputSummary(state.input),
      )
    : '';
  const displayTitle = state.title ? truncateToolDisplayText(state.title) : '';

  if (isWaitingForAnswer) {
    // Outer spacing is owned by the part wrapper in SessionChat's parts map.
    return (
      <div>
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
    // No top margin here — the part wrapper in SessionChat owns vertical
    // spacing so every adjacent tool / thinking / text part is separated by a
    // single, uniform 8px gap. See the comment on the wrapper in `parts.map`.
    <details className="group/tool rounded-lg bg-zinc-50 overflow-hidden">
      <summary className="px-2.5 py-2 cursor-pointer list-none flex items-center gap-2 min-w-0 select-none hover:bg-zinc-50 transition-colors">
        <span className={`${config.iconColor} flex-shrink-0`}>{config.icon}</span>
        <span className="font-medium text-zinc-700 text-xs whitespace-nowrap flex-shrink-0">{toolName.replace(/_/g, ' ')}</span>
        {inputSummary && (
          <span
            className="text-[11px] text-zinc-400 font-mono truncate min-w-0"
          >
            {inputSummary}
          </span>
        )}
        {displayTitle && !inputSummary && (
          <span
            className="text-[11px] text-zinc-400 truncate min-w-0"
          >
            {displayTitle}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          <span className={`text-[11px] font-medium px-1.5 py-0.5 rounded-md ${config.pill}`}>
            {config.label}
          </span>
          <ChevronDown className="w-3 h-3 text-zinc-400 transition-transform group-open/tool:rotate-180" />
        </div>
      </summary>

      <div className="border-t border-zinc-200/60 px-2.5 py-2 space-y-1.5 text-xs">
        {state.input && (
          <details>
            <summary className="cursor-pointer text-[11px] text-zinc-500 font-medium hover:text-zinc-700 transition-colors mb-1">
              {t('chat.tool.inputParams')}
            </summary>
            <pre className="p-2 bg-zinc-950 text-zinc-300 rounded-md text-[11px] overflow-x-auto font-mono leading-relaxed">
              {JSON.stringify(state.input, null, 2)}
            </pre>
          </details>
        )}

        {status === 'completed' && state.output !== undefined && (
          <details open>
            <summary className="cursor-pointer text-[11px] text-zinc-500 font-medium hover:text-zinc-700 transition-colors mb-1">
              {t('chat.tool.outputResult')}
            </summary>
            <pre className="p-2 bg-zinc-950 text-green-400 rounded-md text-[11px] overflow-x-auto max-h-48 overflow-y-auto font-mono leading-relaxed">
              {formatOutput(state.output)}
            </pre>
          </details>
        )}

        {status === 'error' && state.error && (
          <div className="px-2.5 py-1.5 bg-red-50 border border-red-100 rounded-md text-[11px] text-red-600">
            {state.error}
          </div>
        )}

        {state.time?.start && state.time?.end && (
          <div className="text-zinc-400 text-right text-[10px]">
            {((state.time.end - state.time.start) / 1000).toFixed(2)}s
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
    prevLast?.state?.status === nextLast?.state?.status &&
    JSON.stringify(prevLast?.state?.metadata) ===
      JSON.stringify(nextLast?.state?.metadata)
  );
});
