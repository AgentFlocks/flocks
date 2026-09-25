import { useCallback, useEffect, useState, useRef } from 'react';
import { monitoringApi, type MailHistory } from '@/api/securityMonitoring';

const states: Record<string, string> = { queued: '等待发送', sending: '正在发送', sent: '已发送', send_unknown: '发送结果待确认', skipped: '不发送（见原因）', pending: '已收到，待下轮处理', interpreted: '已解读，等待标记或回查', needs_review: '待人工确认', verified: '目标状态已回查确认', mismatch: '回查不一致', failed: '未执行', unrelated: '已转回普通邮件会话', forwarding: '正在转交普通会话' };
const label = (s: string) => states[s] || s;
const date = (s: string) => new Date(s).toLocaleString('zh-CN', { hour12: false });
const button = 'rounded border px-3 py-1.5 text-sm disabled:opacity-40';
function DevelopmentMailNotice() {
  return <p className="text-sm text-amber-700">开发联调模式：暂不要求回信身份认证，仅核对责任人邮箱和告警关联。存在伪造回信触发状态标记的风险，正式使用前需恢复认证。</p>;
}

export function MailSettings({ close, refresh }: { close: () => void; refresh: () => Promise<void> }) {
  const [form, setForm] = useState<MailHistory['settings'] | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [verificationRequired, setVerificationRequired] = useState(true);
  useEffect(() => { let live = true; monitoringApi.mail().then(r => { if (live) { setForm({ enabled: !!r.data.settings.enabled, recipient_email: r.data.settings.recipient_email, responsible_name: r.data.settings.responsible_name }); setVerificationRequired(r.data.sender_verification_required !== false); } }).catch(() => { if (live) setError('配置读取失败'); }); return () => { live = false; }; }, []);
  async function save() {
    if (!form || busy) return;
    setBusy(true); setError('');
    try { await monitoringApi.saveMail(form); await refresh(); close(); }
    catch (e: unknown) {
      const data = (e as { response?: { data?: { message?: unknown; detail?: unknown } } })?.response?.data;
      const message = typeof data?.message === 'string' ? data.message : data?.detail;
      setError(typeof message === 'string' && message.trim() ? message : '邮件配置保存失败');
    }
    finally { setBusy(false); }
  }
  return <div className="fixed inset-0 z-50 flex justify-end bg-black/20" onClick={close}>
    <section role="dialog" aria-modal="true" aria-label="邮件跟进配置" className="h-full w-full max-w-md overflow-auto bg-white p-6 shadow-xl dark:bg-gray-900" onClick={e => e.stopPropagation()}>
      <div className="mb-6 flex justify-between"><h2 className="font-semibold">邮件跟进配置</h2><button onClick={close} aria-label="关闭配置">关闭</button></div>
      {form && <form onSubmit={e => { e.preventDefault(); void save(); }} className="space-y-5">
        <label className="block text-sm">责任人名称（可选）<input className="mt-2 w-full rounded border bg-transparent p-2" value={form.responsible_name} onChange={e => setForm({ ...form, responsible_name: e.target.value })} maxLength={80} /></label>
        <label className="block text-sm">责任人邮箱<input required={form.enabled} type="email" className="mt-2 w-full rounded border bg-transparent p-2" value={form.recipient_email} onChange={e => setForm({ ...form, recipient_email: e.target.value })} /></label>
        <label className="flex gap-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />启用邮件通知及回信处置</label>
        <p className="text-sm text-gray-500">复用 Flocks 已连接的邮件通道和已配置模型。一条告警一封通知；回信先保存，下一轮理解并核对状态。暂停监测后停止自动发信和标记。</p>
        <p className="text-xs text-gray-500">启用时检查通道连接和责任人收件范围。更换邮箱后，旧通知的回复保留待人工核对。邮件通道自身的访问控制仍然有效。</p>
        {!verificationRequired && <DevelopmentMailNotice />}
        <button disabled={busy} className={button} type="submit">{busy ? '正在保存…' : '保存配置'}</button>
      </form>}
      {error && <p role="alert" className="mt-4 text-sm text-red-600">{error}</p>}
    </section>
  </div>;
}

