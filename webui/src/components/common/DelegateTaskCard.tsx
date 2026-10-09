/**
 * DelegateTaskCard — 子 Agent 委派卡片
 *
 * 当 ChatToolPart 检测到 delegate_task 工具时，
 * 替代普通工具卡片，展示子 Agent 名称、任务描述、执行状态和结果摘要。
 */

import { useState, useEffect, useRef } from 'react';
import { IconRobot, IconChevronDown, IconChevronRight, IconExternalLink, IconCircleX } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import type { MessagePart, ToolState } from '@/types';
import { stripTaskMetadata } from '@/features/session-chat/display';
import DelegateDetailSheet from './DelegateDetailSheet';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DELEGATE_TOOLS = new Set(['delegate_task', 'task']);

export function isDelegateTool(toolName: string): boolean {
  return DELEGATE_TOOLS.has(toolName);
}

export function shouldRenderDelegateTaskCard(part: MessagePart): boolean {
  // 1. Explicit delegate tool: render the card.
  if (part.tool && isDelegateTool(part.tool)) {
    return true;
  }

  const state: Partial<ToolState> = part.state || {};
  const input = state.input || {};

  // 2. Unknown/missing tool: only upgrade if the part *also* carries
  //    delegate-shaped input. This prevents MCP tools (e.g. wecom_mcp,
  //    threatbook_mcp) from being misclassified just because their output
  //    happens to contain a `<task_metadata>` block.
  if (!part.tool || part.tool === 'unknown') {
    const hasDelegateInput =
      (typeof input.subagent_type === 'string' && input.subagent_type.trim()) ||
      (typeof input.category === 'string' && input.category.trim());
    if (!hasDelegateInput) {
      return false;
    }
    const output = typeof state.output === 'string' ? state.output : undefined;
    return !!extractSessionId(state.metadata, output);
  }
  return false;
}

interface ActivityStep {
  tool: string;
  title: string;
  status: 'running' | 'completed' | 'error';
}

interface DelegateInfo {
  agentName: string;
  description: string;
  isBackground: boolean;
  childSessionId?: string;
  status: string;
  error?: string;
  output?: string;
  durationMs: number | null;
  steps: ActivityStep[];
  stepCount: number;
  currentText: string;
  elapsed: number;
}

function extractSessionId(
  meta: Record<string, any> | undefined,
  output: string | undefined,
): string | undefined {
  // Top-level meta only trusts the canonical `sessionId` key. Variant
  // casings (sessionID / session_id) are accepted only in the nested
  // `metadata` envelope to avoid matching arbitrary tool metadata that
  // happens to use snake_case or PascalCase.
  const innerMeta = meta?.metadata as Record<string, any> | undefined;
  const sessionId =
    meta?.sessionId ??
    innerMeta?.sessionId ??
    innerMeta?.sessionID ??
    innerMeta?.session_id;

  if (typeof sessionId === 'string' && sessionId.trim()) {
    return sessionId.trim();
  }

  if (!output) return undefined;
  const match = output.match(/<task_metadata>[\s\S]*?session_id:\s*([^\n<]+)[\s\S]*?<\/task_metadata>/i);
  return match?.[1]?.trim() || undefined;
}

