import { useCallback, useLayoutEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const VIEWPORT_PADDING = 8;
const ANCHOR_GAP = 4;

export default function SessionActionsMenu({
  anchor,
  width,
  children,
}: {
  anchor: HTMLElement;
  width: number;
  children: ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const updatePosition = useCallback(() => {
    const menu = menuRef.current;
    const content = contentRef.current;
    if (!menu || !content) return;
    if (!anchor.isConnected) {
      menu.style.visibility = 'hidden';
      return;
    }

    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    const paddingX = Math.min(VIEWPORT_PADDING, viewportWidth / 2);
    const paddingY = Math.min(VIEWPORT_PADDING, viewportHeight / 2);
    const menuWidth = Math.min(width, viewportWidth - paddingX * 2);
    const maxHeight = viewportHeight - paddingY * 2;
    // Apply width first so wrapped content is measured at its final width.
    menu.style.width = `${menuWidth}px`;
    menu.style.maxHeight = `${maxHeight}px`;

    // Measure the unconstrained content, plus 8px padding and two 1px borders,
    // rather than the previously capped popup.
    const height = Math.min(content.getBoundingClientRect().height + 10, maxHeight);
    const rect = anchor.getBoundingClientRect();
    const below = rect.bottom + ANCHOR_GAP;
    const above = rect.top - ANCHOR_GAP - height;
    const viewportBottom = viewportHeight - paddingY;
    const preferredTop = below + height <= viewportBottom ? below : above;

    Object.assign(menu.style, {
      left: `${Math.max(paddingX, Math.min(rect.right - menuWidth, viewportWidth - paddingX - menuWidth))}px`,
      top: `${Math.max(paddingY, Math.min(preferredTop, viewportBottom - height))}px`,
      visibility: 'visible',
    });
  }, [anchor, width]);

  // Re-measure when switching between actions and the project picker.
  useLayoutEffect(updatePosition);

  useLayoutEffect(() => {
    let frame: number | null = null;
    const scheduleUpdate = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        updatePosition();
      });
    };
    const onScroll = (event: Event) => {
      const target = event.target;
      if (target === document || target === window
        || (target instanceof Element && target.contains(anchor))) {
        scheduleUpdate();
      }
    };
    window.addEventListener('resize', scheduleUpdate);
    window.addEventListener('scroll', onScroll, true);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(scheduleUpdate);
    if (contentRef.current) observer?.observe(contentRef.current);
    // Sidebar/layout changes can move the trigger without resizing the button.
    for (let element: HTMLElement | null = anchor; element; element = element.parentElement) {
      observer?.observe(element);
    }
    return () => {
      window.removeEventListener('resize', scheduleUpdate);
      window.removeEventListener('scroll', onScroll, true);
      observer?.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [anchor, updatePosition]);

  return createPortal(
    <div
      ref={menuRef}
      className={`fixed z-50 overflow-x-hidden overflow-y-auto overscroll-contain rounded-card border border-line bg-surface p-1 shadow-overlay ${width === 220 ? 'w-[220px]' : 'w-[132px]'}`}
      style={{ width, visibility: 'hidden' }}
      data-session-menu-portal
      onClick={(event) => event.stopPropagation()}
    >
      <div ref={contentRef} className="grid gap-0.5">
        {children}
      </div>
    </div>,
    document.body,
  );
}