export default function MailFollowup() {
  const [data, setData] = useState<MailHistory | null>(null), [error, setError] = useState('');
  const [offset, setOffset] = useState(0), [tab, setTab] = useState<'sent' | 'received'>('sent');
  const sequence = useRef(0);
  const refresh = useCallback(async () => { const id = ++sequence.current; try { const r = await monitoringApi.mail(offset); if (id === sequence.current) { setData(r.data); setError(''); } } catch { if (id === sequence.current) setError('邮件记录读取失败，当前内容可能已过期'); } }, [offset]);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 10000); return () => { window.clearInterval(timer); sequence.current++; }; }, [refresh]);
  return <div className="min-h-0 flex-1 overflow-auto p-6">
    <div className="mb-4 flex items-center gap-3"><h2 className="font-semibold">邮件跟进</h2><button className={button} onClick={() => void refresh()}>刷新记录</button></div>
    <p className="mb-4 text-sm text-gray-500">跨日期查看当前项目的通知和回信。每条告警单独通知，邮件已发送或收到回复均不代表处置完成。</p>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {data && <>
      {data.sender_verification_required === false && <div className="mb-4"><DevelopmentMailNotice /></div>}
      {!!data.unparsed_count && <p role="alert" className="mb-4 text-sm text-amber-700">当前邮件通道有 {data.unparsed_count} 封邮件未能解析，原信保留在邮箱中，请核对并导出诊断日志。其他回信继续处理。</p>}
      <div className="mb-4 flex flex-wrap gap-4 text-sm"><span>已发送 {data.counts.sent || 0}</span><span>待发送 {data.counts.queued || 0}</span><span>发件待确认 {data.counts.send_unknown || 0}</span><span>待处理回复 {(data.reply_counts.pending || 0) + (data.reply_counts.interpreted || 0)}</span><span>待人工确认 {data.reply_counts.needs_review || 0}</span></div>
      <div className="mb-4 flex gap-3" role="tablist" aria-label="邮件记录类型"><button role="tab" aria-selected={tab === 'sent'} className={button} onClick={() => setTab('sent')}>发信记录</button><button role="tab" aria-selected={tab === 'received'} className={button} onClick={() => setTab('received')}>回信记录</button></div>
      <div className="space-y-3">{tab === 'sent' ? data.notices.map(n => <details key={n.id} className="rounded-lg border bg-white p-4 dark:bg-gray-800">
        <summary className="cursor-pointer text-sm"><strong>{n.event.name}</strong> · {n.event.id} · {n.recipient} · {label(n.state)}{n.items.length > 0 && ` · ${label(n.items[n.items.length - 1].state)}`}<span className="ml-3 text-gray-500">{date(n.created_at)}</span></summary>
        <p className="mt-3 text-sm">主机：{n.event.host || '未知'} · 邮件主题：{n.subject || '等待生成'}</p>
        <pre className="mt-3 whitespace-pre-wrap break-words text-sm">{n.body}</pre>{n.error && <p className="mt-2 text-amber-700">{n.error}</p>}
        <h3 className="mt-4 text-sm font-semibold">回信与状态跟进</h3>
        {n.items.length === 0 ? <p className="text-sm text-gray-500">尚无已关联的处理反馈；未定位回信请查看回信记录。</p> : n.items.map(i => <div key={i.id} className="mt-2 border-t pt-2 text-sm"><p>{label(i.state)} · {i.reason}</p>{i.error && <p>{i.error}</p>}<p className="text-gray-500">目标：{({ 10: '处置中', 70: '已遏制', 40: '处置完成', 60: '忽略' } as Record<number, string>)[i.target]}</p>{i.reply_excerpt && <pre className="mt-2 whitespace-pre-wrap break-words">回信摘录：{i.reply_excerpt}</pre>}</div>)}
      </details>) : data.replies.map(r => <details key={r.id} className="rounded-lg border bg-white p-4 dark:bg-gray-800"><summary className="cursor-pointer text-sm"><strong>{r.payload.subject || '无主题'}</strong> · {r.sender} · {label(r.state)} · {date(r.received_at)}</summary><pre className="mt-3 whitespace-pre-wrap break-words text-sm">{r.payload.text}</pre>{r.payload.sender_verification_bypassed && <p className="mt-2 text-sm text-amber-700">此回信未验证发件人身份，按开发联调规则处理。</p>}{r.targets?.map(t => <p key={t.event_id} className="mt-2 text-sm">告警：{t.name} · {t.event_id} · {label(t.state)}</p>)}{r.error && <p className="mt-2 text-amber-700">{r.error}</p>}{r.result?.items?.map((i, index) => <p key={index} className="mt-2 border-t pt-2 text-sm">解读：{i.reason} · 依据：{i.evidence} · 通知：{i.notice_id}</p>)}</details>)}</div>
      {(tab === 'sent' ? data.notices : data.replies).length === 0 && <p className="py-6 text-gray-500">暂无{tab === 'sent' ? '发信' : '回信'}记录。</p>}
      <div className="mt-5 flex gap-3"><button className={button} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>上一页</button><span className="py-1.5 text-sm">第 {offset / 100 + 1} 页</span><button className={button} disabled={!data.has_more} onClick={() => setOffset(offset + 100)}>下一页</button></div>
    </>}
  </div>;
}
