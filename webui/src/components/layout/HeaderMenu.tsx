import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

interface HeaderMenuProps {
  /** Accessible name of the trigger. */
  label: string;
  trigger: ReactNode;
  triggerClassName: string;
  /** Tooltip on the trigger; defaults to the label. */
  title?: string;
  align?: 'left' | 'right';
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
  menuClassName = 'w-64',
  children,
}: HeaderMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const focusFirstRef = useRef(false);
  const menuId = useId();

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    if (focusFirstRef.current) {
      menuRef.current?.querySelector<HTMLElement>(FOCUSABLE_ITEMS)?.focus();
      focusFirstRef.current = false;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
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
  }, [close, open]);

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

  return (
    <div ref={rootRef} className="relative">
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
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
          className={`absolute top-full z-[60] mt-2 max-h-[calc(100dvh-5rem)] max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1.5 shadow-lg dark:border-zinc-700 dark:bg-zinc-900 ${align === 'right' ? 'right-0' : 'left-0'} ${menuClassName}`}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

export const MENU_ITEM_CLASS =
  'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-zinc-700 transition-colors hover:bg-zinc-100 focus:bg-zinc-100 focus:outline-none dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800';

export const MENU_LABEL_CLASS = 'px-2.5 pb-1 pt-2 text-[11px] font-medium tracking-wide text-zinc-400 dark:text-zinc-500';

export const MENU_SEPARATOR_CLASS = 'my-1 border-t border-zinc-100 dark:border-zinc-800';
