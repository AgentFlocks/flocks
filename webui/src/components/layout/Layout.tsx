import { Outlet, Link, useLocation, matchPath } from 'react-router-dom';
import {
  Home,
  MessageSquare,
  Bot,
  Workflow,
  ListTodo,
  Wrench,
  Brain,
  BookOpen,
  X,
  ChevronLeft,
  ChevronRight,
  Menu,
  Radio,
  FolderOpen,
  Sparkles,
  ArrowUpCircle,
  UserCog,
  Archive,
} from 'lucide-react';
import { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '@/components/common/LanguageSwitcher';
import OnboardingModal, { isOnboardingDismissed } from '@/components/common/OnboardingModal';
import UpdateModal, { UPDATE_DISMISSED_KEY } from '@/components/common/UpdateModal';
import NotificationModal from '@/components/common/NotificationModal';
import { checkUpdate } from '@/api/update';
import { ackNotification, getActiveNotifications, type UserNotification } from '@/api/notifications';
import { useAuth } from '@/contexts/AuthContext';

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 60_000;

export default function Layout() {
  const location = useLocation();
  const { user } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const isHome = location.pathname === '/';
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const { t, i18n } = useTranslation('nav');
  const [hasUpdate, setHasUpdate] = useState(false);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const lastUpdateCheckAtRef = useRef(0);
  const checkingUpdateRef = useRef(false);
  const lastPromptedVersionRef = useRef<string | null>(null);
  const [notifications, setNotifications] = useState<UserNotification[]>([]);
  const [acknowledgingNotificationIds, setAcknowledgingNotificationIds] = useState<string[]>([]);
  const lastNotificationFetchKeyRef = useRef<string | null>(null);
  // useLayoutEffect runs synchronously before paint, so there's no flash on initial load.
  // It also re-runs when the user navigates back to /, covering both cases in one place.
  useLayoutEffect(() => {
    if (isHome && !isOnboardingDismissed()) {
      setShowOnboarding(true);
    }
  }, [isHome]);

  const handleOpenOnboarding = useCallback(() => setShowOnboarding(true), []);

  useEffect(() => {
    window.addEventListener('flocks:open-onboarding', handleOpenOnboarding);
    return () => window.removeEventListener('flocks:open-onboarding', handleOpenOnboarding);
  }, [handleOpenOnboarding]);

  const refreshUpdateStatus = useCallback(async (force = false) => {
    const now = Date.now();
    if (checkingUpdateRef.current) return;
    if (!force && now - lastUpdateCheckAtRef.current < UPDATE_CHECK_MIN_GAP_MS) return;

    checkingUpdateRef.current = true;
    lastUpdateCheckAtRef.current = now;

    try {
      const info = await checkUpdate(i18n.language);

      if (info.current_version) {
        setCurrentVersion(info.current_version);
      }

      if (info.has_update && info.latest_version) {
        setHasUpdate(true);
        setLatestVersion(info.latest_version);

        if (
          lastPromptedVersionRef.current !== info.latest_version
          && localStorage.getItem(UPDATE_DISMISSED_KEY) !== info.current_version
        ) {
          lastPromptedVersionRef.current = info.latest_version;
          setShowUpdate(true);
        }
        return;
      }

      if (!info.error) {
        setHasUpdate(false);
        setLatestVersion(info.latest_version);
      }
    } catch {
      // Keep the last known update state on transient failures.
    } finally {
      checkingUpdateRef.current = false;
    }
  }, [i18n.language]);

  useEffect(() => {
    refreshUpdateStatus(true);

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refreshUpdateStatus();
      }
    }, UPDATE_CHECK_INTERVAL_MS);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshUpdateStatus();
      }
    };

    const handleWindowFocus = () => {
      refreshUpdateStatus();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleWindowFocus);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [refreshUpdateStatus]);

  useEffect(() => {
    if (!user?.id) {
      setNotifications([]);
      setAcknowledgingNotificationIds([]);
      lastNotificationFetchKeyRef.current = null;
      return;
    }

    const fetchKey = `${user.id}:${i18n.language}:${currentVersion ?? 'pending-version'}`;
    if (lastNotificationFetchKeyRef.current === fetchKey) return;
    const previousFetchKey = lastNotificationFetchKeyRef.current;
    lastNotificationFetchKeyRef.current = fetchKey;

    let cancelled = false;
    void getActiveNotifications(i18n.language, currentVersion)
      .then((items) => {
        if (cancelled) return;
        setNotifications((prev) => {
          const byId = new Map(prev.map((item) => [item.id, item]));
          for (const item of items) {
            byId.set(item.id, item);
          }
          return Array.from(byId.values()).sort((a, b) => a.priority - b.priority);
        });
      })
      .catch(() => {
        // Notification failures should never block the main product surface.
        if (lastNotificationFetchKeyRef.current === fetchKey) {
          lastNotificationFetchKeyRef.current = previousFetchKey;
        }
      });

    return () => {
      cancelled = true;
    };
  }, [currentVersion, i18n.language, user?.id]);

  const visibleNotifications = !showOnboarding && !showUpdate && notifications.length > 0
    ? notifications
    : [];

  const removeNotifications = useCallback((items: UserNotification[]) => {
    const visibleIds = new Set(items.map((item) => item.id));
    setNotifications((prev) => prev.filter((item) => !visibleIds.has(item.id)));
  }, []);

  const closeVisibleNotification = useCallback((notification?: UserNotification) => {
    if (visibleNotifications.length === 0 || acknowledgingNotificationIds.length > 0) return;
    removeNotifications(notification ? [notification] : visibleNotifications);
  }, [acknowledgingNotificationIds.length, removeNotifications, visibleNotifications]);

  const dismissVisibleNotificationForever = useCallback(async () => {
    if (acknowledgingNotificationIds.length > 0) return;
    if (visibleNotifications.length === 0) return;
    setAcknowledgingNotificationIds(visibleNotifications.map((item) => item.id));
    try {
      await Promise.all(visibleNotifications.map((item) => ackNotification(item.id)));
    } catch {
      // Keep the UI moving; the server will retry visibility on the next login if dismiss failed.
    } finally {
      removeNotifications(visibleNotifications);
      setAcknowledgingNotificationIds([]);
    }
  }, [acknowledgingNotificationIds.length, removeNotifications, visibleNotifications]);


  const navigation = [
    {
      name: t('home'),
      items: [
        { name: t('flocksHome'), href: '/', icon: Home },
      ],
    },
    {
      name: t('aiWorkbench'),
      items: [
        { name: t('sessions'), href: '/sessions', icon: MessageSquare },
        { name: t('tasks'), href: '/tasks', icon: ListTodo },
        { name: t('workspace'), href: '/workspace', icon: FolderOpen },
      ],
    },
    {
      name: t('agentHub'),
      items: [
        { name: t('agents'), href: '/agents', icon: Bot },
        { name: t('workflows'), href: '/workflows', icon: Workflow },
        { name: t('skills'), href: '/skills', icon: BookOpen },
        { name: t('tools'), href: '/tools', icon: Wrench },
        { name: t('hub'), href: '/hub', icon: Archive },
        { name: t('models'), href: '/models', icon: Brain },
        { name: t('channels'), href: '/channels', icon: Radio },
      ],
    },
    {
      name: t('management'),
      items: [
        { name: t('accountManagement'), href: '/config', icon: UserCog },
      ],
    },
  ];

  const isFullScreenPage =
    matchPath('/workflows/create', location.pathname) ||
    matchPath('/workflows/:id/edit', location.pathname) ||
    matchPath('/workflows/:id', location.pathname) ||
    matchPath('/sessions', location.pathname);

  return (
    <div className="min-h-screen bg-gray-50">
      {showOnboarding && (
        <OnboardingModal
          onClose={() => setShowOnboarding(false)}
        />
      )}
      {showUpdate && (
        <UpdateModal
          onClose={() => setShowUpdate(false)}
          onDismiss={() => setShowUpdate(false)}
        />
      )}
      {visibleNotifications.length > 0 && (
        <NotificationModal
          notifications={visibleNotifications}
          acknowledgingIds={acknowledgingNotificationIds}
          onAcknowledge={closeVisibleNotification}
          onClose={closeVisibleNotification}
          onDismissForever={dismissVisibleNotificationForever}
        />
      )}

      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-600 bg-opacity-75 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`
          fixed inset-y-0 left-0 z-50 bg-white border-r border-gray-200
          transition-all duration-300 ease-in-out
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          ${collapsed ? 'w-16' : 'w-64'}
        `}
      >
        <div className="flex flex-col h-full overflow-hidden">
          {/* Logo */}
          <div className={`flex items-center h-16 border-b border-gray-200 flex-shrink-0 ${collapsed ? 'justify-center px-2' : 'px-4'}`}>
            {collapsed ? (
              <div
                className="w-8 h-8 rounded-lg border border-gray-200 bg-gray-50 flex items-center justify-center flex-shrink-0"
                title="Flocks"
              >
                <Sparkles className="w-4 h-4 text-gray-600" />
              </div>
            ) : (
              <>
                <div className="flex items-center flex-1 min-w-0">
                  <span className="text-xl font-bold text-gray-900 whitespace-nowrap">Flocks</span>
                </div>
                <button
                  onClick={() => setSidebarOpen(false)}
                  className="lg:hidden p-1 text-gray-400 hover:text-gray-600 rounded flex-shrink-0"
                >
                  <X className="w-5 h-5" />
                </button>
              </>
            )}
          </div>

          {/* Navigation */}
          <nav className={`flex-1 overflow-y-auto overflow-x-hidden py-4 ${collapsed ? 'px-2' : 'px-3'}`}>
            {navigation.map((section) => (
              <div key={section.name} className="mb-6">
                {!collapsed && (
                  <h3 className="px-3 mb-2 text-xs font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap">
                    {section.name}
                  </h3>
                )}
                {collapsed && <div className="mb-1 border-t border-gray-100 first:border-none" />}
                <div className="space-y-0.5">
                  {section.items.map((item) => {
                    const isActive = location.pathname === item.href
                      || (item.href !== '/' && location.pathname.startsWith(`${item.href}/`));
                    return (
                      <Link
                        key={item.href}
                        to={item.href}
                        onClick={() => setSidebarOpen(false)}
                        title={collapsed ? item.name : undefined}
                        className={`
                          flex items-center rounded-lg transition-colors duration-150
                          ${collapsed ? 'justify-center p-2.5' : 'px-3 py-2 text-sm font-medium'}
                          ${isActive
                            ? 'bg-slate-100 text-slate-800'
                            : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                          }
                        `}
                      >
                        <item.icon
                          className={`flex-shrink-0 w-5 h-5 ${collapsed ? '' : 'mr-3'} ${isActive ? 'text-slate-700' : 'text-gray-400'}`}
                        />
                        {!collapsed && (
                          <span className="truncate">{item.name}</span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          {/* Bottom: Language switcher + version */}
          <div className={`border-t border-gray-200 flex-shrink-0 ${collapsed ? 'p-2 flex flex-col items-center gap-2' : 'p-4'}`}>
            <LanguageSwitcher collapsed={collapsed} />
            {!collapsed && (
              <>
                {hasUpdate ? (
                  <button
                    onClick={() => setShowUpdate(true)}
                    className="mt-3 w-full rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-3 py-2 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md"
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate font-semibold text-amber-900">
                        {t('newVersion')} {latestVersion ? `v${latestVersion}` : ''}
                      </span>
                      <span className="inline-flex flex-shrink-0 items-center rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm">
                        {t('updateNow')}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-amber-700">
                      {currentVersion
                        ? t('currentVersionLabel', { version: currentVersion })
                        : 'Flocks'}
                    </div>
                    <div className="mt-0.5 text-xs font-medium text-amber-900">
                      AI Native SecOps Platform
                    </div>
                  </button>
                ) : (
                  <button
                    onClick={() => setShowUpdate(true)}
                    className="w-full text-left mt-3 group rounded-lg px-1 py-1 hover:bg-gray-50 transition-colors"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-gray-500 group-hover:text-gray-700 transition-colors">
                        Flocks {currentVersion ? `v${currentVersion}` : '...'}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-gray-400">AI Native SecOps Platform</div>
                  </button>
                )}
              </>
            )}
            {collapsed && (
              <button
                onClick={() => setShowUpdate(true)}
                title={hasUpdate ? t('hasNewVersion', { version: latestVersion ? `v${latestVersion}` : '' }) : t('versionInfo')}
                className={`relative rounded-xl p-2 transition-colors ${
                  hasUpdate
                    ? 'bg-amber-50 text-amber-600 hover:bg-amber-100'
                    : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
                }`}
              >
                {hasUpdate ? <ArrowUpCircle className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
                {hasUpdate && (
                  <>
                    <span className="absolute inset-0 rounded-xl border border-amber-200 animate-pulse" />
                    <span className="absolute top-1 right-1 w-2 h-2 bg-amber-400 rounded-full" />
                  </>
                )}
              </button>
            )}
          </div>
        </div>

        {/* Collapse tab (desktop) */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="
            hidden lg:flex absolute top-1/2 -translate-y-1/2 right-0 z-10
            w-3 h-20 items-center justify-center
            bg-gray-100 hover:bg-gray-200 border border-r-0 border-gray-200 rounded-l-lg
            text-gray-400 hover:text-gray-600
            transition-all duration-200
          "
          title={collapsed ? t('expandNav') : t('collapseNav')}
        >
          {collapsed ? <ChevronRight className="w-2.5 h-2.5" /> : <ChevronLeft className="w-2.5 h-2.5" />}
        </button>
      </aside>

      {/* Mobile top menu button */}
      <div className={`lg:hidden fixed top-0 left-0 z-30 flex items-center h-16 px-4 ${sidebarOpen ? 'hidden' : ''}`}>
        <button
          onClick={() => setSidebarOpen(true)}
          className="p-2 text-gray-500 hover:text-gray-700 bg-white rounded-lg shadow-sm border border-gray-200"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      {/* Main content area */}
      <div
        className={`flex flex-col h-screen transition-all duration-300 ${collapsed ? 'lg:pl-16' : 'lg:pl-64'}`}
      >
        <main className="flex-1 overflow-hidden bg-gray-50">
          {isFullScreenPage ? (
            <Outlet />
          ) : (
            <div className="h-full overflow-y-auto">
              <div className="min-h-full p-6">
                <Outlet />
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
