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
  ServerCog,
  ScrollText,
  ShieldCheck,
  Shield,
  AlertTriangle,
  CircleUserRound,
  type LucideIcon,
} from 'lucide-react';
import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '@/components/common/LanguageSwitcher';
import ThemeToggle from '@/components/common/ThemeToggle';
// Modals are only rendered after the user clicks/triggers them; pulling them
// into the eager Layout chunk costs ~1.7k LOC + i18n keys + lucide icons that
// the home page never needs. To keep the lazy split effective, we don't
// re-import the dismissal helpers from the modal modules (a static named
// import would force Rollup to bundle the whole module eagerly), and instead
// inline the two localStorage keys here. Keep these in sync with the keys
// declared in OnboardingModal.tsx / UpdateModal.tsx.
const ONBOARDING_DISMISSED_KEY = 'flocks_onboarding_dismissed';
const UPDATE_DISMISSED_KEY = 'flocks-update-dismissed';
function isOnboardingDismissed(): boolean {
  return localStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true';
}
const OnboardingModal = lazy(() => import('@/components/common/OnboardingModal'));
const UpdateModal = lazy(() => import('@/components/common/UpdateModal'));
const NotificationModal = lazy(() => import('@/components/common/NotificationModal'));
import { checkUpdate, type VersionInfo } from '@/api/update';
import { consoleUpgradeApi } from '@/api/consoleUpgrade';
import {
  ackNotification,
  getActiveNotifications,
  getNotificationAckStatus,
  type UserNotification,
} from '@/api/notifications';
import { flocksproUsersApi } from '@/api/flocksproUsers';
import { useAuth } from '@/contexts/AuthContext';
import { getLocalizedReleaseNotes } from '@/utils/releaseNotes';
import { useUserDefinedPages } from '@/hooks/useUserDefinedPages';
import { resolveUserDefinedPageIcon } from '@/utils/userDefinedPageIcons';

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 600_000;
type WorkspaceId = 'agent' | 'soc' | 'system';
type NavItem = { name: string; href: string; icon: LucideIcon };
type NavSection = { id: string; name: string; items: NavItem[] };

function formatProVersion(version?: string | null): string | null {
  const normalized = (version || '').trim().replace(/^pro-v/i, '').replace(/^v/i, '');
  return normalized ? `pro-v${normalized}` : null;
}

function formatUpdateVersion(version?: string | null): string | null {
  const raw = (version || '').trim();
  if (!raw) return null;
  return /^(pro-)?v/i.test(raw) ? raw : `v${raw}`;
}

