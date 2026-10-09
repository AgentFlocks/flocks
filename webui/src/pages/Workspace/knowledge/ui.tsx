import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { AlertTriangle, ArrowLeft, ChevronLeft, ChevronRight, FolderOpen, RefreshCw, X } from 'lucide-react';
import type { KnowledgeFolder } from '@/api/knowledgebase';
import { previewActionClass } from '@/components/common/WorkspaceFileView';
import { useTranslation } from 'react-i18next';
import { PAGE_SIZE } from './state';

export const panelClass = 'min-w-0 overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-900';
export const inputClass = 'w-full min-w-0 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger' | 'link';
  size?: 'small' | 'icon';
};

export function Button({ children, className = '', variant, size, ...props }: ButtonProps) {
  // Existing file-manager consumers keep their original appearance unless opted in.
  const styles = variant
    ? `kb-button kb-button--${variant}${size ? ` kb-button--${size}` : ''}`
    : 'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-slate-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800';
  return <button type="button" {...props} className={`${styles} ${className}`}>{children}</button>;
}

export function LoadState({ loading, error, retry, children }: { loading: boolean; error?: string; retry: () => void; children: ReactNode }) {
  const { t } = useTranslation('workspace');
  if (loading) return <p role="status" className="p-6 text-sm text-gray-500">{t('knowledge.loading')}</p>;
  if (error) return <div role="alert" className="space-y-3 p-6 text-sm text-gray-600 dark:text-zinc-300"><p className="flex items-center gap-2"><AlertTriangle className="h-4 w-4" />{t('knowledge.loadFailed')}</p><p className="break-words text-xs">{error}</p><Button onClick={retry}><RefreshCw className="h-4 w-4" />{t('knowledge.retry')}</Button></div>;
  return <>{children}</>;
}

export function Pagination({ page, total, onChange, variant }: { page: number; total: number; onChange: (page: number) => void; variant?: 'knowledge' }) {
  const { t } = useTranslation('workspace');
  return <div className={variant ? 'kb-pagination' : 'flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 px-3 py-2 text-xs text-gray-500 dark:border-zinc-800'}>
    <span>{t('knowledge.pagination', { page })}</span>
    <div className="flex gap-1">
      <Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.previousPage')} disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft className="h-4 w-4" /></Button>
      <Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.nextPage')} disabled={page * PAGE_SIZE >= total} onClick={() => onChange(page + 1)}><ChevronRight className="h-4 w-4" /></Button>
    </div>
  </div>;
}

export function DirectoryNavigation({ breadcrumbs, onNavigate, loading, onRefresh, children, search, currentFolderId }: {
  breadcrumbs: KnowledgeFolder[];
  onNavigate: (id: string | undefined) => void;
  loading: boolean;
  onRefresh: () => void;
  children?: ReactNode;
  search?: ReactNode;
  currentFolderId?: string;
}) {
  const { t } = useTranslation('workspace');
  const trail = <nav aria-label={t('knowledge.files.breadcrumbs')} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-sm">
    <button type="button" aria-label={t('knowledge.files.root')} title={t('knowledge.files.root')} aria-current={breadcrumbs.length <= 1 && !currentFolderId ? 'page' : undefined} onClick={() => onNavigate(undefined)} className="flex-shrink-0 text-sky-700 hover:underline dark:text-sky-400">{search ? <FolderOpen aria-hidden="true" className="h-4 w-4" /> : t('knowledge.files.root')}</button>
    {breadcrumbs.slice(1).map((folder, index) => <span key={folder.id} className="flex min-w-0 flex-shrink-0 items-center gap-1">
      <ChevronRight aria-hidden="true" className="h-3 w-3 flex-shrink-0 text-gray-300" />
      <button type="button" title={folder.name} aria-current={index === breadcrumbs.length - 2 ? 'page' : undefined} onClick={() => onNavigate(folder.id)} className="max-w-48 truncate text-gray-700 hover:underline dark:text-zinc-200">{folder.name}</button>
    </span>)}
  </nav>;
  return <div className="flex min-w-0 flex-wrap items-center gap-2">
    {search ? <div className="min-w-0 flex-1">{search}</div> : trail}
    <div className="flex flex-shrink-0 items-center gap-1">
      {breadcrumbs.length > 1 && <button type="button" onClick={() => onNavigate(breadcrumbs.length === 2 ? undefined : breadcrumbs[breadcrumbs.length - 2].id)} title={t('files.back')} className={previewActionClass}><ArrowLeft className="h-4 w-4" /></button>}
      <button type="button" disabled={loading} onClick={onRefresh} title={t('knowledge.refresh')} className={`${previewActionClass} disabled:cursor-not-allowed disabled:opacity-50`}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button>
      {children}
    </div>
    {search && (breadcrumbs.length > 1 || currentFolderId !== undefined) && <div className="w-full min-w-0">{trail}</div>}
  </div>;
}

export function fileType(name: string): string {
  const filename = name.trim().split(/[\\/]/).pop() ?? '';
  const dot = filename.lastIndexOf('.');
  return dot > 0 && dot < filename.length - 1 ? filename.slice(dot + 1).toUpperCase() : '—';
}

export function CreatedAt({ value }: { value?: string | null }) {
  const { i18n } = useTranslation('workspace');
  // Do not interpret numeric timestamps or other non-ISO strings as dates.
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value)) return <>—</>;
  const date = new Date(value);
  const day = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || !Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value.slice(0, 10)) return <>—</>;
  return <time dateTime={value} title={value}>{date.toLocaleString(i18n.language)}</time>;
}

export function Modal({ title, onClose, children, variant }: { title: string; onClose: () => void; children: ReactNode; variant?: 'knowledge' }) {
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
  return <dialog ref={ref} aria-labelledby={id} className={variant ? 'kb-modal--knowledge' : 'w-[min(40rem,calc(100vw-2rem))] rounded-xl border border-gray-200 bg-white p-0 text-gray-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100'}>
    <div className={variant ? 'kb-modal-header' : 'flex items-center gap-3 border-b border-gray-100 px-4 py-3 dark:border-zinc-800'}><h2 id={id} className="min-w-0 flex-1 truncate text-base font-medium">{title}</h2><Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.close')} onClick={onClose}><X className="h-4 w-4" /></Button></div>
    <div className={variant ? 'kb-modal-body' : 'p-4'}>{children}</div>
  </dialog>;
}
