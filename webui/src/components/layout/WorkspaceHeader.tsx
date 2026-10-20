import { useContext } from 'react';
import { ThemeContext, type ThemeMode } from '@/contexts/ThemeContext';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Bell,
  BookOpen,
  CircleHelp,
  Keyboard,
  ListTodo,
  Menu,
  MessageSquareWarning,
  Languages,
  Layers,
  Monitor,
  Sun,
  Moon,
  Check,
  type LucideIcon,
} from 'lucide-react';
import type { UserNotification } from '@/api/notifications';
import { isSceneSuitesPath, SCENE_SUITES_PATH, type NavWorkspaceId } from '@/utils/navPartitions';
import HeaderMenu, { MENU_ITEM_CLASS, MENU_LABEL_CLASS, MENU_SEPARATOR_CLASS } from './HeaderMenu';

export const DOCS_URL = 'https://agentflocks.github.io/flocks-docs/';
export const FEEDBACK_URL = 'https://github.com/AgentFlocks/flocks/issues';

export interface HeaderWorkspace {
  id: NavWorkspaceId;
  name: string;
  icon: LucideIcon;
  separated?: boolean;
}

interface WorkspaceHeaderProps {
  productName: string;
  /** Without a logo the mark shows the product name's initial. */
  logoUrl?: string | null;
  workspaces: HeaderWorkspace[];
  /** Null while navigation data is loading. */
  currentWorkspace: NavWorkspaceId | null;
  onSelectWorkspace: (id: NavWorkspaceId) => void;
  hasUnseenSuites: boolean;
  onOpenSceneManager: () => void;
  notifications: UserNotification[];
  onOpenNotification: (notification: UserNotification) => void;
  taskCounts?: { running: number; queued: number } | null;
  onOpenShortcuts: () => void;
  onOpenMobileNav: () => void;
}

const ICON_BUTTON_CLASS =
  'relative flex h-8 w-8 shrink-0 sm:h-9 sm:w-9 items-center justify-center rounded-lg text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus [&_svg]:stroke-[1.75]';

const moduleClassName = (selected: boolean, separated = false) =>
  `relative flex shrink-0 items-center gap-1 border-b-2 px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus ${separated ? 'ml-3 before:absolute before:-left-1.5 before:top-4 before:h-6 before:border-l before:border-ui-border' : ''} ${selected ? 'border-red-600 font-medium text-ui-text' : 'border-transparent font-normal text-ui-text hover:bg-ui-hover hover:text-ui-text'}`;

/**
 * The bar above sidebar and content: product name, module navigation,
 * display preferences, notifications and help.
 */
