import { useRef, useState } from 'react';
import { monitoringApi } from '@/api/securityMonitoring';

export default function AutomaticStatusControl({ enabled, ready, refresh }: { enabled: boolean; ready: boolean; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef(false);
  const change = async () => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try { await monitoringApi.setAutomaticStatus(!enabled); }
    catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : '自动标记设置未完成，请刷新后重试。');
    } finally { await refresh(); pending.current = false; setBusy(false); }
  };
  return <section aria-label="自动标记状态" className="mt-4 border-t border-gray-200 pt-3 dark:border-gray-700">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm font-semibold">自动标记状态：{enabled ? '已启用' : '已关闭'}</p>
      <button disabled={busy || (!ready && !enabled)} onClick={() => void change()} className="rounded border border-blue-500 px-3 py-2 text-sm text-blue-600 disabled:opacity-50">{busy ? '正在保存…' : enabled ? '关闭自动标记' : '启用自动标记'}</button>
    </div>
    <p className="mt-2 text-xs text-gray-500">启用后，对本场景已观察及后续查询到的待处置、处置中且未加白或部分加白事件，自动分析并标记状态，无需逐条确认。随监测任务执行；暂停监测后停止自动标记。</p>
    <ul className="mt-2 list-inside list-disc text-xs text-gray-500">
      <li>需跟进或证据不足 → 处置中。</li>
      <li>已确认隔离或恶意实体均受控 → 已遏制，仍需跟进。</li>
      <li>病毒事件恶意文件均已处置，且无其他未解决威胁 → 处置完成。</li>
      <li>XDR 误报结论与业务定性或安全实体证据一致 → 忽略。</li>
    </ul>
    <p className="mt-2 text-xs text-gray-500">每次写回后回查目标状态。失败或结果未知时保留待确认，下一轮仅回查；本功能只标记状态，不执行隔离、封禁或查杀。</p>
    {error && <p role="alert" className="mt-2 text-sm text-red-600">{error}</p>}
  </section>;
}
