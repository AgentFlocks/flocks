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

const paneClass = 'flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-surface dark:border-line dark:bg-surface';
export const fileActionClass = 'p-1 text-fg-muted hover:text-fg-secondary rounded hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-hover dark:hover:text-fg-secondary';
export const previewActionClass = 'p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded dark:hover:bg-hover dark:hover:text-fg-secondary';

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
    {hasPreview && <div data-workspace-preview="true" className={`${paneClass} relative flex-shrink-0`} style={stacked
      ? { width: '100%', minWidth: 0, height: 'calc(50% - 8px)', minHeight: 260 }
      : { width: previewWidth, minWidth: PREVIEW_PANEL_MIN_WIDTH }}>
      {!stacked && <button
        type="button"
        aria-label={t('files.preview.resize')}
        title={t('files.preview.resize')}
        onPointerDown={handleResizeStart}
        className="absolute left-0 top-0 z-10 h-full w-2 touch-none cursor-col-resize border-l border-transparent transition-colors hover:border-info hover:bg-info-soft active:border-info active:bg-info-soft"
      />}
      {preview}
    </div>}
  </div>;
}

export function FileListTable({ header, children, className = '', ...props }: ComponentPropsWithoutRef<'table'> & { header: ReactNode }) {
  return <table {...props} className={`w-full text-sm ${className}`}>
    <thead className="sticky top-0 bg-subtle dark:bg-surface"><tr>{header}</tr></thead>
    <tbody>{children}</tbody>
  </table>;
}

export function FileListRow({ selected = false, className = '', ...props }: ComponentPropsWithoutRef<'tr'> & { selected?: boolean }) {
  return <tr {...props} className={`group border-t border-line cursor-pointer transition-colors ${selected
    ? 'bg-subtle dark:bg-selected'
    : 'hover:bg-hover dark:hover:bg-hover'} ${className}`} />;
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
    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line flex-shrink-0 dark:border-line">
      {icon && <span className="text-sm flex-shrink-0">{icon}</span>}
      <span className="flex-1 min-w-0 text-sm font-medium text-fg truncate dark:text-fg" title={title}>{title}</span>
      {actions && <div className="flex items-center gap-1 flex-shrink-0">{actions}</div>}
    </div>
    {meta && <div className="px-4 py-1.5 bg-subtle border-b border-line flex gap-4 text-xs text-fg-muted flex-shrink-0 dark:border-line dark:bg-subtle dark:text-fg-muted">{meta}</div>}
  </>;
}
