import type { MonitorRun } from '@/api/securityMonitoring';

export const normalizeRoundStatus = (status: string) => status === 'partial' ? 'completed'
  : ['interrupted', 'cancelled'].includes(status) ? 'failed' : status;
const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
const objectValue = (value: unknown): Record<string, unknown> | null => {
  if (typeof value === 'string') {
    try { value = JSON.parse(value); } catch { return null; }
  }
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
};

/** Summaries describe persisted round facts, never infer incident closure or new failures from a backlog. */
export function roundBusinessConclusion(run: MonitorRun): string {
  const status = normalizeRoundStatus(run.status);
  const facts = run.result;
  if (status === 'running') return run.steps.find(step => step.status === 'running')?.tool || '调查执行中';
  if (status === 'queued') return '等待执行';
  if (status === 'failed') {
    const errors = [...(facts?.errors || []), run.error || '', ...run.steps.filter(step => ['failed', 'error'].includes(step.status)).map(step => step.error || '')].join('；');
    const causes: string[] = [];
    const modelFailure = ['调查模型请求超时', '调查模型认证失败', '调查模型访问被拒绝', '调查模型请求受到限流', '调查模型服务暂时不可用', '调查模型连接失败', '调查模型请求失败'].find(cause => errors.includes(cause));
    if (modelFailure) causes.push(modelFailure);
    else if (/模型.*(?:不完整|完整决策|格式无效|截断|空决策|未正常结束)/.test(errors)) causes.push('模型决策未完成');
    if (facts?.mail?.notification?.errors?.length || facts?.mail?.feedback?.errors?.length) causes.push('邮件跟进失败');
    else if (facts?.mail?.health?.errors?.length) causes.push('邮件连接未就绪');
    if (causes.length) return causes.join(' · ');
    const failedTools = run.steps.filter(step => ['failed', 'error'].includes(step.status)).map(step => step.tool);
    if (failedTools.some(tool => /调查|智能体/.test(tool))) return '调查失败 · 待核对';
    if (failedTools.some(tool => /查询|取证/.test(tool))) return '查询失败 · 待重试';
    if (['interrupted', 'cancelled'].includes(run.status)) return '任务中断';
    if (/仍有 \d+ 条调查因系统故障未恢复/.test(errors)) return '历史故障尚待恢复 · 查看原记录';
    return '本轮执行失败 · 展开查看原因';
  }
  if (status !== 'completed') return '状态待核对';
  const verified = count(facts?.mail?.feedback?.verified) || 0;
  const sent = count(facts?.mail?.notification?.sent) || 0;
  const pending = count(facts?.mail?.feedback?.pending) || 0;
  const deferred = count(facts?.deferred) || 0;
  if (verified) return `${verified} 封反馈回查已确认${sent ? ' · 有新通知待回信' : ''}`;
  if (sent) return `已通知 ${sent} 封 · 待回信`;
  if (pending) return `${pending} 封回信待跟进`;
  if (deferred) return `${deferred} 条已保存待续查`;
  if (run.steps.some(step => objectValue(step.output)?.duplicate === true)) return '通知已去重 · 本轮未重发';
  if (facts?.errors?.length) return '调查待续查'; // older partial records retain their evidence gaps
  if (count(facts?.events) === 0) return '零事件 · 无需新通知';
  if ((count(facts?.analyzed) || 0) > 0) return facts?.mail?.enabled === false
    ? '调查完成 · 邮件未启用' : '调查完成 · 无新增通知';
  return '本轮已结束 · 详见记录';
}

export function roundFactSummary(run: MonitorRun): string {
  const facts = run.result;
  if (!facts) return '';
  const parts: string[] = [];
  const queried = count(facts.query_events), resumed = count(facts.resumed_events);
  if (queried !== undefined) parts.push(facts.query_complete === false ? `查询未完成，已保存 ${queried} 条` : `本轮查询 ${queried} 条`);
  if (resumed !== undefined) parts.push(`历史续查 ${resumed} 条`);
  if (count(facts.query_resumed_overlap)) parts.push(`其中 ${facts.query_resumed_overlap} 条重叠`);
  if (queried === undefined && resumed === undefined && count(facts.events) !== undefined) parts.push(`本轮处理 ${facts.events} 条（来源未记录）`);
  if (count(facts.analyzed) !== undefined) parts.push(`已有调查结论 ${facts.analyzed} 条`);
  return parts.join(' · ');
}

export function roundRecoverySummary(run: MonitorRun, timezone: string): string {
  const backlog = run.result?.investigation_backlog;
  const waiting = count(backlog?.system_wait) || 0;
  if (!waiting) return '';
  let label = normalizeRoundStatus(run.status) === 'completed' ? `历史待恢复 ${waiting} 条` : `待恢复记录 ${waiting} 条（含本轮及历史）`;
  if (backlog?.earliest_retry_at && Number.isFinite(Date.parse(backlog.earliest_retry_at))) {
    label += ` · 最早重试 ${new Date(backlog.earliest_retry_at).toLocaleString('zh-CN', { timeZone: timezone, hour12: false })}`;
  }
  if (count(backlog?.retry_exhausted)) label += ` · ${backlog?.retry_exhausted} 条已停止自动重试`;
  return label;
}


/** Presentation only: never mutate the execution/processing order. */
export function newestRunsFirst(runs: MonitorRun[]): MonitorRun[] {
  const time = (value: string) => { const parsed = Date.parse(value); return Number.isFinite(parsed) ? parsed : -Infinity; };
  return [...runs].sort((a, b) => {
    const left = time(a.started_at), right = time(b.started_at);
    return left === right ? b.id.localeCompare(a.id) : left > right ? -1 : 1;
  });
}
