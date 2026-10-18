import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronUp, Crown, Gauge, LogOut, RefreshCw, Settings } from 'lucide-react';
import HeaderMenu, { MENU_ITEM_CLASS, MENU_SEPARATOR_CLASS } from './HeaderMenu';

export const FLOCKS_LLM_USAGE_URL = 'https://portal.agentflocks.com';

interface AccountMenuProps {
  collapsed?: boolean;
  onNavigate?: () => void;
  username: string;
  roleLabel: string;
  initial: string;
  version: string | null;
  update?: { title: string; onClick: () => void } | null;
  isAdmin: boolean;
  isPro: boolean;
  proProductName: string;
  onCheckUpdate: () => void;
  onLogout: () => void;
}

/** Account and version details share one footer entry and one menu. */
export default function AccountMenu({
  collapsed = false, onNavigate, username, roleLabel, initial,
  version, update, isAdmin, isPro, proProductName, onCheckUpdate, onLogout,
}: AccountMenuProps) {
  const { t } = useTranslation('nav');
  return (
    <HeaderMenu
      label={t('accountAndGlobal', { name: username })}
      placement={collapsed ? 'beside' : 'above'}
      triggerClassName={`group relative flex w-full items-center gap-2 rounded-lg text-left hover:bg-zinc-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 dark:hover:bg-zinc-800 ${collapsed ? 'h-10 justify-center' : 'min-h-[58px] px-2 py-2 pr-7'}`}
      trigger={(
        <>
          <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-xs font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {initial}
            {collapsed && update && <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-amber-500" />}
          </span>
          {!collapsed && <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-baseline gap-1.5">
              <span className="min-w-0 truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">{username}</span>
              <span className="shrink-0 whitespace-nowrap text-xs text-zinc-400 dark:text-zinc-500">{roleLabel}</span>
            </span>
            <span className="mt-1 flex items-center gap-1.5">
              {version && <span title={t('versionInfo')} className="inline-flex rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold leading-none text-zinc-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-500">{version}</span>}
              {update && <span title={update.title} className="shrink-0 rounded bg-amber-50 px-1 py-0.5 text-[10px] font-semibold text-amber-600 dark:bg-amber-950 dark:text-amber-300">{t('newVersion')}</span>}
            </span>
          </span>}
          {!collapsed && <ChevronUp className="absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 rotate-180 text-zinc-400 transition-transform group-aria-expanded:rotate-0" />}
        </>
      )}
    >
      {(close) => (
        <>
          <div className="px-2.5 py-2">
            <p className="truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">{username}</p>
            <p className="text-xs text-zinc-400">{roleLabel}</p>
            {version && <p className="mt-1 text-xs text-zinc-400">{version}</p>}
          </div>
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          <Link to="/settings/preferences" role="menuitem" onClick={() => { close(); onNavigate?.(); }} className={MENU_ITEM_CLASS}>
            <Settings className="h-4 w-4 shrink-0 text-zinc-400" />
            {t('systemConfiguration')}
          </Link>
          <a href={FLOCKS_LLM_USAGE_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={() => close()} className={MENU_ITEM_CLASS}>
            <Gauge className="h-4 w-4 shrink-0 text-zinc-400" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{t('modelUsage')}</span>
              <span className="block text-[11px] text-zinc-400">{t('modelUsageHint')}</span>
            </span>
          </a>
          {isAdmin && (
            <Link to="/settings/flockspro" role="menuitem" onClick={() => { close(); onNavigate?.(); }}
              className={`${MENU_ITEM_CLASS} mt-1 border border-zinc-200 bg-gradient-to-r from-zinc-50 to-white dark:border-zinc-700 dark:from-zinc-900 dark:to-zinc-800`}>
              <Crown className="h-4 w-4 shrink-0 text-amber-500" />
              <span className="min-w-0 flex-1 truncate">{isPro ? proProductName : t('upgradePro', { name: proProductName })}</span>
              <span className="shrink-0 rounded bg-zinc-900 px-1.5 text-[10px] font-bold leading-4 text-white dark:bg-zinc-100 dark:text-zinc-900">PRO</span>
            </Link>
          )}
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          {update && (
            <button type="button" role="menuitem" className={MENU_ITEM_CLASS} onClick={() => { close(); update.onClick(); }}>
              <RefreshCw className="h-4 w-4 shrink-0 text-amber-500" />
              {update.title}
            </button>
          )}
          <button type="button" role="menuitem" className={MENU_ITEM_CLASS} onClick={() => { close(); onCheckUpdate(); }}>
            <RefreshCw className="h-4 w-4 shrink-0 text-zinc-400" />
            {t('checkUpdate')}
          </button>
          <button type="button" role="menuitem" onClick={() => { close(); onLogout(); }} className={MENU_ITEM_CLASS}>
            <LogOut className="h-4 w-4 shrink-0 text-zinc-400" />
            {t('logout')}
          </button>
        </>
      )}
    </HeaderMenu>
  );
}
