import MailFollowup, { MailSettings } from './MailFollowup';
import RoundTimeline from './RoundTimeline';
import { StreamingMarkdown } from '@/components/common/StreamingMarkdown';
import { getApiBase } from '@/api/client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ShieldCheck, ArrowUpRight, RefreshCw, Play, Pause, Loader2, Download, CalendarDays, Clock3, Mail, Settings2, CheckCircle2, CircleX } from 'lucide-react';
import EventDisposition from './EventDisposition';
import SessionChat from '@/components/common/SessionChat';
import { monitoringApi, MONITOR_PATH, type MonitorSnapshot, type MonitorRun } from '@/api/securityMonitoring';
import { useSSE } from '@/hooks/useSSE';

const labels: Record<string, string> = { completed: '完成', partial: '完成', failed: '失败', interrupted: '失败', running: '执行中', queued: '排队', cancelled: '失败', risk: '风险', unknown: '待判定', ignored: '可忽略', pending: '待生成', updated: '已更新', active: '已启用', disabled: '已停用' };
const fmt = (value: string | null, tz: string) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: tz, hour12: false }) : '—';
const card = 'rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800';
const runStatus = (status: string) => status === 'partial' ? 'completed' : ['interrupted', 'cancelled'].includes(status) ? 'failed' : status;
const runDescription = (run: MonitorRun) => (run.status === 'running' ? run.steps.find(step => step.status === 'running')?.tool : null) || run.summary || run.error || (runStatus(run.status) === 'completed' ? '本轮已结束；告警处置进度见事件记录。' : runStatus(run.status) === 'failed' ? '本轮异常结束，请查看对话中的原因。' : '等待执行');
const dateInZone = (timezone: string) => {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-');
};
const previousDay = (date: string) => new Date(new Date(`${date}T12:00:00Z`).getTime() - 86400000).toISOString().slice(0, 10);
function RunBadge({ status }: { status: string }) {
  const normalized = runStatus(status);
  return <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${normalized === 'completed' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300' : normalized === 'failed' ? 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300' : 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'}`}>
    {normalized === 'completed' ? <CheckCircle2 size={13} /> : normalized === 'failed' ? <CircleX size={13} /> : normalized === 'running' ? <Loader2 size={13} className="animate-spin" /> : <Clock3 size={13} />}{labels[normalized] || normalized}
  </span>;
}

export default function SecurityMonitor() {
  const location = useLocation(), navigate = useNavigate();
  const view = location.pathname.endsWith('/mail') ? 'mail' : location.pathname.endsWith('/dashboard') ? 'dashboard' : location.pathname.endsWith('/report') ? 'report' : 'session';
  const [day, setDay] = useState('');
  const [data, setData] = useState<MonitorSnapshot | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [report, setReport] = useState<{ key: string; content: string } | null>(null);
  const [reportError, setReportError] = useState('');
  const [reportRetry, setReportRetry] = useState(0);
  const [reportKind, setReportKind] = useState<'timeline' | 'summary'>('timeline');
  const [showSettings, setShowSettings] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [controlError, setControlError] = useState('');
  const [controlMessage, setControlMessage] = useState('');
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const exportPending = useRef(false);
  const controlPending = useRef(false);
  const refreshSequence = useRef(0);
  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    try { const result = await monitoringApi.overview(day || undefined); if (sequence === refreshSequence.current) { setData(result.data); setError(''); } }
    catch { if (sequence === refreshSequence.current) setError('数据更新失败，当前内容可能已过期。'); }
  }, [day]);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 10000); return () => { window.clearInterval(timer); refreshSequence.current++; }; }, [refresh]);
  useSSE({ url: `${getApiBase()}/api/event`, onEvent: event => { if (event.type === 'monitor.execution.started' || event.type === 'task.updated' || event.type === 'monitor.control.changed') void refresh(); }, onReconnect: () => void refresh() });
  const control = async (action: 'start' | 'pause') => {
    if (controlPending.current) return;
    controlPending.current = true;
    setControlBusy(true); setControlError(''); setControlMessage('');
    refreshSequence.current++;
    try {
      const result = await monitoringApi[action]();
      setControlMessage(action === 'start' ? `监测已启动，首轮立即进入执行队列；下次定时执行：${fmt(result.data.scheduledNextRun, result.data.timezone)}` : '监测已暂停，未完成轮次已取消。');
    } catch (err: unknown) {
      const payload = (err as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data;
      const detail = payload?.detail ?? payload?.message;
      setControlError(typeof detail === 'string' ? detail : '监测操作未完成，请刷新状态后重试。');
    } finally {
      await refresh();
      controlPending.current = false; setControlBusy(false);
    }
  };
  const selectedDate = day || data?.businessDate || '';
  const snapshotMatchesDate = !!data && data.businessDate === selectedDate;
  const reportKey = data ? `${selectedDate}:${reportKind}:${data.report.version}` : '';
  useEffect(() => {
    if (view !== 'report' || !data || !snapshotMatchesDate) return;
    let active = true;
    setReportError('');
    if (data.report.status !== 'updated') { setReport(null); return; }
    monitoringApi.report(selectedDate, reportKind).then(r => { if (active) setReport({ key: reportKey, content: r.data }); }).catch(() => { if (active) setReportError('报告读取失败，请刷新后重试。'); });
    return () => { active = false; };
  }, [view, reportKind, selectedDate, reportKey, snapshotMatchesDate, data?.report.status, reportRetry]);
  const currentReport = snapshotMatchesDate && report?.key === reportKey ? report.content : '';
  const changeDate = (value: string) => { setDay(value || dateInZone(data?.timezone || 'Asia/Shanghai')); setReportError(''); };
  const drill = (session: string, message: string) => navigate(`/sessions?session=${encodeURIComponent(session)}&focusMessage=${encodeURIComponent(message)}`);
  const download = () => {
    const url = URL.createObjectURL(new Blob([currentReport], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `安全运营监测-${reportKind === 'summary' ? '当日总结' : '执行时间线'}-${selectedDate}.md`; link.click(); URL.revokeObjectURL(url);
  };
  const downloadDiagnostics = async () => {
    if (exportPending.current) return;
    exportPending.current = true;
    setExportBusy(true); setExportError('');
    try {
      const response = await monitoringApi.diagnostics();
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url; link.download = `security-monitor-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setExportError('诊断日志导出失败，请稍后重试。');
    } finally {
      exportPending.current = false; setExportBusy(false);
    }
  };
  const active = data?.runs.find(r => r.status === 'running');
  const monitoringEnabled = data?.installation.installed && data.installation.status === 'active';
  const monitoringStatus = !data?.installation.installed ? '尚未安装' : !data.installation.ready ? '未就绪' : monitoringEnabled ? '已启动' : '已暂停';
  return <div className="flex h-full min-h-0 flex-col overflow-hidden bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
    <header className="shrink-0 border-b border-gray-200 bg-white px-4 pt-4 dark:border-gray-700 dark:bg-gray-900 sm:px-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-x-5 gap-y-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600 dark:bg-blue-950/50 dark:text-blue-300"><ShieldCheck size={23} /></div>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1"><h1 className="text-xl font-semibold tracking-tight">安全运营监测</h1><p className="text-sm text-gray-500 dark:text-gray-400">监测运营智能体 · 邮件协同处置 · 回查确认</p></div>
        </div>
        <div className="flex items-center gap-2"><button disabled={exportBusy} onClick={() => void downloadDiagnostics()} title="导出近期运行步骤、耗时与错误类型，不含凭据或事件正文" className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"><Download size={15} />{exportBusy ? '正在导出…' : '导出诊断日志'}</button><button onClick={() => { void refresh(); setReportRetry(value => value + 1); }} aria-label="刷新" className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><RefreshCw size={17} /></button></div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1"><nav className="flex shrink-0 gap-4 whitespace-nowrap sm:gap-6" aria-label="监测工作区">
        {([['session', '监测对话'], ['mail', '邮件跟进'], ['dashboard', '总结看板'], ['report', '每日报告']] as const).map(([id, label]) => <Link key={id} to={`${MONITOR_PATH}/${id}`} aria-current={view === id ? 'page' : undefined} className={`border-b-2 py-3 text-sm font-medium ${view === id ? 'border-blue-600 text-blue-600 dark:text-blue-400' : 'border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'}`}>{label}</Link>)}
      </nav>{view !== 'report' && view !== 'mail' && <input aria-label="业务日期" type="date" disabled={controlBusy} value={selectedDate} onChange={e => changeDate(e.target.value)} className="rounded-lg border border-gray-200 bg-transparent px-2 py-1 text-sm dark:border-gray-700" />}</div>
    </header>
    {error && <p role="alert" className="bg-amber-50 px-6 py-2 text-sm text-amber-800">{error}</p>}
    {exportError && <p role="alert" className="bg-amber-50 px-6 py-2 text-sm text-amber-800">{exportError}</p>}
    {data && <section aria-label="监测控制" className="mx-4 mt-3 shrink-0 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm dark:border-gray-700 dark:bg-gray-800 sm:mx-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm"><strong className="inline-flex items-center gap-2"><span aria-hidden="true" className={`h-2 w-2 rounded-full ${monitoringEnabled ? 'bg-emerald-500' : 'bg-gray-400'}`} />监测：{monitoringStatus}</strong><span className="inline-flex items-center gap-1.5 text-gray-500 dark:text-gray-400"><Clock3 size={14} />每 10 分钟触发</span><span className="text-gray-500 dark:text-gray-400">下次检查 {fmt(data.scheduledNextRun, data.timezone)}</span>{active && <span className="inline-flex items-center gap-1.5 text-blue-600"><Loader2 size={14} className="animate-spin" />调查执行中</span>}{data.queued.some(q => q.status === 'queued') && <span className="text-blue-600">一轮等待中</span>}</div>
        <button disabled={controlBusy || !data.installation.installed} onClick={() => void control(monitoringEnabled ? 'pause' : 'start')} className={`inline-flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50 ${monitoringEnabled ? 'border border-gray-200 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700' : 'bg-blue-600 text-white hover:bg-blue-700'}`}>
          {controlBusy ? <Loader2 size={15} className="animate-spin" /> : monitoringEnabled ? <Pause size={15} /> : <Play size={15} />}
          {controlBusy ? '正在处理…' : monitoringEnabled ? '暂停监测' : '检查接入并启动'}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-gray-500 dark:text-gray-400"><div className="flex flex-wrap items-center gap-x-4 gap-y-1"><span title={`同一监测串行执行；忙碌时多次触发合并为一轮。单轮上限 ${Math.round((data.roundTimeoutSeconds || 1200) / 60)} 分钟。`} className="inline-flex items-center gap-1.5"><ShieldCheck size={14} />智能体调查</span><span className="inline-flex items-center gap-1.5"><Mail size={14} />邮件跟进：{data.mail?.enabled ? '已启用' : '未启用'}</span><span>待处理回复 {data.mail?.pending || 0}</span><span>待确认 {data.mail?.needsReview || 0}</span></div><div className="flex items-center gap-4"><Link className="text-blue-600 hover:underline dark:text-blue-400" to={`${MONITOR_PATH}/mail`}>查看邮件记录</Link><button className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400" onClick={() => setShowSettings(true)}><Settings2 size={13} />配置</button></div></div>
      {controlError && <p role="alert" className="mt-2 text-sm text-red-600">{controlError}</p>}
      {controlMessage && <p role="status" className="mt-2 text-sm text-blue-600">{controlMessage}</p>}
    </section>}
    {!data ? <p className="p-6">正在加载监测事实…</p> : <>
      {!data.installation.installed || !data.installation.ready ? <div className="mx-6 mt-4 rounded-lg border border-amber-300 p-4 text-sm">{data.installation.installed ? '已安装但未就绪' : '尚未安装'}：{data.installation.reason}。<Link className="ml-2 text-blue-600" to="/scenes/suites?workspace=host-security-monitor">管理场景</Link></div> : null}
      {view === 'mail' ? <MailFollowup /> : view === 'session' ? <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-wrap justify-between gap-2 px-6 py-3 text-xs text-gray-500"><span>{data.businessDate} · {data.timezone} · {active ? '执行中' : monitoringEnabled ? '等待下一轮' : '监测未启动'}</span>{data.sessionID && <Link className="flex items-center gap-1 text-blue-600" to={`/sessions?session=${data.sessionID}`}>在工作台打开 <ArrowUpRight size={15} /></Link>}</div>
        {data.sessionID ? <SessionChat sessionId={data.sessionID} hideInput display={{ compact: false, showActions: false, showTimestamp: true, collapseIntermediateSteps: false, processGroupsDefaultOpen: true, processGroupsOpenWhileActive: true }} live className="min-h-0 flex-1" /> : <p className="p-8 text-gray-500">当天首次实际启动后，将在这里显示原生监测会话。</p>}
      </div> : <div className="min-h-0 flex-1 overflow-auto p-6">
        {view === 'report' ? <section aria-label="每日报告内容" className={`${card} mx-auto max-w-6xl`}>
          <div className="mb-5 flex flex-wrap items-center justify-between gap-4 border-b border-gray-100 pb-5 dark:border-gray-700"><div><h2 className="flex items-center gap-2 text-lg font-semibold"><CalendarDays size={19} className="text-blue-600" />每日报告</h2><p className="mt-1 text-sm text-gray-500">选择日期，查看当天执行时间线和告警总结。</p></div><div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-sm font-medium">报告日期<input aria-label="报告日期" type="date" value={selectedDate} max={dateInZone(data.timezone)} onChange={e => changeDate(e.target.value)} className="rounded-lg border border-gray-200 bg-transparent px-3 py-2 dark:border-gray-600" /></label><button onClick={() => changeDate('')} className="rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-700">今天</button><button onClick={() => changeDate(previousDay(dateInZone(data.timezone)))} className="rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-700">昨天</button></div></div>
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div className="inline-flex rounded-lg bg-gray-100 p-1 dark:bg-gray-900">{([['timeline', '执行时间线'], ['summary', '当日告警总结']] as const).map(([kind, label]) => <button key={kind} className={`rounded-md px-4 py-2 text-sm font-medium ${reportKind === kind ? 'bg-white text-blue-600 shadow-sm dark:bg-gray-700 dark:text-blue-300' : 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'}`} aria-pressed={reportKind === kind} onClick={() => setReportKind(kind)}>{label}</button>)}</div><button disabled={!currentReport || !!reportError || data.report.status !== 'updated'} onClick={download} className="inline-flex items-center gap-1.5 text-sm text-blue-600 disabled:opacity-40"><Download size={15} />下载 Markdown</button></div>
          <p className="mb-4 text-xs text-gray-500">{selectedDate} · {data.timezone} · {snapshotMatchesDate ? labels[data.report.status] || data.report.status : '正在加载'}</p>
          {!snapshotMatchesDate ? <p role="status" className="py-12 text-center text-sm text-gray-500">正在读取所选日期的报告…</p> : reportKind === 'timeline' ? <>
            {(reportError || data.report.error) && <p role="alert" className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">{reportError || data.report.error} 当前仍可查看已保存的执行记录。</p>}
            <RoundTimeline key={selectedDate} runs={data.runs} timezone={data.timezone} />
          </> : reportError || data.report.error ? <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/30">{reportError || data.report.error}</p> : data.report.status !== 'updated' ? <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center dark:border-gray-700"><CalendarDays size={28} className="mx-auto mb-3 text-gray-400" /><p className="font-medium">{data.runs.length === 0 ? '所选日期暂无监测记录' : '所选日期的报告尚未生成'}</p><p className="mt-2 text-sm text-gray-500">可选择其他日期查看执行时间线与当日告警总结。</p></div> : currentReport ? <div className="prose max-w-none dark:prose-invert"><StreamingMarkdown content={currentReport} isStreaming={false} /></div> : <p role="status" className="py-12 text-center text-sm text-gray-500">正在读取报告…</p>}
        </section> : <div className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{[['任务定义', data.metrics.definitions], ['实际启动轮次', data.metrics.started], ['去重事件', data.metrics.events], ['待处置风险', data.metrics.openRisk ?? data.metrics.risk], ['处置完成', data.metrics.closed ?? 0], ['已遏制', data.metrics.contained ?? 0], ['已忽略', data.metrics.ignored], ['待判定', data.metrics.unknown]].map(([title, value]) => <div className={card} key={title}><div className="text-sm text-gray-500">{title}</div><div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div></div>)}</div>
          <div className={`${card} flex flex-wrap items-center justify-between gap-4`}><div><h2 className="font-semibold">当天会话 · {active ? '执行中' : '空闲'} / {labels[data.installation.status] || data.installation.status}</h2><p className="mt-1 text-sm text-gray-500">当前步骤：{active?.steps.find(s => s.status === 'running')?.tool || '—'} · 下次调度：{fmt(data.nextRun, data.timezone)} · 尝试 {data.metrics.attempts} 次</p></div><Link to={`${MONITOR_PATH}/report`} className="text-sm text-blue-600">查看每日报告</Link></div>
          <div className="grid gap-5 lg:grid-cols-2"><div className={card}><h2 className="mb-4 font-semibold">执行趋势</h2><div className="flex h-20 items-end gap-1" aria-label="实际轮次状态">{data.runs.map(r => <button title={`${fmt(r.started_at, data.timezone)} ${labels[r.status]}`} key={r.id} onClick={() => drill(r.session_id, r.message_id)} className={`min-w-2 flex-1 rounded-t ${runStatus(r.status) === 'completed' ? 'h-14 bg-emerald-500' : runStatus(r.status) === 'failed' ? 'h-8 bg-red-400' : 'h-10 bg-blue-400'}`} />)}</div><p className="mt-2 text-xs text-gray-500">点击定位到本轮起始消息；轮次完成不代表告警已闭环。</p></div><div className={card}><h2 className="mb-3 font-semibold">事件风险分布</h2>{(['risk', 'unknown', 'ignored'] as const).map(key => <button key={key} onClick={() => setFilter(key)} className="mb-3 block w-full text-left text-sm"><span>{labels[key]} · {data.metrics[key]}</span><span className="mt-1 block h-2 rounded bg-gray-100 dark:bg-gray-700"><span className="block h-full rounded bg-blue-500" style={{ width: `${data.metrics.events ? 100 * data.metrics[key] / data.metrics.events : 0}%` }} /></span></button>)}</div></div>
          <div className={card}><h2 className="mb-3 font-semibold">轮次明细</h2><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['计划 / 实际开始', '状态', '本轮结果 / 当前步骤', '对话'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody>{data.runs.map(r => <tr key={r.id} className="border-t dark:border-gray-700"><td className="py-3">{fmt(r.scheduled_for, data.timezone)}<br />{fmt(r.started_at, data.timezone)}</td><td className="px-2"><RunBadge status={r.status} /></td><td className="max-w-xl py-3 pr-4 text-gray-600 dark:text-gray-300">{runDescription(r)}</td><td><button className="text-blue-600" onClick={() => drill(r.session_id, r.message_id)}>查看本轮</button></td></tr>)}</tbody></table></div>{data.runs.length === 0 && <p className="py-4 text-sm text-gray-500">当天尚无实际启动轮次。</p>}</div>
          <div className={card}><div className="mb-3 flex justify-between"><h2 className="font-semibold">事件明细 · 状态标记与回查</h2><select aria-label="风险筛选" value={filter} onChange={e => setFilter(e.target.value)} className="bg-transparent text-sm"><option value="all">全部事件</option>{['risk', 'unknown', 'ignored'].map(x => <option key={x} value={x}>{labels[x]}</option>)}</select></div><table className="w-full text-left text-sm"><thead><tr>{['事件 / 主机', '风险判定', '闭环', '关联'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody>{data.events.filter(e => filter === 'all' || e.risk === filter).map(e => <tr key={e.key} className="border-t dark:border-gray-700"><td className="py-3">{e.name}<div className="text-xs text-gray-500">{e.host || '主机未知'} · {e.device}</div></td><td title={e.reason}>{labels[e.risk]}</td><td className="py-3"><EventDisposition event={e} enabled={data.installation.installed && data.installation.ready} refresh={refresh} /></td><td><button className="text-blue-600" onClick={() => drill(e.sessionID, e.messageID)}>查看关联对话</button></td></tr>)}</tbody></table></div>
          {data.queued.length > 0 && <div className={card}><h2 className="font-semibold">调度、合并与缺失记录</h2>{data.queued.map(q => <p key={q.id} className="mt-2 text-sm text-gray-500">{fmt(q.scheduled_for, data.timezone)} · {q.slot_status === 'coalesced' ? '已合并到下一轮' : labels[q.status] || q.status} {q.error}</p>)}</div>}
        </div>}
      </div>}
    </>}
    {showSettings && <MailSettings close={() => setShowSettings(false)} refresh={refresh} />}
  </div>;
}
