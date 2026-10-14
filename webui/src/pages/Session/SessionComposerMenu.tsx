import {
  useCallback,
  useLayoutEffect,
  useRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { Check } from 'lucide-react';

const MENU_WIDTH = 520;
const MAX_HEIGHT = 480;
const VIEWPORT_PADDING = 16;
const ANCHOR_GAP = 8;
const OPTION_COLUMNS = 'grid-cols-[minmax(0,112px)_minmax(0,1fr)_16px]';

export interface SessionComposerMenuProps extends HTMLAttributes<HTMLDivElement> {
  anchorRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  header?: ReactNode;
  footer?: ReactNode;
  contentClassName?: string;
}

export default function SessionComposerMenu({
  anchorRef,
  children,
  header,
  footer,
  contentClassName,
  className,
  style,
  ...props
}: SessionComposerMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const footerRef = useRef<HTMLDivElement>(null);

  const updatePosition = useCallback(() => {
    const anchor = anchorRef.current;
    const menu = menuRef.current;
    const content = contentRef.current;
    if (!anchor || !menu || !content) return;

    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    const paddingX = Math.min(VIEWPORT_PADDING, viewportWidth / 2);
    const paddingY = Math.min(VIEWPORT_PADDING, viewportHeight / 2);
    const width = Math.min(MENU_WIDTH, Math.max(0, viewportWidth - paddingX * 2));

    // Set the final width before measuring content that may reflow on resize.
    menu.style.width = `${width}px`;
    const anchorRect = anchor.getBoundingClientRect();
    const viewportBottom = viewportHeight - paddingY;
    const aboveEnd = Math.max(paddingY, Math.min(anchorRect.top - ANCHOR_GAP, viewportBottom));
    const belowStart = Math.max(paddingY, Math.min(anchorRect.bottom + ANCHOR_GAP, viewportBottom));
    const above = aboveEnd - paddingY;
    const below = viewportBottom - belowStart;
    // Measure the unconstrained inner content, not the already-capped popup/body.
    // Otherwise a short viewport can make placement alternate on every measurement.
    const naturalHeight = content.getBoundingClientRect().height
      + (headerRef.current?.getBoundingClientRect().height ?? 0)
      + (footerRef.current?.getBoundingClientRect().height ?? 0)
      + 2; // The root's two 1px borders.
    const placeBelow = Math.min(MAX_HEIGHT, naturalHeight) > above && below > above;

    Object.assign(menu.style, {
      left: `${Math.max(paddingX, Math.min(anchorRect.left, viewportWidth - paddingX - width))}px`,
      top: placeBelow ? `${belowStart}px` : 'auto',
      bottom: placeBelow ? 'auto' : `${viewportHeight - aboveEnd}px`,
      maxHeight: `${Math.min(MAX_HEIGHT, placeBelow ? below : above)}px`,
      visibility: 'visible',
    });
  }, [anchorRef]);

  // Also measure after parent renders (for example when loading model groups finishes).
  useLayoutEffect(updatePosition);

  const hasHeader = header != null;
  const hasFooter = footer != null;
  useLayoutEffect(() => {
    let frame: number | null = null;
    let disposed = false;
    const scheduleUpdate = () => {
      if (disposed || frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        updatePosition();
      });
    };
    const onScroll = (event: Event) => {
      const target = event.target;
      // Ignore scrolling inside this portal (and unrelated panels).
      if (target === document || target === window
        || (target instanceof Element && target.contains(anchorRef.current))) {
        scheduleUpdate();
      }
    };
    window.addEventListener('resize', scheduleUpdate);
    window.addEventListener('scroll', onScroll, true);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(scheduleUpdate);
    for (const element of [anchorRef.current, menuRef.current, contentRef.current, headerRef.current, footerRef.current]) {
      if (element) observer?.observe(element);
    }
    // A resized composer/sidebar can move an unchanged-size anchor.
    for (let ancestor = anchorRef.current?.parentElement; ancestor; ancestor = ancestor.parentElement) {
      observer?.observe(ancestor);
    }
    return () => {
      disposed = true;
      window.removeEventListener('resize', scheduleUpdate);
      window.removeEventListener('scroll', onScroll, true);
      observer?.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [anchorRef, hasHeader, hasFooter, updatePosition]);

  return createPortal(
    <div
      {...props}
      ref={menuRef}
      className={`z-50 flex min-w-0 flex-col overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30 ${className ?? ''}`}
      style={{
        ...style,
        position: 'fixed',
        boxSizing: 'border-box',
        width: MENU_WIDTH,
        maxHeight: MAX_HEIGHT,
        visibility: 'hidden',
      }}
    >
      {hasHeader && <div ref={headerRef} className="min-w-0 shrink-0">{header}</div>}
      <div className="min-h-0 overflow-x-hidden overflow-y-auto overscroll-contain" style={{ scrollbarGutter: 'stable' }}>
        <div ref={contentRef} className={`flow-root ${contentClassName ?? ''}`}>{children}</div>
      </div>
      {hasFooter && <div ref={footerRef} className="min-w-0 shrink-0">{footer}</div>}
    </div>,
    document.body,
  );
}

export interface SessionModeOptionProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected: boolean;
  label: string;
  description: string;
  icon?: ReactNode;
}

export function SessionModeOption({
  selected,
  label,
  description,
  icon,
  className,
  type = 'button',
  ...props
}: SessionModeOptionProps) {
  return (
    <button
      {...props}
      type={type}
      title={`${label}\n${description}`}
      className={`grid w-full min-w-0 ${OPTION_COLUMNS} items-center gap-3 rounded-lg border px-2.5 py-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? 'border-blue-300 bg-blue-50 text-zinc-950 shadow-sm dark:border-blue-500/60 dark:bg-blue-500/15 dark:text-zinc-50'
          : 'border-transparent text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
      } ${className ?? ''}`}
    >
      <span className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap text-sm font-medium leading-[14px]">
        {icon && <span className="flex shrink-0 items-center" aria-hidden="true">{icon}</span>}
        <span className="min-w-0 truncate whitespace-nowrap">{label}</span>
      </span>
      <span className={`min-w-0 truncate whitespace-nowrap text-[12px] leading-[18px] ${
        selected ? 'text-zinc-700 dark:text-zinc-200' : 'text-zinc-500 dark:text-zinc-400'
      }`}>{description}</span>
      <Check aria-hidden="true" className={`h-4 w-4 text-blue-600 dark:text-blue-300 ${selected ? '' : 'invisible'}`} />
    </button>
  );
}

export function SessionComposerMenuHeader({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="grid grid-cols-[minmax(0,112px)_minmax(0,1fr)] items-center gap-3 border-b border-zinc-100 px-4 py-2 dark:border-zinc-800">
      <span title={title} className="min-w-0 truncate whitespace-nowrap text-xs font-semibold leading-[18px] text-zinc-700 dark:text-zinc-100">{title}</span>
      <span title={hint} className="min-w-0 truncate whitespace-nowrap text-[12px] leading-[18px] text-zinc-400 dark:text-zinc-500">{hint}</span>
    </div>
  );
}
