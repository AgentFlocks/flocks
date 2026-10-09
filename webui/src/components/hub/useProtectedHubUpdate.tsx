import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { hubAPI, type HubPluginType, type HubUpdatePlan } from '@/api/hub';

export function useProtectedHubUpdate() {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith('zh');
  const [plan, setPlan] = useState<HubUpdatePlan | null>(null);
  const [backupPath, setBackupPath] = useState<string | null>(null);
  const resolveRef = useRef<((confirmed: boolean) => void) | null>(null);
  const busy = useRef(false);
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; resolveRef.current?.(false); resolveRef.current = null; };
  }, []);
  const answer = useCallback((confirmed: boolean) => {
    resolveRef.current?.(confirmed);
    resolveRef.current = null;
    setPlan(null);
    returnFocus.current?.focus();
  }, []);
  useEffect(() => {
    if (!plan) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') answer(false);
      if (event.key === 'Tab') {
        const buttons = dialogRef.current?.querySelectorAll<HTMLButtonElement>('button');
        if (!buttons?.length) return;
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [plan, answer]);

  const update = useCallback(async (type: HubPluginType, id: string, preview?: HubUpdatePlan): Promise<boolean> => {
    if (busy.current) return false;
    busy.current = true;
    try {
      let current = preview ?? (await hubAPI.previewUpdate(type, id)).data;
      while (mounted.current) {
        if (current.requiresConfirmation) {
          const confirmed = await new Promise<boolean>((resolve) => {
            returnFocus.current = document.activeElement as HTMLElement | null;
            resolveRef.current = resolve;
            setPlan(current);
          });
          if (!confirmed || !mounted.current) return false;
        }
        try {
          const response = await hubAPI.update(type, id, current.scope, {
            confirmationToken: current.token,
            confirmChanges: current.requiresConfirmation,
          });
          if (response?.data?.backupPath && mounted.current) setBackupPath(response.data.backupPath);
          return true;
        } catch (error) {
          const conflict = error as { response?: { status?: number; data?: { detail?: { code?: string; plan?: HubUpdatePlan } } } };
          const detail = conflict.response?.data?.detail;
          if (conflict.response?.status === 409 && detail?.code === 'hub_update_confirmation_required' && detail.plan) {
            current = detail.plan;
            continue;
          }
          throw error;
        }
      }
      return false;
    } finally {
      busy.current = false;
    }
  }, []);

  const dialog = <>
    {plan && <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4">
      <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hub-update-confirm-title" className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-xl bg-white p-6 shadow-xl dark:bg-zinc-900">
        <h2 id="hub-update-confirm-title" className="text-lg font-semibold">{zh ? '备份并覆盖现有组件' : 'Back up and overwrite existing components'}</h2>
        <p className="my-3 text-sm text-gray-600 dark:text-zinc-300">{zh
          ? '无论是否修改过组件，更新都会覆盖现有内容。继续后会先完整备份本次将被替换的组件，再安装官方版本；备份失败会停止更新。'
          : 'Updating overwrites existing content, whether or not you have changed it. Continuing first backs up the components being replaced, then installs the official release. A failed backup stops the update.'}</p>
        <div className="min-h-0 overflow-y-auto text-sm">
          {plan.items.filter(item => item.requiresConfirmation).map(item => <div key={`${item.type}:${item.id}`} className="mb-4">
            <p className="font-medium">{item.name} <span className="text-gray-500">({item.id})</span></p>
          </div>)}
        </div>
        <div className="mt-4 flex justify-end gap-3">
          <button autoFocus onClick={() => answer(false)} className="rounded-lg border px-4 py-2">{zh ? '取消' : 'Cancel'}</button>
          <button onClick={() => answer(true)} className="rounded-lg bg-red-600 px-4 py-2 text-white">{zh ? '备份并覆盖更新' : 'Back up and overwrite'}</button>
        </div>
      </section>
    </div>}
    {backupPath && <div role="status" className="fixed bottom-5 right-5 z-[65] max-w-lg break-all rounded-lg border bg-white p-4 shadow-lg dark:bg-zinc-900">
      <p>{zh ? '更新前的备份已保存：' : 'Pre-update backup saved:'}</p>
      <code className="text-sm">{backupPath}</code>
      <button className="ml-3 underline" onClick={() => setBackupPath(null)}>{zh ? '关闭' : 'Dismiss'}</button>
    </div>}
  </>;
  return { update, dialog };
}
