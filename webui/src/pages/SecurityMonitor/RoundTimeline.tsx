import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { CalendarDays, CheckCircle2, ChevronDown, Clock3, ExternalLink, Loader2, XCircle } from 'lucide-react';
import type { MonitorRun } from '@/api/securityMonitoring';

import { newestRunsFirst, normalizeRoundStatus, roundBusinessConclusion, roundFactSummary, roundRecoverySummary } from './roundSummary';
export { roundBusinessConclusion } from './roundSummary';

function displayTime(value: string | null | undefined, timezone: string, date = false) {
  if (!value || !Number.isFinite(new Date(value).getTime())) return '时间未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone, hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    ...(date ? { year: 'numeric', month: '2-digit', day: '2-digit' } as const : {}),
  }).format(new Date(value));
}

function plainFact(value: unknown): string {
  if (typeof value !== 'string') return value == null ? '' : JSON.stringify(value, null, 2);
  try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
}

export default function RoundTimeline({ runs, timezone }: { runs: MonitorRun[]; timezone: string }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const ordered = useMemo(() => newestRunsFirst(runs), [runs]);
  const hasExpanded = ordered.some(run => expanded.has(run.id));
  if (!ordered.length) return <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center dark:border-gray-700">
    <CalendarDays size={28} aria-hidden="true" className="mx-auto mb-3 text-gray-400" />
    <p className="font-medium">所选日期暂无监测记录</p>
    <p className="mt-2 text-sm text-gray-500">可选择其他日期查看执行时间线与当日告警总结。</p>
  </div>;
  return <section aria-label="轮次时间线">
    <div className="mb-5 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
      <p>按开始时间倒序 · 最新在前 · 共 {ordered.length} 轮 · 点击节点查看本轮报告</p>
      <button type="button" disabled={!hasExpanded} onClick={() => setExpanded(new Set())} className="rounded-md px-2 py-1 text-blue-600 hover:bg-blue-50 disabled:cursor-default disabled:opacity-40 dark:text-blue-400 dark:hover:bg-blue-950/40">全部收起</button>
    </div>
    <ol aria-label="执行轮次时间线" className="ml-2 border-l border-gray-200 dark:border-gray-700">
      {ordered.map((run, index) => {
        const status = normalizeRoundStatus(run.status);
        const failed = status === 'failed';
        const complete = status === 'completed';
        const open = expanded.has(run.id);
        const time = displayTime(run.started_at, timezone);
        const statusText = complete ? '完成' : failed ? '失败' : status === 'running' ? '执行中' : status === 'queued' ? '排队' : '待核对';
        const Icon = complete ? CheckCircle2 : failed ? XCircle : status === 'running' ? Loader2 : Clock3;
        const tone = failed ? 'text-red-600 dark:text-red-400' : complete ? 'text-emerald-600 dark:text-emerald-400' : 'text-blue-600 dark:text-blue-400';
        const detailsId = `monitor-round-details-${run.id}`;
        return <li key={run.id} data-testid="round-timeline-node" className="relative pb-4 pl-6 last:pb-0">
          <span className={`absolute -left-[9px] top-4 rounded-full bg-white dark:bg-gray-800 ${tone}`}><Icon aria-hidden="true" size={17} className={status === 'running' ? 'animate-spin' : ''} /></span>
          <div className={`overflow-hidden rounded-xl border ${open ? 'border-blue-200 dark:border-blue-900' : 'border-gray-200 dark:border-gray-700'}`}>
            <button
              type="button" aria-expanded={open} aria-controls={detailsId}
              aria-label={`${time} 第 ${ordered.length - index} 轮：${roundBusinessConclusion(run)}`}
              onClick={() => setExpanded(current => { const next = new Set(current); if (next.has(run.id)) next.delete(run.id); else next.add(run.id); return next; })}
              className="grid w-full grid-cols-[1fr_auto_auto] items-center gap-x-4 gap-y-2 bg-white px-4 py-3 text-left transition-colors hover:bg-gray-50 dark:bg-gray-800 dark:hover:bg-gray-700/60 sm:flex sm:flex-wrap"
            >
              <time dateTime={run.started_at} className="shrink-0 text-sm font-medium tabular-nums text-gray-600 dark:text-gray-300">{time}</time>
              <span className="order-last col-span-3 min-w-0 flex-1 break-words text-sm text-gray-900 dark:text-gray-100 sm:order-none">
                <span className="block font-semibold">{roundBusinessConclusion(run)}</span>
                {roundFactSummary(run) && <span className="mt-1 block text-xs leading-5 text-slate-500 dark:text-slate-400">{roundFactSummary(run)}</span>}
                {roundRecoverySummary(run, timezone) && <span className="mt-1 block text-xs leading-5 text-sky-700 dark:text-sky-300">{roundRecoverySummary(run, timezone)}</span>}
              </span>
              <span className={`text-xs font-medium ${tone}`}>{statusText}</span>
              <ChevronDown aria-hidden="true" size={16} className={`shrink-0 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} />
            </button>
            {open && <section id={detailsId} aria-label={`${time} 本轮报告`} className="space-y-4 border-t border-gray-100 bg-gray-50/60 px-4 py-4 text-sm dark:border-gray-700 dark:bg-gray-900/40">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><h3 className="font-semibold">本轮对话报告</h3><p className="mt-1 text-xs text-gray-500">第 {ordered.length - index} 轮 · 任务完成不代表告警已闭环。</p></div>
                {run.session_id && run.message_id && <Link to={`/sessions?session=${encodeURIComponent(run.session_id)}&focusMessage=${encodeURIComponent(run.message_id)}`} className="inline-flex items-center gap-1 whitespace-nowrap text-blue-600 hover:underline dark:text-blue-400">查看本轮对话<ExternalLink size={13} aria-hidden="true" /></Link>}
              </div>
              <dl className="grid gap-x-6 gap-y-2 text-xs text-gray-500 sm:grid-cols-2 dark:text-gray-400">
                <div><dt className="inline">实际开始：</dt><dd className="inline">{displayTime(run.started_at, timezone, true)}</dd></div>
                <div><dt className="inline">结束时间：</dt><dd className="inline">{run.finished_at ? displayTime(run.finished_at, timezone, true) : status === 'running' ? '执行中' : '未记录'}</dd></div>
              </dl>
              <div className="whitespace-pre-wrap break-words leading-7">{run.summary || run.error || '本轮未记录完整摘要，请查看工具事实或原始对话。'}</div>
              {run.error && run.error !== run.summary && <p className="whitespace-pre-wrap break-words rounded-lg bg-red-50 p-3 text-red-700 dark:bg-red-950/40 dark:text-red-300"><strong>异常原因：</strong>{run.error}</p>}
              {run.next_step && <p className="whitespace-pre-wrap break-words rounded-lg bg-white p-3 dark:bg-gray-800"><strong>下一步：</strong>{run.next_step}</p>}
              <div><h4 className="mb-2 text-xs font-semibold text-gray-500 dark:text-gray-400">工具执行事实</h4>
                {run.steps.length === 0 ? <p className="text-xs text-gray-500">本轮未记录工具步骤。</p> : <ol aria-label="本轮工具记录" className="space-y-2">
                  {run.steps.map(step => <li key={step.id} className="rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
                    <details><summary className="cursor-pointer text-sm"><span className="font-medium">{step.tool}</span><span className={`ml-3 text-xs ${['failed', 'error'].includes(step.status) ? 'text-red-600' : 'text-gray-500'}`}>{({ completed: '完成', failed: '失败', error: '失败', running: '执行中', queued: '排队' } as Record<string, string>)[step.status] || '待核对'}</span></summary>
                      <div className="mt-3 space-y-2 text-xs leading-6">
                        {step.error && <p className="whitespace-pre-wrap break-words text-red-600">{step.error}</p>}
                        {step.input != null && <div><strong>输入参数</strong><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-gray-50 p-2 dark:bg-gray-900">{plainFact(step.input)}</pre></div>}
                        {step.output != null && <div><strong>输出结果</strong><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded bg-gray-50 p-2 dark:bg-gray-900">{plainFact(step.output)}</pre></div>}
                        {step.input == null && step.output == null && !step.error && <p>未记录更多步骤明细，可查看原始对话。</p>}
                      </div>
                    </details>
                  </li>)}
                </ol>}
              </div>
            </section>}
          </div>
        </li>;
      })}
    </ol>
  </section>;
}
