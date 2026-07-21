/**
 * SessionChat — shared Agent Session conversation component.
 *
 * Use this component anywhere the product needs an AI conversation surface:
 * - Main Session page (compact=false)
 * - Workflow edit chat panel
 * - Task execution detail panel
 * - ChatDialog modal
 * - EntitySheet Rex chat tab
 *
 * Capabilities:
 * - Load and render the complete conversation for a session
 * - Stream live updates over SSE
 * - Render text, reasoning, and tool parts
 * - Provide a follow-up composer that can be hidden with hideInput
 * - Support optional copy actions, timestamps, and related affordances
 */

import { useState, useCallback, useRef, useEffect, useMemo, memo } from 'react';
import { Send, Loader2, ChevronDown, Square, Copy, User, FileText, AlertCircle, X, RefreshCw, Pencil, Save, ImageIcon, Paperclip, ArrowUp, Clock, CheckCircle2, XCircle, Brain, Trash2, Bot, Check, ListTree } from 'lucide-react';
import { StreamingMarkdown, useStreamingContent } from './StreamingMarkdown';
import { useTranslation } from 'react-i18next';
import LoadingSpinner from './LoadingSpinner';
import { QuestionTool, type QuestionItem } from './QuestionTool';
import DelegateTaskCard, { isDelegateTool, shouldRenderDelegateTaskCard } from './DelegateTaskCard';
import CommandDropdown, { isSlashCommandName, parseSlashCommand } from './CommandDropdown';
import ImageLightbox from './ImageLightbox';
import { useSessionMessages } from '@/hooks/useSessions';
import { useSSE, type SSEConnectionStatus } from '@/hooks/useSSE';
import { useReasoningToggle } from '@/hooks/useReasoningToggle';
import { sessionApi, type ContextUsageSnapshot, type QueuedPrompt } from '@/api/session';
import client, { getApiBase } from '@/api/client';
import type { Command } from '@/api/skill';
import type { Agent } from '@/api/agent';
import { useToast } from './Toast';
import { buildRunWorkflowHeaderSummary } from './toolStageSummary';
import { areChatMessagePartsRenderEqual } from './sessionChatRenderEquality';
import { workspaceAPI } from '@/api/workspace';
import { formatSmartTime } from '@/utils/time';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import { copyText } from '@/utils/clipboard';
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
import type { Message, MessagePart, SessionGoalState, ToolState } from '@/types';
import {
  buildInstructionDisplayText,
  fetchSessionChatCommands,
  getQueuedPromptText,
  parseInstructionDisplayText,
  resolveSessionChatSSEAction,
  shouldForwardSSEEventToParent,
  type CompactionStage,
  usePendingQuestions,
  useSessionContextUsage,
  useSessionPromptQueue,
  type PendingQuestion,
  type PromptDisplayOptions,
  type SSEChatEvent,
  type SessionChatDisplay,
} from '@/features/session-chat';

export { formatSmartTime };
export type { SSEConnectionStatus };
export {
  buildInstructionDisplayText,
  parseInstructionDisplayText,
  shouldForwardSSEEventToParent,
  type PromptDisplayOptions,
  type SSEChatEvent,
  type SessionChatDisplay,
} from '@/features/session-chat';

// ============================================================================
// Types
// ============================================================================

export type MergedMessage = Message & { _merged?: boolean };

/** Node reference shown above the chat input as a dismissible chip */
export interface NodeRef {
  id: string;
  type: string;
  description?: string;
}

type GoalBannerStatus = 'active' | 'completed' | 'blocked' | 'paused';

interface GoalBannerState {
  objective: string;
  status: GoalBannerStatus;
  reason?: string;
}

export interface ConversationBottomSlotActions {
  sendPrompt: (text: string, options?: PromptDisplayOptions) => void;
  setInput: (text: string) => void;
  focusInput: () => void;
  sending: boolean;
  streaming: boolean;
  sessionId?: string | null;
  hasMessages: boolean;
}

