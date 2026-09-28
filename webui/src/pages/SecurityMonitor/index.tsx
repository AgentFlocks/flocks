import MailFollowup, { MailSettings, Sheet } from './MailFollowup';
import RoundTimeline from './RoundTimeline';
import { newestRunsFirst, roundBusinessConclusion, roundFactSummary, roundRecoverySummary } from './roundSummary';
import { StreamingMarkdown } from '@/components/common/StreamingMarkdown';
import { getApiBase } from '@/api/client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ShieldCheck, ArrowUpRight, RefreshCw, Play, Pause, Loader2, Download, CalendarDays, Clock3, Mail, Settings2, CheckCircle2, CircleX, MoreHorizontal, History, Activity } from 'lucide-react';
import EventDisposition from './EventDisposition';
import SessionChat from '@/components/common/SessionChat';
import { monitoringApi, MONITOR_PATH, type MonitorSnapshot, type MonitorRun } from '@/api/securityMonitoring';
import { useSSE } from '@/hooks/useSSE';

const labels: Record<string, string> = { completed: '完成', partial: '完成', failed: '失败', interrupted: '失败', running: '执行中', queued: '排队', cancelled: '失败', risk: '风险', unknown: '待判定', ignored: '可忽略', pending: '待生成', updated: '已更新', active: '已启用', disabled: '已停用' };
const fmt = (value: string | null, tz: string) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: tz, hour12: false }) : '—';
const card = 'rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800';
const runStatus = (status: string) => status === 'partial' ? 'completed' : ['interrupted', 'cancelled'].includes(status) ? 'failed' : status;
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
  const [chatNotice, setChatNotice] = useState('');
  const [filter, setFilter] = useState('all');
  const [report, setReport] = useState<{ key: string; content: string } | null>(null);
  const [reportError, setReportError] = useState('');
  const [reportRetry, setReportRetry] = useState(0);
  const [reportKind, setReportKind] = useState<'timeline' | 'summary'>('timeline');
  const [showSettings, setShowSettings] = useState(false);
  const [drawer, setDrawer] = useState<'history' | 'health' | null>(null);
  const moreRef = useRef<HTMLDetailsElement>(null);
  const closeMore = () => { if (moreRef.current) moreRef.current.open = false; };
  useEffect(() => {
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !moreRef.current?.contains(event.target as Node)) closeMore();
    };
    document.addEventListener('click', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('click', close); document.removeEventListener('keydown', close); };
  }, []);
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
  useSSE({ url: `${getApiBase()}/api/event`, onEvent: event => { if (event.type === 'monitor.execution.started' || event.type === 'monitor.execution.finished' || event.type === 'task.updated' || event.type === 'monitor.control.changed') void refresh(); }, onReconnect: () => void refresh() });
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
  const changeDate = (value: string) => { setDay(value || dateInZone(data?.timezone || 'Asia/Shanghai'));  setReportError(''); };
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
  const active = data?.currentRun ? { ...data.currentRun, steps: data.runs.find(run => run.id === data.currentRun?.id)?.steps || [] } : data?.currentRun === undefined && (!day || day === dateInZone(data?.timezone || 'Asia/Shanghai')) ? data?.runs.find(r => r.status === 'running') : undefined;
  const monitoringEnabled = data?.installation.installed && data.installation.status === 'active';
  const monitoringStatus = !data?.installation.installed ? '尚未安装' : !data.installation.ready ? '未就绪' : monitoringEnabled ? '已启动' : '已暂停';
  const latestRun = data?.runs.reduce<MonitorRun | undefined>((latest, run) => !latest || run.started_at > latest.started_at ? run : latest, undefined);
  const workbenchRun = data?.runs.find(run => run.status === 'running') || latestRun;
  const workbenchLink = data?.sessionID ? `/sessions?session=${encodeURIComponent(data.sessionID)}${workbenchRun?.message_id ? `&focusMessage=${encodeURIComponent(workbenchRun.message_id)}` : ''}` : null;
  const historySelected = !!day && day !== dateInZone(data?.timezone || 'Asia/Shanghai');
  const orderedRuns = newestRunsFirst(data?.runs || []);
  const healthErrors = data?.mail?.enabled ? data.mail.health?.errors || [] : [];
  const menuAction = 'flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm text-slate-700 hover:bg-sky-50 focus-visible:bg-sky-50 dark:text-slate-200 dark:hover:bg-sky-950 dark:focus-visible:bg-sky-950';
  return <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#f7fbfd] text-[#17212b] dark:bg-slate-950 dark:text-slate-100" data-testid="monitor-primary-page">
    <header className="z-20 shrink-0 border-b border-sky-100 bg-white/95 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950 sm:px-5">
      <section aria-label="监测控制" className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 sm:flex sm:flex-wrap">
        <h1 className="flex min-w-0 items-center gap-2 text-sm font-semibold tracking-tight sm:mr-auto sm:text-base"><ShieldCheck size={20} className="shrink-0 text-[#168a5b] dark:text-emerald-400" /><span className="truncate">安全运营监测</span></h1>
        <div className="col-span-2 col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:order-none sm:gap-x-4">
          <span className="inline-flex items-center gap-1.5 font-medium" title="监测启用状态与当前轮次状态分别展示"><span aria-hidden="true" className={`h-2 w-2 rounded-full ${monitoringEnabled ? 'bg-[#168a5b]' : 'bg-slate-400'}`} />监测：{data ? monitoringStatus : '读取中'}</span>
          {monitoringEnabled && <span className={`inline-flex items-center gap-1 ${active ? 'text-sky-700 dark:text-sky-300' : 'text-slate-500 dark:text-slate-400'}`}>{active && <Loader2 size={12} className="animate-spin motion-reduce:animate-none" />}{active ? '本轮执行中' : '等待下一轮'}</span>}
          <span className="inline-flex items-center gap-1 text-slate-500 dark:text-slate-400" title={data ? fmt(data?.scheduledNextRun || null, data.timezone) : undefined}><Clock3 size={12} />{active && monitoringEnabled ? '下次：本轮结束后等待调度' : `下次监测 ${monitoringEnabled && data?.scheduledNextRun ? new Date(data.scheduledNextRun).toLocaleTimeString('zh-CN', { timeZone: data.timezone, hour12: false }) : '—'}`}</span>
        </div>
        <div className="col-start-2 row-start-1 flex shrink-0 items-center gap-1.5 sm:ml-2">
          <button disabled={controlBusy || !data?.installation.installed} onClick={() => void control(monitoringEnabled ? 'pause' : 'start')} className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-50 ${monitoringEnabled ? 'border border-sky-200 bg-sky-50 text-slate-700 hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100' : 'bg-[#168a5b] text-white hover:bg-emerald-700'}`}>
            {controlBusy ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : monitoringEnabled ? <Pause size={14} /> : <Play size={14} />}{controlBusy ? '正在处理…' : monitoringEnabled ? '暂停监测' : '启动监测'}
          </button>
          <details ref={moreRef} className="relative">
            <summary aria-label="更多监测功能" className="flex cursor-pointer list-none items-center rounded-lg p-2 text-slate-500 hover:bg-sky-50 focus-visible:outline focus-visible:outline-sky-500 dark:hover:bg-slate-800 [&::-webkit-details-marker]:hidden"><MoreHorizontal size={20} /></summary>
            <div className="absolute right-0 top-full z-30 mt-2 max-h-[75vh] w-64 overflow-auto rounded-xl border border-sky-100 bg-white p-2 shadow-xl dark:border-slate-700 dark:bg-slate-900">
              <nav aria-label="监测工作区">{([['session', '监测对话'], ['mail', '邮件跟进'], ['dashboard', '总结看板'], ['report', '每日报告']] as const).map(([id, label]) => <Link key={id} to={`${MONITOR_PATH}/${id}`} onClick={closeMore} aria-current={view === id ? 'page' : undefined} className={menuAction}>{label}{view === id && <CheckCircle2 size={13} className="ml-auto text-emerald-600" />}</Link>)}</nav>
              <div className="my-1 border-t border-sky-100 dark:border-slate-700" />
              {workbenchLink && <Link to={workbenchLink} onClick={closeMore} className={menuAction}><ArrowUpRight size={15} />在工作台打开</Link>}
              <button className={menuAction} onClick={() => { closeMore(); setDrawer('history'); }}><History size={15} />查看历史日期</button>
              <button className={menuAction} onClick={() => { closeMore(); setShowSettings(true); }}><Settings2 size={15} />邮件配置</button>
              <button className={menuAction} onClick={() => { closeMore(); navigate(`${MONITOR_PATH}/dashboard`); setDrawer('health'); }}><Activity size={15} />运行与邮件健康</button>
              <button disabled={exportBusy} onClick={() => void downloadDiagnostics()} className={`${menuAction} disabled:opacity-50`}><Download size={15} />{exportBusy ? '正在导出…' : '导出诊断日志'}</button>
              <button onClick={() => { closeMore(); void refresh(); setReportRetry(value => value + 1); }} className={menuAction}><RefreshCw size={15} />刷新</button>
            </div>
          </details>
        </div>
      </section>
    </header>
    {view === 'dashboard' && chatNotice && <p role="alert" className="shrink-0 bg-sky-50 px-5 py-2 text-sm dark:bg-sky-950">{chatNotice}</p>}
    {view === 'dashboard' && error && <p role="alert" className="shrink-0 border-b border-slate-300 bg-white px-5 py-2 text-sm font-medium dark:bg-slate-900">{error}</p>}
    {view === 'dashboard' && exportError && <p role="alert" className="shrink-0 bg-sky-50 px-5 py-2 text-sm dark:bg-sky-950">{exportError}</p>}
    {view === 'dashboard' && controlError && <p role="alert" className="shrink-0 border-l-4 border-slate-600 bg-white px-5 py-2 text-sm dark:bg-slate-900">{controlError}</p>}
    {view === 'dashboard' && controlMessage && <p role="status" className="sr-only">{controlMessage}</p>}
    {view === 'dashboard' && healthErrors.length > 0 && <div role="alert" className="flex shrink-0 items-center justify-between gap-3 border-b border-sky-200 bg-sky-50 px-5 py-2 text-xs dark:border-sky-900 dark:bg-sky-950"><span className="flex min-w-0 items-center gap-2"><CircleX size={14} className="shrink-0" /><span className="truncate">邮件通道异常：{healthErrors.join('；')}</span></span><button className="shrink-0 font-semibold underline" onClick={() => setDrawer('health')}>查看原因</button></div>}
    {historySelected && view === 'session' && <div className="flex shrink-0 items-center justify-between border-b border-sky-100 bg-sky-50 px-5 py-1.5 text-xs dark:border-sky-900 dark:bg-sky-950"><span>历史会话 · {day}</span><button className="font-medium underline" onClick={() => setDay('')}>回到今天</button></div>}
    {!data ? <p className="p-6">正在加载监测事实…</p> : <>
      {view === 'dashboard' && (!data.installation.installed || !data.installation.ready) ? <div className="mx-6 mt-4 rounded-lg border border-amber-300 p-4 text-sm">{data.installation.installed ? '已安装但未就绪' : '尚未安装'}：{data.installation.reason}。<Link className="ml-2 text-blue-600" to="/scenes/suites?workspace=host-security-monitor">管理场景</Link></div> : null}
      {view === 'mail' ? <MailFollowup /> : view === 'session' ? <div className="flex min-h-0 flex-1 flex-col" aria-label="监测动态对话">
        {data.sessionID ? <SessionChat sessionId={data.sessionID} hideInput monitoring={{ running: !!active, paused: !monitoringEnabled, historical: historySelected, hidePageNotices: true, onPageNotice: setChatNotice }} display={{ compact: false, showActions: false, showTimestamp: true, collapseIntermediateSteps: false, processGroupsDefaultOpen: true, processGroupsOpenWhileActive: true }} live className="min-h-0 flex-1" /> : <div className="m-auto max-w-sm px-6 py-12 text-center"><ShieldCheck size={32} className="mx-auto mb-4 text-[#168a5b]" /><h2 className="font-semibold">{historySelected ? '所选日期暂无监测对话' : '监测对话已准备好'}</h2><p className="mt-2 text-sm leading-6 text-slate-500">{historySelected ? '可以切换历史日期，或回到今天。' : '启动后，查询、调查、邮件跟进和本轮总结会在这里动态展开。'}</p></div>}
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
          <section aria-label="运行提示与健康" className={`${card} flex flex-wrap items-center justify-between gap-3`}><div><h2 className="font-semibold">运行提示与健康</h2><p className="mt-1 text-sm text-slate-500">监测操作与邮件通道提示集中在本看板展示。</p></div><button className="text-sm font-medium text-sky-700 dark:text-sky-300" onClick={() => setDrawer('health')}>查看运行与邮件健康</button></section>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{[['任务定义', data.metrics.definitions], ['实际启动轮次', data.metrics.started], ['去重事件', data.metrics.events], ['待处置风险', data.metrics.openRisk ?? data.metrics.risk], ['处置完成', data.metrics.closed ?? 0], ['已遏制', data.metrics.contained ?? 0], ['已忽略', data.metrics.ignored], ['待判定', data.metrics.unknown]].map(([title, value]) => <div className={card} key={title}><div className="text-sm text-gray-500">{title}</div><div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div></div>)}</div>
          <div className={`${card} flex flex-wrap items-center justify-between gap-4`}><div><h2 className="font-semibold">当天会话 · {active ? '执行中' : '空闲'} / {labels[data.installation.status] || data.installation.status}</h2><p className="mt-1 text-sm text-gray-500">当前步骤：{active?.steps.find(s => s.status === 'running')?.tool || '—'} · 下次调度：{fmt(data.nextRun, data.timezone)} · 尝试 {data.metrics.attempts} 次</p></div><Link to={`${MONITOR_PATH}/report`} className="text-sm text-blue-600">查看每日报告</Link></div>
          <div className="grid gap-5 lg:grid-cols-2"><div className={card}><h2 className="mb-4 font-semibold">执行趋势</h2><div className="flex h-20 items-end gap-1" aria-label="实际轮次状态">{orderedRuns.map(r => <button title={`${fmt(r.started_at, data.timezone)} ${labels[r.status]}`} key={r.id} onClick={() => drill(r.session_id, r.message_id)} className={`min-w-2 flex-1 rounded-t ${runStatus(r.status) === 'completed' ? 'h-14 bg-emerald-500' : runStatus(r.status) === 'failed' ? 'h-8 bg-red-400' : 'h-10 bg-blue-400'}`} />)}</div><p className="mt-2 text-xs text-gray-500">点击定位到本轮起始消息；轮次完成不代表告警已闭环。</p></div><div className={card}><h2 className="mb-3 font-semibold">事件风险分布</h2>{(['risk', 'unknown', 'ignored'] as const).map(key => <button key={key} onClick={() => setFilter(key)} className="mb-3 block w-full text-left text-sm"><span>{labels[key]} · {data.metrics[key]}</span><span className="mt-1 block h-2 rounded bg-gray-100 dark:bg-gray-700"><span className="block h-full rounded bg-blue-500" style={{ width: `${data.metrics.events ? 100 * data.metrics[key] / data.metrics.events : 0}%` }} /></span></button>)}</div></div>
          <div className={card}><h2 className="mb-3 font-semibold">轮次明细</h2><div className="overflow-x-auto"><table className="block w-full text-left text-sm md:table"><thead className="hidden md:table-header-group"><tr>{['计划 / 实际开始', '状态', '本轮结果 / 当前步骤', '对话'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody className="block md:table-row-group">{orderedRuns.map(r => <tr key={r.id} className="block border-t py-3 dark:border-gray-700 md:table-row md:py-0"><td className="block whitespace-nowrap py-2 pr-4 text-xs md:table-cell md:py-3 md:text-sm"><span className="mb-1 block text-slate-500 md:hidden">计划 / 实际开始</span>{fmt(r.scheduled_for, data.timezone)}<br />{fmt(r.started_at, data.timezone)}</td><td className="block py-1 md:table-cell md:px-2"><RunBadge status={r.status} /></td><td className="block w-full break-words py-2 md:table-cell md:min-w-64 md:max-w-xl md:py-3 md:pr-4">
              <p className="font-medium text-slate-800 dark:text-slate-100">{roundBusinessConclusion(r)}</p>
              {roundFactSummary(r) && <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{roundFactSummary(r)}</p>}
              {roundRecoverySummary(r, data.timezone) && <p className="mt-1 text-xs leading-5 text-sky-700 dark:text-sky-300">{roundRecoverySummary(r, data.timezone)}</p>}
              {(r.summary || r.error || r.next_step) && <details className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                <summary className="w-fit cursor-pointer text-sky-700 dark:text-sky-300">展开本轮详情</summary>
                <div className="mt-2 space-y-2 whitespace-pre-wrap break-words rounded-lg border border-sky-100 bg-sky-50/50 p-3 leading-6 dark:border-sky-900 dark:bg-sky-950/30">
                  {r.error && <p><strong>记录原因：</strong>{r.error}</p>}
                  {r.summary && <p>{r.summary}</p>}
                  {r.next_step && <p><strong>下一步：</strong>{r.next_step}</p>}
                </div>
              </details>}
            </td><td className="block py-1 md:table-cell"><button className="whitespace-nowrap text-blue-600" onClick={() => drill(r.session_id, r.message_id)}>查看本轮</button></td></tr>)}</tbody></table></div>{data.runs.length === 0 && <p className="py-4 text-sm text-gray-500">当天尚无实际启动轮次。</p>}</div>
          <div className={card}><div className="mb-3 flex justify-between"><h2 className="font-semibold">事件明细 · 状态标记与回查</h2><select aria-label="风险筛选" value={filter} onChange={e => setFilter(e.target.value)} className="bg-transparent text-sm"><option value="all">全部事件</option>{['risk', 'unknown', 'ignored'].map(x => <option key={x} value={x}>{labels[x]}</option>)}</select></div><table className="w-full text-left text-sm"><thead><tr>{['事件 / 主机', '风险判定', '闭环', '关联'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody>{data.events.filter(e => filter === 'all' || e.risk === filter).map(e => <tr key={e.key} className="border-t dark:border-gray-700"><td className="py-3">{e.name}<div className="text-xs text-gray-500">{e.host || '主机未知'} · {e.device}</div></td><td title={e.reason}>{labels[e.risk]}</td><td className="py-3"><EventDisposition event={e} enabled={data.installation.installed && data.installation.ready} refresh={refresh} /></td><td><button className="text-blue-600" onClick={() => drill(e.sessionID, e.messageID)}>查看关联对话</button></td></tr>)}</tbody></table></div>
          {data.queued.length > 0 && <div className={card}><h2 className="font-semibold">调度、合并与缺失记录</h2>{data.queued.map(q => <p key={q.id} className="mt-2 text-sm text-gray-500">{fmt(q.scheduled_for, data.timezone)} · {q.slot_status === 'coalesced' ? '已合并到下一轮' : labels[q.status] || q.status} {q.error}</p>)}</div>}
        </div>}
      </div>}
    </>}
    {drawer && (drawer === 'history' || view === 'dashboard') && <Sheet title={drawer === 'history' ? '历史监测记录' : '运行与邮件健康'} close={() => setDrawer(null)}>
      {drawer === 'history' ? <div className="space-y-4"><p className="text-sm text-slate-500">按监测业务时区选择日期。后台的新轮次不会改变正在查看的历史记录。</p><label className="block text-sm font-medium">业务日期<input aria-label="业务日期" type="date" disabled={controlBusy} value={selectedDate} max={dateInZone(data?.timezone || 'Asia/Shanghai')} onChange={e => changeDate(e.target.value)} className="mt-2 block w-full rounded-lg border border-sky-200 bg-transparent px-3 py-2 dark:border-sky-900" /></label><button className={menuAction} onClick={() => { setDay(''); setDrawer(null); }}>回到今天</button></div> : <div className="space-y-5 text-sm"><div><p className="font-semibold">智能体调查</p><p className="mt-2 text-slate-500">每 10 分钟触发，同一监测串行执行；单轮上限 {Math.round((data?.roundTimeoutSeconds || 1200) / 60)} 分钟。忙碌时多次触发合并为一轮。</p></div>{data?.investigation && <div><p className="font-semibold">调查待办</p><p className="mt-2 leading-7 text-slate-500">待调查 {data.investigation.pending} · 延后续查 {data.investigation.deferred} · 等待系统恢复 {data.investigation.system_wait} · 待人工确认 {data.investigation.needs_review}</p>{data.investigation.earliest_retry_at && <p className="mt-1 text-xs text-slate-500">最早重试：{fmt(data.investigation.earliest_retry_at, data.timezone)}</p>}</div>}{data?.metrics.investigatedEvents !== undefined && <div><p className="font-semibold">有事件调查完成情况</p><p className="mt-2 text-slate-500">{data.metrics.investigationCompletedEvents || 0} / {data.metrics.investigatedEvents} 条 · {data.metrics.investigationCompletionRate == null ? '暂无有事件调查' : `${Math.round(data.metrics.investigationCompletionRate * 100)}%`}</p></div>}<div><p className="font-semibold">邮件跟进：{data?.mail?.enabled ? '已启用' : '未启用'}</p><p className="mt-2 text-slate-500">待处理回复 {data?.mail?.pending || 0} · 待确认 {data?.mail?.needsReview || 0}</p></div>{(['receive', 'send'] as const).map(direction => { const health = data?.mail?.health?.[direction]; const recovered = health?.state === 'healthy' && !!health.last_error_at && !!health.last_success_at && Date.parse(health.last_success_at) > Date.parse(health.last_error_at); return <div key={direction} className="rounded-xl border border-sky-100 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950"><h3 className="font-semibold">{direction === 'receive' ? '收信连接' : '发信连接'} · {({ healthy: '正常', unavailable: '不可用', unknown: '尚未确认', disabled: '未启用' } as Record<string, string>)[health?.state || 'unknown']}{recovered ? '（已恢复）' : ''}</h3><dl className="mt-3 space-y-2 text-xs"><div>{direction === 'send' && health?.last_success_stage === 'probe' ? '连接检查成功' : '最近成功'}：{fmt(health?.last_success_at || null, data?.timezone || 'Asia/Shanghai')}</div>{health?.last_error_at && <div>{recovered ? '上次异常（已恢复）' : '最近失败'}：{fmt(health.last_error_at, data?.timezone || 'Asia/Shanghai')} · {health.last_error_stage || health.stage || '阶段未知'} · {health.error_type || '原因未知'}</div>}{!!health?.consecutive_failures && <div>连续失败：{health.consecutive_failures} 次</div>}{health?.state !== 'healthy' && health?.next_retry_at && <div>下次重试：{fmt(health.next_retry_at, data?.timezone || 'Asia/Shanghai')}</div>}</dl></div>; })}{healthErrors.map((message, index) => <p key={index} className="font-medium">{message}</p>)}<Link className={menuAction} onClick={() => setDrawer(null)} to={`${MONITOR_PATH}/mail`}><Mail size={15} />查看邮件记录</Link></div>}
    </Sheet>}
    {showSettings && <MailSettings close={() => setShowSettings(false)} refresh={refresh} />}
  </div>;
}