export function extractDelegateInfo(state: Partial<ToolState>, subTaskLabel: string): DelegateInfo {
  const input = state.input || {};
  const agentRaw = input.subagent_type || input.category || 'unknown';
  const agentName = typeof agentRaw === 'string'
    ? agentRaw.charAt(0).toUpperCase() + agentRaw.slice(1)
    : String(agentRaw);

  let durationMs: number | null = null;
  if (state.time?.start) {
    const end = state.time.end || Date.now();
    durationMs = end - state.time.start;
  }

  let output: string | undefined;
  if (state.output !== undefined && state.output !== null) {
    output = typeof state.output === 'string' ? state.output : JSON.stringify(state.output);
  }

  // metadata can be flat { sessionId, ... } (completed state / ToolResult)
  // or double-nested { title, metadata: { sessionId, ... } } (ctx.metadata callback during running)
  const meta = state.metadata as Record<string, any> | undefined;
  const innerMeta = meta?.metadata as Record<string, any> | undefined;

  const childSessionId = extractSessionId(meta, typeof state.output === 'string' ? state.output : undefined);
  const steps = (meta?.steps ?? innerMeta?.steps ?? []) as ActivityStep[];
  const stepCount = (meta?.stepCount ?? innerMeta?.stepCount ?? 0) as number;
  const currentText = (meta?.currentText ?? innerMeta?.currentText ?? '') as string;
  const elapsed = (meta?.elapsed ?? innerMeta?.elapsed ?? 0) as number;
  const isBackground = !!input.run_in_background || Boolean(meta?.background ?? innerMeta?.background);
  const submitted = Boolean(meta?.submitted ?? innerMeta?.submitted);
  const runtimeStatus = meta?.status ?? innerMeta?.status;
  if (submitted) {
    output = meta?.result ?? innerMeta?.result;
    const finishedAt = meta?.finished_at ?? innerMeta?.finished_at;
    durationMs = state.time?.start ? (finishedAt || Date.now()) - state.time.start : null;
  }
  if (output !== undefined && output !== null) {
    output = stripTaskMetadata(typeof output === 'string' ? output : JSON.stringify(output)).trim() || undefined;
  }
  const error = state.error ? stripTaskMetadata(state.error).trim() || undefined : undefined;
  const backgroundStatus = isBackground
    ? (typeof runtimeStatus === 'string' && runtimeStatus.trim()
      ? runtimeStatus
      : state.status === 'completed' && childSessionId
        ? 'running'
        : undefined)
    : undefined;

  return {
    agentName,
    description: input.description || subTaskLabel,
    isBackground,
    childSessionId,
    status: (submitted ? runtimeStatus : backgroundStatus) || state.status || 'pending',
    error: submitted && runtimeStatus === 'error' ? output : error,
    output,
    durationMs,
    steps,
    stepCount,
    currentText,
    elapsed,
  };
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  const mins = Math.floor(secs / 60);
  const remainSecs = Math.round(secs % 60);
  return `${mins}m${remainSecs}s`;
}

function truncateOutput(text: string, maxLen = 300): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + '…';
}

// ---------------------------------------------------------------------------
// Status bar colors & icons (labels resolved via i18n in component)
// ---------------------------------------------------------------------------

