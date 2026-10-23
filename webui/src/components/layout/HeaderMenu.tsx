import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface HeaderMenuProps {
  /** Accessible name of the trigger. */
  label: string;
  trigger: ReactNode;
  triggerClassName: string;
  /** Tooltip on the trigger; defaults to the label. */
  title?: string;
  align?: 'left' | 'right';
  /** Sidebar menus use a portal so their clipping container cannot hide them. */
  placement?: 'below' | 'above' | 'beside';
  menuClassName?: string;
  children: (close: (restoreFocus?: boolean) => void) => ReactNode;
}

const FOCUSABLE_ITEMS = '[role="menuitem"],[role="menuitemradio"]';

/**
 * A button that opens a menu below it. Closes on outside clicks, Escape and
 * item activation; ArrowUp/ArrowDown/Home/End move between items.
 */
export default function HeaderMenu({
  label,
  trigger,
  triggerClassName,
  title,
  align = 'left',
  placement = 'below',
  menuClassName = 'w-64',
  children,
}: HeaderMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const focusFirstRef = useRef(false);
  const menuId = useId();
  const [position, setPosition] = useState<CSSProperties | null>(null);
  const portalled = placement !== 'below';

  useLayoutEffect(() => {
    if (!open || !portalled) {
      setPosition(null);
      return undefined;
    }
    const place = () => {
      const anchor = rootRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const width = menuRef.current?.offsetWidth || 256;
      const bottomEdge = placement === 'beside' ? anchor.bottom : anchor.top - 8;
      const left = placement === 'beside' ? anchor.right + 8 : anchor.left;
      setPosition({
        left: Math.max(8, Math.min(left, window.innerWidth - width - 8)),
        bottom: Math.max(8, window.innerHeight - bottomEdge),
        maxHeight: Math.max(80, bottomEdge - 64),
      });
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, placement, portalled]);

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open || (portalled && !position)) return undefined;
    if (focusFirstRef.current) {
      menuRef.current?.querySelector<HTMLElement>(FOCUSABLE_ITEMS)?.focus();
      focusFirstRef.current = false;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') close(true);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [close, open, portalled, position]);

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE_ITEMS));
    if (items.length === 0) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
    items[next]?.focus();
  };

  const menu = open ? (
    <div
      ref={menuRef}
      id={menuId}
      role="menu"
      aria-label={label}
      onKeyDown={onMenuKeyDown}
      style={portalled ? position ?? { visibility: 'hidden' } : undefined}
      // Phones: a header menu is a full-width sheet under the header, so a menu opened
      // mid-bar cannot run off the screen; a portalled menu is already placed on screen.
      className={`${portalled ? 'fixed' : `absolute top-full mt-2 ${align === 'right' ? 'right-0' : 'left-0'}`} z-[60] max-h-[calc(100dvh-5rem)] max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-ui-border bg-ui-surface p-1.5 text-ui-text shadow-ui-panel [&_svg]:stroke-[1.75] ${menuClassName}${portalled ? '' : ' max-md:fixed max-md:inset-x-3 max-md:top-14 max-md:w-auto'}`}
    >
      {children(close)}
    </div>
  ) : null;

  return (
    <div ref={rootRef} className="relative max-md:min-w-0">
      <button
        ref={buttonRef}
        type="button"
        title={title ?? label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={(event) => {
          // Keyboard activation (Enter/Space) arrives without a pointer.
          focusFirstRef.current = event.detail === 0;
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowDown') return;
          event.preventDefault();
          focusFirstRef.current = true;
          if (open) menuRef.current?.querySelector<HTMLElement>(FOCUSABLE_ITEMS)?.focus();
          else setOpen(true);
        }}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {portalled ? createPortal(menu, document.body) : menu}
    </div>
  );
}

export const MENU_ITEM_CLASS =
  'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm font-normal text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus:bg-ui-hover focus:text-ui-text focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus aria-checked:bg-ui-selected aria-checked:font-medium aria-checked:text-ui-text';

export const MENU_LABEL_CLASS = 'px-2.5 pb-1 pt-2 text-[11px] font-medium tracking-wide text-ui-muted';

export const MENU_SEPARATOR_CLASS = 'my-1 border-t border-ui-border';
