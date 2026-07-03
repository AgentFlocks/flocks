import { Outlet, Link, useLocation, matchPath } from 'react-router-dom';
import {
  Home,
  MessageSquare,
  Bot,
  Brain,
  Workflow,
  ListTodo,
  Wrench,
  BookOpen,
  Radio,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Menu,
  FolderOpen,
  Sparkles,
  Archive,
  ServerCog,
  LogOut,
  Settings,
  ArrowUpCircle,
  RefreshCw,
  type LucideIcon,
} from 'lucide-react';
import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
// Modals are only rendered after the user clicks/triggers them; pulling them
// into the eager Layout chunk costs ~1.7k LOC + i18n keys + lucide icons that
// the home page never needs. To keep the lazy split effective, we don't
// re-import dismissal helpers from the modal modules (a static named import
// would force Rollup to bundle the whole module eagerly).
const ONBOARDING_DISMISSED_KEY = 'flocks_onboarding_dismissed';
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
import { UPDATE_DISMISSED_KEY, buildUpdateDismissalKey, isUpdateDismissed } from '@/utils/updateDismissal';
import { useWebUIContractPages } from '@/hooks/useWebUIContractPages';
import { resolveWebUIContractPageIcon } from '@/utils/webuiContractPageIcons';
import { buildWebUIContractWorkspaceSections } from '@/utils/webuiContractWorkspaceSections';

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 600_000;

interface LayoutNavItem {
  name: string;
  href: string;
  icon: LucideIcon;
  opensWorkspaceMenu?: boolean;
  workspaceId?: string;
}

interface LayoutNavSection {
  name: string;
  items: LayoutNavItem[];
}

function formatProVersion(version?: string | null): string | null {
  const normalized = (version || '').trim().replace(/^pro-v/i, '').replace(/^v/i, '');
  return normalized ? `v${normalized}` : null;
}

function formatUpdateVersion(version?: string | null): string | null {
  const raw = (version || '').trim();
  if (!raw) return null;
  return /^(pro-)?v/i.test(raw) ? raw : `v${raw}`;
}

function currentProductVersion(info: VersionInfo, isFlocksproActive: boolean): string | null {
  if (isFlocksproActive || info.edition === 'flockspro') {
    return info.current_bundle_version || info.current_version || null;
  }
  return info.current_version || null;
}

