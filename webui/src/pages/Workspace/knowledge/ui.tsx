import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconChevronLeft,
  IconChevronRight,
  IconFolderOpen,
  IconRefresh,
  IconX,
} from '@tabler/icons-react';
import type { KnowledgeFolder } from '@/api/knowledgebase';
import { previewActionClass } from '@/components/common/WorkspaceFileView';
import { useTranslation } from 'react-i18next';
import { PAGE_SIZE } from './state';

export const panelClass = 'min-w-0 overflow-hidden rounded-xl border border-line bg-surface dark:border-line dark:bg-surface';
export const inputClass = 'w-full min-w-0 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-focus disabled:opacity-50 dark:border-line dark:bg-surface dark:text-fg';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger' | 'link';
  size?: 'small' | 'icon';
};

export function Button({ children, className = '', variant, size, ...props }: ButtonProps) {
  // Existing file-manager consumers keep their original appearance unless opted in.
  const styles = variant
    ? `kb-button kb-button--${variant}${size ? ` kb-button--${size}` : ''}`
    : 'inline-flex items-center justify-center gap-2 rounded-lg border border-line px-3 py-2 text-sm text-fg-secondary hover:bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:opacity-50 dark:border-line dark:text-fg-secondary dark:hover:bg-hover';
  return <button type="button" {...props} className={`${styles} ${className}`}>{children}</button>;
}

export function LoadState({ loading, error, retry, children }: { loading: boolean; error?: string; retry: () => void; children: ReactNode }) {
  const { t } = useTranslation('workspace');
  if (loading) return <p role="status" className="p-6 text-sm text-fg-muted">{t('knowledge.loading')}</p>;
  if (error) return <div role="alert" className="space-y-3 p-6 text-sm text-fg-secondary dark:text-fg-secondary"><p className="flex items-center gap-2"><IconAlertTriangle aria-hidden="true" className="h-4 w-4" />{t('knowledge.loadFailed')}</p><p className="break-words text-xs">{error}</p><Button onClick={retry}><IconRefresh aria-hidden="true" className="h-4 w-4" />{t('knowledge.retry')}</Button></div>;
  return <>{children}</>;
}

export function Pagination({ page, total, onChange, variant }: { page: number; total: number; onChange: (page: number) => void; variant?: 'knowledge' }) {
  const { t } = useTranslation('workspace');
  return <div className={variant ? 'kb-pagination' : 'flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-2 text-xs text-fg-muted dark:border-line'}>
    <span>{t('knowledge.pagination', { page })}</span>
    <div className="flex gap-1">
      <Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.previousPage')} disabled={page <= 1} onClick={() => onChange(page - 1)}><IconChevronLeft aria-hidden="true" className="h-4 w-4" /></Button>
      <Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.nextPage')} disabled={page * PAGE_SIZE >= total} onClick={() => onChange(page + 1)}><IconChevronRight aria-hidden="true" className="h-4 w-4" /></Button>
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
    <button type="button" aria-label={t('knowledge.files.root')} title={t('knowledge.files.root')} aria-current={breadcrumbs.length <= 1 && !currentFolderId ? 'page' : undefined} onClick={() => onNavigate(undefined)} className="flex-shrink-0 text-info hover:underline dark:text-info">{search ? <IconFolderOpen aria-hidden="true" className="h-4 w-4" /> : t('knowledge.files.root')}</button>
    {breadcrumbs.slice(1).map((folder, index) => <span key={folder.id} className="flex min-w-0 flex-shrink-0 items-center gap-1">
      <IconChevronRight aria-hidden="true" className="h-3 w-3 flex-shrink-0 text-fg-placeholder" />
      <button type="button" title={folder.name} aria-current={index === breadcrumbs.length - 2 ? 'page' : undefined} onClick={() => onNavigate(folder.id)} className="max-w-48 truncate text-fg-secondary hover:underline dark:text-fg-secondary">{folder.name}</button>
    </span>)}
  </nav>;
  return <div className="flex min-w-0 flex-wrap items-center gap-2">
    {search ? <div className="min-w-0 flex-1">{search}</div> : trail}
    <div className="flex flex-shrink-0 items-center gap-1">
      {breadcrumbs.length > 1 && <button type="button" onClick={() => onNavigate(breadcrumbs.length === 2 ? undefined : breadcrumbs[breadcrumbs.length - 2].id)} title={t('files.back')} className={previewActionClass}><IconArrowLeft aria-hidden="true" className="h-4 w-4" /></button>}
      <button type="button" disabled={loading} onClick={onRefresh} title={t('knowledge.refresh')} className={`${previewActionClass} disabled:cursor-not-allowed disabled:opacity-50`}><IconRefresh aria-hidden="true" className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button>
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
  return <dialog ref={ref} aria-labelledby={id} className={variant ? 'kb-modal--knowledge' : 'w-[min(40rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-0 text-fg dark:border-line dark:bg-surface dark:text-fg'}>
    <div className={variant ? 'kb-modal-header' : 'flex items-center gap-3 border-b border-line px-4 py-3 dark:border-line'}><h2 id={id} className="min-w-0 flex-1 truncate text-base font-medium">{title}</h2><Button variant={variant ? 'quiet' : undefined} size="icon" aria-label={t('knowledge.close')} onClick={onClose}><IconX aria-hidden="true" className="h-4 w-4" /></Button></div>
    <div className={variant ? 'kb-modal-body' : 'p-4'}>{children}</div>
  </dialog>;
}
