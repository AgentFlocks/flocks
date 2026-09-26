import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { AlertTriangle, ChevronLeft, ChevronRight, RefreshCw, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PAGE_SIZE } from './state';

export const panelClass = 'min-w-0 overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-900';
export const inputClass = 'w-full min-w-0 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100';

export function Button({ children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className={`inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800 ${className}`}>{children}</button>;
}

export function LoadState({ loading, error, retry, children }: { loading: boolean; error?: string; retry: () => void; children: ReactNode }) {
  const { t } = useTranslation('workspace');
  if (loading) return <p role="status" className="p-6 text-sm text-gray-500">{t('knowledge.loading')}</p>;
  if (error) return <div role="alert" className="space-y-3 p-6 text-sm text-gray-600 dark:text-zinc-300"><p className="flex items-center gap-2"><AlertTriangle className="h-4 w-4" />{t('knowledge.loadFailed')}</p><p className="break-words text-xs">{error}</p><Button onClick={retry}><RefreshCw className="h-4 w-4" />{t('knowledge.retry')}</Button></div>;
  return <>{children}</>;
}

export function Pagination({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  const { t } = useTranslation('workspace');
  return <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-3 py-2 text-xs text-gray-500 dark:border-zinc-800">
    <span>{t('knowledge.pagination', { page, total })}</span>
    <div className="flex gap-1">
      <Button aria-label={t('knowledge.previousPage')} disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft className="h-4 w-4" /></Button>
      <Button aria-label={t('knowledge.nextPage')} disabled={page * PAGE_SIZE >= total} onClick={() => onChange(page + 1)}><ChevronRight className="h-4 w-4" /></Button>
    </div>
  </div>;
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const { t } = useTranslation('workspace');
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.open = true;
    const onCancel = (event: Event) => { event.preventDefault(); close.current(); };
    dialog.addEventListener('cancel', onCancel);
    return () => dialog.removeEventListener('cancel', onCancel);
  }, []);
  return <dialog ref={ref} aria-labelledby={id} className="w-[min(40rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white p-0 text-gray-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100">
    <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3 dark:border-zinc-800"><h2 id={id} className="min-w-0 flex-1 truncate text-base font-medium">{title}</h2><Button aria-label={t('knowledge.close')} onClick={onClose}><X className="h-4 w-4" /></Button></div>
    <div className="p-4">{children}</div>
  </dialog>;
}
