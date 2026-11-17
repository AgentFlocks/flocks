import { Suspense, lazy, useState, type ComponentType, type LazyExoticComponent } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronUp, Gauge, LogOut, RefreshCw, Settings, type LucideIcon } from 'lucide-react';
import LazyLoadErrorBoundary from '@/components/common/LazyLoadErrorBoundary';
import { preloadI18nNamespaces } from '@/i18nResources';
import type { ExtensionDialog } from '@ext/registry';
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
  /** How the tag is drawn: the action colour (default), a quiet outline, or red. */
  badgeTone?: 'action' | 'muted' | 'danger';
  /** Opened in a dialog over the page instead of following `href`. */
  dialog?: ExtensionDialog;
}

/** The red tag of an edition (`Pro`): beside the version, and on an entry drawn in that tone. */
export const EDITION_BADGE_CLASS =
  'shrink-0 rounded bg-red-600 px-1.5 text-[10px] font-semibold leading-4 text-white dark:bg-red-500';
const BADGE_CLASSES: Record<NonNullable<AccountMenuExtraItem['badgeTone']>, string> = {
  action: 'shrink-0 rounded bg-ui-action px-1.5 text-[10px] font-semibold leading-4 text-ui-on-action',
  muted: 'shrink-0 rounded border border-ui-border px-1.5 text-[10px] font-semibold leading-4 text-ui-secondary',
  danger: EDITION_BADGE_CLASS,
};

type DialogComponent = LazyExoticComponent<ComponentType<{ onClose: () => void }>>;
// One lazy component per dialog loader, so reopening a dialog does not load (or remount) it anew.
const dialogComponents = new WeakMap<ExtensionDialog, DialogComponent>();
function dialogComponent(dialog: ExtensionDialog): DialogComponent {
  let component = dialogComponents.get(dialog);
  if (!component) {
    component = lazy(() => Promise.all([dialog.load(), preloadI18nNamespaces(dialog.i18nNamespaces ?? [])]).then(([module]) => module));
    dialogComponents.set(dialog, component);
  }
  return component;
}

interface AccountMenuProps {
  collapsed?: boolean;
  onNavigate?: () => void;
  username: string;
  roleLabel: string;
  initial: string;
  version: string | null;
  /** The edition this build is (`Pro`), drawn as a red tag beside the version; none for the open edition. */
  editionBadge?: string | null;
  update?: { title: string; onClick: () => void } | null;
  /** Extension entries placed in the account menu itself. */
  extensionItems?: AccountMenuExtraItem[];
  onCheckUpdate: () => void;
  onLogout: () => void;
}

/** Account and version details share one footer entry and one menu. */
export default function AccountMenu({
  collapsed = false, onNavigate, username, roleLabel, initial,
  version, editionBadge, update, extensionItems = [], onCheckUpdate, onLogout,
}: AccountMenuProps) {
  const { t } = useTranslation('nav');
  // The entry whose dialog is open over the page, if any.
  const [dialogItem, setDialogItem] = useState<AccountMenuExtraItem | null>(null);
  const Dialog = dialogItem?.dialog ? dialogComponent(dialogItem.dialog) : null;
  const edition = editionBadge?.trim() || null;
  return (
    <>
    <HeaderMenu
      label={t('accountAndGlobal', { name: username })}
      placement={collapsed ? 'beside' : 'above'}
      triggerClassName={`group relative flex w-full items-center gap-2 rounded-lg text-left transition-colors hover:bg-ui-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus ${collapsed ? 'h-10 justify-center' : 'min-h-[58px] px-2 py-2 pr-7'}`}
      trigger={(
        <>
          <span className="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ui-subtle text-xs font-medium text-ui-secondary">
            {initial}
            {collapsed && update && <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-amber-500" />}
            {/* The edition at a glance, collapsed or not: on the avatar, where the name and version leave no room. */}
            {edition && (
              <span
                data-testid="edition-badge"
                className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 rounded bg-red-600 px-1 text-[9px] font-semibold leading-3 text-white ring-2 ring-ui-sidebar dark:bg-red-500"
              >
                {edition}
              </span>
            )}
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
            {(version || edition) && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-ui-muted">
                {version}
                {edition && <span className={EDITION_BADGE_CLASS}>{edition}</span>}
              </p>
            )}
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
          {extensionItems.map((item) => {
            const className = item.highlight ? `${MENU_ITEM_CLASS} mt-1 border border-ui-border bg-ui-subtle` : MENU_ITEM_CLASS;
            const body = (
              <>
                <item.icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${item.highlight ? 'text-amber-500' : 'text-ui-secondary'}`} />
                <span className="min-w-0 flex-1 truncate text-left">{item.name}</span>
                {item.badge && <span className={BADGE_CLASSES[item.badgeTone ?? 'action']}>{item.badge}</span>}
              </>
            );
            return item.dialog ? (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                aria-haspopup="dialog"
                onClick={() => { close(); setDialogItem(item); }}
                className={className}
              >
                {body}
              </button>
            ) : (
              <Link key={item.id} to={item.href} role="menuitem" onClick={() => { close(); onNavigate?.(); }} className={className}>
                {body}
              </Link>
            );
          })}
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
    {/* On the document body: the sidebar is a containing block for fixed boxes, and would hold the dialog in it. */}
    {Dialog && dialogItem && createPortal(
      <LazyLoadErrorBoundary mode="overlay">
        <Suspense fallback={null}>
          <Dialog
            onClose={() => {
              setDialogItem(null);
              onNavigate?.();
            }}
          />
        </Suspense>
      </LazyLoadErrorBoundary>,
      document.body,
    )}
    </>
  );
}
