import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronUp, Gauge, LogOut, RefreshCw, Settings, type LucideIcon } from 'lucide-react';
import HeaderMenu, { MENU_ITEM_CLASS, MENU_SEPARATOR_CLASS } from './HeaderMenu';

export const FLOCKS_LLM_USAGE_URL = 'https://portal.agentflocks.com';

/** An entry an extension registry contributed to the menu. */
export interface AccountMenuExtraItem {
  id: string;
  name: string;
  href: string;
  icon: LucideIcon;
  /** Drawn as a framed call-to-action instead of a plain row. */
  highlight?: boolean;
  /** Short tag shown at the end of the row. */
  badge?: string;
}

interface AccountMenuProps {
  collapsed?: boolean;
  onNavigate?: () => void;
  username: string;
  roleLabel: string;
  initial: string;
  version: string | null;
  update?: { title: string; onClick: () => void } | null;
  /** Extension entries placed in the account menu itself. */
  extensionItems?: AccountMenuExtraItem[];
  onCheckUpdate: () => void;
  onLogout: () => void;
}

/** Account and version details share one footer entry and one menu. */
export default function AccountMenu({
  collapsed = false, onNavigate, username, roleLabel, initial,
  version, update, extensionItems = [], onCheckUpdate, onLogout,
}: AccountMenuProps) {
  const { t } = useTranslation('nav');
  return (
    <HeaderMenu
      label={t('accountAndGlobal', { name: username })}
      placement={collapsed ? 'beside' : 'above'}
      triggerClassName={`group relative flex w-full items-center gap-2 rounded-lg text-left transition-colors hover:bg-ui-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus ${collapsed ? 'h-10 justify-center' : 'min-h-[58px] px-2 py-2 pr-7'}`}
      trigger={(
        <>
          <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ui-subtle text-xs font-medium text-ui-secondary">
            {initial}
            {collapsed && update && <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-amber-500" />}
          </span>
          {!collapsed && <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-baseline gap-1.5">
              <span className="min-w-0 truncate text-sm font-medium text-ui-text">{username}</span>
              <span className="shrink-0 whitespace-nowrap text-xs text-ui-muted">{roleLabel}</span>
            </span>
            <span className="mt-1 flex items-center gap-1.5">
              {version && <span title={t('versionInfo')} className="inline-flex rounded border border-ui-border bg-ui-surface px-1.5 py-0.5 text-[10px] font-medium leading-none text-ui-secondary">{version}</span>}
              {update && <span title={update.title} className="shrink-0 rounded bg-amber-50 px-1 py-0.5 text-[10px] font-semibold text-amber-600 dark:bg-amber-950 dark:text-amber-300">{t('newVersion')}</span>}
            </span>
          </span>}
          {!collapsed && <ChevronUp aria-hidden="true" className="absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 rotate-180 stroke-[1.75] text-ui-secondary transition-transform group-aria-expanded:rotate-0" />}
        </>
      )}
    >
      {(close) => (
        <>
          <div className="px-2.5 py-2">
            <p className="truncate text-sm font-semibold text-ui-text">{username}</p>
            <p className="text-xs text-ui-muted">{roleLabel}</p>
            {version && <p className="mt-1 text-xs text-ui-muted">{version}</p>}
          </div>
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          {/* A plain link: no trailing chevron, which would promise a submenu. */}
          <Link to="/settings/preferences" role="menuitem" onClick={() => { close(); onNavigate?.(); }} className={MENU_ITEM_CLASS}>
            <Settings aria-hidden="true" className="h-4 w-4 shrink-0 text-ui-secondary" />
            {t('systemConfiguration')}
          </Link>
          <a href={FLOCKS_LLM_USAGE_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={() => close()} className={MENU_ITEM_CLASS}>
            <Gauge aria-hidden="true" className="h-4 w-4 shrink-0 text-ui-secondary" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{t('modelUsage')}</span>
              <span className="block text-[11px] text-ui-muted">{t('modelUsageHint')}</span>
            </span>
          </a>
          {extensionItems.map((item) => (
            <Link
              key={item.id}
              to={item.href}
              role="menuitem"
              onClick={() => { close(); onNavigate?.(); }}
              className={item.highlight ? `${MENU_ITEM_CLASS} mt-1 border border-ui-border bg-ui-subtle` : MENU_ITEM_CLASS}
            >
              <item.icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${item.highlight ? 'text-amber-500' : 'text-ui-secondary'}`} />
              <span className="min-w-0 flex-1 truncate">{item.name}</span>
              {item.badge && (
                <span className="shrink-0 rounded bg-ui-action px-1.5 text-[10px] font-semibold leading-4 text-ui-on-action">{item.badge}</span>
              )}
            </Link>
          ))}
          <div className={MENU_SEPARATOR_CLASS} role="separator" />
          {update && (
            <button type="button" role="menuitem" className={MENU_ITEM_CLASS} onClick={() => { close(); update.onClick(); }}>
              <RefreshCw aria-hidden="true" className="h-4 w-4 shrink-0 text-amber-500" />
              {update.title}
            </button>
          )}
          <button type="button" role="menuitem" className={MENU_ITEM_CLASS} onClick={() => { close(); onCheckUpdate(); }}>
            <RefreshCw aria-hidden="true" className="h-4 w-4 shrink-0 text-ui-secondary" />
            {t('checkUpdate')}
          </button>
          <button type="button" role="menuitem" onClick={() => { close(); onLogout(); }} className={MENU_ITEM_CLASS}>
            <LogOut aria-hidden="true" className="h-4 w-4 shrink-0 text-ui-secondary" />
            {t('logout')}
          </button>
        </>
      )}
    </HeaderMenu>
  );
}