const STATUS_STYLE: Record<string, {
  barColor: string;
  bgColor: string;
  borderColor: string;
  textColor: string;
  badgeBg: string;
  pulse?: boolean;
}> = {
  pending: {
    barColor: 'bg-selected',
    bgColor: 'bg-canvas',
    borderColor: 'border-line',
    textColor: 'text-fg-secondary',
    badgeBg: 'bg-subtle text-fg-secondary',
  },
  running: {
    barColor: 'bg-info',
    bgColor: 'bg-info-soft',
    borderColor: 'border-info',
    textColor: 'text-info',
    badgeBg: 'bg-info-soft text-info',
    pulse: true,
  },
  completed: {
    barColor: 'bg-success',
    bgColor: 'bg-success-soft',
    borderColor: 'border-success',
    textColor: 'text-success',
    badgeBg: 'bg-success-soft text-success',
  },
  error: {
    barColor: 'bg-action',
    bgColor: 'bg-danger-soft',
    borderColor: 'border-danger',
    textColor: 'text-danger',
    badgeBg: 'bg-danger-soft text-danger',
  },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface DelegateTaskCardProps {
  part: MessagePart;
  processStep?: boolean;
}

export default function DelegateTaskCard({ part, processStep = false }: DelegateTaskCardProps) {
  const { t } = useTranslation('common');
  let state: Partial<ToolState> = {};
  let info: DelegateInfo;
  try {
    state = part.state || {};
    info = extractDelegateInfo(state, t('delegate.subTask'));
  } catch {
    info = {
      agentName: 'Agent',
      description: t('delegate.subTask'),
      isBackground: false,
      childSessionId: undefined,
      status: 'pending',
      error: undefined,
      output: undefined,
      durationMs: null,
      steps: [],
      stepCount: 0,
      currentText: '',
      elapsed: 0,
    };
  }
  const cfg = STATUS_STYLE[info.status] ?? STATUS_STYLE.pending;

  const [sheetOpen, setSheetOpen] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(info.durationMs);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Live elapsed timer for running state
  useEffect(() => {
    if (info.status === 'running' && state.time?.start) {
      const tick = () => setElapsed(Date.now() - state.time!.start!);
      tick();
      intervalRef.current = setInterval(tick, 1000);
      return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
    }
    setElapsed(info.durationMs);
  }, [info.status, state.time?.start, state.time?.end, info.durationMs]);

  if (processStep) {
    return (
      <>
        <details data-testid="chat-process-delegate-step" className="group/delegate min-w-0">
          <summary className="flex min-h-7 cursor-pointer list-none items-center gap-2 text-sm font-medium text-fg-muted transition-colors hover:text-fg dark:text-fg-muted dark:hover:text-fg-secondary [&::-webkit-details-marker]:hidden">
            <span className="inline-grid h-[18px] w-[18px] flex-[0_0_18px] place-items-center text-ai dark:text-ai">
              <IconRobot aria-hidden="true" className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-shrink-0 text-ai dark:text-ai">
              {t('delegate.delegatedTo', { agent: info.agentName })}
            </span>
            <span className="min-w-0 truncate font-normal text-fg-muted dark:text-fg-muted">
              {info.description}
            </span>
            {info.isBackground && (
              <span className="flex-shrink-0 rounded bg-ai-soft px-1 py-0.5 text-xs font-medium leading-none text-ai dark:bg-ai-soft dark:text-ai">
                {t('delegate.background')}
              </span>
            )}
            <span className={`ml-auto flex flex-shrink-0 items-center gap-1 text-xs ${cfg.textColor}`}>
              {cfg.pulse && (
                <span className="h-1.5 w-1.5 rounded-full bg-info animate-pulse" />
              )}
              {t(`delegate.${info.status}`, { defaultValue: info.status })}
            </span>
            <IconChevronDown aria-hidden="true" className="ml-0.5 h-4 w-4 flex-shrink-0 text-fg-muted transition-transform group-open/delegate:rotate-180 dark:text-fg-muted" />
          </summary>

          <div className="mb-[9px] ml-2 mt-[3px] space-y-2 border-l border-line py-1.5 pl-[26px] pr-0 text-xs text-fg-muted dark:border-line dark:text-fg-muted">
            {elapsed !== null && elapsed > 0 && (
              <div className="text-xs text-fg-muted dark:text-fg-muted">
                {info.status === 'running' ? t('delegate.elapsedRunning') : t('delegate.elapsedDone')}
                {' '}
                {formatDuration(elapsed)}
                {info.stepCount > 0 && ` · ${info.stepCount} ${t('delegate.steps')}`}
              </div>
            )}

            {info.status === 'running' && info.steps.length > 0 && (
              <div className="space-y-1">
                {info.steps.map((step, index) => (
                  <div key={`${step.tool}-${index}`} className="flex min-w-0 items-center gap-1.5 text-xs leading-snug">
                    <span className={
                      step.status === 'completed'
                        ? 'text-success'
                        : step.status === 'error'
                          ? 'text-danger'
                          : 'text-info animate-pulse'
                    }>
                      {step.status === 'completed' ? '✓' : step.status === 'error' ? '✗' : '◌'}
                    </span>
                    <span className="w-16 flex-shrink-0 truncate font-mono text-xs text-fg-muted">
                      {step.tool}
                    </span>
                    <span className="truncate text-fg-muted">{step.title}</span>
                  </div>
                ))}
                {info.currentText && (
                  <div className="truncate pl-4 text-xs italic text-fg-muted">
                    ⋯ {info.currentText.slice(-80)}
                  </div>
                )}
              </div>
            )}

            {info.status === 'error' && info.error && (
              <div className="flex items-start gap-1.5 rounded-md bg-danger-soft p-2 text-xs text-danger dark:bg-danger-soft dark:text-danger">
                <IconCircleX aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
                <span className="break-words">{info.error}</span>
              </div>
            )}

            {info.status === 'completed' && info.output && (
              <details>
                <summary className="cursor-pointer select-none text-xs font-medium text-fg-muted hover:text-fg-secondary dark:text-fg-muted dark:hover:text-fg-secondary">
                  {t('delegate.resultSummary')}
                </summary>
                <div className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-line bg-surface p-2 text-xs leading-relaxed text-fg-secondary dark:border-line dark:bg-surface dark:text-fg-secondary">
                  {truncateOutput(info.output)}
                </div>
              </details>
            )}

            <button
              type="button"
              onClick={() => {
                if (!info.childSessionId) return;
                setSheetOpen(true);
              }}
              disabled={!info.childSessionId}
              className={`flex items-center gap-1 text-xs font-medium transition-colors group ${
                info.childSessionId
                  ? 'cursor-pointer text-ai hover:text-ai dark:text-ai dark:hover:text-ai'
                  : 'cursor-not-allowed text-fg-muted dark:text-fg'
              }`}
            >
              <IconExternalLink aria-hidden="true" className="h-4 w-4" />
              {t('delegate.viewExecution')}
              <IconChevronRight aria-hidden="true" className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </button>
          </div>
        </details>

        {info.childSessionId && (
          <DelegateDetailSheet
            open={sheetOpen}
            onClose={() => setSheetOpen(false)}
            sessionId={info.childSessionId}
            agentName={info.agentName}
            description={info.description}
            status={info.status}
          />
        )}
      </>
    );
  }

  return (
    <>
      <div className={`mt-2 rounded-lg border ${cfg.borderColor} ${cfg.bgColor} overflow-hidden`}>
        {/* Color accent bar */}
        <div className={`h-0.5 ${cfg.barColor} ${cfg.pulse ? 'animate-pulse' : ''}`} />

        <div className="px-3 py-2.5">
          {/* Header row */}
          <div className="flex items-center gap-2">
            {/* Agent avatar */}
            <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-ai-soft text-ai text-xs font-bold flex-shrink-0 shadow-sm">
              {info.agentName.charAt(0)}
            </span>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-fg truncate">
                  {info.agentName}
                </span>
                {info.isBackground && (
                  <span className="text-xs px-1 py-0.5 rounded bg-ai-soft text-ai font-medium leading-none">
                    {t('delegate.background')}
                  </span>
                )}
              </div>
              <p className="text-xs text-fg-muted truncate mt-0.5">{info.description}</p>
            </div>

            {/* Status badge */}
            <span className={`flex-shrink-0 text-xs font-semibold px-1.5 py-0.5 rounded-full leading-none ${cfg.badgeBg}`}>
              {cfg.pulse && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-info animate-pulse mr-1 align-middle" />
              )}
              {t(`delegate.${info.status}`, { defaultValue: info.status })}
            </span>
          </div>

          {/* Duration */}
          {elapsed !== null && elapsed > 0 && (
            <div className="mt-1.5 text-xs text-fg-muted">
              {info.status === 'running' ? `⏱ ${t('delegate.elapsedRunning')} ` : `⏱ ${t('delegate.elapsedDone')} `}
              {formatDuration(elapsed)}
              {info.stepCount > 0 && ` · ${info.stepCount} ${t('delegate.steps')}`}
            </div>
          )}

          {/* Live activity stream */}
          {info.status === 'running' && info.steps.length > 0 && (
            <div className="mt-2 space-y-0.5 py-1.5 px-2 rounded-md bg-surface border border-info">
              {info.steps.map((step, i) => (
                <div key={i} className="flex items-center gap-1.5 text-xs leading-snug">
                  <span className={
                    step.status === 'completed'
                      ? 'text-success'
                      : step.status === 'error'
                        ? 'text-danger'
                        : 'text-info animate-pulse'
                  }>
                    {step.status === 'completed' ? '✓' : step.status === 'error' ? '✗' : '◌'}
                  </span>
                  <span className="font-mono text-xs text-fg-muted w-14 flex-shrink-0">
                    {step.tool}
                  </span>
                  <span className="text-fg-muted truncate">{step.title}</span>
                </div>
              ))}
              {info.currentText && (
                <div className="text-xs text-fg-muted italic truncate pt-0.5 pl-5">
                  ⋯ {info.currentText.slice(-80)}
                </div>
              )}
            </div>
          )}

          {/* Error */}
          {info.status === 'error' && info.error && (
            <div className="mt-2 flex items-start gap-1.5 p-2 rounded-md bg-danger-soft text-danger text-xs">
              <IconCircleX aria-hidden="true" className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span className="break-words">{info.error}</span>
            </div>
          )}

          {/* Output preview */}
          {info.status === 'completed' && info.output && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs font-medium text-fg-muted hover:text-fg-secondary select-none">
                📤 {t('delegate.resultSummary')}
              </summary>
              <div className="mt-1 p-2 bg-surface rounded-md border border-line text-xs text-fg-secondary whitespace-pre-wrap break-words max-h-40 overflow-y-auto leading-relaxed">
                {truncateOutput(info.output)}
              </div>
            </details>
          )}

          {/* View detail button — always visible */}
          <button
            onClick={() => {
              if (!info.childSessionId) return;
              setSheetOpen(true);
            }}
            disabled={!info.childSessionId}
            className={`mt-2 flex items-center gap-1 text-xs font-medium transition-colors group ${
              info.childSessionId
                ? 'text-danger hover:text-danger cursor-pointer'
                : 'text-fg-muted cursor-not-allowed'
            }`}
          >
            <IconExternalLink aria-hidden="true" className="w-4 h-4" />
            {t('delegate.viewDialog')}
            <IconChevronRight aria-hidden="true" className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
          </button>
        </div>
      </div>

      {/* Detail sheet */}
      {info.childSessionId && (
        <DelegateDetailSheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          sessionId={info.childSessionId}
          agentName={info.agentName}
          description={info.description}
          status={info.status}
        />
      )}
    </>
  );
}
