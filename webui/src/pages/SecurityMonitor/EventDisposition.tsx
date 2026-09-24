import { useRef, useState } from 'react';
import { monitoringApi, type MonitorEvent } from '@/api/securityMonitoring';

const states: Record<string, string> = { writing: '正在写回', pending: '结果待确认', mismatch: '尚未闭环', failed: '未执行', verified: '回查已确认' };
const xdrStates: Record<number, string> = { 0: '待处置', 10: '处置中', 30: '已防护', 40: '已处置', 50: '已挂起', 60: '忽略（接受风险）', 70: '已遏制' };

function requestId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // getRandomValues is also available on HTTP intranet deployments.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default function EventDisposition({ event, enabled, refresh }: { event: MonitorEvent; enabled: boolean; refresh: () => Promise<void> }) {
  const [dialog, setDialog] = useState(false);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const request = useRef('');
  const submittedComment = useRef<string | null>(null);
  const record = event.dispositionRecord;
  const uncertain = record && ['writing', 'pending'].includes(record.status);
  const execute = async (recheck: boolean) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      if (recheck && record) await monitoringApi.recheckDisposition(record.id);
      else {
        submittedComment.current ??= comment.trim();
        await monitoringApi.confirmDisposition({ request_id: request.current, event_key: event.key, comment: submittedComment.current, confirmed: true });
      }
      setDialog(false);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : '请求未完成，请刷新后回查状态；重试本次确认会沿用原请求。');
    } finally {
      await refresh(); pending.current = false; setBusy(false);
    }
  };
  return <div className="space-y-1">
    <p>{event.closure === 'closed' ? '已闭环（XDR 回查确认）' : event.closure === 'contained' ? '已遏制（仍需跟进）' : event.closure === 'ignored' ? '已忽略（XDR 回查确认）' : '未闭环'}</p>
      {record && <><p className="text-xs text-gray-500">{record.mode === 'automatic' ? '自动标记 · ' : ''}{states[record.status]}{record.target_status !== undefined ? ` · 目标：${xdrStates[record.target_status] || '未知状态'}` : ''}{record.observed_status !== null ? ` · XDR ${xdrStates[record.observed_status] || '未知状态'}（${record.observed_status}）` : ''}</p>
      {record.mode === 'automatic' && <p className="text-xs text-gray-500">{record.comment}</p>}
      {record.error && <p className="text-xs text-amber-700">{record.error}</p>}
      {record.session_id && <a className="block text-xs text-blue-600" href={`/sessions?session=${encodeURIComponent(record.session_id)}`}>处置记录</a>}
      <button disabled={busy || !enabled} className="mr-3 text-blue-600 disabled:opacity-40" onClick={() => void execute(true)}>回查状态</button></>}
    {event.automaticReason && <p className="text-xs text-gray-500">{event.automaticReason}</p>}
    {event.closure !== 'ignored' && event.closure !== 'closed' && !uncertain && <button disabled={busy || !enabled} className="text-blue-600 disabled:opacity-40" onClick={() => { request.current = requestId(); submittedComment.current = null; setComment(''); setError(''); setDialog(true); }}>确认已处置</button>}
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {dialog && <div role="dialog" aria-modal="true" aria-label="确认事件处置" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onKeyDown={e => {
      if (e.key === 'Escape' && !busy) setDialog(false);
      if (e.key === 'Tab') {
        const items = e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled)');
        const first = items[0], last = items[items.length - 1];
        if (!first || (e.shiftKey && document.activeElement === first) || (!e.shiftKey && document.activeElement === last)) {
          e.preventDefault(); (e.shiftKey ? last : first)?.focus();
        }
      }
    }}>
      <div className="w-full max-w-lg rounded-xl bg-white p-6 text-gray-900 shadow-xl dark:bg-gray-800 dark:text-gray-100">
        <h2 className="mb-3 text-lg font-semibold">确认事件处置</h2>
        <p>{event.name}</p><p className="mt-1 break-all text-xs text-gray-500">设备：{event.device} · 事件：{event.key}</p>
        <p className="my-3 text-sm">确认后将向 XDR 写入“已处置”（40）及以下说明，再回查确认闭环。请先完成实际处置；此操作不执行主机隔离或修复。</p>
        <label className="block text-sm">处置说明<textarea autoFocus disabled={busy || submittedComment.current !== null} maxLength={2048} value={comment} onChange={e => setComment(e.target.value)} className="mt-2 block w-full rounded border bg-transparent p-2" rows={4} /></label>
        <div className="mt-4 flex justify-end gap-3"><button disabled={busy} onClick={() => setDialog(false)}>取消</button><button disabled={busy || !enabled || !comment.trim()} onClick={() => void execute(false)} className="rounded bg-blue-600 px-3 py-2 text-white disabled:opacity-40">{busy ? '写回并回查中…' : '确认写回 XDR'}</button></div>
      </div>
    </div>}
  </div>;
}
