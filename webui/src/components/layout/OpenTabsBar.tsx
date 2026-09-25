import { useEffect, useRef, useState } from 'react';
import type { DragEvent as ReactDragEvent } from 'react';
import { Link } from 'react-router-dom';
import { IconPlus, IconX, type TablerIcon } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

export interface OpenTabsBarTab {
  href: string;
  /** Location to open when the tab is activated (last visited path inside it). */
  path: string;
  name: string;
  icon?: TablerIcon;
}

export interface OpenTabsBarGroup {
  id: string;
  name: string;
  items: Array<{ href: string; name: string; icon?: TablerIcon }>;
}

interface OpenTabsBarProps {
  tabs: OpenTabsBarTab[];
  activeHref: string | null;
  /** Sidebar entries that are not open yet, grouped like the sidebar. */
  closedGroups: OpenTabsBarGroup[];
  onClose: (href: string) => void;
  onOpen: (href: string) => void;
  /** Drag one tab onto another to move it there (browser-style reordering). */
  onReorder?: (fromHref: string, toHref: string) => void;
}

/**
 * Browser-style tab strip above the page content: one tab per visited sidebar
 * entry, closable and draggable, with a "+" menu listing the entries that are
 * not open yet.
 */
export default function OpenTabsBar({ tabs, activeHref, closedGroups, onClose, onOpen, onReorder }: OpenTabsBarProps) {
  const { t } = useTranslation('nav');
  const [menuOpen, setMenuOpen] = useState(false);
  const [draggingHref, setDraggingHref] = useState<string | null>(null);
  const [dragOverHref, setDragOverHref] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const activeTabRef = useRef<HTMLAnchorElement | null>(null);
  const hasClosedItems = closedGroups.some((group) => group.items.length > 0);
  const reorderable = Boolean(onReorder) && tabs.length > 1;

  useEffect(() => {
    if (!menuOpen) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setMenuOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [menuOpen]);

  // The strip scrolls on narrow screens; keep the active tab in view.
  useEffect(() => {
    activeTabRef.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [activeHref, tabs.length]);

  const handleDragStart = (event: ReactDragEvent<HTMLDivElement>, href: string) => {
    setDraggingHref(href);
    setDragOverHref(null);
    try {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', href);
    } catch {
      // Some environments restrict dataTransfer; dragging still works through state.
    }
  };

  const handleDragOver = (event: ReactDragEvent<HTMLDivElement>, href: string) => {
    if (!draggingHref || draggingHref === href) return;
    event.preventDefault();
    try {
      event.dataTransfer.dropEffect = 'move';
    } catch {
      // Ignore dataTransfer restrictions.
    }
    if (dragOverHref !== href) setDragOverHref(href);
  };

  const handleDrop = (event: ReactDragEvent<HTMLDivElement>, href: string) => {
    if (!draggingHref) return;
    event.preventDefault();
    if (draggingHref !== href) onReorder?.(draggingHref, href);
    setDraggingHref(null);
    setDragOverHref(null);
  };

  const handleDragEnd = () => {
    setDraggingHref(null);
    setDragOverHref(null);
  };

  return (
    <div className="relative z-20 flex h-11 shrink-0 items-end gap-1 border-b border-line bg-subtle pl-20 pr-3 lg:pl-3 dark:border-line dark:bg-surface">
      <div
        role="tablist"
        aria-label={t('openTabs')}
        className="flex h-9 min-w-0 items-end gap-1 overflow-x-auto overflow-y-hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {tabs.map((tab) => {
          const active = tab.href === activeHref;
          const Icon = tab.icon;
          const isDragging = draggingHref === tab.href;
          const isDropTarget = dragOverHref === tab.href && draggingHref !== null && !isDragging;
          return (
            <div
              key={tab.href}
              data-tab-href={tab.href}
              draggable={reorderable || undefined}
              onDragStart={reorderable ? (event) => handleDragStart(event, tab.href) : undefined}
              onDragOver={reorderable ? (event) => handleDragOver(event, tab.href) : undefined}
              onDragLeave={reorderable ? () => { if (dragOverHref === tab.href) setDragOverHref(null); } : undefined}
              onDrop={reorderable ? (event) => handleDrop(event, tab.href) : undefined}
              onDragEnd={reorderable ? handleDragEnd : undefined}
              className={`group relative flex h-9 max-w-[220px] shrink-0 items-center rounded-t-lg border border-b-0 transition-colors ${
                active
                  ? '-mb-px border-line bg-canvas text-fg dark:border-line dark:bg-canvas dark:text-fg'
                  : 'border-transparent text-fg-muted hover:bg-hover hover:text-fg dark:text-fg-muted dark:hover:bg-hover dark:hover:text-fg'
              } ${reorderable ? 'cursor-grab active:cursor-grabbing' : ''} ${isDragging ? 'opacity-50' : ''} ${isDropTarget ? 'ring-2 ring-inset ring-focus dark:ring-focus' : ''}`}
            >
              <Link
                to={tab.path}
                role="tab"
                aria-selected={active}
                aria-current={active ? 'page' : undefined}
                ref={active ? activeTabRef : undefined}
                draggable={false}
                title={tab.name}
                className="flex min-w-0 items-center gap-2 py-2 pl-3 pr-1 text-sm font-medium outline-none focus-visible:underline"
              >
                {Icon ? <Icon aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-fg-secondary dark:text-fg-secondary' : 'text-fg-muted dark:text-fg-muted'}`} /> : null}
                <span className="min-w-0 truncate">{tab.name}</span>
              </Link>
              <button
                type="button"
                aria-label={t('closeTab', { title: tab.name })}
                title={t('closeTab', { title: tab.name })}
                onClick={() => onClose(tab.href)}
                className={`ui-btn-icon mr-1.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded transition-colors hover:bg-hover hover:text-fg dark:hover:bg-hover dark:hover:text-fg ${
                  active ? 'text-fg-muted' : 'text-fg opacity-0 group-hover:opacity-100 focus-visible:opacity-100 dark:text-fg'
                }`}
              >
                <IconX aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
      {/* The "+" lives outside the scrolling strip so its menu is never clipped. */}
      <div ref={menuRef} className="relative mb-1 shrink-0">
        <button
          type="button"
          aria-label={t('openPage')}
          title={t('openPage')}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
          className="ui-btn-icon inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-hover hover:text-fg dark:text-fg-muted dark:hover:bg-hover dark:hover:text-fg"
        >
          <IconPlus aria-hidden="true" className="h-4 w-4" />
        </button>
        {menuOpen && (
          <div
            role="menu"
            aria-label={t('openPage')}
            className="absolute left-0 top-full z-30 mt-1 max-h-[70vh] min-w-48 overflow-y-auto rounded-lg border border-line bg-surface py-1 shadow-lg dark:border-line dark:bg-surface"
          >
            {!hasClosedItems ? (
              <div className="px-3 py-2 text-xs text-fg-muted dark:text-fg-muted">{t('allPagesOpen')}</div>
            ) : (
              closedGroups.filter((group) => group.items.length > 0).map((group) => (
                <div key={group.id} className="py-1">
                  {group.name ? (
                    <div className="px-3 pb-1 pt-1 text-xs font-semibold uppercase tracking-wider text-fg-muted dark:text-fg-muted">
                      {group.name}
                    </div>
                  ) : null}
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.href}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setMenuOpen(false);
                          onOpen(item.href);
                        }}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-fg-secondary transition-colors hover:bg-hover hover:text-fg dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg"
                      >
                        {Icon ? <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-fg-muted" /> : null}
                        <span className="min-w-0 truncate">{item.name}</span>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
