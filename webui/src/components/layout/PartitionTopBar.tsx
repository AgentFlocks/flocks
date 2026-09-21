import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router-dom';
import { Settings, type LucideIcon } from 'lucide-react';
import type { NavPartitionId } from '@/utils/navPartitions';

export interface PartitionTopBarItem {
  id: NavPartitionId;
  name: string;
  icon: LucideIcon;
}

interface PartitionTopBarProps {
  items: PartitionTopBarItem[];
  activeId: NavPartitionId | null;
  onSelect: (id: NavPartitionId) => void;
  action?: { href: string; name: string; icon: LucideIcon; active?: boolean; badge?: boolean };
  settingsGroups?: Array<{ name: string; items: Array<{ name: string; href: string; icon: LucideIcon; external?: boolean }> }>;
}

/**
 * Installed workspace tabs above the content. Switching a partition swaps the
 * sidebar menu and lands on the page that partition was left on; the panes of
 * the other partitions stay alive underneath.
 */
export default function PartitionTopBar({
  items,
  activeId,
  onSelect,
  action,
  settingsGroups = [],
}: PartitionTopBarProps) {
  const { t } = useTranslation('nav');
  const location = useLocation();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const settingsButtonRef = useRef<HTMLButtonElement>(null);
  const keyboardOpen = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (closeTimer.current) clearTimeout(closeTimer.current); }, []);

  useEffect(() => { setSettingsOpen(false); }, [location.pathname, location.search]);
  useEffect(() => {
    if (!settingsOpen) return;
    if (keyboardOpen.current) {
      settingsRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
      keyboardOpen.current = false;
    }
    const closeOutside = (event: PointerEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) setSettingsOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setSettingsOpen(false); settingsButtonRef.current?.focus(); }
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeEscape);
    };
  }, [settingsOpen]);

  // z-[35]: the settings menu hangs below the bar over page content, so the bar
  // must sit above page chrome (contract pages use z-30 for sticky toolbars)
  // while staying under the mobile drawer backdrop (z-40) and page dialogs (z-40/50).
  return (
    <div data-partition-top-bar className="relative z-[35] flex h-11 shrink-0 items-center gap-1 border-b border-zinc-200 bg-zinc-100 pl-16 pr-2 lg:pl-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex min-w-0 flex-1 items-center gap-1">
      <div
        role="tablist"
        aria-label={t('partitions')}
        className="flex min-w-0 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {items.map((item) => {
          const active = item.id === activeId;
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-current={active ? 'page' : undefined}
              onClick={() => onSelect(item.id)}
              title={item.name}
              className={`flex h-8 shrink-0 items-center gap-2 rounded-lg px-2 sm:px-3 text-sm font-semibold transition-colors ${
                active
                  ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                  : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100'
              }`}
            >
              <Icon className={`hidden h-4 w-4 shrink-0 sm:block ${active ? 'text-zinc-600 dark:text-zinc-300' : 'text-zinc-400 dark:text-zinc-500'}`} />
              <span className="max-w-36 truncate sm:max-w-56">{item.name}</span>
            </button>
          );
        })}
      </div>
      {action && (
        <Link
          to={action.href}
          title={action.name}
          aria-current={action.active ? 'page' : undefined}
          aria-label={action.name}
          className={`inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-dashed px-2 text-xs font-normal transition-colors ${
            action.active
              ? 'border-zinc-400 text-zinc-800 dark:border-zinc-600 dark:text-zinc-100'
              : 'border-zinc-300 text-zinc-500 hover:bg-white/60 hover:text-zinc-800 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-100'
          }`}
        >
          <action.icon className="h-3.5 w-3.5 shrink-0" />
          <span className="whitespace-nowrap">{action.name}</span>
          {action.badge && <span className="rounded bg-emerald-100 px-1 text-[9px] font-semibold leading-4 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">NEW</span>}
        </Link>
      )}
      </div>
      <div ref={settingsRef} className="relative ml-auto shrink-0"
        onMouseEnter={() => { if (closeTimer.current) clearTimeout(closeTimer.current); setSettingsOpen(true); }}
        onMouseLeave={() => { closeTimer.current = setTimeout(() => setSettingsOpen(false), 120); }}
        onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setSettingsOpen(false); }}>
        <button ref={settingsButtonRef} type="button" title={t('partitionSettings')} aria-label={t('partitionSettings')}
          aria-haspopup="menu" aria-expanded={settingsOpen} aria-controls="topbar-settings-menu"
          onClick={() => setSettingsOpen(true)}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowDown') return;
            event.preventDefault();
            if (settingsOpen) settingsRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
            else { keyboardOpen.current = true; setSettingsOpen(true); }
          }}
          className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${activeId === 'settings' ? 'bg-white text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50' : 'text-zinc-500 hover:bg-white/60 dark:text-zinc-400 dark:hover:bg-zinc-800'}`}>
          <Settings className="h-4 w-4" />
        </button>
        {settingsOpen && <div className="absolute right-0 top-full z-30 pt-1">
          <div id="topbar-settings-menu" role="menu" aria-label={t('partitionSettings')}
            onKeyDown={(event) => {
              if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              const entries = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'));
              const index = entries.indexOf(document.activeElement as HTMLElement);
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? entries.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + entries.length) % entries.length;
              entries[next]?.focus();
            }}
            className="max-h-[calc(100dvh-3.5rem)] w-60 max-w-[calc(100vw-1rem)] overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1.5 shadow-lg dark:border-zinc-700 dark:bg-zinc-900">
            {settingsGroups.map((group) => <div key={group.name} role="group" aria-label={group.name}>
              <p className="px-2 py-1.5 text-xs text-zinc-400">{group.name}</p>
              {group.items.map((item) => <Link key={item.href} to={item.href} role="menuitem" target={item.external ? '_blank' : undefined} rel={item.external ? 'noopener noreferrer' : undefined}
                onClick={() => setSettingsOpen(false)} className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-zinc-700 hover:bg-zinc-100 focus:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800">
                <item.icon className="h-4 w-4 shrink-0" /><span>{item.name}</span>
              </Link>)}
            </div>)}
          </div>
        </div>}
      </div>
    </div>
  );
}
