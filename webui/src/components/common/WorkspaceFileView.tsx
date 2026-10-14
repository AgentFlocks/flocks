import { useCallback, useEffect, useRef, useState, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

const PREVIEW_PANEL_DEFAULT_RATIO = 0.5;
const PREVIEW_PANEL_MIN_WIDTH = 420;
const PREVIEW_PANEL_MIN_LIST_WIDTH = 360;

function getViewportWidth(): number {
  return typeof window === 'undefined' ? PREVIEW_PANEL_MIN_WIDTH * 2 : window.innerWidth;
}

function getPreviewPanelMaxWidth(containerWidth: number): number {
  return Math.max(PREVIEW_PANEL_MIN_WIDTH, containerWidth - PREVIEW_PANEL_MIN_LIST_WIDTH);
}

function getDefaultPreviewPanelWidth(containerWidth = getViewportWidth()): number {
  return Math.min(
    getPreviewPanelMaxWidth(containerWidth),
    Math.max(PREVIEW_PANEL_MIN_WIDTH, Math.floor(containerWidth * PREVIEW_PANEL_DEFAULT_RATIO)),
  );
}

const paneClass = 'flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-900';
export const fileActionClass = 'p-1 text-gray-400 hover:text-gray-600 rounded hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-zinc-800 dark:hover:text-zinc-200';
export const previewActionClass = 'p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded dark:hover:bg-zinc-800 dark:hover:text-zinc-200';

/** The file manager's measured list and resizable preview, without resource-specific behavior. */
export function WorkspaceFileSplit({ list, preview, className = '' }: {
  list: (listWidth: number) => ReactNode;
  preview?: ReactNode;
  className?: string;
}) {
  const { t } = useTranslation('workspace');
  const splitRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const userResized = useRef(false);
  const stopResize = useRef<(() => void) | null>(null);
  const [previewWidth, setPreviewWidth] = useState(() => getDefaultPreviewPanelWidth());
  const [containerWidth, setContainerWidth] = useState(getViewportWidth);
  const [listWidth, setListWidth] = useState(900);
  const hasPreview = Boolean(preview);
  const stacked = hasPreview && containerWidth < PREVIEW_PANEL_MIN_WIDTH + PREVIEW_PANEL_MIN_LIST_WIDTH + 16;

  useEffect(() => {
    const node = listRef.current;
    if (!node) return;
    if (node.clientWidth > 0) setListWidth(node.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setListWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = splitRef.current;
    if (!node) return;
    const syncPreviewWidth = (containerWidth: number) => {
      if (containerWidth <= 0) return;
      setContainerWidth(containerWidth);
      setPreviewWidth(current => userResized.current
        ? Math.min(getPreviewPanelMaxWidth(containerWidth), Math.max(PREVIEW_PANEL_MIN_WIDTH, current))
        : getDefaultPreviewPanelWidth(containerWidth));
    };
    syncPreviewWidth(node.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => syncPreviewWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => stopResize.current?.(), [hasPreview, stacked]);

  const handleResizeStart = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    stopResize.current?.();
    userResized.current = true;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = previewWidth;
    const containerWidth = splitRef.current?.clientWidth || getViewportWidth();
    const maxWidth = getPreviewPanelMaxWidth(containerWidth);
    event.currentTarget.setPointerCapture?.(pointerId);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      setPreviewWidth(Math.min(maxWidth, Math.max(PREVIEW_PANEL_MIN_WIDTH, startWidth - (moveEvent.clientX - startX))));
    };
    const cleanup = () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerEnd);
      window.removeEventListener('pointercancel', handlePointerEnd);
      stopResize.current = null;
    };
    const handlePointerEnd = (endEvent: PointerEvent) => {
      if (endEvent.pointerId === pointerId) cleanup();
    };
    stopResize.current = cleanup;
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerEnd);
    window.addEventListener('pointercancel', handlePointerEnd);
  }, [previewWidth]);

  return <div ref={splitRef} data-layout={stacked ? 'stacked' : 'split'} className={`flex h-full min-h-0 min-w-0 gap-4 ${stacked ? 'flex-col overflow-y-auto' : ''} ${className}`}>
    <div ref={listRef} className={`${paneClass} min-w-0 ${stacked ? 'flex-shrink-0' : 'flex-1'}`} style={stacked ? { height: 'calc(50% - 8px)', minHeight: 200 } : undefined}>
      {list(listWidth)}
    </div>
    {hasPreview && <div className={`${paneClass} relative flex-shrink-0`} style={stacked
      ? { width: '100%', minWidth: 0, height: 'calc(50% - 8px)', minHeight: 260 }
      : { width: previewWidth, minWidth: PREVIEW_PANEL_MIN_WIDTH }}>
      {!stacked && <button
        type="button"
        aria-label={t('files.preview.resize')}
        title={t('files.preview.resize')}
        onPointerDown={handleResizeStart}
        className="absolute left-0 top-0 z-10 h-full w-2 touch-none cursor-col-resize border-l border-transparent transition-colors hover:border-sky-300 hover:bg-sky-50/70 active:border-sky-400 active:bg-sky-100"
      />}
      {preview}
    </div>}
  </div>;
}

export function FileListTable({ header, children, className = '', ...props }: ComponentPropsWithoutRef<'table'> & { header: ReactNode }) {
  return <table {...props} className={`w-full text-sm ${className}`}>
    <thead className="sticky top-0 bg-gray-50 dark:bg-zinc-900/95"><tr>{header}</tr></thead>
    <tbody>{children}</tbody>
  </table>;
}

export function FileListRow({ selected = false, className = '', ...props }: ComponentPropsWithoutRef<'tr'> & { selected?: boolean }) {
  return <tr {...props} className={`group border-t border-gray-50 cursor-pointer transition-colors ${selected
    ? 'bg-slate-100 dark:bg-zinc-800/70'
    : 'hover:bg-gray-50 dark:hover:bg-zinc-900/70'} ${className}`} />;
}

export function FileListActions({ className = '', onClick, ...props }: ComponentPropsWithoutRef<'div'>) {
  return <div {...props} className={`flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 ${className}`} onClick={event => {
    event.stopPropagation();
    onClick?.(event);
  }} />;
}

export function PreviewPanelHeader({ title, icon, actions, meta }: {
  title: string;
  icon?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return <>
    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100 flex-shrink-0 dark:border-zinc-800">
      {icon && <span className="text-sm flex-shrink-0">{icon}</span>}
      <span className="flex-1 min-w-0 text-sm font-medium text-gray-800 truncate dark:text-zinc-100" title={title}>{title}</span>
      {actions && <div className="flex items-center gap-1 flex-shrink-0">{actions}</div>}
    </div>
    {meta && <div className="px-4 py-1.5 bg-gray-50 border-b border-gray-100 flex gap-4 text-xs text-gray-400 flex-shrink-0 dark:border-zinc-800 dark:bg-zinc-800/50 dark:text-zinc-500">{meta}</div>}
  </>;
}