function buildUpdateNotification(info: VersionInfo | null, language: string): UserNotification | null {
  const releaseNotes = getLocalizedReleaseNotes(info?.release_notes, language);
  if (!info || info.error || !releaseNotes) return null;

  const version = info.latest_version ?? info.current_version;
  if (!version || version === 'unknown') return null;

  const isZh = language.toLowerCase().startsWith('zh');
  return {
    id: `whats-new-${version}`,
    kind: 'whats_new',
    title: isZh ? `Flocks ${formatUpdateVersion(version)} 更新内容` : `What's new in Flocks ${formatUpdateVersion(version)}`,
    summary: isZh ? '这里是本次版本值得关注的新功能和变化。' : 'Here are the highlights from this version.',
    body: releaseNotes,
    highlights: [],
    version,
    priority: 20,
  };
}

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
  const [updateInfo, setUpdateInfo] = useState<VersionInfo | null>(null);
  const [hasCompletedUpdateCheck, setHasCompletedUpdateCheck] = useState(false);
  const lastUpdateCheckAtRef = useRef(0);
  const checkingUpdateRef = useRef(false);
  const lastPromptedVersionRef = useRef<string | null>(null);
  const [notifications, setNotifications] = useState<UserNotification[]>([]);
  const [updateNotification, setUpdateNotification] = useState<UserNotification | null>(null);
  const [backendNotificationsReady, setBackendNotificationsReady] = useState(false);
  const [updateNotificationReady, setUpdateNotificationReady] = useState(false);
  const [acknowledgingNotificationIds, setAcknowledgingNotificationIds] = useState<string[]>([]);
  const lastNotificationFetchKeyRef = useRef<string | null>(null);
  const [hasFlocksproCapability, setHasFlocksproCapability] = useState(false);
  const [isFlocksproActive, setIsFlocksproActive] = useState(false);
  const [flocksproStatusReady, setFlocksproStatusReady] = useState(false);
  const [flocksproVersion, setFlocksproVersion] = useState<string | null>(null);
  const canManageUpdates = user?.role === 'admin';
  const { pages: userDefinedPages } = useUserDefinedPages();
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
    if (!flocksproStatusReady) return;
    if (isFlocksproActive) {
      setUpdateInfo(null);
      setHasUpdate(false);
      setLatestVersion(null);
      setHasCompletedUpdateCheck(true);
      return;
    }

    const now = Date.now();
    if (checkingUpdateRef.current) return;
    if (!force && now - lastUpdateCheckAtRef.current < UPDATE_CHECK_MIN_GAP_MS) return;

    checkingUpdateRef.current = true;
    lastUpdateCheckAtRef.current = now;

    try {
      const info = await checkUpdate(i18n.language);
      setUpdateInfo(info);

      if (info.current_version) {
        setCurrentVersion(info.current_version);
      }

      if (info.has_update && info.latest_version) {
        setHasUpdate(true);
        setLatestVersion(info.latest_version);

        if (
          canManageUpdates
          && lastPromptedVersionRef.current !== info.latest_version
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
      setHasCompletedUpdateCheck(true);
    }
  }, [canManageUpdates, flocksproStatusReady, i18n.language, isFlocksproActive]);

  useEffect(() => {
    if (!flocksproStatusReady) return undefined;

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
  }, [flocksproStatusReady, refreshUpdateStatus]);

  useEffect(() => {
    let cancelled = false;
    if (!user?.id || user.role !== 'admin') {
      setHasFlocksproCapability(false);
      return () => {
        cancelled = true;
      };
    }
    const refreshCapability = () => {
      void flocksproUsersApi.hasCapability()
        .then((ok) => {
          if (!cancelled) {
            setHasFlocksproCapability(ok);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setHasFlocksproCapability(false);
          }
        });
    };
    refreshCapability();
    window.addEventListener('flockspro-license-status-changed', refreshCapability);
    return () => {
      cancelled = true;
      window.removeEventListener('flockspro-license-status-changed', refreshCapability);
    };
  }, [user?.id, user?.role]);

  useEffect(() => {
    let cancelled = false;
    setFlocksproStatusReady(false);
    if (!user?.id) {
      setIsFlocksproActive(false);
      setFlocksproVersion(null);
      setFlocksproStatusReady(true);
      return () => {
        cancelled = true;
      };
    }
    const refreshFlocksproStatus = () => {
      setFlocksproStatusReady(false);
      void Promise.all([
        flocksproUsersApi.getLicenseStatus().catch(() => null),
        consoleUpgradeApi.getProPackageStatus().catch(() => null),
      ])
        .then(([licenseStatus, packageStatus]) => {
          if (cancelled) return;
          const active = licenseStatus?.pro_enabled === true || packageStatus?.pro_enabled === true;
          setIsFlocksproActive(active);
          const version = active
            ? formatProVersion(packageStatus?.flockspro_component_version || packageStatus?.installed_version)
            : null;
          setFlocksproVersion(version);
        })
        .catch(() => {
          if (!cancelled) {
            setIsFlocksproActive(false);
            setFlocksproVersion(null);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setFlocksproStatusReady(true);
          }
        });
    };
    refreshFlocksproStatus();
    window.addEventListener('flockspro-license-status-changed', refreshFlocksproStatus);
    return () => {
      cancelled = true;
      window.removeEventListener('flockspro-license-status-changed', refreshFlocksproStatus);
    };
  }, [user?.id]);

  useEffect(() => {
    if (!user?.id) {
      setNotifications([]);
      setUpdateNotification(null);
      setBackendNotificationsReady(false);
      setUpdateNotificationReady(false);
      setAcknowledgingNotificationIds([]);
      lastNotificationFetchKeyRef.current = null;
      return;
    }
    if (!hasCompletedUpdateCheck) return;

    const fetchKey = `${user.id}:${i18n.language}:${currentVersion ?? 'pending-version'}`;
    if (lastNotificationFetchKeyRef.current === fetchKey) return;
    const previousFetchKey = lastNotificationFetchKeyRef.current;
    lastNotificationFetchKeyRef.current = fetchKey;
    setBackendNotificationsReady(false);

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
        setBackendNotificationsReady(true);
      })
      .catch(() => {
        // Notification failures should never block the main product surface.
        if (lastNotificationFetchKeyRef.current === fetchKey) {
          lastNotificationFetchKeyRef.current = previousFetchKey;
        }
        if (!cancelled) {
          setBackendNotificationsReady(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [currentVersion, hasCompletedUpdateCheck, i18n.language, user?.id]);

  useEffect(() => {
    if (!user?.id) {
      setUpdateNotification(null);
      setUpdateNotificationReady(false);
      return;
    }
    if (!hasCompletedUpdateCheck) return;

    setUpdateNotificationReady(false);
    const notification = buildUpdateNotification(updateInfo, i18n.language);
    if (!notification) {
      setUpdateNotification(null);
      setUpdateNotificationReady(true);
      return;
    }

    let cancelled = false;
    void getNotificationAckStatus(notification.id)
      .then((status) => {
        if (cancelled) return;
        setUpdateNotification(status.acknowledged ? null : notification);
        setUpdateNotificationReady(true);
      })
      .catch(() => {
        if (cancelled) return;
        setUpdateNotification(notification);
        setUpdateNotificationReady(true);
      });

    return () => {
      cancelled = true;
    };
  }, [hasCompletedUpdateCheck, i18n.language, updateInfo, user?.id]);

  const allNotifications = updateNotification
    ? [...notifications, updateNotification].sort((a, b) => a.priority - b.priority)
    : notifications;
  const visibleNotifications = backendNotificationsReady && updateNotificationReady && !showOnboarding && !showUpdate && allNotifications.length > 0
    ? allNotifications
    : [];

  const removeNotifications = useCallback((items: UserNotification[]) => {
    const visibleIds = new Set(items.map((item) => item.id));
    setNotifications((prev) => prev.filter((item) => !visibleIds.has(item.id)));
    setUpdateNotification((prev) => (prev && visibleIds.has(prev.id) ? null : prev));
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


  const isNavigationItemActive = useCallback((href: string) => {
    if (href === '/') return location.pathname === '/';
    return location.pathname === href || location.pathname.startsWith(`${href}/`);
  }, [location.pathname]);

  const socPageLinks = useMemo<NavItem[]>(
    () => userDefinedPages
      .filter((page) => page.enabled && page.buildStatus === 'ready')
      .map((page) => ({
        name: page.title,
        href: `/soc/pages/${page.id}`,
        icon: resolveUserDefinedPageIcon(page.icon),
      })),
    [userDefinedPages],
  );

  const systemPaths = [
    '/config',
    '/channels',
    '/permissions',
    '/monitoring',
    '/audit-logs',
    '/flockspro-upgrade',
    '/system-logs',
  ];
  const isSystemPath = systemPaths.some((path) =>
    location.pathname === path || location.pathname.startsWith(`${path}/`),
  );
  const currentWorkspace: WorkspaceId = location.pathname === '/soc'
    || location.pathname.startsWith('/soc/')
    ? 'soc'
    : isSystemPath
      ? 'system'
      : 'agent';

  const workspaceTabs = useMemo(
    () => [
      { id: 'agent' as const, name: t('agentWorkspace'), href: '/', icon: Bot },
      { id: 'soc' as const, name: t('socWorkspace'), href: '/soc', icon: Shield },
      { id: 'system' as const, name: t('systemConfig'), href: '/config', icon: UserCog },
    ],
    [t],
  );

  // Stable across re-renders triggered by location changes (sidebar nav clicks).
  // The active item is computed separately from location.pathname while rendering.
  const workspaceNavigation = useMemo<Record<WorkspaceId, NavSection[]>>(
    () => ({
      agent: [
        {
          id: 'home',
          name: t('home'),
          items: [
            { name: t('flocksHome'), href: '/', icon: Home },
            ...userDefinedPages
              .filter((page) => page.enabled && page.placement === 'home.after' && page.buildStatus === 'ready')
              .map((page) => ({
                name: page.title,
                href: page.route,
                icon: resolveUserDefinedPageIcon(page.icon),
              })),
          ],
        },
        {
          id: 'aiWorkbench',
          name: t('aiWorkbench'),
          items: [
            { name: t('sessions'), href: '/sessions', icon: MessageSquare },
            { name: t('workspace'), href: '/workspace', icon: FolderOpen },
            { name: t('tasks'), href: '/tasks', icon: ListTodo },
            { name: t('workflows'), href: '/workflows', icon: Workflow },
          ],
        },
        {
          id: 'agentHub',
          name: t('agentHub'),
          items: [
            { name: t('agents'), href: '/agents', icon: Bot },
            { name: t('skills'), href: '/skills', icon: BookOpen },
            { name: t('tools'), href: '/tools', icon: Wrench },
            { name: t('deviceIntegration'), href: '/devices', icon: ServerCog },
            { name: t('hub'), href: '/hub', icon: Archive },
            { name: t('models'), href: '/models', icon: Brain },
          ],
        },
      ],
      soc: [
        {
          id: 'socWorkspace',
          name: t('socWorkspace'),
          items: [
            { name: t('socOverview'), href: '/soc', icon: Shield },
            { name: t('socAlerts'), href: '/soc/alerts', icon: AlertTriangle },
            ...socPageLinks,
          ],
        },
      ],
      system: [
        {
          id: 'systemConfig',
          name: t('systemConfig'),
          items: [
            { name: t('accountManagement'), href: '/config', icon: UserCog },
            { name: t('channels'), href: '/channels', icon: Radio },
            { name: t('permissions'), href: '/permissions', icon: ShieldCheck },
            { name: t('monitoring'), href: '/monitoring', icon: ServerCog },
            { name: t('systemLog'), href: '/system-logs', icon: ScrollText },
            ...(hasFlocksproCapability && user?.role === 'admin'
              ? [{ name: t('auditLogs'), href: '/audit-logs', icon: ShieldCheck }]
              : []),
            ...(user?.role === 'admin'
              ? [{ name: t('flocksproUpgrade'), href: '/flockspro-upgrade', icon: ArrowUpCircle }]
              : []),
          ],
        },
      ],
    }),
    [hasFlocksproCapability, socPageLinks, t, user?.role, userDefinedPages],
  );
  const navigation = workspaceNavigation[currentWorkspace];

  const isFullScreenPage =
    matchPath('/workflows/create', location.pathname) ||
    matchPath('/workflows/:id/edit', location.pathname) ||
    matchPath('/workflows/:id', location.pathname) ||
    matchPath('/sessions', location.pathname) ||
    matchPath('/devices', location.pathname);
  const productName = isFlocksproActive ? 'Flocks Pro' : 'Flocks';
  const displayVersion = isFlocksproActive
    ? flocksproVersion || (currentVersion ? formatProVersion(currentVersion) : null)
    : currentVersion ? `v${currentVersion}` : null;
  const currentVersionLabel = isFlocksproActive
    ? t('currentProductVersionLabel', { version: displayVersion || productName })
    : currentVersion
    ? t('currentVersionLabel', { version: currentVersion })
    : productName;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-zinc-950 dark:text-zinc-100">
      {/* Modals render lazily — fallback={null} keeps the chunk download
          invisible to the user (they're already triggering an async UI). */}
      <Suspense fallback={null}>
        {showOnboarding && (
          <OnboardingModal
            onClose={() => setShowOnboarding(false)}
          />
        )}
        {showUpdate && (
          <UpdateModal
            initialInfo={updateInfo}
            edition={isFlocksproActive ? 'flockspro' : 'flocks'}
            canUpgrade={canManageUpdates}
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
      </Suspense>

      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-600 bg-opacity-75 z-40 lg:hidden dark:bg-black dark:bg-opacity-75"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <header className="fixed inset-x-0 top-0 z-50 h-14 border-b border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex h-full items-stretch justify-between">
          <div className={`flex w-52 shrink-0 items-center border-r border-zinc-200 transition-all duration-300 dark:border-zinc-800 ${collapsed ? 'lg:w-16' : 'lg:w-52'}`}>
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="ml-3 rounded-md p-2 text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100 lg:hidden"
              aria-label={t('openNav')}
            >
              <Menu className="h-5 w-5" />
            </button>
            <Link
              to="/"
              className={`flex min-w-0 flex-1 items-center px-3 ${collapsed ? 'lg:justify-center lg:px-0' : 'lg:px-6'}`}
              title={productName}
            >
              {collapsed ? (
                <Sparkles className="hidden h-5 w-5 text-red-500 lg:block" />
              ) : null}
              <span className={`truncate text-xl font-bold text-zinc-950 dark:text-zinc-50 ${collapsed ? 'lg:hidden' : ''}`}>
                {productName}
              </span>
            </Link>
          </div>
          <nav className="flex min-w-0 flex-1 items-stretch overflow-x-auto">
            {workspaceTabs.map((tab) => {
              const isActive = tab.id === currentWorkspace;
              return (
                <Link
                  key={tab.id}
                  to={tab.href}
                  className={`relative inline-flex min-w-28 items-center justify-center gap-2 border-r border-zinc-200 px-6 text-sm font-semibold whitespace-nowrap transition-colors dark:border-zinc-800 ${
                    isActive
                      ? 'bg-zinc-100 text-zinc-950 dark:bg-zinc-900 dark:text-zinc-50'
                      : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100'
                  }`}
                >
                  {isActive ? <span className="absolute inset-x-0 top-0 h-0.5 bg-red-500" /> : null}
                  <tab.icon className={`h-4 w-4 ${isActive ? 'text-red-600' : 'text-zinc-400 dark:text-zinc-500'}`} />
                  {tab.name}
                </Link>
              );
            })}
          </nav>
          <div className="hidden shrink-0 items-center gap-2 border-l border-zinc-200 px-4 dark:border-zinc-800 md:flex">
            <LanguageSwitcher />
            <ThemeToggle />
            <Link
              to="/config"
              className="inline-flex max-w-56 items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 hover:text-zinc-950 dark:text-zinc-300 dark:hover:bg-zinc-900 dark:hover:text-zinc-50"
              title={user?.username || t('accountManagement')}
            >
              <CircleUserRound className="h-4 w-4 text-zinc-500" />
              <span className="truncate">{user?.username || t('accountManagement')}</span>
            </Link>
          </div>
        </div>
      </header>

      <aside
        className={`
          fixed bottom-0 left-0 top-14 z-40 bg-zinc-100 border-r border-zinc-200 dark:bg-zinc-950 dark:border-zinc-800
          transition-all duration-300 ease-in-out
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          ${collapsed ? 'w-16' : 'w-52'}
        `}
      >
        <div className="flex flex-col h-full overflow-hidden">
          {/* Navigation */}
          <nav className={`flex-1 overflow-y-auto overflow-x-hidden py-4 ${collapsed ? 'px-2' : 'px-3'}`}>
            {navigation.map((section) => (
              <div key={section.id} className="mb-6">
                {!collapsed && section.name && (
                  <h3 className="px-3 mb-2 text-xs font-semibold text-zinc-400 uppercase tracking-wider whitespace-nowrap dark:text-zinc-500">
                    {section.name}
                  </h3>
                )}
                {collapsed && <div className="mb-1 border-t border-zinc-200 first:border-none dark:border-zinc-800" />}
                <div className="space-y-0.5">
                  {section.items.map((item) => {
                    const isActive = isNavigationItemActive(item.href);
                    return (
                      <Link
                        key={item.href}
                        to={item.href}
                        onClick={() => setSidebarOpen(false)}
                        title={collapsed ? item.name : undefined}
                        className={`
                          flex items-center rounded-lg transition-all duration-150
                          ${collapsed ? 'justify-center p-2.5' : 'px-3 py-2 text-sm font-medium'}
                          ${isActive
                            ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                            : 'text-zinc-600 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
                          }
                        `}
                      >
                        <item.icon
                          className={`flex-shrink-0 w-5 h-5 ${collapsed ? '' : 'mr-3'} ${isActive ? 'text-zinc-700 dark:text-zinc-100' : 'text-zinc-400 dark:text-zinc-500'}`}
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

          {/* Bottom: mobile controls + version */}
          <div className={`border-t border-zinc-200 flex-shrink-0 dark:border-zinc-800 ${collapsed ? 'p-2 flex flex-col items-center gap-2' : 'p-4'}`}>
            <div className={`flex md:hidden ${collapsed ? 'flex-col items-center gap-2' : 'items-center gap-2'}`}>
              <LanguageSwitcher collapsed={collapsed} />
              <ThemeToggle collapsed={collapsed} />
            </div>
            {!collapsed && (
              <>
                {hasUpdate && canManageUpdates ? (
                  <button
                    onClick={() => setShowUpdate(true)}
                    className="mt-3 w-full rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-3 py-2 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md dark:border-amber-500/30 dark:from-amber-950/60 dark:via-orange-950/50 dark:to-rose-950/50"
                  >
                    <div className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate font-semibold text-amber-900 dark:text-amber-100">
                        {t('newVersion')} {formatUpdateVersion(latestVersion) || ''}
                      </span>
                      <span className="inline-flex flex-shrink-0 items-center rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm">
                        {t('updateNow')}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      {currentVersionLabel}
                    </div>
                    <div className="mt-0.5 text-xs font-medium text-amber-900 dark:text-amber-100">
                      AI Native SecOps Platform
                    </div>
                  </button>
                ) : (
                  <button
                    onClick={() => setShowUpdate(true)}
                    className="w-full text-left mt-3 group rounded-lg px-1 py-1 hover:bg-white/60 transition-colors dark:hover:bg-zinc-900"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-zinc-500 group-hover:text-zinc-800 transition-colors dark:text-zinc-400 dark:group-hover:text-zinc-100">
                        {productName} {displayVersion || '...'}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-zinc-400 dark:text-zinc-500">AI Native SecOps Platform</div>
                  </button>
                )}
              </>
            )}
            {collapsed && (
              <button
                onClick={() => setShowUpdate(true)}
                title={hasUpdate && canManageUpdates ? t('hasNewVersion', { version: formatUpdateVersion(latestVersion) || '' }) : t('versionInfo')}
                className={`relative rounded-xl p-2 transition-colors ${
                  hasUpdate && canManageUpdates
                    ? 'bg-amber-50 text-amber-600 hover:bg-amber-100 dark:bg-amber-950/50 dark:text-amber-300 dark:hover:bg-amber-900/60'
                    : 'text-zinc-400 hover:text-zinc-600 hover:bg-white/60 dark:hover:bg-zinc-900 dark:hover:text-zinc-100'
                }`}
              >
                {hasUpdate && canManageUpdates ? <ArrowUpCircle className="w-4 h-4" /> : <Sparkles className="w-4 h-4" />}
                {hasUpdate && canManageUpdates && (
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
            bg-zinc-200 hover:bg-zinc-300 border border-r-0 border-zinc-200 rounded-l-lg
            text-zinc-400 hover:text-zinc-600
            dark:bg-zinc-900 dark:hover:bg-zinc-800 dark:border-zinc-800 dark:text-zinc-500 dark:hover:text-zinc-100
            transition-all duration-200
          "
          title={collapsed ? t('expandNav') : t('collapseNav')}
        >
          {collapsed ? <ChevronRight className="w-2.5 h-2.5" /> : <ChevronLeft className="w-2.5 h-2.5" />}
        </button>
      </aside>

      {/* Mobile close button */}
      <div className={`lg:hidden fixed left-44 top-2 z-50 flex items-center ${sidebarOpen ? '' : 'hidden'}`}>
        <button
          onClick={() => setSidebarOpen(false)}
          className="rounded-md border border-white/10 bg-[#101218] p-2 text-zinc-400 shadow-sm transition-colors hover:bg-white/5 hover:text-zinc-100"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Main content area */}
      <div
        className={`flex flex-col h-screen pt-14 transition-all duration-300 ${collapsed ? 'lg:pl-16' : 'lg:pl-52'}`}
      >
        <main className="flex-1 overflow-hidden bg-gray-50 dark:bg-zinc-950">
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
