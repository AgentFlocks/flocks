import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Bell,
  BookOpen,
  Check,
  ChevronDown,
  CircleHelp,
  Keyboard,
  ListTodo,
  Menu,
  MessageSquareWarning,
  Settings2,
  type LucideIcon,
} from 'lucide-react';
import type { UserNotification } from '@/api/notifications';
import type { NavWorkspaceId } from '@/utils/navPartitions';
import HeaderMenu, { MENU_ITEM_CLASS, MENU_LABEL_CLASS, MENU_SEPARATOR_CLASS } from './HeaderMenu';

export const DOCS_URL = 'https://agentflocks.github.io/flocks-docs/';
export const FEEDBACK_URL = 'https://github.com/AgentFlocks/flocks/issues';

export interface HeaderWorkspace {
  id: NavWorkspaceId;
  name: string;
  icon: LucideIcon;
}

interface WorkspaceHeaderProps {
  productName: string;
  update?: { title: string; onClick: () => void } | null;
  workspaces: HeaderWorkspace[];
  /** Null while navigation data is loading. */
  currentWorkspace: NavWorkspaceId | null;
  onSelectWorkspace: (id: NavWorkspaceId) => void;
  sceneManager: { href: string; hasNew: boolean; active: boolean };
  notifications: UserNotification[];
  onOpenNotification: (notification: UserNotification) => void;
  taskCounts?: { running: number; queued: number } | null;
  onOpenShortcuts: () => void;
  onOpenMobileNav: () => void;
  /** Whether the off-canvas navigation is open (narrow screens). */
  mobileNavOpen?: boolean;
}

const ICON_BUTTON_CLASS =
  'relative flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100';

function NewBadge({ label }: { label: string }) {
  return (
    <span className="ml-auto inline-flex shrink-0 items-center rounded px-1.5 text-[10px] font-semibold leading-4 text-red-700 bg-red-50 dark:bg-red-500/15 dark:text-red-300">
      {label}
    </span>
  );
}

/**
 * The bar above sidebar and content: product name, the workspace switcher
 * (with the scene manager), notifications and help.
 */