export default function WorkspaceHeader({
  productName,
  logoUrl,
  workspaces,
  currentWorkspace,
  onSelectWorkspace,
  hasUnseenSuites,
  onOpenSceneManager,
  notifications,
  onOpenNotification,
  taskCounts,
  onOpenShortcuts,
  onOpenMobileNav,
}: WorkspaceHeaderProps) {
  const { t, i18n } = useTranslation('nav');
  const { mode, setTheme } = useContext(ThemeContext);
  const ThemeIcon = mode === 'system' ? Monitor : mode === 'dark' ? Moon : Sun;
  const themeOptions: { mode: ThemeMode; label: string; icon: LucideIcon }[] = [
    { mode: 'system', label: t('systemTheme'), icon: Monitor },
    { mode: 'light', label: t('lightTheme'), icon: Sun },
    { mode: 'dark', label: t('darkTheme'), icon: Moon },
  ];
  const navigate = useNavigate();
  const location = useLocation();
  const viewingScenes = isSceneSuitesPath(location.pathname);
  const activeTasks = (taskCounts?.running ?? 0) + (taskCounts?.queued ?? 0);
  const hasUnread = notifications.length > 0 || activeTasks > 0;
  const brandInitial = (productName.trim().charAt(0) || 'F').toUpperCase();

  return (
    <header
      data-workspace-header
      className="fixed inset-x-0 top-0 z-50 flex h-14 items-center gap-1 border-b border-ui-border bg-ui-surface px-2 sm:gap-3 sm:px-5 lg:gap-0 lg:pl-0"
    >
      <button
        type="button"
        onClick={onOpenMobileNav}
        className={`${ICON_BUTTON_CLASS} lg:hidden`}
        aria-label={t('openNav')}
        title={t('openNav')}
      >
        <Menu className="h-5 w-5" />
      </button>

      <Link to="/" className="flex min-w-0 shrink-0 items-center gap-2 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus lg:w-[calc(var(--sidebar-expanded-width)-1px)] lg:px-5" title={productName}>
        {logoUrl ? (
          <img src={logoUrl} alt="" aria-hidden="true" data-brand-logo className="h-8 w-8 shrink-0" />
        ) : (
          <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-red-600 text-base font-semibold text-white sm:flex">
            {brandInitial}
          </span>
        )}
        <span className="hidden min-w-0 max-w-[9rem] truncate sm:inline text-lg font-semibold tracking-tight text-ui-text sm:max-w-[14rem] sm:text-xl">
          {productName}
        </span>
      </Link>


      <div className="h-6 shrink-0 border-l border-ui-border" aria-hidden="true" />

      <nav aria-label={t('modules')} className="flex h-full min-w-0 flex-1 items-stretch overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {workspaces.map((workspace) => {
          const selected = workspace.id === currentWorkspace;
          const Icon = workspace.icon;
          return (
            <button
              key={workspace.id}
              type="button"
              onClick={() => onSelectWorkspace(workspace.id)}
              aria-current={selected ? 'page' : undefined}
              className={moduleClassName(selected, workspace.separated)}
            >
              <Icon className="hidden h-4 w-4 shrink-0 stroke-[1.75] xl:block" />
              <span className="whitespace-nowrap">{workspace.name}</span>
            </button>
          );
        })}
        <Link
          to={SCENE_SUITES_PATH}
          onClick={onOpenSceneManager}
          aria-label={t('sceneManagement')}
          aria-current={viewingScenes ? 'page' : undefined}
          className={moduleClassName(viewingScenes, true)}
        >
          <Layers className="hidden h-4 w-4 shrink-0 xl:block" />
          <span className="whitespace-nowrap">{t('sceneManagement')}</span>
          {hasUnseenSuites && <>
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" aria-hidden="true" />
            <span className="sr-only">{t('newBadge')}</span>
          </>}
        </Link>
      </nav>

      <div className="flex shrink-0 items-center sm:gap-0.5 lg:ml-3">
        <HeaderMenu label={t('language')} align="right" menuClassName="w-40" triggerClassName={ICON_BUTTON_CLASS} trigger={<Languages className="h-[18px] w-[18px]" />}>
          {(close) => (
            <>
              <div className={MENU_LABEL_CLASS}>{t('language')}</div>
              {[{ code: 'zh-CN', label: '简体中文' }, { code: 'en-US', label: 'English' }].map((language) => {
                const selected = i18n.language.startsWith(language.code.slice(0, 2));
                return (
                  <button key={language.code} type="button" role="menuitemradio" aria-checked={selected} className={MENU_ITEM_CLASS}
                    onClick={() => { void i18n.changeLanguage(language.code); close(true); }}>
                    <span className="flex-1">{language.label}</span>
                    {selected && <Check className="h-4 w-4" />}
                  </button>
                );
              })}
            </>
          )}
        </HeaderMenu>

        <HeaderMenu label={t('theme')} align="right" menuClassName="w-40" triggerClassName={ICON_BUTTON_CLASS} trigger={<ThemeIcon className="h-[18px] w-[18px]" />}>
          {(close) => (
            <>
              <div className={MENU_LABEL_CLASS}>{t('theme')}</div>
              {themeOptions.map((option) => (
                <button key={option.mode} type="button" role="menuitemradio" aria-checked={mode === option.mode} className={MENU_ITEM_CLASS}
                  onClick={() => { setTheme(option.mode); close(true); }}>
                  <option.icon className="h-4 w-4 shrink-0" />
                  <span className="flex-1">{option.label}</span>
                  {mode === option.mode && <Check className="h-4 w-4" />}
                </button>
              ))}
            </>
          )}
        </HeaderMenu>

        <HeaderMenu
          label={t('notifications')}
          align="right"
          menuClassName="w-80"
          triggerClassName={ICON_BUTTON_CLASS}
          trigger={(
            <>
              <Bell className="h-[18px] w-[18px]" />
              {hasUnread && (
                <span data-notification-dot className="absolute right-2 top-2 h-2 w-2 rounded-full bg-red-500 ring-2 ring-ui-surface" />
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
                  <ListTodo className="h-4 w-4 shrink-0 text-ui-secondary" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{t('tasksInProgress', { count: activeTasks })}</span>
                    <span className="block text-xs text-ui-muted">{t('viewTaskCenter')}</span>
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
                  <Bell className="h-4 w-4 shrink-0 self-start text-ui-secondary" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{notification.title}</span>
                    {notification.summary && (
                      <span className="line-clamp-2 block text-xs text-ui-muted">{notification.summary}</span>
                    )}
                  </span>
                </button>
              ))}
              {!hasUnread && (
                <p className="px-2.5 py-3 text-sm text-ui-muted">{t('noNotifications')}</p>
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
                <BookOpen className="h-4 w-4 shrink-0 text-ui-secondary" />
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
                <Keyboard className="h-4 w-4 shrink-0 text-ui-secondary" />
                {t('helpShortcuts')}
              </button>
              <a href={FEEDBACK_URL} target="_blank" rel="noopener noreferrer" role="menuitem" onClick={() => close()} className={MENU_ITEM_CLASS}>
                <MessageSquareWarning className="h-4 w-4 shrink-0 text-ui-secondary" />
                {t('helpFeedback')}
              </a>
            </>
          )}
        </HeaderMenu>
      </div>
    </header>
  );
}
