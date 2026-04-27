/**
 * DelegateTaskCard — 子 Agent 委派卡片
 *
 * 当 ChatToolPart 检测到 delegate_task / call_omo_agent 工具时，
 * 替代普通工具卡片，展示子 Agent 名称、任务描述、执行状态和结果摘要。
 */

import { useState, useEffect, useRef } from 'react';
import { ChevronRight, ExternalLink, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { MessagePart, ToolState } from '@/types';
import DelegateDetailSheet from './DelegateDetailSheet';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DELEGATE_TOOLS = new Set(['delegate_task', 'call_omo_agent', 'task']);

export function isDelegateTool(toolName: string): boolean {
  return DELEGATE_TOOLS.has(toolName);
}

export function shouldRenderDelegateTaskCard(part: MessagePart): boolean {
  if (part.tool && isDelegateTool(part.tool)) {
    return true;
  }

  const state: Partial<ToolState> = part.state || {};
  const input = state.input || {};
  if (typeof input.subagent_type === 'string' && input.subagent_type.trim()) {
    return true;
  }

  const output = typeof state.output === 'string' ? state.output : undefined;
  return !!extractSessionId(state.metadata, output);
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
  const innerMeta = meta?.metadata as Record<string, any> | undefined;
  const sessionId =
    meta?.sessionId ??
    meta?.sessionID ??
    meta?.session_id ??
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

function extractDelegateInfo(state: Partial<ToolState>, subTaskLabel: string): DelegateInfo {
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
    const raw = typeof state.output === 'string' ? state.output : JSON.stringify(state.output);
    const cleaned = raw.replace(/<task_metadata>[\s\S]*?<\/task_metadata>/g, '').trim();
    output = cleaned || undefined;
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

  return {
    agentName,
    description: input.description || subTaskLabel,
    isBackground: !!input.run_in_background,
    childSessionId,
    status: state.status || 'pending',
    error: state.error,
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
    barColor: 'bg-gray-300',
    bgColor: 'bg-gray-50',
    borderColor: 'border-gray-200',
    textColor: 'text-gray-600',
    badgeBg: 'bg-gray-100 text-gray-600',
  },
  running: {
    barColor: 'bg-sky-500',
    bgColor: 'bg-sky-50/60',
    borderColor: 'border-sky-200',
    textColor: 'text-sky-700',
    badgeBg: 'bg-sky-100 text-sky-700',
    pulse: true,
  },
  completed: {
    barColor: 'bg-emerald-500',
    bgColor: 'bg-emerald-50/50',
    borderColor: 'border-emerald-200',
    textColor: 'text-emerald-700',
    badgeBg: 'bg-emerald-100 text-emerald-700',
  },
  error: {
    barColor: 'bg-red-500',
    bgColor: 'bg-red-50/50',
    borderColor: 'border-red-200',
    textColor: 'text-red-700',
    badgeBg: 'bg-red-100 text-red-700',
  },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface DelegateTaskCardProps {
  part: MessagePart;
}

export default function DelegateTaskCard({ part }: DelegateTaskCardProps) {
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

  return (
    <>
      <div className={`mt-2 rounded-lg border ${cfg.borderColor} ${cfg.bgColor} overflow-hidden`}>
        {/* Color accent bar */}
        <div className={`h-0.5 ${cfg.barColor} ${cfg.pulse ? 'animate-pulse' : ''}`} />

        <div className="px-3 py-2.5">
          {/* Header row */}
          <div className="flex items-center gap-2">
            {/* Agent avatar */}
            <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gradient-to-br from-violet-500 to-red-500 text-white text-[10px] font-bold flex-shrink-0 shadow-sm">
              {info.agentName.charAt(0)}
            </span>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-gray-800 truncate">
                  {info.agentName}
                </span>
                {info.isBackground && (
                  <span className="text-[9px] px-1 py-0.5 rounded bg-violet-100 text-violet-600 font-medium leading-none">
                    {t('delegate.background')}
                  </span>
                )}
              </div>
              <p className="text-[11px] text-gray-500 truncate mt-0.5">{info.description}</p>
            </div>

            {/* Status badge */}
            <span className={`flex-shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full leading-none ${cfg.badgeBg}`}>
              {cfg.pulse && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse mr-1 align-middle" />
              )}
              {t(`delegate.${info.status}`, { defaultValue: info.status })}
            </span>
          </div>

          {/* Duration */}
          {elapsed !== null && elapsed > 0 && (
            <div className="mt-1.5 text-[10px] text-gray-400">
              {info.status === 'running' ? `⏱ ${t('delegate.elapsedRunning')} ` : `⏱ ${t('delegate.elapsedDone')} `}
              {formatDuration(elapsed)}
              {info.stepCount > 0 && ` · ${info.stepCount} ${t('delegate.steps')}`}
            </div>
          )}

          {/* Live activity stream */}
          {info.status === 'running' && info.steps.length > 0 && (
            <div className="mt-2 space-y-0.5 py-1.5 px-2 rounded-md bg-white/50 border border-sky-100">
              {info.steps.map((step, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[11px] leading-snug">
                  <span className={
                    step.status === 'completed'
                      ? 'text-emerald-500'
                      : step.status === 'error'
                        ? 'text-red-500'
                        : 'text-sky-400 animate-pulse'
                  }>
                    {step.status === 'completed' ? '✓' : step.status === 'error' ? '✗' : '◌'}
                  </span>
                  <span className="font-mono text-[10px] text-gray-500 w-14 flex-shrink-0">
                    {step.tool}
                  </span>
                  <span className="text-gray-400 truncate">{step.title}</span>
                </div>
              ))}
              {info.currentText && (
                <div className="text-[10px] text-gray-400 italic truncate pt-0.5 pl-5">
                  ⋯ {info.currentText.slice(-80)}
                </div>
              )}
            </div>
          )}

          {/* Error */}
          {info.status === 'error' && info.error && (
            <div className="mt-2 flex items-start gap-1.5 p-2 rounded-md bg-red-100/80 text-red-700 text-[11px]">
              <XCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="break-words">{info.error}</span>
            </div>
          )}

          {/* Output preview */}
          {info.status === 'completed' && info.output && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] font-medium text-gray-500 hover:text-gray-700 select-none">
                📤 {t('delegate.resultSummary')}
              </summary>
              <div className="mt-1 p-2 bg-white/70 rounded-md border border-gray-100 text-[11px] text-gray-700 whitespace-pre-wrap break-words max-h-40 overflow-y-auto leading-relaxed">
                {truncateOutput(info.output)}
              </div>
            </details>
          )}

          {/* View detail button — always visible */}
          <button
            onClick={() => info.childSessionId && setSheetOpen(true)}
            disabled={!info.childSessionId}
            className={`mt-2 flex items-center gap-1 text-[11px] font-medium transition-colors group ${
              info.childSessionId
                ? 'text-red-600 hover:text-red-800 cursor-pointer'
                : 'text-gray-400 cursor-not-allowed'
            }`}
          >
            <ExternalLink className="w-3 h-3" />
            {t('delegate.viewDialog')}
            <ChevronRight className="w-3 h-3 transition-transform group-hover:translate-x-0.5" />
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