export default function WorkspaceHeader({
  productName,
  update,
  workspaces,
  currentWorkspace,
  onSelectWorkspace,
  sceneManager,
  notifications,
  onOpenNotification,
  taskCounts,
  onOpenShortcuts,
  onOpenMobileNav,
  mobileNavOpen = false,
}: WorkspaceHeaderProps) {
  const { t } = useTranslation('nav');
  const navigate = useNavigate();
  const current = workspaces.find((workspace) => workspace.id === currentWorkspace) ?? null;
  const CurrentIcon = current?.icon;
  const activeTasks = (taskCounts?.running ?? 0) + (taskCounts?.queued ?? 0);
  const hasUnread = notifications.length > 0 || activeTasks > 0;
  const brandInitial = (productName.trim().charAt(0) || 'F').toUpperCase();

  return (
    <header
      data-workspace-header
      className="fixed inset-x-0 top-0 z-50 flex h-14 items-center gap-2 border-b border-zinc-200 bg-white px-3 sm:gap-4 sm:px-5 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <button
        type="button"
        onClick={onOpenMobileNav}
        className={`${ICON_BUTTON_CLASS} lg:hidden`}
        aria-label={t('openNav')}
        aria-expanded={mobileNavOpen}
        title={t('openNav')}
      >
        <Menu aria-hidden="true" className="h-5 w-5" />
      </button>

      <Link to="/" className="flex min-w-0 shrink-0 items-center gap-2 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400" title={productName}>
        <span aria-hidden="true" className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-red-600 text-base font-bold text-white sm:flex">
          {brandInitial}
        </span>
        <span className="max-w-[9rem] truncate text-lg font-bold tracking-tight text-zinc-900 sm:max-w-[14rem] sm:text-xl dark:text-zinc-50">
          {productName}
        </span>
      </Link>
      {update && (
        <button
          type="button"
          onClick={update.onClick}
          title={update.title}
          aria-label={update.title}
          className="relative -ml-1 inline-flex h-4 shrink-0 items-center rounded-sm bg-amber-50 px-1 text-[10px] font-bold leading-none text-amber-600 transition-colors hover:bg-amber-100 dark:bg-amber-950/70 dark:text-amber-300 dark:hover:bg-amber-900"
        >
          {t('newVersion')}
          <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-amber-400" />
        </button>
      )}

      <div className="h-6 shrink-0 border-l border-zinc-200 dark:border-zinc-800" aria-hidden="true" />

      <HeaderMenu
        label={t('switchWorkspace')}
        title={current?.name ?? t('switchWorkspace')}
        triggerClassName="flex h-9 min-w-0 max-w-[11rem] items-center gap-2 rounded-lg px-2 text-sm font-semibold text-zinc-800 transition-colors hover:bg-zinc-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 sm:max-w-xs sm:px-3 dark:text-zinc-100 dark:hover:bg-zinc-800"
        trigger={(
          <>
            {CurrentIcon && <CurrentIcon className="hidden h-4 w-4 shrink-0 text-zinc-500 sm:block dark:text-zinc-400" />}
            <span className="min-w-0 truncate" data-current-workspace>{current?.name ?? t('agentWorkspace')}</span>
            {sceneManager.hasNew && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" aria-hidden="true" />}
            <ChevronDown className="h-4 w-4 shrink-0 text-zinc-400" />
          </>
        )}
        menuClassName="w-64"
      >
        {(close) => (
          <>
            {workspaces.map((workspace) => {
              const selected = workspace.id === currentWorkspace;
              const Icon = workspace.icon;
              return (
                <button
                  key={workspace.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => {
                    close();
                    onSelectWorkspace(workspace.id);
                  }}
                  className={`${MENU_ITEM_CLASS} ${selected ? 'font-semibold text-zinc-950 dark:text-white' : ''}`}
                >
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                    {selected && <Check className="h-4 w-4" />}
                  </span>
                  <Icon className="h-4 w-4 shrink-0 text-zinc-400" />
                  <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                </button>
              );
            })}
            <div className={MENU_SEPARATOR_CLASS} role="separator" />
            <button
              type="button"
              role="menuitem"
              aria-current={sceneManager.active ? 'page' : undefined}
              onClick={() => {
                close();
                navigate(sceneManager.href);
              }}
              className={MENU_ITEM_CLASS}
            >
              <span className="h-4 w-4 shrink-0" />
              <Settings2 className="h-4 w-4 shrink-0 text-zinc-400" />
              <span className="min-w-0 flex-1 truncate">{t('sceneManagement')}</span>
              {sceneManager.hasNew && <NewBadge label={t('newBadge')} />}
            </button>
          </>
        )}
      </HeaderMenu>

      <div className="flex-1" />

      <div className="flex shrink-0 items-center gap-0.5">
        <HeaderMenu
          label={t('notifications')}
          align="right"
          menuClassName="w-80"
          triggerClassName={ICON_BUTTON_CLASS}
          trigger={(
            <>
              <Bell className="h-[18px] w-[18px]" />
              {hasUnread && (
                <span data-notification-dot className="absolute right-2 top-2 h-2 w-2 rounded-full bg-red-500 ring-2 ring-white dark:ring-zinc-900" />
              )}
            </>
          )}
        >
          {(close) => (
            <>
              <div className={MENU_LABEL_CLASS}>{t('notifications')}</div>
              {activeTasks > 0 && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    close();
                    navigate('/tasks');
                  }}
                  className={MENU_ITEM_CLASS}
                >
                  <ListTodo className="h-4 w-4 shrink-0 text-zinc-400" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{t('tasksInProgress', { count: activeTasks })}</span>
                    <span className="block text-xs text-zinc-400">{t('viewTaskCenter')}</span>
                  </span>
                </button>
              )}
              {notifications.map((notification) => (
                <button
                  key={notification.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    close();
                    onOpenNotification(notification);
                  }}
                  className={MENU_ITEM_CLASS}
                >
                  <Bell className="h-4 w-4 shrink-0 self-start text-zinc-400" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{notification.title}</span>
                    {notification.summary && (
                      <span className="line-clamp-2 block text-xs text-zinc-400">{notification.summary}</span>
                    )}
                  </span>
                </button>
              ))}
              {!hasUnread && (
                <p className="px-2.5 py-3 text-sm text-zinc-400">{t('noNotifications')}</p>
              )}
            </>
          )}
        </HeaderMenu>

        <HeaderMenu
          label={t('help')}
          align="right"
          menuClassName="w-60"
          triggerClassName={ICON_BUTTON_CLASS}
          trigger={<CircleHelp className="h-[18px] w-[18px]" />}
        >
          {(close) => (
            <>
              <div className={MENU_LABEL_CLASS}>{t('helpAndSupport')}</div>
              <a href={DOCS_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={() => close()} className={MENU_ITEM_CLASS}>
                <BookOpen className="h-4 w-4 shrink-0 text-zinc-400" />
                {t('helpDocs')}
              </a>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  onOpenShortcuts();
                }}
                className={MENU_ITEM_CLASS}
              >
                <Keyboard className="h-4 w-4 shrink-0 text-zinc-400" />
                {t('helpShortcuts')}
              </button>
              <a href={FEEDBACK_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={() => close()} className={MENU_ITEM_CLASS}>
                <MessageSquareWarning className="h-4 w-4 shrink-0 text-zinc-400" />
                {t('helpFeedback')}
              </a>
            </>
          )}
        </HeaderMenu>
      </div>
    </header>
  );
}