function latestProductVersion(info: VersionInfo, isFlocksproActive: boolean): string | null {
  if (isFlocksproActive || info.edition === 'flockspro') {
    return info.latest_bundle_version || info.latest_version || null;
  }
  return info.latest_version || null;
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
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const isHome = location.pathname === '/';
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const { t, i18n } = useTranslation('nav');
  const { t: tWebUIContractPage } = useTranslation('webuiContractPage');
  const { t: tAuth } = useTranslation('auth');
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
  const [isFlocksproActive, setIsFlocksproActive] = useState(false);
  const [flocksproStatusReady, setFlocksproStatusReady] = useState(false);
  const [flocksproVersion, setFlocksproVersion] = useState<string | null>(null);
  const canManageUpdates = user?.role === 'admin';
  const { pages: webuiContractPages, workspaces: webuiContractWorkspaces = [] } = useWebUIContractPages();
  const [openWorkspaceMenuId, setOpenWorkspaceMenuId] = useState<string | null>(null);
  const [collapsedWorkspaceSectionIds, setCollapsedWorkspaceSectionIds] = useState<Set<string>>(() => new Set());
  const workspaceMenuCloseTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!accountMenuOpen) return undefined;

    const handlePointerDown = (event: PointerEvent) => {
      if (accountMenuRef.current?.contains(event.target as Node)) return;
      setAccountMenuOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [accountMenuOpen]);

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

    const now = Date.now();
    if (checkingUpdateRef.current) return;
    if (!force && now - lastUpdateCheckAtRef.current < UPDATE_CHECK_MIN_GAP_MS) return;

    checkingUpdateRef.current = true;
    lastUpdateCheckAtRef.current = now;

    try {
      const edition = isFlocksproActive ? 'flockspro' : 'flocks';
      const info = await checkUpdate(i18n.language, edition);
      setUpdateInfo(info);

      const displayCurrentVersion = currentProductVersion(info, edition === 'flockspro');
      const displayLatestVersion = latestProductVersion(info, edition === 'flockspro');

      if (displayCurrentVersion) {
        setCurrentVersion(displayCurrentVersion);
      }

      if (info.has_update && displayLatestVersion) {
        setHasUpdate(true);
        setLatestVersion(displayLatestVersion);
        const updateDismissalKey = buildUpdateDismissalKey(info);

        if (
          canManageUpdates
          && updateDismissalKey
          && lastPromptedVersionRef.current !== updateDismissalKey
          && !isUpdateDismissed(info, localStorage.getItem(UPDATE_DISMISSED_KEY))
        ) {
          lastPromptedVersionRef.current = updateDismissalKey;
          setShowUpdate(true);
        }
        return;
      }

      if (!info.error) {
        setHasUpdate(false);
        setLatestVersion(displayLatestVersion);
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
            ? formatProVersion(packageStatus?.installed_version || packageStatus?.flockspro_component_version)
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


  // Stable across re-renders triggered by location changes (sidebar nav clicks)
  // — the array only depends on the i18n translation function, which itself is
  // stable as long as the language doesn't change. Without this, every route
  // switch rebuilt the whole nav structure and cascaded re-renders down to
  // every <Link>, contributing to perceptible navigation lag.
  const navigation = useMemo<LayoutNavSection[]>(
    () => {
      const sceneWorkspaceItems = webuiContractWorkspaces
        .filter((workspace) => workspace.enabled && (workspace.placement === 'sceneWorkspace' || workspace.placement === 'aiWorkbench'))
        .map((workspace) => ({
          name: workspace.title,
          href: workspace.route,
          icon: resolveWebUIContractPageIcon(workspace.icon),
          opensWorkspaceMenu: true,
          workspaceId: workspace.id,
        }));

      return [
        {
          name: '',
          items: [
            { name: t('flocksHome'), href: '/', icon: Home },
            ...webuiContractPages
              .filter((page) => !page.workspaceId && page.enabled && page.placement === 'home.after' && page.buildStatus === 'ready')
              .map((page) => ({
                name: page.title,
                href: page.route,
                icon: resolveWebUIContractPageIcon(page.icon),
              })),
          ],
        },
        {
          name: t('aiWorkbench'),
          items: [
            { name: t('sessions'), href: '/sessions', icon: MessageSquare },
            { name: t('workspace'), href: '/workspace', icon: FolderOpen },
            { name: t('tasks'), href: '/tasks', icon: ListTodo },
            { name: t('workflows'), href: '/workflows', icon: Workflow },
          ],
        },
        {
          name: t('agentHub'),
          items: [
            { name: t('agents'), href: '/agents', icon: Bot },
            { name: t('skills'), href: '/skills', icon: BookOpen },
            { name: t('tools'), href: '/tools', icon: Wrench },
            { name: t('hub'), href: '/hub', icon: Archive },
            { name: t('models'), href: '/models', icon: Brain },
            { name: t('channels'), href: '/channels', icon: Radio },
          ],
        },
        {
          name: t('sceneWorkspaces'),
          items: [
            ...sceneWorkspaceItems,
            { name: t('deviceIntegration'), href: '/devices', icon: ServerCog },
          ],
        },
      ];
    },
    [webuiContractPages, webuiContractWorkspaces, t],
  );

  const isFullScreenPage =
    matchPath('/workflows/create', location.pathname) ||
    matchPath('/workflows/:id/edit', location.pathname) ||
    matchPath('/workflows/:id', location.pathname) ||
    matchPath('/sessions', location.pathname) ||
    matchPath('/devices', location.pathname) ||
    matchPath('/contracts/webui/*', location.pathname);
  const productName = isFlocksproActive ? 'Flocks Pro' : 'Flocks';
  const displayVersion = isFlocksproActive
    ? updateInfo?.edition === 'flockspro'
      ? formatProVersion(currentProductVersion(updateInfo, true))
      : flocksproVersion || (currentVersion ? formatProVersion(currentVersion) : null)
    : formatUpdateVersion(currentVersion);
  const accountInitial = (user?.username || productName || 'F').trim().charAt(0).toUpperCase();
  const accountRoleLabel = user?.role === 'admin' ? tAuth('admin.roleAdmin') : tAuth('admin.roleMember');
  const hasVisibleUpdate = hasUpdate && canManageUpdates;
  const showFlocksproUpgradeEntry = canManageUpdates;
  const productUpdateTitle = hasVisibleUpdate
    ? t('hasNewVersion', { version: formatUpdateVersion(latestVersion) || '' })
    : productName;
  const settingsReturnState = {
    from: {
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
    },
  };
  const activeWorkspaceMenu = useMemo(
    () => webuiContractWorkspaces.find((workspace) => workspace.id === openWorkspaceMenuId && workspace.enabled) ?? null,
    [openWorkspaceMenuId, webuiContractWorkspaces],
  );
  const ActiveWorkspaceMenuIcon = activeWorkspaceMenu
    ? resolveWebUIContractPageIcon(activeWorkspaceMenu.icon)
    : null;
  const activeWorkspaceSections = useMemo(
    () => (activeWorkspaceMenu ? buildWebUIContractWorkspaceSections(activeWorkspaceMenu) : []),
    [activeWorkspaceMenu],
  );

  const cancelWorkspaceMenuClose = useCallback(() => {
    if (workspaceMenuCloseTimerRef.current === null) return;
    window.clearTimeout(workspaceMenuCloseTimerRef.current);
    workspaceMenuCloseTimerRef.current = null;
  }, []);

  const openWorkspaceMenu = useCallback((workspaceId?: string) => {
    if (!workspaceId) return;
    cancelWorkspaceMenuClose();
    setOpenWorkspaceMenuId(workspaceId);
  }, [cancelWorkspaceMenuClose]);

  const scheduleWorkspaceMenuClose = useCallback(() => {
    cancelWorkspaceMenuClose();
    workspaceMenuCloseTimerRef.current = window.setTimeout(() => {
      setOpenWorkspaceMenuId(null);
      workspaceMenuCloseTimerRef.current = null;
    }, 120);
  }, [cancelWorkspaceMenuClose]);

  useEffect(() => () => cancelWorkspaceMenuClose(), [cancelWorkspaceMenuClose]);

  useEffect(() => {
    setCollapsedWorkspaceSectionIds(new Set());
  }, [openWorkspaceMenuId]);

  useEffect(() => {
    if (openWorkspaceMenuId && !activeWorkspaceMenu) {
      setOpenWorkspaceMenuId(null);
    }
  }, [activeWorkspaceMenu, openWorkspaceMenuId]);

  const toggleWorkspaceSection = useCallback((sectionId: string) => {
    setCollapsedWorkspaceSectionIds((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      return next;
    });
  }, []);

  const openManualUpdateCheck = useCallback(() => {
    setAccountMenuOpen(false);
    setUpdateInfo(null);
    setShowUpdate(true);
  }, []);

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

      <aside
        className={`
          fixed inset-y-0 left-0 z-50 bg-zinc-100 border-r border-zinc-200 dark:bg-zinc-950 dark:border-zinc-800
          transition-all duration-300 ease-in-out
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          ${collapsed ? 'w-16' : 'w-52'}
        `}
      >
        <div className="flex flex-col h-full overflow-visible">
          {/* Logo */}
          <div className={`flex items-center h-16 flex-shrink-0 ${collapsed ? 'justify-center px-2' : 'pl-6 pr-4'}`}>
            {collapsed ? (
              <div
                className="relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
                title={productName}
              >
                <Sparkles className="w-4 h-4 text-zinc-500 dark:text-zinc-300" />
                {hasVisibleUpdate && (
                  <button
                    type="button"
                    onClick={() => setShowUpdate(true)}
                    className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-amber-400 ring-2 ring-zinc-100 transition-colors hover:bg-amber-500 dark:ring-zinc-950"
                    title={productUpdateTitle}
                    aria-label={productUpdateTitle}
                  />
                )}
              </div>
            ) : (
              <>
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="min-w-0 text-xl font-bold text-zinc-900 whitespace-nowrap dark:text-zinc-50">{productName}</span>
                  {hasVisibleUpdate && (
                    <button
                      type="button"
                      onClick={() => setShowUpdate(true)}
                      title={productUpdateTitle}
                      aria-label={productUpdateTitle}
                      className="relative inline-flex h-4 shrink-0 items-center rounded-sm bg-amber-50 px-1 text-[10px] font-bold leading-none text-amber-600 transition-colors hover:bg-amber-100 dark:bg-amber-950/70 dark:text-amber-300 dark:hover:bg-amber-900"
                    >
                      {t('newVersion')}
                      <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-amber-400 ring-1 ring-zinc-100 dark:ring-zinc-950" />
                    </button>
                  )}
                </div>
                <button
                  onClick={() => setSidebarOpen(false)}
                  className="lg:hidden p-1 text-zinc-400 hover:text-zinc-600 rounded flex-shrink-0 dark:hover:text-zinc-100"
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
                {!collapsed && section.name && (
                  <h3 className="px-3 mb-2 text-xs font-semibold text-zinc-400 uppercase tracking-wider whitespace-nowrap dark:text-zinc-500">
                    {section.name}
                  </h3>
                )}
                {collapsed && <div className="mb-1 border-t border-zinc-200 first:border-none dark:border-zinc-800" />}
                <div className="space-y-0.5">
                  {section.items.map((item) => {
                    const isActive = location.pathname === item.href
                      || (item.href !== '/' && location.pathname.startsWith(`${item.href}/`));
                    return (
                      <Link
                        key={item.href}
                        to={item.href}
                        onMouseEnter={() => {
                          if (item.opensWorkspaceMenu) {
                            openWorkspaceMenu(item.workspaceId);
                          } else {
                            scheduleWorkspaceMenuClose();
                          }
                        }}
                        onMouseLeave={() => {
                          if (item.opensWorkspaceMenu) {
                            scheduleWorkspaceMenuClose();
                          }
                        }}
                        onClick={(event) => {
                          if (item.opensWorkspaceMenu) {
                            event.preventDefault();
                            openWorkspaceMenu(item.workspaceId);
                            return;
                          }
                          setOpenWorkspaceMenuId(null);
                          setSidebarOpen(false);
                        }}
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
                          <>
                            <span className="min-w-0 flex-1 truncate">{item.name}</span>
                            {item.opensWorkspaceMenu && (
                              <ChevronRight
                                className={`ml-2 h-4 w-4 flex-shrink-0 ${openWorkspaceMenuId === item.workspaceId ? 'text-zinc-500 dark:text-zinc-300' : 'text-zinc-400 dark:text-zinc-500'}`}
                              />
                            )}
                          </>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          {/* Bottom account entry */}
          <div
            ref={accountMenuRef}
            className={`relative border-t border-zinc-200 flex-shrink-0 dark:border-zinc-800 ${collapsed ? 'p-2' : 'p-3'}`}
          >
            {accountMenuOpen && (
              <div className={`absolute z-50 overflow-hidden rounded-lg border border-zinc-200 bg-white py-1.5 shadow-lg dark:border-zinc-800 dark:bg-zinc-900 ${
                collapsed ? 'bottom-2 left-full ml-2 w-48' : 'bottom-full left-3 right-3 mb-2'
              }`}>
                {showFlocksproUpgradeEntry && (
                  <Link
                    to="/settings/flockspro"
                    state={settingsReturnState}
                    onClick={() => {
                      setAccountMenuOpen(false);
                      setSidebarOpen(false);
                    }}
                    className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                  >
                    <ArrowUpCircle className="h-4 w-4 text-zinc-400" />
                    {t('flocksproUpgrade')}
                  </Link>
                )}
                <button
                  type="button"
                  onClick={openManualUpdateCheck}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                >
                  <RefreshCw className="h-4 w-4 text-zinc-400" />
                  {t('checkUpdate')}
                </button>
                <Link
                  to="/settings/preferences"
                  state={settingsReturnState}
                  onClick={() => {
                    setAccountMenuOpen(false);
                    setSidebarOpen(false);
                  }}
                  className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                >
                  <Settings className="h-4 w-4 text-zinc-400" />
                  {t('settings')}
                </Link>
                <button
                  type="button"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    void logout();
                  }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                >
                  <LogOut className="h-4 w-4 text-zinc-400" />
                  {t('logout')}
                </button>
              </div>
            )}
            {collapsed ? (
              <button
                type="button"
                onClick={() => setAccountMenuOpen((value) => !value)}
                title={user?.username || t('settings')}
                aria-label={user?.username ? `${user.username} ${t('settings')}` : t('settings')}
                className="flex h-9 w-full items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-white/70 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-100"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-200 text-xs font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {accountInitial}
                </span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setAccountMenuOpen((value) => !value)}
                className="relative flex min-h-[62px] w-full items-center gap-2 rounded-lg px-2 py-2 pr-7 text-left transition-colors hover:bg-white/70 dark:hover:bg-zinc-900"
                aria-expanded={accountMenuOpen}
                aria-label={user?.username ? `${user.username} ${t('settings')}` : t('settings')}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-xs font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  {accountInitial}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-baseline gap-1.5">
                    <span className="min-w-0 truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">
                      {user?.username || productName}
                    </span>
                    <span className="shrink-0 whitespace-nowrap text-xs text-zinc-400 dark:text-zinc-500">
                      {accountRoleLabel}
                    </span>
                  </span>
                  {displayVersion && (
                    <span
                      className="mt-1 inline-flex w-fit rounded border border-zinc-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold leading-none text-zinc-400 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-500"
                      title={t('versionInfo')}
                    >
                      {displayVersion}
                    </span>
                  )}
                </span>
                <ChevronUp className={`absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400 transition-transform ${accountMenuOpen ? 'rotate-180' : ''}`} />
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

      {activeWorkspaceMenu && (
        <nav
          aria-label={tWebUIContractPage('workspace.sectionNavigation')}
          onMouseEnter={cancelWorkspaceMenuClose}
          onMouseLeave={scheduleWorkspaceMenuClose}
          className={`fixed inset-y-0 z-[60] flex w-52 max-w-[calc(100vw-4rem)] flex-col border-r border-zinc-200 bg-zinc-100 text-zinc-600 shadow-2xl shadow-zinc-900/10 transition-[left] duration-300 ease-in-out dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300 dark:shadow-black/30 ${
            collapsed ? 'left-16' : 'left-52'
          }`}
        >
          <div className="flex h-16 items-center gap-3 border-b border-zinc-200 px-4 dark:border-white/10">
            {ActiveWorkspaceMenuIcon && (
              <ActiveWorkspaceMenuIcon className="h-5 w-5 shrink-0 text-zinc-500 dark:text-zinc-300" />
            )}
            <div className="min-w-0 flex-1 truncate text-base font-bold text-zinc-950 dark:text-white" title={activeWorkspaceMenu.title}>
              {activeWorkspaceMenu.title}
            </div>
            <button
              type="button"
              onClick={() => setOpenWorkspaceMenuId(null)}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-white/70 hover:text-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-200 dark:hover:bg-white/10 dark:hover:text-white dark:focus:ring-zinc-700"
              title={tWebUIContractPage('workspace.collapseSidebar')}
              aria-label={tWebUIContractPage('workspace.collapseSidebar')}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 space-y-2 overflow-y-auto px-3 py-4">
            {activeWorkspaceSections.length > 0 ? (
              activeWorkspaceSections.map((workspaceSection) => {
                const sectionActive = workspaceSection.pages.some((page) => location.pathname === `${activeWorkspaceMenu.route}/${page.id}`);
                const showPageChildren = workspaceSection.pages.length > 1;
                const sectionCollapsed = collapsedWorkspaceSectionIds.has(workspaceSection.id);
                return (
                  <div key={workspaceSection.id} className="space-y-1">
                    <div
                      className={`flex h-8 items-center rounded-md px-3 text-xs font-semibold uppercase tracking-wider transition-colors ${
                        sectionActive
                          ? 'text-zinc-500 dark:text-zinc-400'
                          : 'text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300'
                      }`}
                    >
                      {showPageChildren ? (
                        <button
                          type="button"
                          onClick={() => toggleWorkspaceSection(workspaceSection.id)}
                          className="min-w-0 flex-1 truncate text-left"
                        >
                          {workspaceSection.label}
                        </button>
                      ) : (
                        <Link
                          to={`${activeWorkspaceMenu.route}/${workspaceSection.defaultPageId}`}
                          onClick={() => {
                            setOpenWorkspaceMenuId(null);
                            setSidebarOpen(false);
                          }}
                          title={workspaceSection.label}
                          className="min-w-0 flex-1 truncate"
                        >
                          {workspaceSection.label}
                        </Link>
                      )}
                      {showPageChildren ? (
                        <button
                          type="button"
                          onClick={() => toggleWorkspaceSection(workspaceSection.id)}
                          className="ml-2 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-white/60 hover:text-zinc-700 dark:text-zinc-500 dark:hover:bg-white/10 dark:hover:text-zinc-200"
                          aria-label={sectionCollapsed ? tWebUIContractPage('workspace.expandSidebar') : tWebUIContractPage('workspace.collapseSidebar')}
                          title={sectionCollapsed ? tWebUIContractPage('workspace.expandSidebar') : tWebUIContractPage('workspace.collapseSidebar')}
                        >
                          <ChevronRight className={`h-3.5 w-3.5 transition-transform ${sectionCollapsed ? '' : 'rotate-90'}`} />
                        </button>
                      ) : null}
                    </div>

                    {showPageChildren && !sectionCollapsed ? (
                      <div className="space-y-1">
                        {workspaceSection.pages.map((page) => {
                          const pageActive = location.pathname === `${activeWorkspaceMenu.route}/${page.id}`;
                          return (
                            <Link
                              key={page.id}
                              to={`${activeWorkspaceMenu.route}/${page.id}`}
                              onClick={() => {
                                setOpenWorkspaceMenuId(null);
                                setSidebarOpen(false);
                              }}
                              className={`flex h-10 items-center rounded-md px-3 text-sm font-semibold transition-colors ${
                                pageActive
                                  ? 'bg-white text-zinc-950 shadow-sm dark:bg-white/10 dark:text-white'
                                  : 'text-zinc-500 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-zinc-100'
                              }`}
                            >
                              <span className="truncate">{page.title}</span>
                            </Link>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })
            ) : (
              <div className="px-3 py-2 text-sm text-zinc-400 dark:text-zinc-500">
                {tWebUIContractPage('workspace.empty')}
              </div>
            )}
          </div>
        </nav>
      )}

      {/* Mobile top menu button */}
      <div className={`lg:hidden fixed top-0 left-0 z-30 flex items-center h-16 px-4 ${sidebarOpen ? 'hidden' : ''}`}>
        <button
          onClick={() => setSidebarOpen(true)}
          className="p-2 text-gray-500 hover:text-gray-700 bg-white rounded-lg shadow-sm border border-gray-200 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-50"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      {/* Main content area */}
      <div
        className={`flex flex-col h-screen transition-all duration-300 ${collapsed ? 'lg:pl-16' : 'lg:pl-52'}`}
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
