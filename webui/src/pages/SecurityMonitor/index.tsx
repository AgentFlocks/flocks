import { StreamingMarkdown } from '@/components/common/StreamingMarkdown';
import { getApiBase } from '@/api/client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ShieldCheck, ArrowUpRight, RefreshCw, Play, Pause, Loader2 } from 'lucide-react';
import SessionChat from '@/components/common/SessionChat';
import PageHeader from '@/components/common/PageHeader';
import { monitoringApi, MONITOR_PATH, type MonitorSnapshot } from '@/api/securityMonitoring';
import { useSSE } from '@/hooks/useSSE';

const labels: Record<string, string> = { completed: '完成', partial: '部分完成', failed: '失败', interrupted: '已中断', running: '执行中', queued: '排队', cancelled: '已取消', risk: '风险', unknown: '待判定', ignored: '可忽略', pending: '待生成', updated: '已更新', active: '已启用', disabled: '已停用' };
const fmt = (value: string | null, tz: string) => value ? new Date(value).toLocaleString('zh-CN', { timeZone: tz, hour12: false }) : '—';
const card = 'rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-800';

export default function SecurityMonitor() {
  const location = useLocation(), navigate = useNavigate();
  const view = location.pathname.endsWith('/dashboard') ? 'dashboard' : location.pathname.endsWith('/report') ? 'report' : 'session';
  const [day, setDay] = useState('');
  const [data, setData] = useState<MonitorSnapshot | null>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');
  const [report, setReport] = useState('');
  const [controlBusy, setControlBusy] = useState(false);
  const [controlError, setControlError] = useState('');
  const [controlMessage, setControlMessage] = useState('');
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
      setControlMessage(action === 'start' ? `监测已启动，下次执行：${fmt(result.data.scheduledNextRun, result.data.timezone)}` : '监测已暂停，未完成轮次已取消。');
    } catch (err: unknown) {
      const payload = (err as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data;
      const detail = payload?.detail ?? payload?.message;
      setControlError(typeof detail === 'string' ? detail : '监测操作未完成，请刷新状态后重试。');
    } finally {
      await refresh();
      controlPending.current = false; setControlBusy(false);
    }
  };
  useEffect(() => {
    if (view !== 'report' || !data) return;
    let active = true;
    setReport('');
    monitoringApi.report(data.businessDate).then(r => { if (active) setReport(r.data); }).catch(() => { if (active) setReport('当日报告尚未生成，或导出失败。'); });
    return () => { active = false; };
  }, [view, data?.businessDate, data?.report.version]);
  const drill = (session: string, message: string) => navigate(`/sessions?session=${encodeURIComponent(session)}&focusMessage=${encodeURIComponent(message)}`);
  const download = () => {
    const url = URL.createObjectURL(new Blob([report], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `安全运营监测-${data?.businessDate}.md`; link.click(); URL.revokeObjectURL(url);
  };
  const active = data?.runs.find(r => r.status === 'running');
  const monitoringEnabled = data?.installation.installed && data.installation.status === 'active';
  const monitoringStatus = !data?.installation.installed ? '尚未安装' : !data.installation.ready ? '未就绪' : monitoringEnabled ? '已启动' : '已暂停';
  return <div className="flex h-full min-h-0 flex-col bg-gray-50 text-gray-900 dark:bg-gray-900 dark:text-gray-100">
    <div className="border-b border-gray-200 px-6 pt-5 dark:border-gray-700">
      <PageHeader title="安全运营监测" description="只读查询与关联分析 · 处置未启用" icon={<ShieldCheck size={24} />} action={<button onClick={() => void refresh()} aria-label="刷新" className="p-2"><RefreshCw size={18} /></button>} />
      <div className="flex items-center justify-between gap-4"><nav className="flex gap-6" aria-label="监测工作区">
        {([['session', '监测对话'], ['dashboard', '总结看板'], ['report', '累计报告']] as const).map(([id, label]) => <Link key={id} to={`${MONITOR_PATH}/${id}`} className={`border-b-2 py-3 text-sm ${view === id ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500'}`}>{label}</Link>)}
      </nav><input aria-label="业务日期" type="date" disabled={controlBusy} value={day || data?.businessDate || ''} onChange={e => setDay(e.target.value)} className="rounded border bg-transparent px-2 py-1 text-sm" /></div>
    </div>
    {error && <p role="alert" className="bg-amber-50 px-6 py-2 text-sm text-amber-800">{error}</p>}
    {data && <section aria-label="监测控制" className="mx-6 mt-4 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm"><p className="font-semibold">监测状态：{monitoringStatus}</p><p className="mt-1 text-gray-500">每 10 分钟执行 · 下次执行：{fmt(data.scheduledNextRun, data.timezone)}</p></div>
        <button disabled={controlBusy || !data.installation.installed} onClick={() => void control(monitoringEnabled ? 'pause' : 'start')} className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
          {controlBusy ? <Loader2 size={16} className="animate-spin" /> : monitoringEnabled ? <Pause size={16} /> : <Play size={16} />}
          {controlBusy ? '正在处理…' : monitoringEnabled ? '暂停监测' : '检查接入并启动'}
        </button>
      </div>
      <p className="mt-2 text-xs text-gray-500">启动前检查接入配置，连接与查询结果以实际监测为准。</p>
      {controlError && <p role="alert" className="mt-2 text-sm text-red-600">{controlError}</p>}
      {controlMessage && <p role="status" className="mt-2 text-sm text-blue-600">{controlMessage}</p>}
    </section>}
    {!data ? <p className="p-6">正在加载监测事实…</p> : <>
      {!data.installation.installed || !data.installation.ready ? <div className="mx-6 mt-4 rounded-lg border border-amber-300 p-4 text-sm">{data.installation.installed ? '已安装但未就绪' : '尚未安装'}：{data.installation.reason}。<Link className="ml-2 text-blue-600" to="/scenes/suites?workspace=host-security-monitor">管理场景</Link></div> : null}
      {view === 'session' ? <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex justify-between px-6 py-3 text-sm text-gray-500"><span>{data.businessDate} · {data.timezone} · {active ? '执行中' : monitoringEnabled ? '等待下一轮' : '监测未启动'} · 下次 {fmt(data.nextRun, data.timezone)}</span>{data.sessionID && <Link className="flex items-center gap-1 text-blue-600" to={`/sessions?session=${data.sessionID}`}>在工作台打开 <ArrowUpRight size={15} /></Link>}</div>
        {data.sessionID ? <SessionChat sessionId={data.sessionID} hideInput display={{ compact: false, showActions: false, showTimestamp: true, collapseIntermediateSteps: false, processGroupsDefaultOpen: true, processGroupsOpenWhileActive: true }} live className="min-h-0 flex-1" /> : <p className="p-8 text-gray-500">当天首次实际启动后，将在这里显示原生监测会话。</p>}
      </div> : <div className="min-h-0 flex-1 overflow-auto p-6">
        {view === 'report' ? <div className={card}><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">每日累计报告 · {labels[data.report.status] || data.report.status}</h2><button disabled={data.report.status !== 'updated'} onClick={download} className="text-sm text-blue-600 disabled:opacity-40">下载 Markdown</button></div>{data.report.error && <p role="alert">{data.report.error}</p>}<div className="prose max-w-none dark:prose-invert"><StreamingMarkdown content={report} isStreaming={false} /></div></div> : <div className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{[['任务定义', data.metrics.definitions], ['实际启动轮次', data.metrics.started], ['去重事件', data.metrics.events], ['待处置风险', data.metrics.risk], ['待判定', data.metrics.unknown]].map(([title, value]) => <div className={card} key={title}><div className="text-sm text-gray-500">{title}</div><div className="mt-2 text-3xl font-semibold tabular-nums">{value}</div></div>)}</div>
          <div className={`${card} flex flex-wrap items-center justify-between gap-4`}><div><h2 className="font-semibold">当天会话 · {active ? '执行中' : '空闲'} / {labels[data.installation.status] || data.installation.status}</h2><p className="mt-1 text-sm text-gray-500">当前步骤：{active?.steps.find(s => s.status === 'running')?.tool || '—'} · 下次调度：{fmt(data.nextRun, data.timezone)} · 尝试 {data.metrics.attempts} 次</p></div><Link to={`${MONITOR_PATH}/report`} className="text-sm text-blue-600">查看累计报告</Link></div>
          <div className="grid gap-5 lg:grid-cols-2"><div className={card}><h2 className="mb-4 font-semibold">执行趋势</h2><div className="flex h-20 items-end gap-1" aria-label="实际轮次状态">{data.runs.map(r => <button title={`${fmt(r.started_at, data.timezone)} ${labels[r.status]}`} key={r.id} onClick={() => drill(r.session_id, r.message_id)} className={`min-w-2 flex-1 rounded-t ${r.status === 'completed' ? 'h-14 bg-blue-500' : r.status === 'running' ? 'h-10 bg-blue-300' : 'h-8 bg-amber-400'}`} />)}</div><p className="mt-2 text-xs text-gray-500">点击轮次查看对话；仅展示实际尝试，缺失时隙不计成功。</p></div><div className={card}><h2 className="mb-3 font-semibold">事件风险分布</h2>{(['risk', 'unknown', 'ignored'] as const).map(key => <button key={key} onClick={() => setFilter(key)} className="mb-3 block w-full text-left text-sm"><span>{labels[key]} · {data.metrics[key]}</span><span className="mt-1 block h-2 rounded bg-gray-100 dark:bg-gray-700"><span className="block h-full rounded bg-blue-500" style={{ width: `${data.metrics.events ? 100 * data.metrics[key] / data.metrics.events : 0}%` }} /></span></button>)}</div></div>
          <div className={card}><h2 className="mb-3 font-semibold">轮次明细</h2><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{['计划 / 实际开始', '状态', '当前步骤', '对话'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody>{data.runs.map(r => <tr key={r.id} className="border-t dark:border-gray-700"><td className="py-3">{fmt(r.scheduled_for, data.timezone)}<br />{fmt(r.started_at, data.timezone)}</td><td>{labels[r.status] || r.status}</td><td>{r.steps.find(s => s.status === 'running')?.tool || r.error || '—'}</td><td><button className="text-blue-600" onClick={() => drill(r.session_id, r.message_id)}>查看本轮</button></td></tr>)}</tbody></table></div>{data.runs.length === 0 && <p className="py-4 text-sm text-gray-500">当天尚无实际启动轮次。</p>}</div>
          <div className={card}><div className="mb-3 flex justify-between"><h2 className="font-semibold">事件明细 · 处置未启用</h2><select aria-label="风险筛选" value={filter} onChange={e => setFilter(e.target.value)} className="bg-transparent text-sm"><option value="all">全部事件</option>{['risk', 'unknown', 'ignored'].map(x => <option key={x} value={x}>{labels[x]}</option>)}</select></div><table className="w-full text-left text-sm"><thead><tr>{['事件 / 主机', '风险判定', '闭环', '关联'].map(x => <th key={x} className="pb-3 font-medium text-gray-500">{x}</th>)}</tr></thead><tbody>{data.events.filter(e => filter === 'all' || e.risk === filter).map(e => <tr key={e.key} className="border-t dark:border-gray-700"><td className="py-3">{e.name}<div className="text-xs text-gray-500">{e.host || '主机未知'} · {e.device}</div></td><td title={e.reason}>{labels[e.risk]}</td><td>未闭环</td><td><button className="text-blue-600" onClick={() => drill(e.sessionID, e.messageID)}>查看关联对话</button></td></tr>)}</tbody></table></div>
          {data.queued.length > 0 && <div className={card}><h2 className="font-semibold">排队与缺失记录</h2>{data.queued.map(q => <p key={q.id} className="mt-2 text-sm text-gray-500">{fmt(q.scheduled_for, data.timezone)} · {labels[q.status] || q.status} {q.error}</p>)}</div>}
        </div>}
      </div>}
    </>}
  </div>;
}
