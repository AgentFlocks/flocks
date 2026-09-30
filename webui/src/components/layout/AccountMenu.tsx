import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Blocks,
  Bot,
  Brain,
  ChevronRight,
  ChevronUp,
  Crown,
  Gauge,
  Library,
  LogOut,
  Radio,
  RefreshCw,
  ServerCog,
  Settings2,
  Sparkles,
  Wrench,
  type LucideIcon,
} from 'lucide-react';
import { MENU_ITEM_CLASS, MENU_LABEL_CLASS, MENU_SEPARATOR_CLASS } from './HeaderMenu';

export const FLOCKS_LLM_USAGE_URL = 'https://portal.agentflocks.com';

interface AccountMenuProps {
  collapsed: boolean;
  username: string;
  roleLabel: string;
  initial: string;
  version: string | null;
  isAdmin: boolean;
  isPro: boolean;
  proProductName: string;
  hubName: string;
  onNavigate: () => void;
  onCheckUpdate: () => void;
  onLogout: () => void;
}

interface MenuLink {
  name: string;
  href: string;
  icon: LucideIcon;
}

/**
 * The account entry at the bottom of the sidebar. Its menu holds everything
 * that does not change with the workspace: AI capabilities, integrations,
 * system management, usage, the Pro license, updates and sign-out.
 */
export default function AccountMenu({
  collapsed,
  username,
  roleLabel,
  initial,
  version,
  isAdmin,
  isPro,
  proProductName,
  hubName,
  onNavigate,
  onCheckUpdate,
  onLogout,
}: AccountMenuProps) {
  const { t } = useTranslation('nav');
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const focusFirstRef = useRef(false);

  useEffect(() => {
    if (!open) return undefined;
    if (focusFirstRef.current) {
      menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
      focusFirstRef.current = false;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const close = () => setOpen(false);
  const follow = () => {
    close();
    onNavigate();
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
    items[next]?.focus();
  };

  const aiCapabilities: MenuLink[] = [
    { name: t('agents'), href: '/agents', icon: Bot },
    { name: t('skills'), href: '/skills', icon: Sparkles },
    { name: t('tools'), href: '/tools', icon: Wrench },
    { name: t('knowledgeBase'), href: '/workspace', icon: Library },
  ];
  const integrations: MenuLink[] = [
    { name: t('deviceIntegration'), href: '/devices', icon: ServerCog },
    { name: hubName, href: '/hub', icon: Blocks },
    { name: t('models'), href: '/models', icon: Brain },
    { name: t('channels'), href: '/channels', icon: Radio },
  ];

  const renderLinks = (items: MenuLink[]) => items.map((item) => (
    <Link key={item.href} to={item.href} role="menuitem" onClick={follow} className={MENU_ITEM_CLASS}>
      <item.icon className="h-4 w-4 shrink-0 text-zinc-400" />
      <span className="min-w-0 flex-1 truncate">{item.name}</span>
    </Link>
  ));

  return (
    <div ref={rootRef} className={`relative shrink-0 border-t border-zinc-200 dark:border-zinc-800 ${collapsed ? 'p-2' : 'p-3'}`}>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={t('accountMenu')}
          onKeyDown={onMenuKeyDown}
          data-account-menu
          className={`absolute z-[60] max-h-[calc(100dvh-8rem)] w-64 overflow-y-auto rounded-xl border border-zinc-200 bg-white p-1.5 shadow-lg dark:border-zinc-700 dark:bg-zinc-900 ${
            collapsed ? 'bottom-2 left-full ml-2' : 'bottom-full left-3 mb-2'
          }`}
        >
          <div className={MENU_LABEL_CLASS}>{t('aiCapabilities')}</div>
          {renderLinks(aiCapabilities)}
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          <div className={MENU_LABEL_CLASS}>{t('integrationsAndExtensions')}</div>
          {renderLinks(integrations)}
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          <Link to="/settings/preferences" role="menuitem" onClick={follow} className={MENU_ITEM_CLASS}>
            <Settings2 className="h-4 w-4 shrink-0 text-zinc-400" />
            <span className="min-w-0 flex-1 truncate">{t('systemManagement')}</span>
            <ChevronRight className="h-4 w-4 shrink-0 text-zinc-400" />
          </Link>
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          <a href={FLOCKS_LLM_USAGE_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={close} className={MENU_ITEM_CLASS}>
            <Gauge className="h-4 w-4 shrink-0 text-zinc-400" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{t('modelUsage')}</span>
              <span className="block text-[11px] text-zinc-400">{t('modelUsageHint')}</span>
            </span>
          </a>
          {isAdmin && (
            <Link
              to="/settings/flockspro"
              role="menuitem"
              onClick={follow}
              className={`${MENU_ITEM_CLASS} mt-1 border border-zinc-200 bg-gradient-to-r from-zinc-50 to-white dark:border-zinc-700 dark:from-zinc-900 dark:to-zinc-800`}
            >
              <Crown className="h-4 w-4 shrink-0 text-amber-500" />
              <span className="min-w-0 flex-1 truncate">{isPro ? proProductName : t('upgradePro', { name: proProductName })}</span>
              <span className="shrink-0 rounded bg-zinc-900 px-1.5 text-[10px] font-bold leading-4 text-white dark:bg-zinc-100 dark:text-zinc-900">PRO</span>
            </Link>
          )}
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              close();
              onCheckUpdate();
            }}
            className={MENU_ITEM_CLASS}
          >
            <RefreshCw className="h-4 w-4 shrink-0 text-zinc-400" />
            {t('checkUpdate')}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              close();
              onLogout();
            }}
            className={MENU_ITEM_CLASS}
          >
            <LogOut className="h-4 w-4 shrink-0 text-zinc-400" />
            {t('logout')}
          </button>
        </div>
      )}
      <button
        ref={buttonRef}
        type="button"
        onClick={(event) => {
          focusFirstRef.current = event.detail === 0;
          setOpen((value) => !value);
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('accountAndGlobal', { name: username })}
        title={collapsed ? t('accountAndGlobal', { name: username }) : undefined}
        data-tip={collapsed ? t('accountAndGlobal', { name: username }) : undefined}
        className={collapsed
          ? 'flex h-10 w-full items-center justify-center rounded-lg transition-colors hover:bg-white/70 dark:hover:bg-zinc-900'
          : 'relative flex min-h-[58px] w-full items-center gap-2 rounded-lg px-2 py-2 pr-7 text-left transition-colors hover:bg-white/70 dark:hover:bg-zinc-900'}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-xs font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {initial}
        </span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span className="min-w-0 truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">{username}</span>
                <span className="shrink-0 whitespace-nowrap text-xs text-zinc-400 dark:text-zinc-500">{roleLabel}</span>
              </span>
              {version && (
                <span
                  className="mt-1 inline-flex w-fit rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold leading-none text-zinc-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-500"
                  title={t('versionInfo')}
                >
                  {version}
                </span>
              )}
            </span>
            <ChevronUp className={`absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400 transition-transform ${open ? '' : 'rotate-180'}`} />
          </>
        )}
      </button>
    </div>
  );
}