function getMessagePartDisplayText(part: MessagePart): string {
  const metadataDisplayText = part.metadata?.displayText ?? part.metadata?.display_text;
  return typeof metadataDisplayText === 'string' && metadataDisplayText
    ? metadataDisplayText
    : part.text || '';
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
  /** Optional short display text for the auto-sent initialMessage bubble. */
  initialDisplayText?: string | null;
  /** Called immediately after initialMessage has been consumed (sent) */
  onInitialMessageConsumed?: () => void;
  /** Agent name to include in prompt_async requests */
  agentName?: string;
  /** Model override to include in prompt_async requests */
  model?: { providerID: string; modelID: string } | null;
  /** Agents available for one-turn @mention routing. */
  mentionAgents?: Agent[];
  /** Display configuration (compact, showActions, showTimestamp) */
  display?: SessionChatDisplay;
  /** Custom welcome content when no messages. Can be a render prop receiving setInput. */
  welcomeContent?: React.ReactNode | ((setInput: (text: string) => void) => React.ReactNode);
  /** Extra content rendered below the conversation area and above the composer. */
  conversationBottomSlot?: React.ReactNode | ((actions: ConversationBottomSlotActions) => React.ReactNode);
  /** Called when SSE connection status changes */
  onSseStatusChange?: (status: SSEConnectionStatus) => void;
  /** Forward SSE events with properties to parent (global events like session.updated) */
  onSSEEvent?: (event: SSEChatEvent) => void;
  /** Called on session errors from SSE */
  onError?: (message: string) => void;
  /** Extra content injected into the left side of the composer toolbar */
  toolbarSlot?: React.ReactNode;
  /** Minimum textarea height in px. Defaults to the compact single-line composer height. */
  composerTextareaMinHeight?: number;
  /** Maximum textarea height in px. Defaults to the existing compact/full-page values. */
  composerTextareaMaxHeight?: number;
  /** Extra content injected between left toolbar controls and right actions */
  centerToolbarSlot?: React.ReactNode;
  /** Context window size for the current model; enables composer usage ring. */
  contextWindowTokens?: number | null;
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
  onCreateAndSend?: (
    text: string,
    imageParts?: ImagePartData[],
    agentOverride?: string,
    modelOverride?: { providerID: string; modelID: string } | null,
    options?: PromptDisplayOptions,
  ) => Promise<unknown> | unknown;
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

type UploadedDocumentAttachmentLike = {
  id?: string;
  status?: AttachmentStatus;
  workspacePath?: string;
  isImage?: boolean;
};

const APPROX_CHARS_PER_TOKEN = 4;

function countTokensLikeCompaction(text: string | null | undefined): number {
  if (!text) return 0;
  return Math.floor(text.length / APPROX_CHARS_PER_TOKEN);
}

const INSIGNIFICANT_THINKING_TEXT_RE = /^[\p{P}\p{S}]+$/u;

export function getRenderableThinkingText(part: Pick<MessagePart, 'type' | 'text' | 'thinking'>): string {
  if (part.type !== 'reasoning' && part.type !== 'thinking') return '';
  const text = (part.text || part.thinking || '').trim();
  if (!text || INSIGNIFICANT_THINKING_TEXT_RE.test(text)) return '';
  return text;
}

const StreamingReasoningText = memo(function StreamingReasoningText({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming: boolean;
}) {
  const displayContent = useStreamingContent(content, isStreaming);
  return <>{displayContent}</>;
});

function stringifyToolPayload(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function estimatePartTokens(part: MessagePart): number {
  if (part.type === 'text') {
    return countTokensLikeCompaction(part.text);
  }
  if (part.type === 'reasoning' || part.type === 'thinking') {
    return countTokensLikeCompaction(part.text);
  }
  if (part.type === 'tool' && part.state) {
    const inputTokens = countTokensLikeCompaction(stringifyToolPayload(part.state.input));
    const isCompacted = Boolean((part.state.time as { compacted?: boolean } | undefined)?.compacted);
    const outputTokens = isCompacted
      ? 10
      : countTokensLikeCompaction(stringifyToolPayload(part.state.output));
    return inputTokens + outputTokens;
  }
  return 0;
}

export interface ContextUsageBreakdownSegment {
  key:
    | 'systemPrompt'
    | 'toolDefinitions'
    | 'tools'
    | 'skillLoad'
    | 'agentDelegation'
    | 'conversation'
    | 'reasoning'
    | 'draft'
    | 'compactedHistory';
  tokens: number;
  colorClass: string;
  included: boolean;
}

export interface ContextUsageBreakdown {
  usedTokens: number;
  compactedTokens: number;
  segments: ContextUsageBreakdownSegment[];
  excludedSegments: ContextUsageBreakdownSegment[];
}

const CONTEXT_SEGMENT_COLORS: Record<ContextUsageBreakdownSegment['key'], string> = {
  systemPrompt: 'bg-zinc-400',
  toolDefinitions: 'bg-violet-400',
  tools: 'bg-indigo-400',
  skillLoad: 'bg-amber-400',
  agentDelegation: 'bg-emerald-500',
  conversation: 'bg-slate-500',
  reasoning: 'bg-rose-400',
  draft: 'bg-sky-400',
  compactedHistory: 'bg-zinc-300',
};

const CONTEXT_SEGMENT_KEYS = new Set(Object.keys(CONTEXT_SEGMENT_COLORS));
const CONTEXT_USAGE_FIXED_SEGMENT_KEYS = [
  'systemPrompt',
  'toolDefinitions',
  'conversation',
  'reasoning',
  'tools',
  'skillLoad',
  'agentDelegation',
] as const satisfies readonly ContextUsageBreakdownSegment['key'][];
const CONTEXT_USAGE_FIXED_SEGMENT_KEY_SET = new Set<ContextUsageBreakdownSegment['key']>(
  CONTEXT_USAGE_FIXED_SEGMENT_KEYS,
);

function estimateMessageTokens(message: Message): number {
  return message.parts.reduce((sum, part) => sum + estimatePartTokens(part), 0);
}

function estimateActiveMessageBreakdown(messages: Message[]): Pick<ContextUsageBreakdown, 'segments' | 'usedTokens'> {
  let conversationTokens = 0;
  let reasoningTokens = 0;

  messages.forEach((message) => {
    if (message.compacted) return;
    message.parts.forEach((part) => {
      const tokens = estimatePartTokens(part);
      if (part.type === 'reasoning' || part.type === 'thinking') {
        reasoningTokens += tokens;
      } else {
        conversationTokens += tokens;
      }
    });
  });

  const segments: ContextUsageBreakdownSegment[] = [];
  if (conversationTokens > 0) {
    segments.push({
      key: 'conversation',
      tokens: conversationTokens,
      colorClass: CONTEXT_SEGMENT_COLORS.conversation,
      included: true,
    });
  }
  if (reasoningTokens > 0) {
    segments.push({
      key: 'reasoning',
      tokens: reasoningTokens,
      colorClass: CONTEXT_SEGMENT_COLORS.reasoning,
      included: true,
    });
  }

  return {
    usedTokens: conversationTokens + reasoningTokens,
    segments,
  };
}

function normalizeContextSegment(segment: {
  key: string;
  tokens: number;
  included?: boolean;
}): ContextUsageBreakdownSegment | null {
  const rawKey = segment.key === 'otherContext' ? 'conversation' : segment.key;
  if (!CONTEXT_SEGMENT_KEYS.has(rawKey)) {
    return null;
  }
  const key = rawKey as ContextUsageBreakdownSegment['key'];
  return {
    key,
    tokens: Math.max(0, Math.round(segment.tokens || 0)),
    colorClass: CONTEXT_SEGMENT_COLORS[key],
    included: segment.included !== false,
  };
}

function addContextSegmentTokens(
  segments: ContextUsageBreakdownSegment[],
  key: ContextUsageBreakdownSegment['key'],
  tokens: number,
): void {
  if (tokens <= 0) return;
  const existing = segments.find((segment) => segment.key === key);
  if (existing) {
    existing.tokens += tokens;
    return;
  }
  segments.push({
    key,
    tokens,
    colorClass: CONTEXT_SEGMENT_COLORS[key],
    included: true,
  });
}

function normalizeFixedContextSegments(
  segments: ContextUsageBreakdownSegment[],
): ContextUsageBreakdownSegment[] {
  const byKey = new Map<ContextUsageBreakdownSegment['key'], ContextUsageBreakdownSegment>();
  for (const segment of segments) {
    if (!CONTEXT_USAGE_FIXED_SEGMENT_KEY_SET.has(segment.key)) {
      continue;
    }
    const existing = byKey.get(segment.key);
    if (existing) {
      existing.tokens += segment.tokens;
    } else {
      byKey.set(segment.key, { ...segment, included: true });
    }
  }

  return CONTEXT_USAGE_FIXED_SEGMENT_KEYS.map((key) => {
    const segment = byKey.get(key);
    if (segment) {
      return segment;
    }
    return {
      key,
      tokens: 0,
      colorClass: CONTEXT_SEGMENT_COLORS[key],
      included: true,
    };
  });
}

export function buildContextUsageBreakdown(
  messages: Message[],
  draft: string,
  snapshot?: ContextUsageSnapshot | null,
): ContextUsageBreakdown {
  const compactedTokens = messages.reduce((total, message) => (
    message.compacted ? total + estimateMessageTokens(message) : total
  ), 0);
  const draftTokens = countTokensLikeCompaction(draft);

  if (snapshot) {
    const serverSegments = (snapshot.segments || [])
      .map(normalizeContextSegment)
      .filter((segment): segment is ContextUsageBreakdownSegment => Boolean(segment));
    const segments = [...serverSegments];

    addContextSegmentTokens(segments, 'conversation', draftTokens);

    return {
      usedTokens: Math.max(0, snapshot.usedTokens || 0) + draftTokens,
      compactedTokens: Math.max(0, snapshot.compactedTokens || 0),
      segments: normalizeFixedContextSegments(segments),
      excludedSegments: [],
    };
  }

  const activeBreakdown = estimateActiveMessageBreakdown(messages);
  const segments: ContextUsageBreakdownSegment[] = [...activeBreakdown.segments];

  addContextSegmentTokens(segments, 'conversation', draftTokens);

  return {
    usedTokens: activeBreakdown.usedTokens + draftTokens,
    compactedTokens,
    segments: normalizeFixedContextSegments(segments),
    excludedSegments: [],
  };
}

function formatTokenCount(tokens: number): string {
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(tokens >= 10000000 ? 0 : 1)}M`;
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(tokens >= 10000 ? 0 : 1)}K`;
  return String(tokens);
}

function getContextUsageLabel(
  t: ReturnType<typeof useTranslation>['t'],
  key: ContextUsageBreakdownSegment['key'],
): string {
  const fallback: Record<ContextUsageBreakdownSegment['key'], string> = {
    systemPrompt: 'System prompt',
    toolDefinitions: 'Tool definitions',
    tools: 'Tool calls',
    skillLoad: 'Skill loads',
    agentDelegation: 'Agent delegation',
    conversation: 'Conversation',
    reasoning: 'Reasoning',
    draft: 'Current draft',
    compactedHistory: 'Compacted history',
  };
  const i18nKey = `chat.contextUsage.breakdown.${key}`;
  const label = t(i18nKey);
  return label === i18nKey ? fallback[key] : label;
}

function ContextUsageRing({
  percent,
  title,
  usedTokens,
  totalTokens,
  breakdown,
}: {
  percent: number;
  title: string;
  usedTokens: number;
  totalTokens: number;
  breakdown: ContextUsageBreakdown;
}) {
  const { t } = useTranslation('session');
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const clamped = Math.max(0, Math.min(100, percent));
  const radius = 9;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference * (1 - clamped / 100);
  const strokeClass = clamped >= 90
    ? 'stroke-red-500'
    : clamped >= 75
      ? 'stroke-amber-500'
      : clamped >= 50
        ? 'stroke-sky-500'
        : 'stroke-zinc-400';
  const rows = breakdown.segments;
  const activeSegments = breakdown.segments.filter((segment) => segment.tokens > 0);

  useEffect(() => {
    if (!open) return undefined;

    const handlePointerDown = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  return (
    <div
      ref={wrapperRef}
      className="relative inline-flex h-6 w-6 shrink-0 items-center justify-center"
    >
      <button
        type="button"
        className="relative inline-flex h-6 w-6 items-center justify-center rounded-full transition-colors hover:bg-zinc-200/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-500"
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <svg className="absolute inset-0 h-6 w-6 -rotate-90" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r={radius} fill="none" strokeWidth="2" className="stroke-zinc-200 dark:stroke-zinc-800" />
          <circle
            cx="12"
            cy="12"
            r={radius}
            fill="none"
            strokeWidth="2"
            strokeLinecap="round"
            className={strokeClass}
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
          />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label={t('chat.contextUsage.title')}
          className="absolute bottom-full right-0 z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white text-zinc-800 shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:shadow-xl dark:shadow-black/30"
        >
          <div className="border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('chat.contextUsage.title')}</div>
                <div className="truncate text-[10px] text-zinc-400 dark:text-zinc-500">
                  {t('chat.contextUsage.tokens', {
                    used: formatTokenCount(usedTokens),
                    total: formatTokenCount(totalTokens),
                  })}
                </div>
              </div>
              <span className="shrink-0 rounded bg-zinc-50 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-300">
                {t('chat.contextUsage.full', { percent: clamped })}
              </span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
              <div
                className="flex h-full overflow-hidden rounded-full"
                style={{ width: `${clamped}%` }}
              >
                {activeSegments.map((segment) => (
                  <div
                    key={segment.key}
                    className={segment.colorClass}
                    style={{
                      flex: '0 0 auto',
                      width: `${Math.min(100, (segment.tokens / Math.max(1, usedTokens)) * 100)}%`,
                    }}
                  />
                ))}
              </div>
            </div>
          </div>

          <div className="max-h-[13.5rem] space-y-0.5 overflow-y-auto p-1.5">
            {rows.map((segment) => (
              <div
                key={segment.key}
                role="menuitem"
                className="flex min-w-0 items-center justify-between gap-3 rounded-md px-2 py-1.5 text-xs text-zinc-700 dark:text-zinc-300"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className={`h-3 w-3 shrink-0 rounded-[3px] ${segment.colorClass}`} />
                  <span className="truncate font-medium text-zinc-800 dark:text-zinc-100">
                    {getContextUsageLabel(t, segment.key)}
                  </span>
                </div>
                <span className={segment.included ? 'shrink-0 text-zinc-600 dark:text-zinc-300' : 'shrink-0 text-zinc-400 dark:text-zinc-500'}>
                  {segment.included
                    ? formatTokenCount(segment.tokens)
                    : t('chat.contextUsage.excludedTokens', { tokens: formatTokenCount(segment.tokens) })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function isSuccessfulUploadedDocumentAttachment(
  attachment: UploadedDocumentAttachmentLike,
): attachment is UploadedDocumentAttachmentLike & { status: 'success'; workspacePath: string; isImage?: false } {
  return (
    attachment.status === 'success'
    && !attachment.isImage
    && typeof attachment.workspacePath === 'string'
    && attachment.workspacePath.length > 0
  );
}

export function dedupeUploadedDocumentAttachments<T extends UploadedDocumentAttachmentLike>(items: T[]): T[] {
  const latestIndexByPath = new Map<string, number>();

  items.forEach((attachment, index) => {
    if (isSuccessfulUploadedDocumentAttachment(attachment)) {
      latestIndexByPath.set(attachment.workspacePath, index);
    }
  });

  return items.filter((attachment, index) => {
    if (!isSuccessfulUploadedDocumentAttachment(attachment)) {
      return true;
    }
    return latestIndexByPath.get(attachment.workspacePath) === index;
  });
}

export function listUploadedDocumentPaths(items: UploadedDocumentAttachmentLike[]): string[] {
  const seen = new Set<string>();
  const paths: string[] = [];

  items.forEach((attachment) => {
    if (!isSuccessfulUploadedDocumentAttachment(attachment)) {
      return;
    }
    if (seen.has(attachment.workspacePath)) {
      return;
    }
    seen.add(attachment.workspacePath);
    paths.push(attachment.workspacePath);
  });

  return paths;
}

// Composer drafts are persisted to ``localStorage`` so navigating away from
// the page (e.g. clicking the sidebar to open Agents / Workflows) and coming
// back doesn't lose the half-typed message. Keyed per session so two sessions
// don't share a draft, and namespaced to avoid colliding with other features.
import { readChatDraft, writeChatDraft } from '@/utils/chatDraft';

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
    const widthClass = isUser
      ? (isEditing ? 'w-full max-w-full' : 'max-w-full')
      : 'w-full max-w-full';

    return `${widthClass} min-w-0 px-4 py-3 rounded-[20px] text-sm break-words shadow-sm ${
      isUser
        ? 'bg-sky-50 border border-sky-100 text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-none'
        : 'bg-white border border-zinc-200/90 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:shadow-none'
    }`;
  }

  const widthClass = isUser
    ? (isEditing ? 'w-full' : 'w-auto')
    : 'w-full';

  return `${widthClass} min-w-0 max-w-full px-5 py-4 rounded-[24px] text-sm break-words shadow-sm ${
    isUser
      ? 'bg-sky-50 border border-sky-100 text-zinc-900 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-none'
      : 'bg-white border border-zinc-200/90 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:shadow-none'
  }`;
}

export function getInstructionDisplayBubbleClassName(compact: boolean): string {
  return `${compact ? 'px-2.5 py-1.5' : 'px-3 py-2'} rounded-lg border border-rose-100 bg-rose-50/80 text-sm text-rose-700 shadow-none dark:border-rose-500/30 dark:bg-rose-950/30 dark:text-rose-200`;
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
    return compact ? 'w-full max-w-full' : 'w-full';
  }

  if (compact) {
    return isEditing ? 'w-full max-w-[90%]' : 'w-fit max-w-[90%]';
  }

  return isEditing ? 'w-full max-w-full' : 'w-fit max-w-[88%]';
}

export function getCompactionDividerClassName(compact: boolean): string {
  const messageInset = compact ? 'pl-[38px]' : 'pl-[42px]';
  return `${compact ? 'my-3' : 'my-4'} flex w-full min-w-0 items-center gap-3 ${messageInset} pr-1 text-xs text-zinc-500`;
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

export function isActiveToolPart(part?: Pick<MessagePart, 'type' | 'state'> | null): boolean {
  return (
    (part?.type === 'tool' || part?.type === 'toolCall') &&
    (part.state?.status === 'pending' || part.state?.status === 'running')
  );
}

export function hasActiveToolPart(parts?: Array<Pick<MessagePart, 'type' | 'state'>> | null): boolean {
  return parts?.some(isActiveToolPart) ?? false;
}

export function isActiveSessionStatus(status?: { type?: string } | null): boolean {
  return status?.type === 'busy' || status?.type === 'compacting' || status?.type === 'retry';
}

export function getEditingActionBarClassName(): string {
  return 'mt-3 flex w-full items-center justify-end gap-1.5';
}

export function getStandaloneThinkingBubbleClassName(compact: boolean): string {
  return getMessageBubbleClassName({ compact, isUser: false, isEditing: false });
}

export function getRenderableFileUrl(url: string): string {
  if (!url.startsWith('file://')) {
    return url;
  }

  try {
    const parsed = new URL(url);
    let path = decodeURIComponent(parsed.pathname);
    if (/^\/[A-Za-z]:\//.test(path)) {
      path = path.slice(1);
    } else if (parsed.hostname) {
      path = `//${parsed.hostname}${path}`;
    }
    return `${getApiBase()}/api/file/download?path=${encodeURIComponent(path)}`;
  } catch {
    return url;
  }
}

export function shouldRenderMessage(
  message: Pick<Message, 'role' | 'parts' | 'finish' | 'error'>,
  options?: { isActive?: boolean },
): boolean {
  if (
    message.role === 'assistant' &&
    (message.parts?.length ?? 0) === 0 &&
    message.finish !== 'summary' &&
    !message.error &&
    !options?.isActive
  ) {
    return false;
  }
  if (
    message.role === 'assistant' &&
    (message.parts?.length ?? 0) === 0 &&
    message.finish === 'stop' &&
    !message.error
  ) {
    return false;
  }
  if (
    message.role === 'assistant' &&
    message.finish === 'stop' &&
    !message.error &&
    message.parts?.length &&
    message.parts.every((part) => {
      if (part.type === 'text') return !(part.text || '').trim();
      if (part.type === 'reasoning' || part.type === 'thinking') return !getRenderableThinkingText(part);
      return false;
    })
  ) {
    return false;
  }
  return true;
}

export interface ChatTimelineItem {
  message: MergedMessage;
  isActive: boolean;
}

export function buildChatTimelineItems({
  messages,
  skipIndices,
  isStreaming,
}: {
  messages: MergedMessage[];
  skipIndices: Set<number>;
  isStreaming: boolean;
}): ChatTimelineItem[] {
  const items: ChatTimelineItem[] = [];
  for (let index = 0; index < messages.length; index++) {
    if (skipIndices.has(index)) continue;
    const message = messages[index];
    const isActive =
      isStreaming &&
      index === messages.length - 1 &&
      message.role === 'assistant' &&
      !message.finish;
    if (!shouldRenderMessage(message, { isActive })) continue;
    items.push({ message, isActive });
  }
  return items;
}

export function areChatTimelineItemsRenderEqual(
  prevItems: ChatTimelineItem[],
  nextItems: ChatTimelineItem[],
): boolean {
  if (prevItems.length !== nextItems.length) return false;

  for (let index = 0; index < prevItems.length; index++) {
    const prev = prevItems[index];
    const next = nextItems[index];
    if (prev.isActive !== next.isActive) return false;

    const prevMessage = prev.message;
    const nextMessage = next.message;
    if (prevMessage === nextMessage) continue;
    if (prevMessage.id !== nextMessage.id) return false;
    if (prevMessage.role !== nextMessage.role) return false;
    if (prevMessage.finish !== nextMessage.finish) return false;
    if (prevMessage.error !== nextMessage.error) return false;
    if (prevMessage.agent !== nextMessage.agent) return false;
    if (prevMessage.timestamp !== nextMessage.timestamp) return false;
    if (prevMessage.compacted !== nextMessage.compacted) return false;
    if (!areChatMessagePartsRenderEqual(prevMessage.parts, nextMessage.parts)) return false;
  }

  return true;
}

function useStableChatTimelineSegments(items: ChatTimelineItem[]): {
  historyItems: ChatTimelineItem[];
  tailItems: ChatTimelineItem[];
} {
  const previousRef = useRef<{
    historyItems: ChatTimelineItem[];
    tailItems: ChatTimelineItem[];
  } | null>(null);

  return useMemo(() => {
    const tailStart = items.length > 0 && items[items.length - 1].isActive
      ? items.length - 1
      : items.length;
    const nextHistoryItems = tailStart === items.length ? items : items.slice(0, tailStart);
    const nextTailItems = tailStart === items.length ? [] : items.slice(tailStart);
    const previous = previousRef.current;

    const historyItems = previous && areChatTimelineItemsRenderEqual(previous.historyItems, nextHistoryItems)
      ? previous.historyItems
      : nextHistoryItems;
    const tailItems = previous && areChatTimelineItemsRenderEqual(previous.tailItems, nextTailItems)
      ? previous.tailItems
      : nextTailItems;
    const next = { historyItems, tailItems };
    previousRef.current = next;
    return next;
  }, [items]);
}

export function getMessageErrorText(message: Pick<Message, 'error'>): string {
  const error = message.error as any;
  if (!error) return '';
  if (typeof error === 'string') return error;
  if (typeof error.data?.displayMessage === 'string' && error.data.displayMessage.trim()) {
    return error.data.displayMessage;
  }
  if (typeof error.message === 'string' && error.message.trim()) return error.message;
  if (typeof error.data?.message === 'string' && error.data.message.trim()) {
    return error.data.message;
  }
  if (typeof error.code === 'string' && error.code.trim()) return error.code;
  if (typeof error.name === 'string' && error.name.trim()) return error.name;
  return 'Message failed';
}

export function getUserAvatarContainerClassName(compact: boolean): string {
  return `pointer-events-none flex flex-shrink-0 items-start justify-center pt-1 ${
    compact ? 'h-7' : 'h-8'
  }`;
}

export function getUserAvatarSpacerClassName(_compact: boolean): string {
  return 'h-0';
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

function getGoalBannerKey(goal: GoalBannerState | null): string {
  return goal ? `${goal.status}:${goal.objective}` : '';
}

function getDismissedGoalStorageKey(sessionId?: string | null): string | null {
  return sessionId ? `flocks:session:${sessionId}:dismissedGoal` : null;
}

function readDismissedGoalKey(sessionId?: string | null): string {
  const storageKey = getDismissedGoalStorageKey(sessionId);
  if (!storageKey || typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(storageKey) || '';
  } catch {
    return '';
  }
}

function writeDismissedGoalKey(sessionId: string | null | undefined, goalKey: string): void {
  const storageKey = getDismissedGoalStorageKey(sessionId);
  if (!storageKey || typeof window === 'undefined') return;
  try {
    if (goalKey) {
      window.localStorage.setItem(storageKey, goalKey);
    } else {
      window.localStorage.removeItem(storageKey);
    }
  } catch {
    // Ignore unavailable storage; dismissal still works for the current mount.
  }
}

export type ProcessGroupOpenState = Record<string, boolean>;

function getProcessGroupOpenStorageKey(sessionId?: string | null): string | null {
  return sessionId ? `flocks:session:${sessionId}:processGroupsOpen` : null;
}

function readProcessGroupOpenState(sessionId?: string | null): ProcessGroupOpenState {
  const storageKey = getProcessGroupOpenStorageKey(sessionId);
  if (!storageKey || typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, boolean] => (
        typeof entry[0] === 'string' && typeof entry[1] === 'boolean'
      )),
    );
  } catch {
    return {};
  }
}

function writeProcessGroupOpenState(sessionId: string | null | undefined, state: ProcessGroupOpenState): void {
  const storageKey = getProcessGroupOpenStorageKey(sessionId);
  if (!storageKey || typeof window === 'undefined') return;
  try {
    if (Object.keys(state).length > 0) {
      window.localStorage.setItem(storageKey, JSON.stringify(state));
    } else {
      window.localStorage.removeItem(storageKey);
    }
  } catch {
    // Ignore unavailable storage; process groups still work for this render.
  }
}

function toGoalBannerState(goal: SessionGoalState | null | undefined): GoalBannerState | null {
  const objective = typeof goal?.objective === 'string' ? goal.objective.trim() : '';
  const status = typeof goal?.status === 'string' ? goal.status : '';
  if (!objective || !['active', 'completed', 'blocked', 'paused'].includes(status)) {
    return null;
  }
  return {
    objective,
    status: status as GoalBannerStatus,
    reason: typeof goal?.reason === 'string' ? goal.reason : undefined,
  };
}

function getGoalStatusLabel(t: ReturnType<typeof useTranslation>['t'], status: GoalBannerStatus): string {
  const fallback: Record<GoalBannerStatus, string> = {
    active: 'Goal',
    completed: 'Completed',
    blocked: 'Blocked',
    paused: 'Paused',
  };
  const key = `chat.goal.status.${status}`;
  const label = t(key);
  return label === key ? fallback[status] : label;
}

function getGoalBannerTone(status: GoalBannerStatus): {
  root: string;
  dot: string;
  icon: React.ReactNode;
} {
  if (status === 'completed') {
    return {
      root: 'border-emerald-200 bg-emerald-50 text-emerald-900',
      dot: 'bg-emerald-500',
      icon: <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />,
    };
  }
  if (status === 'blocked') {
    return {
      root: 'border-red-200 bg-red-50 text-red-900',
      dot: 'bg-red-500',
      icon: <AlertCircle className="h-3.5 w-3.5 text-red-600" />,
    };
  }
  if (status === 'paused') {
    return {
      root: 'border-amber-200 bg-amber-50 text-amber-900',
      dot: 'bg-amber-500',
      icon: <Clock className="h-3.5 w-3.5 text-amber-600" />,
    };
  }
  return {
    root: 'border-sky-200 bg-sky-50 text-sky-950',
    dot: 'bg-sky-500',
    icon: <ListTree className="h-3.5 w-3.5 text-sky-600" />,
  };
}

function GoalBanner({
  goal,
  t,
  onDismiss,
}: {
  goal: GoalBannerState;
  t: ReturnType<typeof useTranslation>['t'];
  onDismiss: () => void;
}) {
  const tone = getGoalBannerTone(goal.status);
  const statusLabel = getGoalStatusLabel(t, goal.status);
  return (
    <div className={`mb-2 flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs shadow-sm ${tone.root}`}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
      <span className="shrink-0">{tone.icon}</span>
      <span className="shrink-0 font-semibold">{statusLabel}</span>
      <span className="min-w-0 flex-1 truncate font-medium">{goal.objective}</span>
      {goal.reason && goal.status !== 'active' && (
        <span className="hidden min-w-0 max-w-[35%] truncate text-[11px] opacity-70 sm:inline">
          {goal.reason}
        </span>
      )}
      <button
        type="button"
        onClick={onDismiss}
        className="ml-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-current opacity-60 transition hover:bg-black/5 hover:opacity-100"
        title={t('chat.goal.dismiss')}
        aria-label={t('chat.goal.dismiss')}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

interface QueuedPromptPanelProps {
  items: QueuedPrompt[];
  expanded: boolean;
  editingId: string | null;
  editingText: string;
  actionId: string | null;
  t: ReturnType<typeof useTranslation>['t'];
  onToggle: () => void;
  onEditStart: (item: QueuedPrompt) => void;
  onEditChange: (text: string) => void;
  onEditCancel: () => void;
  onEditSave: (item: QueuedPrompt) => void;
  onRemove: (item: QueuedPrompt) => void;
  onRunNow: (item: QueuedPrompt) => void;
}

function QueuedPromptPanel({
  items,
  expanded,
  editingId,
  editingText,
  actionId,
  t,
  onToggle,
  onEditStart,
  onEditChange,
  onEditCancel,
  onEditSave,
  onRemove,
  onRunNow,
}: QueuedPromptPanelProps) {
  if (items.length === 0) return null;

  return (
    <div className="mb-2 overflow-hidden rounded-xl border border-zinc-200 bg-zinc-950/[0.02] dark:border-zinc-800 dark:bg-zinc-900/60">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100/70 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? '' : '-rotate-90'}`} />
        <span>{t('chat.queue.count', { count: items.length })}</span>
      </button>
      {expanded && (
        <div className="max-h-40 overflow-y-auto border-t border-zinc-200 dark:border-zinc-800">
          {items.map((item) => {
            const isEditing = editingId === item.id;
            const isBusy = actionId === item.id || item.status === 'executing';
            const text = getQueuedPromptText(item);
            const instructionLabel = parseInstructionDisplayText(text);
            return (
              <div key={item.id} className="flex items-start gap-2 border-b border-zinc-100 px-3 py-2 last:border-b-0 dark:border-zinc-800">
                <div className="mt-1 h-2 w-2 flex-shrink-0 rounded-full border border-zinc-400 dark:border-zinc-500" />
                <div className="min-w-0 flex-1">
                  {isEditing ? (
                    <textarea
                      value={editingText}
                      onChange={(event) => onEditChange(event.target.value)}
                      className="w-full resize-none rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-800 outline-none focus:border-zinc-300 focus:ring-2 focus:ring-zinc-100 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-zinc-700 dark:focus:ring-zinc-800/70"
                      rows={2}
                    />
                  ) : (
                    instructionLabel ? (
                      <span className="inline-flex max-w-full items-center truncate rounded-md border border-rose-100 bg-rose-50 px-2 py-1 text-xs font-semibold leading-none text-rose-700 dark:border-rose-500/30 dark:bg-rose-950/30 dark:text-rose-200">
                        {instructionLabel}
                      </span>
                    ) : (
                      <div className="line-clamp-2 text-xs text-zinc-700 dark:text-zinc-300">{text || t('chat.queue.attachmentOnly')}</div>
                    )
                  )}
                </div>
                <div className="flex flex-shrink-0 items-center gap-1">
                  {isEditing ? (
                    <>
                      <button
                        type="button"
                        onClick={() => onEditSave(item)}
                        disabled={isBusy || !editingText.trim()}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                        title={t('chat.save')}
                      >
                        <Save className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={onEditCancel}
                        disabled={isBusy}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                        title={t('chat.cancel')}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => onEditStart(item)}
                        disabled={isBusy}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                        title={t('chat.queue.edit')}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onRunNow(item)}
                        disabled={isBusy}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                        title={t('chat.queue.runNow')}
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove(item)}
                        disabled={isBusy}
                        className="rounded p-1 text-zinc-500 hover:bg-red-50 hover:text-red-600 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-red-950/40 dark:hover:text-red-300"
                        title={t('chat.queue.remove')}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatAgentName(name: string): string {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function findMentionTrigger(text: string, cursor: number): { start: number; end: number; query: string } | null {
  const beforeCursor = text.slice(0, cursor);
  const match = beforeCursor.match(/(^|\s)@([^\s@]*)$/);
  if (!match) return null;
  const query = match[2] ?? '';
  return {
    start: beforeCursor.length - query.length - 1,
    end: cursor,
    query,
  };
}

function resolveMentionAgentName(text: string, agents: Agent[]): string | null {
  const sorted = [...agents].sort((a, b) => b.name.length - a.name.length);
  for (const agent of sorted) {
    const pattern = new RegExp(`(^|\\s)@${escapeRegExp(agent.name)}(?=$|\\s|[,.!?;:，。！？；：])`, 'i');
    if (pattern.test(text)) return agent.name;
  }
  return null;
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
  initialDisplayText,
  agentName,
  model,
  display,
  welcomeContent,
  conversationBottomSlot,
  onSseStatusChange,
  onSSEEvent,
  onError,
  onCreateAndSend,
  onCreateNewSession,
  onInitialMessageConsumed,
  supportsVision,
  toolbarSlot,
  composerTextareaMinHeight,
  composerTextareaMaxHeight,
  centerToolbarSlot,
  contextWindowTokens,
  mentionAgents = [],
}: SessionChatProps) {
  const { t, i18n } = useTranslation('session');
  const toast = useToast();
  const compact = display?.compact ?? true;
  const fullWidth = display?.fullWidth ?? false;
  const showActions = display?.showActions ?? false;
  const showTimestamp = display?.showTimestamp ?? false;
  const collapseIntermediateSteps = display?.collapseIntermediateSteps ?? false;
  const processGroupsDefaultOpen = display?.processGroupsDefaultOpen ?? false;
  const processGroupsOpenWhileActive = display?.processGroupsOpenWhileActive ?? false;
  const effectiveComposerTextareaMinHeight = composerTextareaMinHeight ?? 24;
  const effectiveComposerTextareaMaxHeight = composerTextareaMaxHeight ?? (compact ? 96 : 200);
  const effectivePlaceholder = placeholder ?? t('chat.placeholder');
  const effectiveEmptyText = emptyText ?? t('chat.emptyText');
  // Restore any persisted draft on first mount so navigating away (e.g.
  // sidebar → Agents → back to Sessions) doesn't wipe the user's half-typed
  // message. Subsequent session changes are re-hydrated by the effect below.
  const [input, setInput] = useState<string>(() => readChatDraft(sessionId));
  const [sending, setSending] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const activeToolPartIdsRef = useRef<Set<string>>(new Set());
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  // Lightbox preview for composer thumbnails. Shares the same overlay
  // component used by message bubbles so the click-to-enlarge gesture is
  // consistent across the upload tray and the rendered chat history.
  const [composerPreview, setComposerPreview] = useState<{ url: string; alt?: string } | null>(null);
  const [isCompacting, setIsCompacting] = useState(false);
  const [compactingMessage, setCompactingMessage] = useState('');
  const [goalBanner, setGoalBanner] = useState<GoalBannerState | null>(null);
  const [dismissedGoalKey, setDismissedGoalKey] = useState(() => readDismissedGoalKey(sessionId));
  const {
    items: queuedPrompts,
    expanded: queueExpanded,
    setExpanded: setQueueExpanded,
    editingId: editingQueueId,
    editingText: editingQueueText,
    setEditingText: setEditingQueueText,
    actionId: queueActionId,
    refresh: fetchPromptQueue,
    applyItems: applyPromptQueueItems,
    enqueue: enqueuePrompt,
    startEdit: startQueuedEdit,
    cancelEdit: cancelQueuedEdit,
    saveEdit: saveQueuedEdit,
    remove: removeQueuedPrompt,
    runNow: runQueuedPromptNow,
  } = useSessionPromptQueue(sessionId);
  const [processGroupOpenState, setProcessGroupOpenState] = useState<ProcessGroupOpenState>(() => (
    readProcessGroupOpenState(sessionId)
  ));
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
  const {
    snapshot: contextUsageSnapshot,
    refreshing: contextUsageRefreshing,
    contextWindowTokens: contextUsageWindowTokens,
    refresh: refreshContextUsage,
    applyPushSnapshot: applyContextUsagePushSnapshot,
    stopRefreshing: stopContextUsageRefreshing,
  } = useSessionContextUsage(sessionId);
  const isCompactingRef = useRef(false);
  const prevStreamingRef = useRef(false);
  // Tracks "sessionId::message" key to prevent double-send in React StrictMode
  const initialMessageSentRef = useRef('');
  const abortingRef = useRef(false);
  const sessionBusyRef = useRef(false);
  const goalHydrationVersionRef = useRef(0);
  // ID of the assistant message that was aborted; used to ignore its finish event
  const abortedMessageIdRef = useRef<string | null>(null);
  const suppressStreamingUntilIdleRef = useRef(false);
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
  const messagesContentRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isAtBottomRef = useRef(true);
  const scrollToBottomRafRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);

  // Slash command autocomplete state
  const [commands, setCommands] = useState<Command[]>([]);
  const [showCommandDropdown, setShowCommandDropdown] = useState(false);
  const [commandQuery, setCommandQuery] = useState('');
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const commandsLoadedAtRef = useRef(0);
  const commandsLoadingRef = useRef(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionRange, setMentionRange] = useState<{ start: number; end: number } | null>(null);
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const [pendingAgentName, setPendingAgentName] = useState(agentName || 'rex');
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
  const canSend = !sending && !hasUploadingFiles &&
    (!!input.trim() || successfulDocAttachments.length > 0 || successfulImageAttachments.length > 0);
  const filteredMentionAgents = useMemo(() => {
    const q = mentionQuery.trim().toLowerCase();
    return mentionAgents
      .filter((agent) => !q || agent.name.toLowerCase().startsWith(q))
      .slice(0, 12);
  }, [mentionAgents, mentionQuery]);

  const scrollToBottom = useCallback(() => {
    if (!isAtBottomRef.current) return;
    if (scrollToBottomRafRef.current !== null) return;
    scrollToBottomRafRef.current = requestAnimationFrame(() => {
      scrollToBottomRafRef.current = null;
      messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
    });
  }, []);

  useEffect(() => () => {
    if (scrollToBottomRafRef.current !== null) {
      cancelAnimationFrame(scrollToBottomRafRef.current);
      scrollToBottomRafRef.current = null;
    }
  }, []);

  const loadOlderMessagesRef = useRef<(() => Promise<void>) | null>(null);
  const hasMoreMessagesRef = useRef(false);
  const loadingOlderMessagesRef = useRef(false);
  const rafScheduledRef = useRef(false);
  const handleScroll = useCallback(() => {
    if (rafScheduledRef.current) return;
    rafScheduledRef.current = true;
    requestAnimationFrame(() => {
      const el = scrollContainerRef.current;
      if (el) {
        isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
        if (el.scrollTop <= 80 && hasMoreMessagesRef.current && !loadingOlderMessagesRef.current) {
          const previousHeight = el.scrollHeight;
          const previousTop = el.scrollTop;
          const loadPromise = loadOlderMessagesRef.current?.();
          if (loadPromise) void loadPromise.finally(() => {
            requestAnimationFrame(() => {
              const current = scrollContainerRef.current;
              if (!current) return;
              current.scrollTop = current.scrollHeight - previousHeight + previousTop;
            });
          });
        }
      }
      rafScheduledRef.current = false;
    });
  }, []);

  const {
    messages,
    loading,
    loadingOlder,
    hasMore: hasMoreMessages,
    refetch,
    loadOlder,
    addMessage,
    updateMessage,
    updateMessagePart,
    replaceMessageText,
    markMessageStopped,
    truncateAfterMessage,
  } =
    useSessionMessages(sessionId || undefined);
  useEffect(() => { loadOlderMessagesRef.current = loadOlder; }, [loadOlder]);
  useEffect(() => { hasMoreMessagesRef.current = hasMoreMessages; }, [hasMoreMessages]);
  useEffect(() => { loadingOlderMessagesRef.current = loadingOlder; }, [loadingOlder]);
  const contextUsageMessages = contextUsageRefreshing && !contextUsageSnapshot ? [] : messages;
  const contextUsageBreakdown = useMemo(
    () => buildContextUsageBreakdown(contextUsageMessages, input, contextUsageSnapshot),
    [contextUsageMessages, input, contextUsageSnapshot],
  );
  const estimatedContextTokens = contextUsageBreakdown.usedTokens;
  const resolvedContextWindowTokens = contextUsageSnapshot?.contextWindow && contextUsageSnapshot.contextWindow > 0
    ? contextUsageSnapshot.contextWindow
    : contextUsageWindowTokens > 0
      ? contextUsageWindowTokens
    : (contextWindowTokens || 0);
  const contextUsagePercent = resolvedContextWindowTokens > 0
    ? Math.min(100, Math.round((estimatedContextTokens / resolvedContextWindowTokens) * 100))
    : 0;
  const contextUsageTitle = resolvedContextWindowTokens > 0
    ? t('chat.contextUsageTitle', {
        used: formatTokenCount(estimatedContextTokens),
        total: formatTokenCount(resolvedContextWindowTokens),
        percent: contextUsagePercent,
      })
    : t('chat.contextUsageUnknown');

  // Keep a ref to latest messages so handleAbort can read it without stale closure
  const messagesRef = useRef(messages);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const hasUserMessage = useMemo(() => messages.some((m) => m.role === 'user'), [messages]);

  const sseEnabled = Boolean(sessionId) && (live || isStreaming || !hideInput);

  useEffect(() => {
    goalHydrationVersionRef.current += 1;
    const hydrationVersion = goalHydrationVersionRef.current;

    if (!sessionId) {
      setGoalBanner(null);
      setDismissedGoalKey('');
      return;
    }

    setGoalBanner(null);
    setDismissedGoalKey(readDismissedGoalKey(sessionId));

    sessionApi.get(sessionId).then((session) => {
      if (goalHydrationVersionRef.current !== hydrationVersion) return;
      setGoalBanner(toGoalBannerState(session.goal));
      setDismissedGoalKey(readDismissedGoalKey(sessionId));
    }).catch((err) => {
      if (goalHydrationVersionRef.current !== hydrationVersion) return;
      setGoalBanner(null);
      setDismissedGoalKey(readDismissedGoalKey(sessionId));
      console.warn('[SessionChat] Failed to fetch session goal:', err);
    });
  }, [sessionId]);

  const handleSSEEvent = useCallback(
    (event: SSEChatEvent) => {
      // Forward only global events or events relevant to this chat. The global
      // stream can be very noisy when multiple sessions run in parallel.
      if (shouldForwardSSEEventToParent(event, sessionId)) onSSEEvent?.(event);

      const action = resolveSessionChatSSEAction(event, sessionId);

      switch (action.kind) {
        case 'ignore':
          return;
        case 'session-cleared':
          abortingRef.current = false;
          sessionBusyRef.current = false;
          activeToolPartIdsRef.current.clear();
          abortedMessageIdRef.current = null;
          suppressStreamingUntilIdleRef.current = false;
          setIsStreaming(false);
          setGoalBanner(null);
          setDismissedGoalKey('');
          refetch();
          void refreshContextUsage({ clear: true });
          return;
        case 'session-status':
          if (action.statusType === 'busy') {
            sessionBusyRef.current = true;
            if (
              !abortingRef.current &&
              !suppressStreamingUntilIdleRef.current
            ) setIsStreaming(true);
            setIsCompacting(false);
            isCompactingRef.current = false;
          } else if (action.statusType === 'compacting') {
            sessionBusyRef.current = true;
            if (
              !abortingRef.current &&
              !suppressStreamingUntilIdleRef.current
            ) setIsStreaming(true);
            setIsCompacting(true);
            isCompactingRef.current = true;
            setCompactingMessage(action.message || t('chat.compacting'));
            // Reset progress state on each new compaction cycle so a stale
            // run's stages do not leak into a fresh "Compacting..." panel.
            setCompactionStages([]);
          } else if (action.statusType === 'idle') {
            sessionBusyRef.current = false;
            suppressStreamingUntilIdleRef.current = false;
            activeToolPartIdsRef.current.clear();
            setIsStreaming(false);
            setIsCompacting(false);
            isCompactingRef.current = false;
            setCompactingMessage('');
            setCompactionStages([]);
            refetch();
            void refreshContextUsage({ skipIfFreshMs: 500 });
          }
          return;
        case 'message-updated': {
          const { info } = action;
          updateMessage(info);
          if (
            info.role === 'assistant' &&
            (abortingRef.current || suppressStreamingUntilIdleRef.current)
          ) {
            if (info.id) {
              abortedMessageIdRef.current = info.id;
              markMessageStopped(info.id);
            }
            setIsStreaming(false);
            setSending(false);
            if (info.finish || info.time?.completed) {
              void refreshContextUsage();
            }
          } else if (info.finish || info.time?.completed) {
            const shouldRefetch = shouldRefetchFinishedMessage({
              finishedMessageId: info.id,
              abortedMessageId: abortedMessageIdRef.current,
            });
            // Preserve locally streamed partial text when the user aborts. The
            // backend never persists in-flight text chunks, so refetching here
            // would replace the visible partial response with an empty message.
            if (shouldRefetch) {
              refetch();
              if (!sessionBusyRef.current && activeToolPartIdsRef.current.size === 0) {
                setIsStreaming(false);
              }
            }
            void refreshContextUsage();
            abortingRef.current = false;
            abortedMessageIdRef.current = null;
          } else if (
            info.role === 'assistant' &&
            !info.finish &&
            !abortingRef.current
          ) {
            setIsStreaming(true);
          }
          return;
        }
        case 'message-part-updated': {
          const part = action.part as Pick<MessagePart, 'id' | 'type' | 'state'>;
          if (part.id) {
            if (isActiveToolPart(part)) {
              activeToolPartIdsRef.current.add(part.id);
              if (!abortingRef.current && !suppressStreamingUntilIdleRef.current) setIsStreaming(true);
            } else {
              activeToolPartIdsRef.current.delete(part.id);
            }
          }
          updateMessagePart(action.part, action.delta);
          scrollToBottom();
          return;
        }
        case 'question-asked':
          handleQuestionAsked(action.callID, action.requestId, action.questions as QuestionItem[]);
          scrollToBottom();
          return;
        case 'question-resolved':
          removeByRequestId(action.requestId);
          return;
        case 'compaction-progress':
          if (action.stage === 'complete' && action.data.result === 'continue') {
            void refreshContextUsage({ skipIfFreshMs: 500 });
          }
          // Single source of truth: append into ``compactionStages`` and let
          // the progress bar derive ``done/total`` from it via useMemo.
          // ``chunk_done`` arrives in non-deterministic order under
          // ``asyncio.gather``; deduplicate by chunk index here so SSE
          // reconnects / accidental re-deliveries are idempotent.
          setCompactionStages((prev) => {
            if (action.stage === 'chunk_done') {
              const chunkIdx = typeof action.data.chunk === 'number' ? action.data.chunk : undefined;
              if (chunkIdx !== undefined && prev.some(
                (e) => e.stage === 'chunk_done' && (e.data as { chunk?: number }).chunk === chunkIdx,
              )) {
                return prev;
              }
            }
            return [...prev, { stage: action.stage, data: action.data, ts: Date.now() }];
          });
          return;
        case 'prompt-queue-updated':
          applyPromptQueueItems(action.items);
          return;
        case 'goal-updated': {
          const nextGoal = toGoalBannerState(action.goal);
          if (nextGoal) {
            goalHydrationVersionRef.current += 1;
            setGoalBanner(nextGoal);
            setDismissedGoalKey(readDismissedGoalKey(sessionId));
          }
          return;
        }
        case 'context-compacted':
          void refreshContextUsage({ skipIfFreshMs: 500 });
          return;
        case 'context-usage-updated':
          applyContextUsagePushSnapshot(action.snapshot);
          return;
        case 'session-error':
          setIsStreaming(false);
          setIsCompacting(false);
          setCompactionStages([]);
          stopContextUsageRefreshing();
          void refreshContextUsage({ skipIfFreshMs: 500 });
          abortingRef.current = false;
          sessionBusyRef.current = false;
          activeToolPartIdsRef.current.clear();
          onError?.(action.message || t('chat.placeholder'));
          return;
      }
    },
    [
      sessionId,
      updateMessage,
      updateMessagePart,
      refetch,
      refreshContextUsage,
      applyContextUsagePushSnapshot,
      stopContextUsageRefreshing,
      handleQuestionAsked,
      removeByRequestId,
      applyPromptQueueItems,
      onSSEEvent,
      onError,
      scrollToBottom,
      t,
    ],
  );

  const handleQuestionAnswer = useCallback(
    async (callID: string, requestId: string, answers: string[][]) => {
      try {
        await submitAnswer(callID, requestId, answers);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        if (onError) {
          onError(message);
        } else {
          toast.error(t('chat.questionSubmitFailed', 'Submit failed'), message);
        }
      }
    },
    [onError, submitAnswer, t, toast],
  );

  const handleQuestionReject = useCallback(
    async (callID: string, requestId: string) => {
      try {
        await submitReject(callID, requestId);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        if (onError) {
          onError(message);
        } else {
          toast.error(t('chat.questionCancelFailed', 'Cancel failed'), message);
        }
      }
    },
    [onError, submitReject, t, toast],
  );

  const { status: sseStatus } = useSSE({
    url: `${getApiBase()}/api/event`,
    onEvent: handleSSEEvent,
    onReconnect: () => {
      if (!sessionId) return;
      refetch();
      refreshContextUsage();
      fetchPromptQueue();
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

  useEffect(() => {
    if (!isStreaming && !sending && !isCompacting) return;
    const target = messagesContentRef.current;
    if (!target || typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(() => {
      scrollToBottom();
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [isStreaming, sending, isCompacting, scrollToBottom]);

  // Auto-resize textarea
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const nextHeight = Math.min(
      Math.max(el.scrollHeight, effectiveComposerTextareaMinHeight),
      effectiveComposerTextareaMaxHeight,
    );
    el.style.height = `${nextHeight}px`;
  }, [effectiveComposerTextareaMaxHeight, effectiveComposerTextareaMinHeight]);
  useEffect(() => { autoResize(); }, [input, autoResize]);

  useEffect(() => {
    if (!sending && !isStreaming) {
      setPendingAgentName(agentName || 'rex');
    }
  }, [agentName, sending, isStreaming]);

  // Reset state on session change
  useEffect(() => {
    setIsStreaming(false);
    setAttachments([]);
    setIsDragOver(false);
    setIsCompacting(false);
    setCompactingMessage('');
    setCompactionStages([]);
    setGoalBanner(null);
    setDismissedGoalKey('');
    setMentionRange(null);
    setMentionQuery('');
    setSelectedMentionIndex(0);
    setPendingAgentName(agentName || 'rex');
    abortingRef.current = false;
    abortedMessageIdRef.current = null;
    suppressStreamingUntilIdleRef.current = false;
    sessionBusyRef.current = false;
    statusCheckedRef.current = null;
    isAtBottomRef.current = true;
    clearPendingQuestions();
    // Swap the draft when the session changes — needed for callers that
    // don't force a remount (Session/index.tsx does, but other consumers
    // such as WorkflowDetail/ChatTab may swap sessionId without a remount).
    setInput(readChatDraft(sessionId));
    setProcessGroupOpenState(readProcessGroupOpenState(sessionId));
  }, [sessionId, agentName, clearPendingQuestions]);

  const handleProcessGroupOpenChange = useCallback((key: string, open: boolean) => {
    setProcessGroupOpenState(prev => {
      if (prev[key] === open) return prev;
      const next = { ...prev, [key]: open };
      writeProcessGroupOpenState(sessionId, next);
      return next;
    });
  }, [sessionId]);

  useEffect(() => {
    fetchPromptQueue();
  }, [fetchPromptQueue]);

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
        if (status?.type === 'busy' && !suppressStreamingUntilIdleRef.current) {
          sessionBusyRef.current = true;
          setIsStreaming(true);
        } else if (status?.type === 'compacting' && !suppressStreamingUntilIdleRef.current) {
          sessionBusyRef.current = true;
          setIsStreaming(true);
          setIsCompacting(true);
          isCompactingRef.current = true;
          setCompactingMessage(status.message || t('chat.compacting'));
        } else {
          sessionBusyRef.current = false;
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
      if (document.visibilityState === 'visible') {
        refetch();
        fetchPromptQueue();
      }
    };
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, [sessionId, refetch, fetchPromptQueue]);

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

  /** Lazily load slash commands and periodically revalidate while autocomplete is used. */
  const loadCommandsIfNeeded = useCallback(async (): Promise<void> => {
    if (commandsLoadingRef.current || Date.now() - commandsLoadedAtRef.current < 5_000) return;
    commandsLoadingRef.current = true;
    try {
      setCommands(await fetchSessionChatCommands());
      commandsLoadedAtRef.current = Date.now();
    } catch {
      commandsLoadedAtRef.current = 0;
    } finally {
      commandsLoadingRef.current = false;
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
    abortedMessageIdRef.current = null;
    suppressStreamingUntilIdleRef.current = false;
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
      if (command === 'goal' && args.trim()) {
        goalHydrationVersionRef.current += 1;
        writeDismissedGoalKey(sessionId, '');
        setGoalBanner({ objective: args.trim(), status: 'active' });
        setDismissedGoalKey('');
      }
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
  const sendText = async (
    text: string,
    imageParts: ImagePartData[] = [],
    agentOverride?: string,
    options?: PromptDisplayOptions,
  ) => {
    if (!sessionId) return;
    const effectiveAgent = agentOverride || agentName;
    const visibleText = options?.displayText || text;
    // Clear abort state immediately so SSE events for the new stream are not suppressed
    abortingRef.current = false;
    abortedMessageIdRef.current = null;
    suppressStreamingUntilIdleRef.current = false;
    // Force scroll to bottom when user sends a new message
    isAtBottomRef.current = true;
    setSending(true);
    setIsStreaming(true);
    setPendingAgentName(effectiveAgent || 'rex');

    const tempId = `temp-${Date.now()}`;
    const tempParts: MessagePart[] = [];
    if (visibleText) tempParts.push({ id: `${tempId}-text`, type: 'text', text: visibleText });
    imageParts.forEach((img, i) => {
      tempParts.push({ id: `${tempId}-img-${i}`, type: 'file', url: img.url, mime: img.mime, filename: img.filename });
    });

    addMessage({
      id: tempId,
      sessionID: sessionId,
      role: 'user',
      parts: tempParts.length > 0 ? tempParts : [{ id: `${tempId}-part`, type: 'text', text: visibleText }],
      timestamp: Date.now(),
      agent: effectiveAgent,
    } as Message);

    try {
      const payload: Record<string, unknown> = {
        parts: buildPromptParts(text, imageParts),
      };
      if (effectiveAgent) payload.agent = effectiveAgent;
      if (model) payload.model = model;
      if (options?.displayText) payload.displayText = options.displayText;

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

  const enqueueText = async (
    text: string,
    imageParts: ImagePartData[] = [],
    agentOverride?: string,
    options?: PromptDisplayOptions,
  ) => {
    if (!sessionId) return;
    const effectiveAgent = agentOverride || agentName;
    try {
      await enqueuePrompt({
        parts: buildPromptParts(text, imageParts),
        ...(effectiveAgent ? { agent: effectiveAgent } : {}),
        ...(model ? { model } : {}),
        ...(options?.displayText ? { displayText: options.displayText } : {}),
      });
    } catch (err: any) {
      const statusCode = err?.response?.status;
      const detail = err?.response?.data?.detail;
      const message = statusCode === 409
        ? t('chat.queue.full')
        : detail || err?.message || t('chat.queue.enqueueFailed');
      toast.error(message);
      throw err;
    }
  };

  const handleComposerPrompt = async (text: string, options?: PromptDisplayOptions) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    setInput('');
    setShowCommandDropdown(false);
    setMentionRange(null);
    setAttachments([]);

    if (sessionId && isStreaming) {
      try {
        await enqueueText(trimmed, [], undefined, options);
      } catch {
        setInput(trimmed);
      }
      return;
    }

    if (!sessionId) {
      if (!onCreateAndSend) {
        setInput(trimmed);
        textareaRef.current?.focus();
        return;
      }
      setSending(true);
      try {
        setPendingAgentName(agentName || 'rex');
        await onCreateAndSend(trimmed, [], agentName, model, options);
      } catch {
        setInput(trimmed);
      } finally {
        setSending(false);
      }
      return;
    }

    try {
      await sendText(trimmed, [], agentName, options);
    } catch {
      setInput(trimmed);
    }
  };

  const handleSend = async () => {
    if (!canSend) return;
    const rawText = input.trim();
    const docAttachmentsToSend = [...successfulDocAttachments];
    const imageAttachmentsToSend = [...successfulImageAttachments];
    const text = buildMessageText(rawText, docAttachmentsToSend);
    const mentionedAgent = resolveMentionAgentName(rawText, mentionAgents);

    // Need either text content or image attachments
    if (!text && imageAttachmentsToSend.length === 0) return;

    setInput('');
    setShowCommandDropdown(false);
    setMentionRange(null);

    const imageParts: ImagePartData[] = imageAttachmentsToSend.map((a) => ({
      url: a.dataUrl!,
      mime: a.file.type,
      filename: a.name,
    }));

    // Keep client-side commands local even while Rex is streaming.
    const parsed = docAttachmentsToSend.length === 0 && imageAttachmentsToSend.length === 0
      ? parseSlashCommand(rawText) : null;
    if (parsed?.command === 'new') {
      if (onCreateNewSession) {
        await onCreateNewSession();
      }
      return;
    }

    if (sessionId && isStreaming) {
      try {
        await enqueueText(text, imageParts, mentionedAgent || undefined);
        setAttachments([]);
      } catch {
        setInput(rawText);
        setAttachments([...docAttachmentsToSend, ...imageAttachmentsToSend]);
      }
      return;
    }

    // Route slash commands through the command API (requires an active session, no images)
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
          const effectiveAgent = mentionedAgent || agentName;
          setPendingAgentName(effectiveAgent || 'rex');
          await onCreateAndSend(text, imageParts, effectiveAgent || undefined, model);
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
      await sendText(text, imageParts, mentionedAgent || undefined);
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
    const sentKey = `${sessionId}::${initialMessage}::${initialDisplayText ?? ''}`;
    if (initialMessageSentRef.current === sentKey) return;
    initialMessageSentRef.current = sentKey;
    sendText(
      initialMessage,
      [],
      undefined,
      initialDisplayText ? { displayText: initialDisplayText } : undefined,
    ).catch(() => {});
    onInitialMessageConsumed?.();
  }, [initialDisplayText, initialMessage, sessionId]);

  const insertMention = useCallback((name: string) => {
    const currentValue = textareaRef.current?.value ?? input;
    const cursorPos = textareaRef.current?.selectionStart ?? currentValue.length;
    const currentRange = findMentionTrigger(currentValue, cursorPos) ?? mentionRange;
    if (!currentRange) return;
    const next = `${currentValue.slice(0, currentRange.start)}@${name} ${currentValue.slice(currentRange.end)}`;
    const cursor = currentRange.start + name.length + 2;
    setInput(next);
    setMentionRange(null);
    setMentionQuery('');
    setSelectedMentionIndex(0);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(cursor, cursor);
    });
  }, [input, mentionRange]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    const currentValue = e.currentTarget instanceof HTMLTextAreaElement ? e.currentTarget.value : input;
    const activeMention = mentionRange
      ? findMentionTrigger(currentValue, textareaRef.current?.selectionStart ?? currentValue.length)
      : null;
    if (mentionRange && !activeMention) {
      setMentionRange(null);
    }
    if (activeMention && filteredMentionAgents.length > 0) {
      if (e.key === 'Escape') {
        e.preventDefault();
        setMentionRange(null);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedMentionIndex((i) => (i - 1 + filteredMentionAgents.length) % filteredMentionAgents.length);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedMentionIndex((i) => (i + 1) % filteredMentionAgents.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current)) {
        e.preventDefault();
        const chosen = filteredMentionAgents[selectedMentionIndex] ?? filteredMentionAgents[0];
        if (chosen) {
          insertMention(chosen.name);
        }
        return;
      }
    }

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
    // Record the ID of the message being aborted so we can ignore its finish event later.
    const lastAsstMsg = [...messagesRef.current].reverse().find(
      (m) => m.role === 'assistant' && !m.finish,
    );
    const shouldRestoreActivity = isStreaming || sending || sessionBusyRef.current || Boolean(lastAsstMsg?.id);
    abortedMessageIdRef.current = lastAsstMsg?.id || null;
    abortingRef.current = true;
    suppressStreamingUntilIdleRef.current = true;
    sessionBusyRef.current = false;
    setIsStreaming(false);
    setSending(false);
    try {
      await client.post(`/api/session/${sessionId}/abort`);
      if (lastAsstMsg?.id) {
        markMessageStopped(lastAsstMsg.id);
      }
      setTimeout(() => { abortingRef.current = false; }, ABORT_SSE_SETTLE_DELAY);
    } catch (err) {
      console.error('[SessionChat] Abort failed:', err);
      abortingRef.current = false;
      abortedMessageIdRef.current = null;
      suppressStreamingUntilIdleRef.current = false;
      if (shouldRestoreActivity) {
        sessionBusyRef.current = true;
        setIsStreaming(true);
        if (sending) setSending(true);
      }
    }
  }, [isStreaming, markMessageStopped, sending, sessionId]);

  const handleQueuedEditStart = useCallback((item: QueuedPrompt) => {
    startQueuedEdit(item);
  }, [startQueuedEdit]);

  const handleQueuedEditCancel = useCallback(() => {
    cancelQueuedEdit();
  }, [cancelQueuedEdit]);

  const handleQueuedEditSave = useCallback(async (item: QueuedPrompt) => {
    try {
      await saveQueuedEdit(item);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || t('chat.queue.updateFailed'));
    }
  }, [saveQueuedEdit, t, toast]);

  const handleQueuedRemove = useCallback(async (item: QueuedPrompt) => {
    try {
      await removeQueuedPrompt(item);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || t('chat.queue.removeFailed'));
    }
  }, [removeQueuedPrompt, t, toast]);

  const handleQueuedRunNow = useCallback(async (item: QueuedPrompt) => {
    try {
      const didRun = await runQueuedPromptNow(item);
      if (!didRun) return;
      abortingRef.current = false;
      abortedMessageIdRef.current = null;
      suppressStreamingUntilIdleRef.current = false;
      setIsStreaming(true);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || err?.message || t('chat.queue.runNowFailed'));
    }
  }, [runQueuedPromptNow, t, toast]);

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
        const res = await client.get(`/api/session/${sessionId}/message`, {
          params: { page: true, limit: 50, include_archived: true },
        });
        const msgs: any[] = Array.isArray(res.data) ? res.data : (res.data?.items || []);
        const lastMsg = msgs[msgs.length - 1];
        if (lastMsg?.info?.role === 'assistant' && (lastMsg.info.finish || lastMsg.info.time?.completed)) {
          const hasFetchedActiveTool = msgs.some((msg) => hasActiveToolPart(msg.parts));
          if (hasFetchedActiveTool) {
            return;
          }
          activeToolPartIdsRef.current.clear();
          const statusRes = await client.get('/api/session/status');
          const status = statusRes.data?.[sessionId];
          if (isActiveSessionStatus(status)) {
            return;
          }
          refetch();
          setIsStreaming(false);
        }
      } catch { /* ignore */ }
    }, FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [isStreaming, sessionId, refetch]);

  // Copy text to clipboard
  const handleCopy = useCallback((text: string) => {
    void copyText(text).catch(() => {});
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
    abortedMessageIdRef.current = null;
    suppressStreamingUntilIdleRef.current = false;
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
    abortedMessageIdRef.current = null;
    suppressStreamingUntilIdleRef.current = false;
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

  // ── Merged messages ──
  // Archived-by-compaction messages stay visible in the UI timeline. The
  // summary message itself renders as a divider, so multiple compactions
  // naturally appear as multiple chronological separators.
  const { merged, skipIndices } = useMemo(() => {
    const merged = mergeConsecutiveAssistantMessages(messages);
    const skipIndices = new Set<number>();

    for (let idx = 0; idx < merged.length; idx++) {
      const msg = merged[idx];
      if (msg.parts.length > 0 && msg.parts.every(p => p.synthetic)) {
        skipIndices.add(idx);
        continue;
      }
    }

    return { merged, skipIndices };
  }, [messages]);
  const timelineItems = useMemo(() => (
    buildChatTimelineItems({ messages: merged, skipIndices, isStreaming })
  ), [isStreaming, merged, skipIndices]);
  const { historyItems, tailItems } = useStableChatTimelineSegments(timelineItems);

  // ── Styling based on compact mode ──
  const msgAreaClass = compact
    ? 'relative flex flex-col flex-1 min-h-0 overflow-y-auto bg-gray-50 px-4 py-4 dark:bg-zinc-950'
    : 'relative flex flex-col flex-1 min-h-0 overflow-y-auto bg-gray-50 py-6 dark:bg-zinc-950';

  const msgListClass = compact
    ? fullWidth ? 'space-y-3 w-full px-4' : 'space-y-3'
    : fullWidth ? 'space-y-5 w-full px-5' : 'space-y-5 w-[min(76%,64rem)] mx-auto px-6';
  const visibleGoalBanner = goalBanner && getGoalBannerKey(goalBanner) !== dismissedGoalKey
    ? goalBanner
    : null;
  const handleDismissGoalBanner = useCallback(() => {
    const goalKey = getGoalBannerKey(visibleGoalBanner);
    writeDismissedGoalKey(sessionId, goalKey);
    setDismissedGoalKey(goalKey);
  }, [sessionId, visibleGoalBanner]);

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
          <div ref={messagesContentRef} className={msgListClass}>
            {hasMoreMessages && (
              <div className="flex justify-center pb-2">
                <button
                  type="button"
                  onClick={() => void loadOlder()}
                  disabled={loadingOlder}
                  className="inline-flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-500 transition-colors hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {loadingOlder ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ChevronDown className="h-3.5 w-3.5 rotate-180" />}
                  <span>{loadingOlder ? t('chat.loadingOlder', 'Loading...') : t('chat.loadOlder', 'Load earlier messages')}</span>
                </button>
              </div>
            )}
            <ChatMessageTimeline
              items={historyItems}
              pendingQuestions={pendingQuestions}
              onQuestionAnswer={handleQuestionAnswer}
              onQuestionReject={handleQuestionReject}
              showActions={showActions}
              showTimestamp={showTimestamp}
              collapseIntermediateSteps={collapseIntermediateSteps}
              processGroupsDefaultOpen={processGroupsDefaultOpen}
              processGroupsOpenWhileActive={processGroupsOpenWhileActive}
              processGroupOpenState={processGroupOpenState}
              onProcessGroupOpenChange={handleProcessGroupOpenChange}
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
            />
            <ChatMessageTimeline
              items={tailItems}
              pendingQuestions={pendingQuestions}
              onQuestionAnswer={handleQuestionAnswer}
              onQuestionReject={handleQuestionReject}
              showActions={showActions}
              showTimestamp={showTimestamp}
              collapseIntermediateSteps={collapseIntermediateSteps}
              processGroupsDefaultOpen={processGroupsDefaultOpen}
              processGroupsOpenWhileActive={processGroupsOpenWhileActive}
              processGroupOpenState={processGroupOpenState}
              onProcessGroupOpenChange={handleProcessGroupOpenChange}
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
            />

            {/* Compacting indicator with live progress stages */}
            {isCompacting && (
              <div className={`group relative ${!compact ? 'w-full' : ''} flex`}>
                <div className={compact ? `flex gap-2.5 ${getMessageGroupClassName({ compact, isUser: false, isEditing: false })}` : 'flex w-full min-w-0'}>
                  <span
                    className={`inline-flex items-center justify-center rounded-full bg-red-500 text-white font-bold shadow-sm ring-2 ring-white flex-shrink-0 dark:ring-zinc-950 ${
                      compact ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm'
                    } ${compact ? '' : 'absolute -left-10 top-1'}`}
                  >
                    {formatAgentName(pendingAgentName).charAt(0).toUpperCase()}
                  </span>
                  <div className="flex flex-col items-start flex-1 min-w-0">
                    <div className={`flex items-center gap-2 ${compact ? 'h-7' : 'h-8'}`}>
                      <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">{formatAgentName(pendingAgentName)}</span>
                    </div>
                    <div className="flex flex-col min-w-0 w-full">
                      <div className={`${compact ? 'w-full max-w-full px-4 py-3 rounded-[20px]' : 'w-full px-5 py-4 rounded-[24px]'} text-sm break-words shadow-sm bg-amber-50 border border-amber-200 dark:border-amber-500/35 dark:bg-amber-950/30 dark:shadow-none`}>
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
                  </div>
                </div>
              </div>
            )}

            {/* Standalone thinking indicator when no incomplete message exists */}
            {(isStreaming || sending) && !isCompacting && !(messages.length > 0 && messages[messages.length - 1].role === 'assistant' && !messages[messages.length - 1].finish) && (
              <div className={`group relative ${!compact ? 'w-full' : ''} flex`}>
                <div className={compact ? `flex gap-2.5 ${getMessageGroupClassName({ compact, isUser: false, isEditing: false })}` : 'flex w-full min-w-0'}>
                  <span
                    className={`inline-flex items-center justify-center rounded-full bg-red-500 text-white font-bold shadow-sm ring-2 ring-white flex-shrink-0 dark:ring-zinc-950 ${
                      compact ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm'
                    } ${compact ? '' : 'absolute -left-10 top-1'}`}
                  >
                    {formatAgentName(pendingAgentName).charAt(0).toUpperCase()}
                  </span>
                  <div className="flex flex-col items-start flex-1 min-w-0">
                    <div className={`flex items-center gap-2 ${compact ? 'h-7' : 'h-8'}`}>
                      <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">{formatAgentName(pendingAgentName)}</span>
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
        <div ref={messagesEndRef} className="h-0 flex-shrink-0" />
      </div>

      {/* Suggestions — shown before user sends any message */}
      {suggestions && suggestions.length > 0 && !hasUserMessage && !hideInput && (
        <div className="flex-shrink-0 px-3 pt-2.5 pb-2 border-t border-gray-100 bg-white dark:border-zinc-800 dark:bg-zinc-950">
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
        <div className={`flex-shrink-0 bg-white ${compact ? 'px-4 py-3' : 'py-4'} dark:bg-zinc-950`}>
          <div className={`relative min-w-0 ${!compact ? (fullWidth ? 'w-full px-5' : 'w-[min(76%,64rem)] mx-auto px-6') : ''}`}>
            {conversationBottomSlot && (
              <div className="mb-2 min-w-0">
                {typeof conversationBottomSlot === 'function'
                  ? conversationBottomSlot({
                    sendPrompt: (text, options) => { void handleComposerPrompt(text, options); },
                    setInput: (text) => {
                      setInput(text);
                      requestAnimationFrame(() => textareaRef.current?.focus());
                    },
                    focusInput: () => textareaRef.current?.focus(),
                    sending,
                    streaming: isStreaming,
                    sessionId,
                    hasMessages: messages.length > 0,
                  })
                  : conversationBottomSlot}
              </div>
            )}
            {visibleGoalBanner && (
              <GoalBanner
                goal={visibleGoalBanner}
                t={t}
                onDismiss={handleDismissGoalBanner}
              />
            )}
            <QueuedPromptPanel
              items={queuedPrompts}
              expanded={queueExpanded}
              editingId={editingQueueId}
              editingText={editingQueueText}
              actionId={queueActionId}
              t={t}
              onToggle={() => setQueueExpanded((value) => !value)}
              onEditStart={handleQueuedEditStart}
              onEditChange={setEditingQueueText}
              onEditCancel={handleQueuedEditCancel}
              onEditSave={handleQueuedEditSave}
              onRemove={handleQueuedRemove}
              onRunNow={handleQueuedRunNow}
            />
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
            <AgentMentionDropdown
              visible={Boolean(mentionRange) && filteredMentionAgents.length > 0}
              agents={filteredMentionAgents}
              selectedIndex={selectedMentionIndex}
              displayLang={i18n.language}
              onSelect={(agent) => insertMention(agent.name)}
            />
            <div
              onDragOver={handleComposerDragOver}
              onDragLeave={handleComposerDragLeave}
              onDrop={handleComposerDrop}
              className={`rounded-2xl border transition-all ${
                isCompacting
                  ? 'border-amber-200 bg-amber-50/30 dark:border-amber-500/35 dark:bg-amber-950/25'
                  : isDragOver
                    ? 'border-sky-300 bg-sky-50/60 ring-4 ring-sky-100 dark:border-sky-500/50 dark:bg-sky-950/35 dark:ring-sky-500/10'
                    : isStreaming
                      ? 'border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/70'
                      : 'border-zinc-200 bg-zinc-50 hover:border-zinc-300 focus-within:border-zinc-300 focus-within:bg-white focus-within:ring-4 focus-within:ring-zinc-100 dark:border-zinc-800 dark:bg-zinc-900/70 dark:hover:border-zinc-700 dark:focus-within:border-zinc-700 dark:focus-within:bg-zinc-900 dark:focus-within:ring-zinc-800/60'
              }`}
            >
                {/* Node reference chip */}
                {nodeRef && (
                  <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-slate-400 flex-shrink-0" />
                    <code className="text-[11px] font-mono font-semibold text-slate-700 truncate flex-1 dark:text-slate-200">{nodeRef.id}</code>
                    <span className="text-[10px] text-slate-400 flex-shrink-0 dark:text-slate-500">{nodeRef.type}</span>
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
                              isUploading ? 'border-sky-200 bg-sky-50 dark:border-sky-500/35 dark:bg-sky-950/30' : 'border-gray-200 bg-gray-50 dark:border-zinc-800 dark:bg-zinc-900'
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
                                : 'border-gray-200 bg-gray-50 text-gray-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200'
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
                      const cursor = e.target.selectionStart ?? val.length;
                      const mention = mentionAgents.length > 0 ? findMentionTrigger(val, cursor) : null;
                      const trimmed = val.trimStart();
                      const slashQuery = trimmed.startsWith('/') ? trimmed.slice(1) : '';
                      if (mention && !trimmed.startsWith('/')) {
                        setMentionRange({ start: mention.start, end: mention.end });
                        setMentionQuery(mention.query);
                        setSelectedMentionIndex(0);
                        setShowCommandDropdown(false);
                      } else if (
                        trimmed.startsWith('/') &&
                        !trimmed.includes(' ') &&
                        (slashQuery === '' || isSlashCommandName(slashQuery)) &&
                        successfulAttachments.length === 0
                      ) {
                        void loadCommandsIfNeeded();
                        setCommandQuery(slashQuery);
                        setSelectedCommandIndex(0);
                        setShowCommandDropdown(true);
                        setMentionRange(null);
                      } else {
                        setShowCommandDropdown(false);
                        setMentionRange(null);
                      }
                    }}
                    onBlur={() => { setTimeout(() => { setShowCommandDropdown(false); setMentionRange(null); }, 100); }}
                    onCompositionStart={() => { isComposingRef.current = true; }}
                    onCompositionEnd={() => { isComposingRef.current = false; }}
                    onPaste={handleComposerPaste}
                    onKeyDown={handleKeyDown}
                    placeholder={
                      isCompacting
                        ? t('chat.placeholderCompacting')
                        : isStreaming
                          ? t('chat.queue.placeholderStreaming')
                          : nodeRef
                            ? t('chat.placeholderNodeRef', { nodeId: nodeRef.id })
                            : effectivePlaceholder
                    }
                    className={`w-full resize-none outline-none bg-transparent text-sm placeholder-zinc-400 dark:placeholder-zinc-600 ${
                      sending ? 'text-zinc-400 cursor-not-allowed dark:text-zinc-500' : 'text-zinc-900 dark:text-zinc-100'
                    }`}
                    style={{
                      minHeight: `${effectiveComposerTextareaMinHeight}px`,
                      maxHeight: `${effectiveComposerTextareaMaxHeight}px`,
                    }}
                    disabled={sending}
                    rows={1}
                  />
                </div>

                {/* Bottom toolbar inside the composer card */}
                <div className="flex items-center gap-1 px-2 pb-2">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={sending}
                    title={t('chat.upload.selectWithImage')}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-200/60 hover:text-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>

                  <div className="mx-1 h-4 w-px shrink-0 bg-zinc-200 dark:bg-zinc-800" />

                  {toolbarSlot}

                  {centerToolbarSlot && (
                    <div className="ml-1">
                      {centerToolbarSlot}
                    </div>
                  )}

                  <div className="flex-1" />

                  {resolvedContextWindowTokens > 0 && (
                    <ContextUsageRing
                      percent={contextUsagePercent}
                      title={contextUsageTitle}
                      usedTokens={estimatedContextTokens}
                      totalTokens={resolvedContextWindowTokens}
                      breakdown={contextUsageBreakdown}
                    />
                  )}

                  {isStreaming ? (
                    <>
                      {canSend && (
                        <button
                          onClick={handleSend}
                          title={hasUploadingFiles ? t('chat.upload.waiting') : t('chat.queue.enqueue')}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-sky-500 text-white shadow-sm transition-all hover:bg-sky-600 hover:shadow"
                        >
                          {sending || hasUploadingFiles ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowUp className="w-4 h-4" strokeWidth={2.5} />}
                        </button>
                      )}
                      <button
                        onClick={handleAbort}
                        title={t('chat.stopTitle')}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-zinc-800 text-white hover:bg-zinc-900 shadow-sm transition-all"
                      >
                        <Square className="w-3 h-3 fill-current" />
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={handleSend}
                      disabled={!canSend}
                      title={hasUploadingFiles ? t('chat.upload.waiting') : undefined}
                      className={`inline-flex h-8 w-8 items-center justify-center rounded-full transition-all ${
                        canSend
                          ? 'bg-sky-500 text-white hover:bg-sky-600 shadow-sm hover:shadow'
                          : 'cursor-not-allowed border border-zinc-300 bg-zinc-200 text-zinc-400 dark:border-[#5a6573] dark:bg-[#46515e] dark:text-[#b8c2cc]'
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

function AgentMentionDropdown({
  visible,
  agents,
  selectedIndex,
  displayLang,
  onSelect,
}: {
  visible: boolean;
  agents: Agent[];
  selectedIndex: number;
  displayLang: string;
  onSelect: (agent: Agent) => void;
}) {
  const { t } = useTranslation('session');
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const item = listRef.current?.children[selectedIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  if (!visible) return null;

  return (
    <div
      className="absolute bottom-full left-0 right-0 z-50 mb-1 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30"
      onMouseDown={(e) => e.preventDefault()}
    >
      <div className="border-b border-gray-100 bg-gray-50 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-500">
        {t('chat.mention.title')}
      </div>
      <div ref={listRef} className="max-h-64 overflow-y-auto p-1">
        {agents.map((agent, idx) => {
          const desc = getAgentDisplayDescription(agent, displayLang) || t('smartAssistant');
          return (
            <button
              key={agent.name}
              type="button"
              onClick={() => onSelect(agent)}
              onMouseDown={(e) => e.preventDefault()}
              className={`flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors ${
                idx === selectedIndex ? 'bg-sky-50 text-sky-800 dark:bg-sky-950/45 dark:text-sky-100' : 'text-gray-800 hover:bg-gray-50 dark:text-zinc-200 dark:hover:bg-zinc-800'
              }`}
            >
              <Bot className="h-3.5 w-3.5 shrink-0 text-gray-400 dark:text-zinc-500" />
              <span className="shrink-0 font-mono text-sm font-semibold">@{agent.name}</span>
              <span className="min-w-0 truncate text-xs text-gray-500 dark:text-zinc-400">{desc}</span>
            </button>
          );
        })}
      </div>
      <div className="flex gap-3 border-t border-gray-100 bg-gray-50 px-3 py-1 text-[10px] text-gray-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-500">
        <span><kbd className="font-mono">↑↓</kbd> {t('chat.mention.navigate')}</span>
        <span><kbd className="font-mono">Enter</kbd>/<kbd className="font-mono">Tab</kbd> {t('chat.mention.select')}</span>
      </div>
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
  collapseIntermediateSteps?: boolean;
  processGroupsDefaultOpen?: boolean;
  processGroupsOpenWhileActive?: boolean;
  processGroupOpenState?: ProcessGroupOpenState;
  onProcessGroupOpenChange?: (key: string, open: boolean) => void;
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
}

interface ChatMessageTimelineProps extends Omit<ChatMessageBubbleProps, 'message' | 'isActive'> {
  items: ChatTimelineItem[];
}

function ChatMessageTimelineInner({
  items,
  pendingQuestions,
  onQuestionAnswer,
  onQuestionReject,
  showActions,
  showTimestamp,
  collapseIntermediateSteps,
  processGroupsDefaultOpen,
  processGroupsOpenWhileActive,
  processGroupOpenState,
  onProcessGroupOpenChange,
  compact,
  onCopy,
  editingMessageId,
  editingText,
  actionsDisabled,
  actionMessageId,
  onEditStart,
  onEditChange,
  onEditCancel,
  onEditSave,
  onEditSend,
  onRegenerate,
}: ChatMessageTimelineProps) {
  return (
    <>
      {items.map(({ message, isActive }) => (
        <ChatMessageBubble
          key={message.id}
          message={message}
          isActive={isActive}
          pendingQuestions={pendingQuestions}
          onQuestionAnswer={onQuestionAnswer}
          onQuestionReject={onQuestionReject}
          showActions={showActions}
          showTimestamp={showTimestamp}
          collapseIntermediateSteps={collapseIntermediateSteps}
          processGroupsDefaultOpen={processGroupsDefaultOpen}
          processGroupsOpenWhileActive={processGroupsOpenWhileActive}
          processGroupOpenState={processGroupOpenState}
          onProcessGroupOpenChange={onProcessGroupOpenChange}
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
    </>
  );
}

export const ChatMessageTimeline = memo(ChatMessageTimelineInner);

function ProcessGroupDetails({
  defaultOpen,
  open,
  onOpenChange,
  summary,
  children,
}: {
  defaultOpen: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  summary: React.ReactNode;
  children: React.ReactNode;
}) {
  const isControlled = typeof open === 'boolean';
  const [internalOpen, setInternalOpen] = useState(defaultOpen);

  useEffect(() => {
    if (!isControlled) {
      setInternalOpen(defaultOpen);
    }
  }, [defaultOpen, isControlled]);

  const effectiveOpen = isControlled ? open : internalOpen;
  const handleSummaryClick = useCallback((event: React.MouseEvent<HTMLElement>) => {
    event.preventDefault();
    const nextOpen = !effectiveOpen;
    if (!isControlled) {
      setInternalOpen(nextOpen);
    }
    onOpenChange?.(nextOpen);
  }, [effectiveOpen, isControlled, onOpenChange]);

  return (
    <details
      open={effectiveOpen}
      data-testid="chat-process-group"
      className="group/process mt-2 first:mt-0 overflow-hidden rounded-lg border border-zinc-200/90 bg-white/80 shadow-none"
    >
      <summary
        onClick={handleSummaryClick}
        className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-50"
      >
        {summary}
        <ChevronDown className="ml-auto h-3 w-3 flex-shrink-0 text-zinc-400 transition-transform group-open/process:rotate-180" />
      </summary>
      {children}
    </details>
  );
}

function ChatMessageBubbleInner({
  message,
  isActive = false,
  pendingQuestions,
  onQuestionAnswer,
  onQuestionReject,
  showActions = false,
  showTimestamp = false,
  collapseIntermediateSteps = false,
  processGroupsDefaultOpen = false,
  processGroupsOpenWhileActive = false,
  processGroupOpenState,
  onProcessGroupOpenChange,
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
}: ChatMessageBubbleProps) {
  const { t } = useTranslation('session');
  const isUser = message.role === 'user';
  const parts: MessagePart[] = Array.isArray(message.parts) ? message.parts : [];
  const { getPartExpanded, togglePart } = useReasoningToggle(parts, message.finish);
  // Lightbox state for inline image previews. Browsers block top-level
  // navigation to ``data:`` URLs (the format we send for chat images), so a
  // ``window.open`` would land on a blank page. We open an in-app overlay
  // instead — same UX, no popup blocker / data-URL restriction headaches.
  const [previewImage, setPreviewImage] = useState<{ url: string; alt?: string } | null>(null);
  if (message.finish === 'summary') {
    return (
      <div className={getCompactionDividerClassName(compact)}>
        <span className="h-px flex-1 bg-zinc-200" />
        <span className="shrink-0 px-1.5 font-medium text-zinc-500">
          {t('chat.contextCompressed')}
        </span>
        <span className="h-px flex-1 bg-zinc-200" />
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
  const instructionDisplayLabel = isUser && !isEditing && editableTextParts.length === 1
    ? parseInstructionDisplayText(getMessagePartDisplayText(editableTextParts[0]))
    : null;

  const bubbleClass = instructionDisplayLabel
    ? getInstructionDisplayBubbleClassName(compact)
    : getMessageBubbleClassName({ compact, isUser, isEditing });
  const messageGroupClass = getMessageGroupClassName({ compact, isUser, isEditing });
  const actionBarClass = `flex items-center gap-1.5`;
  const editingActionBarClass = getEditingActionBarClassName();
  const iconButtonClass = 'group/action relative inline-flex h-6 w-6 items-center justify-center rounded-full border border-gray-200/80 bg-white/80 text-gray-400 transition-colors duration-150 hover:border-gray-300 hover:text-gray-700 disabled:opacity-40 disabled:cursor-not-allowed dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-500 dark:hover:border-zinc-700 dark:hover:text-zinc-200';
  const tooltipClass = 'pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity duration-150 group-hover/action:opacity-100';
  const messageErrorText = isUser ? '' : getMessageErrorText(message);

  const avatarSize = compact ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm';
  const avatar = isUser ? (
    <span className={`inline-flex items-center justify-center rounded-full bg-gradient-to-b from-sky-400 to-blue-500 text-white shadow-sm ring-2 ring-white flex-shrink-0 dark:ring-zinc-950 ${avatarSize}`}>
      <User className={compact ? 'w-3 h-3' : 'w-3.5 h-3.5'} />
    </span>
  ) : (
    <span className={`inline-flex items-center justify-center rounded-full bg-red-500 text-white font-bold shadow-sm ring-2 ring-white flex-shrink-0 dark:ring-zinc-950 ${avatarSize}`}>
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
          messageErrorText ? (
            <div className="flex items-start gap-2 py-1 text-sm text-red-700" role="alert">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />
              <span className="whitespace-pre-wrap break-words">{messageErrorText}</span>
            </div>
          ) : (
            <div className="flex items-center gap-1 py-1" aria-label={t('chat.thinking')}>
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:-0.3s]" />
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:-0.15s]" />
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce" />
            </div>
          )
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
          const fileParts = parts.filter((p) => p.type === 'file');
          const displayParts = parts.filter((p) => p.type !== 'file');
          const isQuestionToolPart = (part: MessagePart): boolean => {
            if (part.type !== 'tool') return false;
            return isQuestionToolName(part.tool || '');
          };
          const isPendingQuestionToolPart = (part: MessagePart): boolean => {
            if (!isQuestionToolPart(part)) return false;
            return !!(part.callID && pendingQuestions?.[part.callID]);
          };
          const isIntermediateProcessPart = (part: MessagePart): boolean => {
            if (part.type === 'reasoning' || part.type === 'thinking') {
              return !!getRenderableThinkingText(part);
            }
            if (part.type !== 'tool') return false;
            if (isPendingQuestionToolPart(part)) return false;
            return true;
          };
          const isRenderableTextPart = (part: MessagePart): boolean => (
            part.type === 'text' && !!getMessagePartDisplayText(part).trim()
          );
          const isRenderableDisplayPart = (part: MessagePart): boolean => {
            if (isIntermediateProcessPart(part)) return true;
            if (part.type === 'text') return isRenderableTextPart(part);
            if (part.type === 'tool') return true;
            if (part.type === 'file') return !!part.url;
            return false;
          };
          const activeTailPart = isActive
            ? [...displayParts].reverse().find(isRenderableDisplayPart)
            : undefined;
          const renderPart = (part: MessagePart, i: number, isVisible = true) => (
            // Spacing between consecutive parts is owned by this wrapper,
            // not by individual part components. Each part used to set its
            // own `mt-2 first:mt-0`, but since every part lives in its own
            // wrapper div, `first:` always matched and the gap collapsed
            // to zero between, e.g., a tool card and the next thinking
            // block, making them look glued together.
            <div key={part.id || i} className="mt-2 first:mt-0">
              {/* Text */}
              {part.type === 'text' && (() => {
                const rawText = part.text || '';
                const nodeRefMatch = isUser
                  ? rawText.match(/^@@node:([^|\n]+)\|([^\n]+)\n([\s\S]*)$/)
                  : null;
                const partDisplayText = getMessagePartDisplayText(part);
                if (!partDisplayText.trim()) return null;
                const displayText = nodeRefMatch && partDisplayText === rawText ? nodeRefMatch[3] : partDisplayText;
                const instructionLabel = isUser ? parseInstructionDisplayText(displayText) : null;
                if (instructionLabel) {
                  return (
                    <span className="inline-flex max-w-full items-center truncate text-sm font-semibold leading-none text-rose-700">
                      {instructionLabel}
                    </span>
                  );
                }
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
              {(part.type === 'reasoning' || part.type === 'thinking') && (() => {
                const thinkingText = getRenderableThinkingText(part);
                if (!thinkingText) return null;
                const partKey = part.id || `reasoning-${i}`;
                const isThinking = part === activeTailPart;
                const isExpanded = isThinking || getPartExpanded(partKey);
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
                    {isExpanded && isVisible && (
                      <div className="mt-1 px-2.5 py-2 bg-zinc-50 rounded-md border border-zinc-200 text-[11px] text-zinc-500 whitespace-pre-wrap font-mono leading-relaxed max-h-52 overflow-y-auto">
                        <StreamingReasoningText
                          content={thinkingText}
                          isStreaming={isThinking}
                        />
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          );
          const renderProcessGroup = (group: Array<{ part: MessagePart; index: number }>, groupIndex: number) => {
            const reasoningCount = group.filter(({ part }) => part.type === 'reasoning' || part.type === 'thinking').length;
            const toolCount = group.filter(({ part }) => part.type === 'tool').length;
            const textCount = group.filter(({ part }) => part.type === 'text').length;
            const summary = [
              reasoningCount > 0 ? t('chat.process.reasoningCount', { count: reasoningCount }) : '',
              toolCount > 0 ? t('chat.process.toolCount', { count: toolCount }) : '',
              textCount > 0 ? t('chat.process.textCount', { count: textCount }) : '',
            ].filter(Boolean).join(' · ');
            const processGroupOpen = processGroupsDefaultOpen || (processGroupsOpenWhileActive && isActive);
            const processGroupKey = `${message.id}:process:${groupIndex}`;
            const hasStoredOpenState = !!processGroupOpenState
              && Object.prototype.hasOwnProperty.call(processGroupOpenState, processGroupKey);
            const effectiveProcessGroupOpen = hasStoredOpenState
              ? processGroupOpenState[processGroupKey]
              : processGroupOpen;
            return (
              <ProcessGroupDetails
                key={`process-${groupIndex}`}
                defaultOpen={processGroupOpen}
                open={effectiveProcessGroupOpen}
                onOpenChange={(open) => onProcessGroupOpenChange?.(processGroupKey, open)}
                summary={(
                  <>
                    <ListTree className="h-3.5 w-3.5 flex-shrink-0 text-zinc-400" />
                    <span className="flex-shrink-0 font-semibold text-zinc-700">
                      {t('chat.process.title', { count: group.length })}
                    </span>
                    {summary && (
                      <span className="min-w-0 truncate text-zinc-500">
                        {summary}
                      </span>
                    )}
                  </>
                )}
              >
                <div className="border-t border-zinc-200/70 px-2.5 py-2">
                  {group.map(({ part, index }) => renderPart(part, index, effectiveProcessGroupOpen))}
                </div>
              </ProcessGroupDetails>
            );
          };
          const renderDisplayParts = () => {
            if (!collapseIntermediateSteps || isUser) {
              return displayParts.map((part, index) => renderPart(part, index));
            }
            const nodes: React.ReactNode[] = [];
            let processGroup: Array<{ part: MessagePart; index: number }> = [];
            let processGroupIndex = 0;
            const lastIntermediateProcessIndex = displayParts.reduce((lastIndex, part, index) => (
              isIntermediateProcessPart(part) ? index : lastIndex
            ), -1);
            const flushProcessGroup = () => {
              if (processGroup.length === 0) return;
              nodes.push(renderProcessGroup(processGroup, processGroupIndex));
              processGroup = [];
              processGroupIndex += 1;
            };
            displayParts.forEach((part, index) => {
              if (isIntermediateProcessPart(part) || (isRenderableTextPart(part) && index <= lastIntermediateProcessIndex)) {
                processGroup.push({ part, index });
                return;
              }
              if (!isRenderableDisplayPart(part)) return;
              if (isPendingQuestionToolPart(part) || isRenderableTextPart(part)) {
                flushProcessGroup();
              }
              nodes.push(renderPart(part, index));
            });
            flushProcessGroup();
            return nodes;
          };
          return (
            <>
              {fileParts.length > 0 && (
                <div className="mb-2 flex flex-row flex-wrap items-center gap-2">
                  {fileParts.map((part, i) => {
                    const isImage = (part.mime || '').startsWith('image/');
                    if (isImage && part.url) {
                      const imageUrl = getRenderableFileUrl(part.url);
                      return (
                        <img
                          key={part.id || `file-${i}`}
                          src={imageUrl}
                          alt={part.filename || ''}
                          className="h-24 w-24 flex-shrink-0 rounded-lg border border-gray-200 object-cover bg-gray-50 cursor-zoom-in transition-transform hover:scale-[1.02]"
                          onClick={() => setPreviewImage({ url: imageUrl, alt: part.filename })}
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
                {renderDisplayParts()}
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
    if (!compact) {
      return (
        <div className="group relative flex w-full min-w-0 justify-end">
          <div className={`flex min-w-0 flex-col items-end ${isEditing ? 'w-full' : 'max-w-[88%]'}`}>
            {bubble}
            {footer}
          </div>
          <div className="absolute -right-10 top-1">
            {avatar}
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
      <div className={`group relative ${!compact ? 'w-full' : ''} flex min-w-0 justify-end`}>
        <div className={`flex min-w-0 items-start justify-end gap-2 ${messageGroupClass}`}>
          <div className={`flex min-w-0 flex-col items-end ${isEditing ? 'w-full' : 'max-w-full'}`}>
            {bubble}
            {footer}
          </div>
          <div className={getUserAvatarContainerClassName(compact)}>
            {avatar}
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

  if (!compact) {
    return (
      <div className="group relative flex w-full min-w-0">
        <div className="absolute -left-10 top-1">
          {avatar}
        </div>
        <div className="flex w-full min-w-0 flex-col items-start">
          <div className={`flex items-center gap-2 ${headerHeight}`}>
            <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              {agentName}
            </span>
          </div>
          <div className="flex w-full min-w-0 flex-col">
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
    <div className="group relative flex">
      <div className={`flex gap-2.5 ${messageGroupClass}`}>
        {avatar}
        <div className="flex flex-col items-start flex-1 min-w-0">
          <div className={`flex items-center gap-2 ${headerHeight}`}>
            <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
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
    .map(([k, v]) => {
      if (Array.isArray(v)) return `${k}=[${v.length} items]`;
      if (v && typeof v === 'object') return `${k}=${JSON.stringify(v)}`;
      return `${k}=${String(v)}`;
    })
    .join(', ');
}

type TodoSummaryEntry = {
  id?: string;
  content: string;
  status?: string;
  activeForm?: string;
};
type TodoTranslator = (key: string) => string;

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

function getTodoActionLabel(action: unknown): string {
  if (action === 'read') return 'Read todos';
  if (action === 'write') return 'Update todos';
  return 'Todos';
}

export function buildTodoSummary(state: Partial<ToolState>, t?: TodoTranslator): string {
  const metadata = state.metadata ?? {};
  const currentTodos = pickTodoEntries(metadata.newTodos, metadata.todos, state.input?.todos);
  if (currentTodos.length === 0) return getTodoActionLabel(state.input?.action);
  const totalCount = currentTodos.length;
  const terminalCount = currentTodos.filter(
    (todo) => todo.status === 'completed' || todo.status === 'cancelled',
  ).length;
  const inProgressCount = currentTodos.filter((todo) => todo.status === 'in_progress').length;
  const hasCancelled = currentTodos.some((todo) => todo.status === 'cancelled');

  let summary =
    terminalCount === totalCount
      ? hasCancelled
        ? `${t?.('chat.tool.todoSummary.done') ?? 'Done'} ${terminalCount}/${totalCount}`
        : `${t?.('chat.tool.todoSummary.completed') ?? 'Completed'} ${terminalCount}/${totalCount}`
      : `${t?.('chat.tool.todoSummary.progress') ?? 'Progress'} ${terminalCount}/${totalCount}`;

  if (inProgressCount > 0 && terminalCount < totalCount) {
    summary += ` · ${t?.('chat.tool.todoSummary.inProgress') ?? 'In progress'} ${inProgressCount}`;
  }

  return summary;
}

function todoStatusLabel(status: string | undefined, t: TodoTranslator): string {
  switch (status) {
    case 'completed':
      return t('chat.tool.todoStatus.completed');
    case 'in_progress':
      return t('chat.tool.todoStatus.inProgress');
    case 'cancelled':
      return t('chat.tool.todoStatus.cancelled');
    case 'pending':
      return t('chat.tool.todoStatus.pending');
    default:
      return status || 'pending';
  }
}

function todoStatusIcon(status: string | undefined): React.ReactNode {
  switch (status) {
    case 'completed':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
          <Check className="h-3 w-3" strokeWidth={3} />
        </span>
      );
    case 'in_progress':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full border border-sky-400 bg-white">
          <span className="h-1.5 w-1.5 rounded-full bg-sky-500" />
        </span>
      );
    case 'cancelled':
      return (
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-zinc-200 text-zinc-500">
          <X className="h-2.5 w-2.5" strokeWidth={2.5} />
        </span>
      );
    default:
      return <span className="h-4 w-4 rounded-full border border-zinc-300 bg-white" />;
  }
}

function todoTextClass(status: string | undefined): string {
  switch (status) {
    case 'completed':
      return 'text-zinc-500';
    case 'in_progress':
      return 'font-medium text-zinc-800';
    case 'cancelled':
      return 'text-zinc-400 line-through decoration-zinc-300';
    default:
      return 'text-zinc-600';
  }
}

function todoStatusLabelClass(status: string | undefined): string {
  switch (status) {
    case 'completed':
      return 'text-emerald-600';
    case 'in_progress':
      return 'text-sky-600';
    case 'cancelled':
      return 'text-zinc-400';
    default:
      return 'text-zinc-400';
  }
}

function isQuestionToolName(toolName: string): boolean {
  const normalized = toolName.toLowerCase();
  return normalized === 'question' || normalized === 'request_user_input' || normalized.includes('question');
}

function isBashToolName(toolName: string): boolean {
  return toolName.toLowerCase() === 'bash';
}

function formatToolPayload(output: unknown): string {
  if (typeof output === 'string') {
    try { return JSON.stringify(JSON.parse(output), null, 2); } catch { return output; }
  }
  return JSON.stringify(output, null, 2);
}

function readQuestionItems(value: unknown): QuestionItem[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    .map((item) => ({
      question: typeof item.question === 'string' ? item.question : '',
      header: typeof item.header === 'string' ? item.header : undefined,
      type: typeof item.type === 'string' ? item.type as QuestionItem['type'] : undefined,
      options: Array.isArray(item.options) ? item.options as QuestionItem['options'] : undefined,
      multiple: typeof item.multiple === 'boolean' ? item.multiple : undefined,
      custom: typeof item.custom === 'boolean' ? item.custom : undefined,
    }))
    .filter((item) => item.question.trim().length > 0);
}

function normalizeQuestionAnswer(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item ?? '').trim())
      .filter(Boolean);
  }
  if (value == null) return [];
  const text = String(value).trim();
  return text ? [text] : [];
}

function formatQuestionAnswerValue(question: QuestionItem, value: string, t: TodoTranslator): string {
  const normalized = value.trim().toLowerCase();
  if (question.type === 'confirm') {
    if (normalized === 'yes' || normalized === 'true') return t('chat.questionResult.yes');
    if (normalized === 'no' || normalized === 'false') return t('chat.questionResult.no');
  }
  return value;
}

function ChatQuestionResult({
  state,
  statusLabel,
  statusIcon,
  statusIconColor,
  t,
}: {
  state: Partial<ToolState>;
  statusLabel: string;
  statusIcon: React.ReactNode;
  statusIconColor: string;
  t: TodoTranslator;
}) {
  const questions = readQuestionItems(state.input?.questions);
  if (questions.length === 0) return null;
  const rawAnswers = Array.isArray(state.metadata?.answers) ? state.metadata.answers : [];
  const status = state.status || 'pending';
  const displayStatus = statusLabel;
  const isCompleted = status === 'completed';
  const isError = status === 'error';
  const answerLabelClass = isCompleted
    ? 'text-emerald-600'
    : isError
    ? 'text-red-500'
    : 'text-zinc-500';
  const answerChipClass = isCompleted
    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
    : isError
    ? 'border-red-200 bg-red-50 text-red-600'
    : 'border-zinc-200 bg-zinc-50 text-zinc-600';
  const statusPillClass = isCompleted
    ? 'bg-emerald-50 text-emerald-600'
    : isError
    ? 'bg-red-50 text-red-500'
    : 'bg-zinc-100 text-zinc-500';
  const firstQuestion = questions[0]?.header || questions[0]?.question || '';

  return (
    <details className="group/tool rounded-lg bg-zinc-50 overflow-hidden">
      <summary className="px-2.5 py-2 cursor-pointer list-none flex items-start gap-2 min-w-0 select-none hover:bg-zinc-50 transition-colors">
        <span className={`${statusIconColor} flex-shrink-0 mt-0.5`}>{statusIcon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium text-zinc-700 text-xs whitespace-nowrap flex-shrink-0">question</span>
            {firstQuestion && (
              <span className="text-[11px] text-zinc-400 truncate min-w-0">
                {firstQuestion}
              </span>
            )}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0 self-center">
          <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${statusPillClass}`}>
            {displayStatus}
          </span>
          <ChevronDown className="w-3 h-3 text-zinc-400 transition-transform group-open/tool:rotate-180" />
        </div>
      </summary>
      <div className="border-t border-zinc-200/60 px-2.5 py-2 space-y-1.5 text-xs">
        {questions.map((question, index) => {
          const answers = normalizeQuestionAnswer(rawAnswers[index]);
          const displayAnswers = answers.length > 0
            ? answers.map((answer) => formatQuestionAnswerValue(question, answer, t))
            : [t('chat.questionResult.unanswered')];
          return (
            <div
              key={`${question.question}-${index}`}
              className="space-y-2 py-2 first:pt-2 last:pb-0"
            >
              <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-2">
                <span className="pt-0.5 text-[11px] font-medium text-zinc-400">
                  {t('chat.questionResult.questionLabel')}
                </span>
                <div className="min-w-0">
                  {question.header && (
                    <div className="mb-0.5 text-[11px] font-medium text-zinc-500">{question.header}</div>
                  )}
                  <div className="text-xs leading-5 text-zinc-700">{question.question}</div>
                </div>
              </div>
              <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-2">
                <span className={`pt-0.5 text-[11px] font-medium ${answerLabelClass}`}>
                  {t('chat.questionResult.answerLabel')}
                </span>
                <div className="flex min-w-0 flex-wrap gap-1.5">
                  {displayAnswers.map((answer, answerIndex) => (
                    <span
                      key={`${answer}-${answerIndex}`}
                      className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${answerChipClass}`}
                    >
                      {answer}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  return value as Record<string, unknown>;
}

function parseJsonRecord(value: string): Record<string, unknown> | undefined {
  try {
    return asRecord(JSON.parse(value));
  } catch {
    return undefined;
  }
}

function pickStringValue(data: Record<string, unknown> | undefined, ...keys: string[]): string | undefined {
  if (!data) return undefined;
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'string' && value.trim().length > 0) return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  }
  return undefined;
}

function pickNumberValue(data: Record<string, unknown> | undefined, ...keys: string[]): number | undefined {
  if (!data) return undefined;
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return undefined;
}

function pickBooleanValue(data: Record<string, unknown> | undefined, ...keys: string[]): boolean {
  if (!data) return false;
  for (const key of keys) {
    const value = data[key];
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (normalized === 'true') return true;
      if (normalized === 'false') return false;
    }
  }
  return false;
}

function extractBashMetadata(text: string): { output: string; metadataLines: string[] } {
  const metadataMatch = text.match(/\n*<bash_metadata>\n?([\s\S]*?)\n?<\/bash_metadata>\s*$/);
  if (!metadataMatch) return { output: text, metadataLines: [] };
  return {
    output: text.slice(0, metadataMatch.index).trimEnd(),
    metadataLines: metadataMatch[1]
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean),
  };
}

function buildBashCommandSummary(state: Partial<ToolState>): string {
  const command = pickStringValue(state.input, 'command', 'cmd', 'shell');
  return truncateToolDisplayText(command || state.title || '');
}

function getBashDescription(state: Partial<ToolState>): string {
  const command = pickStringValue(state.input, 'command', 'cmd', 'shell') || '';
  const metadata = asRecord(state.metadata);
  const description = pickStringValue(state.input, 'description')
    || pickStringValue(metadata, 'description')
    || state.title
    || '';
  return description && description !== command ? description : '';
}

function buildBashHeaderSummary(state: Partial<ToolState>): string {
  return truncateToolDisplayText(getBashDescription(state) || buildBashCommandSummary(state));
}

function formatBashDuration(state: Partial<ToolState>): string | undefined {
  const start = state.time?.start;
  const end = state.time?.end;
  if (typeof start === 'number' && typeof end === 'number' && end >= start) {
    return `${((end - start) / 1000).toFixed(2)}s`;
  }
  const metadata = asRecord(state.metadata);
  const durationMs = pickNumberValue(metadata, 'duration_ms', 'durationMs', 'elapsed_ms', 'elapsedMs');
  if (durationMs !== undefined) return `${(durationMs / 1000).toFixed(2)}s`;
  return undefined;
}

function normalizeBashOutput(state: Partial<ToolState>) {
  const metadata = asRecord(state.metadata);
  const outputRecord = asRecord(state.output)
    || (typeof state.output === 'string' ? parseJsonRecord(state.output) : undefined);
  const stdout = pickStringValue(outputRecord, 'stdout');
  const stderr = pickStringValue(outputRecord, 'stderr');
  const genericOutput = pickStringValue(outputRecord, 'output', 'text', 'result')
    ?? (typeof state.output === 'string' ? state.output : undefined)
    ?? pickStringValue(metadata, 'output');
  const errorFromOutput = pickStringValue(outputRecord, 'error');
  const rawError = errorFromOutput || state.error || '';
  const errorSummary = rawError.split('\n\n')[0]?.trim() || '';
  const extracted = extractBashMetadata(stdout || genericOutput || '');
  const fallbackRawOutput = !stdout && !genericOutput && state.output !== undefined && !outputRecord
    ? formatToolPayload(state.output)
    : '';

  return {
    stdout: stdout ? extracted.output : '',
    stderr: stderr || '',
    output: stdout ? '' : (extracted.output || fallbackRawOutput),
    metadataLines: extracted.metadataLines,
    errorSummary,
    timedOut: pickBooleanValue(metadata, 'timed_out', 'timedOut'),
    aborted: pickBooleanValue(metadata, 'aborted'),
  };
}

function ChatBashPayload({
  state,
  t,
}: {
  state: Partial<ToolState>;
  t: TodoTranslator;
}) {
  const command = pickStringValue(state.input, 'command', 'cmd', 'shell') || state.title || '';
  const workdir = pickStringValue(state.input, 'workdir', 'cwd', 'directory');
  const timeout = pickNumberValue(state.input, 'timeout', 'timeout_ms', 'timeoutMs');
  const duration = formatBashDuration(state);
  const output = normalizeBashOutput(state);
  const hasOutput = !!(output.stdout || output.output || output.stderr || output.errorSummary);

  return (
    <div className="space-y-2">
      {command && (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-zinc-500">{t('chat.bash.command')}</div>
          <pre className="rounded-md bg-zinc-950 p-2 text-[11px] leading-relaxed text-zinc-100 overflow-x-auto max-h-64 overflow-y-auto font-mono">
            <span className="text-zinc-500">$ </span>{command}
          </pre>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {workdir && (
          <span className="rounded-md border border-zinc-200 bg-white px-1.5 py-0.5 text-zinc-500">
            {t('chat.bash.workdir')} <span className="font-mono text-zinc-600">{workdir}</span>
          </span>
        )}
        {duration && (
          <span className="rounded-md border border-zinc-200 bg-white px-1.5 py-0.5 text-zinc-500">
            {t('chat.bash.duration')} {duration}
          </span>
        )}
        {timeout !== undefined && (
          <span className="rounded-md border border-zinc-200 bg-white px-1.5 py-0.5 text-zinc-500">
            {t('chat.bash.timeout')} {timeout}ms
          </span>
        )}
        {output.timedOut && (
          <span className="rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 font-medium text-amber-700">
            {t('chat.bash.timedOut')}
          </span>
        )}
        {output.aborted && (
          <span className="rounded-md border border-zinc-200 bg-zinc-100 px-1.5 py-0.5 font-medium text-zinc-600">
            {t('chat.bash.aborted')}
          </span>
        )}
      </div>

      {output.metadataLines.length > 0 && (
        <div className="rounded-md bg-amber-50 px-2 py-1.5 text-[11px] leading-5 text-amber-700">
          {output.metadataLines.join(' · ')}
        </div>
      )}

      {output.errorSummary && !output.stderr && (
        <div className="rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-5 text-red-600">
          {output.errorSummary}
        </div>
      )}

      {output.stdout && (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-zinc-500">{t('chat.bash.stdout')}</div>
          <pre className="rounded-md bg-zinc-950 p-2 text-[11px] leading-relaxed text-emerald-300 overflow-x-auto max-h-64 overflow-y-auto font-mono">
            {output.stdout}
          </pre>
        </div>
      )}

      {output.output && (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-zinc-500">{t('chat.bash.output')}</div>
          <pre className="rounded-md bg-zinc-950 p-2 text-[11px] leading-relaxed text-emerald-300 overflow-x-auto max-h-64 overflow-y-auto font-mono">
            {output.output}
          </pre>
        </div>
      )}

      {output.stderr && (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-red-500">{t('chat.bash.stderr')}</div>
          <pre className="rounded-md bg-red-950 p-2 text-[11px] leading-relaxed text-red-100 overflow-x-auto max-h-64 overflow-y-auto font-mono">
            {output.stderr}
          </pre>
        </div>
      )}

      {!hasOutput && (
        <div className="rounded-md bg-white px-2 py-1.5 text-[11px] text-zinc-400 border border-zinc-200/70">
          {t('chat.bash.noOutput')}
        </div>
      )}
    </div>
  );
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

  // Pending question state is the source of truth. Tool status can briefly
  // arrive as completed after reconnects or transport races, but the user
  // still needs the answer UI while the question request exists.
  const isWaitingForAnswer = !!pendingQuestion;

  type StatusCfg = {
    icon: React.ReactNode;
    iconColor: string;
    pill: string;      // Status pill classes.
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
  const isBashTool = isBashToolName(toolName);
  const todoEntries = toolName === 'todo'
    ? pickTodoEntries(state.metadata?.newTodos, state.metadata?.todos, state.input?.todos)
    : [];
  const showGenericToolPayload = toolName !== 'todo' && !isBashTool;
  const isTodoTool = toolName === 'todo';

  // Reuse the shared helpers so the truncation rules stay in sync with the
  // delegate-task card and any other places that render tool input previews.
  const inputSummary = state.input
    ? truncateToolDisplayText(
        toolName === 'todo'
          ? buildTodoSummary(state, t)
          : isBashTool
          ? buildBashHeaderSummary(state)
          : buildToolInputSummary(state.input),
      )
    : '';
  const displayTitle = state.title ? truncateToolDisplayText(state.title) : '';
  const workflowHeaderSummary = truncateToolDisplayText(buildRunWorkflowHeaderSummary(toolName, state, t));
  const statusBadgeClass = isTodoTool
    ? 'text-[11px] font-medium text-zinc-500'
    : `text-[11px] font-medium px-1.5 py-0.5 rounded-md ${config.pill}`;

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

  if (isQuestionToolName(toolName) && readQuestionItems(state.input?.questions).length > 0) {
    return (
      <ChatQuestionResult
        state={state}
        statusLabel={config.label}
        statusIcon={config.icon}
        statusIconColor={config.iconColor}
        t={t}
      />
    );
  }

  return (
    // No top margin here — the part wrapper in SessionChat owns vertical
    // spacing so every adjacent tool / thinking / text part is separated by a
    // single, uniform 8px gap. See the comment on the wrapper in `parts.map`.
    <details className="group/tool rounded-lg bg-zinc-50 overflow-hidden">
      <summary className="px-2.5 py-2 cursor-pointer list-none flex items-start gap-2 min-w-0 select-none hover:bg-zinc-50 transition-colors">
        <span className={`${config.iconColor} flex-shrink-0 mt-0.5`}>{config.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium text-zinc-700 text-xs whitespace-nowrap flex-shrink-0">{toolName.replace(/_/g, ' ')}</span>
            {workflowHeaderSummary ? (
              <span className="text-[11px] text-emerald-700 truncate min-w-0">
                {workflowHeaderSummary}
              </span>
            ) : (
              <>
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
              </>
            )}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5 flex-shrink-0 self-center">
          <span className={statusBadgeClass}>
            {config.label}
          </span>
          <ChevronDown className="w-3 h-3 text-zinc-400 transition-transform group-open/tool:rotate-180" />
        </div>
      </summary>

      <div className="border-t border-zinc-200/60 px-2.5 py-2 space-y-1.5 text-xs">
        {isTodoTool && todoEntries.length > 0 && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 text-[11px] font-medium text-zinc-500">
              <span>{t('chat.tool.todoStages')}</span>
              <span className="font-normal text-zinc-400">{todoEntries.length}</span>
            </div>
            <div className="divide-y divide-zinc-100">
              {todoEntries.map((todo, index) => (
                <div
                  key={todo.id || index}
                  className="grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 py-1.5 text-[11px] first:pt-0 last:pb-0"
                >
                  <span className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center">
                    {todoStatusIcon(todo.status)}
                  </span>
                  <span className={`min-w-0 leading-5 ${todoTextClass(todo.status)}`}>
                    {todo.activeForm && todo.status === 'in_progress' ? todo.activeForm : todo.content}
                  </span>
                  <span
                    className={`flex-shrink-0 whitespace-nowrap leading-5 ${todoStatusLabelClass(todo.status)}`}
                  >
                    {todoStatusLabel(todo.status, t)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {isBashTool && (
          <ChatBashPayload state={state} t={t} />
        )}

        {showGenericToolPayload && state.input && (
          <details>
            <summary className="cursor-pointer text-[11px] text-zinc-500 font-medium hover:text-zinc-700 transition-colors mb-1">
              {t('chat.tool.inputParams')}
            </summary>
            <pre className="p-2 bg-zinc-950 text-zinc-300 rounded-md text-[11px] overflow-x-auto font-mono leading-relaxed">
              {JSON.stringify(state.input, null, 2)}
            </pre>
          </details>
        )}

        {showGenericToolPayload && status === 'completed' && state.output !== undefined && (
          <details open>
            <summary className="cursor-pointer text-[11px] text-zinc-500 font-medium hover:text-zinc-700 transition-colors mb-1">
              {t('chat.tool.outputResult')}
            </summary>
            <pre className="p-2 bg-zinc-950 text-green-400 rounded-md text-[11px] overflow-x-auto max-h-48 overflow-y-auto font-mono leading-relaxed">
              {formatToolPayload(state.output)}
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
 * Fast path:
 * - structural props: isActive, role, finish, parts.length
 * - per-part render probe with early exits and ref equality reuse
 *
 * Only triggers a re-render when something actually visible has changed,
 * avoiding unnecessary reconciliation during high-frequency streaming.
 */
export const ChatMessageBubble = memo(ChatMessageBubbleInner, (prev, next) => {
  if (prev.isActive !== next.isActive) return false;
  if (prev.showActions !== next.showActions) return false;
  if (prev.collapseIntermediateSteps !== next.collapseIntermediateSteps) return false;
  if (prev.processGroupsDefaultOpen !== next.processGroupsDefaultOpen) return false;
  if (prev.processGroupsOpenWhileActive !== next.processGroupsOpenWhileActive) return false;
  if (prev.processGroupOpenState !== next.processGroupOpenState) return false;
  if (prev.editingMessageId !== next.editingMessageId) return false;
  if (prev.editingText !== next.editingText) return false;
  if (prev.actionsDisabled !== next.actionsDisabled) return false;
  if (prev.actionMessageId !== next.actionMessageId) return false;
  if (prev.message.finish !== next.message.finish) return false;
  const prevParts = prev.message.parts as any[] | undefined;
  const nextParts = next.message.parts as any[] | undefined;
  if ((prevParts?.length ?? 0) !== (nextParts?.length ?? 0)) return false;
  if (prev.pendingQuestions !== next.pendingQuestions) return false;
  // Text placeholders can now be created before later tool parts arrive.
  // Compare each rendered part so mid-array text streaming still repaints.
  return areChatMessagePartsRenderEqual(prev.message.parts, next.message.parts);
});
