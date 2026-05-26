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
  ShieldCheck,
  Shield,
  AlertTriangle,
  Network,
  ServerCog,
  Bug,
  MailWarning,
  Radar,
  Globe2,
} from 'lucide-react';
import { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '@/components/common/LanguageSwitcher';
import OnboardingModal, { isOnboardingDismissed } from '@/components/common/OnboardingModal';
import UpdateModal, { UPDATE_DISMISSED_KEY } from '@/components/common/UpdateModal';
import NotificationModal from '@/components/common/NotificationModal';
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

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 600_000;
type WorkspaceId = 'agent' | 'soc' | 'system';

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
      setHasCompletedUpdateCheck(true);
    }
  }, [flocksproStatusReady, i18n.language, isFlocksproActive]);

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
      setFlocksproVersion(null);
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
    if (!user?.id || user.role !== 'admin') {
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
        flocksproUsersApi.getLicenseStatus(),
        consoleUpgradeApi.getProPackageStatus().catch(() => null),
      ])
        .then(([licenseStatus, packageStatus]) => {
          if (cancelled) return;
          const active = licenseStatus.pro_enabled === true;
          setIsFlocksproActive(active);
          const version = active
            ? formatProVersion(
                packageStatus?.compare_version ||
                  packageStatus?.display_version ||
                  packageStatus?.installed_version ||
                  packageStatus?.flockspro_component_version,
              )
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
  }, [user?.id, user?.role]);

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
    if (location.pathname === href) return true;
    return location.pathname.startsWith(`${href}/`);
  }, [location.pathname]);

  const systemPaths = ['/config', '/permissions', '/monitoring', '/audit-logs', '/flockspro-upgrade', '/system-logs'];
  const isSystemPath = systemPaths.some((path) =>
    location.pathname === path || location.pathname.startsWith(`${path}/`)
  );
  const currentWorkspace: WorkspaceId = location.pathname.startsWith('/soc/')
    || location.pathname === '/soc'
    ? 'soc'
    : isSystemPath
      ? 'system'
      : 'agent';

  const workspaceTabs: Array<{ id: WorkspaceId; name: string; href: string; icon: typeof Bot }> = [
    { id: 'agent', name: t('agentWorkspace'), href: '/sessions', icon: Bot },
    { id: 'soc', name: t('socWorkspace'), href: '/soc', icon: Shield },
    { id: 'system', name: t('systemCenter'), href: '/config', icon: UserCog },
  ];

  type NavItem = { name: string; href: string; icon: typeof Bot };
  type NavSection = { id: string; name: string; items: NavItem[] };
  const workspaceNavigation: Record<WorkspaceId, NavSection[]> = {
    agent: [
      {
        id: 'home',
        name: t('home'),
        items: [
          { name: t('flocksHome'), href: '/', icon: Home },
        ],
      },
      {
        id: 'aiWorkbench',
        name: t('aiWorkbench'),
        items: [
          { name: t('sessions'), href: '/sessions', icon: MessageSquare },
          { name: t('tasks'), href: '/tasks', icon: ListTodo },
          { name: t('workspace'), href: '/workspace', icon: FolderOpen },
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
          { name: t('knowledge'), href: '/knowledge', icon: ShieldCheck },
          { name: t('hub'), href: '/hub', icon: Archive },
          { name: t('models'), href: '/models', icon: Brain },
          { name: t('channels'), href: '/channels', icon: Radio },
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
          { name: t('socCases'), href: '/soc/cases', icon: MessageSquare },
          { name: t('socAssets'), href: '/soc/assets', icon: Network },
          { name: t('socIntel'), href: '/soc/intel', icon: Radar },
          { name: t('socVulnerabilities'), href: '/soc/vulnerabilities', icon: Bug },
          { name: t('socDrills'), href: '/soc/drills', icon: MailWarning },
          { name: t('socAttackSurface'), href: '/soc/attack-surface', icon: Globe2 },
          { name: t('socReports'), href: '/soc/reports', icon: Archive },
        ],
      },
    ],
    system: [
      {
        id: 'systemCenter',
        name: t('systemCenter'),
        items: [
          { name: t('accountManagement'), href: '/config', icon: UserCog },
          { name: t('permissions'), href: '/permissions', icon: ShieldCheck },
          ...(hasFlocksproCapability && user?.role === 'admin'
            ? [{ name: t('auditLogs'), href: '/audit-logs', icon: Shield }]
            : []),
          { name: t('monitoring'), href: '/monitoring', icon: Radio },
          ...(user?.role === 'admin'
            ? [{ name: t('flocksproUpgrade'), href: '/flockspro-upgrade', icon: ArrowUpCircle }]
            : []),
        ],
      },
    ],
  };
  const navigationSections = workspaceNavigation[currentWorkspace];

  const isFullScreenPage =
    matchPath('/workflows/create', location.pathname) ||
    matchPath('/workflows/:id/edit', location.pathname) ||
    matchPath('/workflows/:id', location.pathname) ||
    matchPath('/sessions', location.pathname);
  const productName = isFlocksproActive ? 'Flocks Pro' : 'Flocks';
  const displayVersion = isFlocksproActive
    ? flocksproVersion || (currentVersion ? `v${currentVersion}` : null)
    : currentVersion ? `v${currentVersion}` : null;
  const currentVersionLabel = isFlocksproActive
    ? t('currentProductVersionLabel', { version: displayVersion || productName })
    : currentVersion
    ? t('currentVersionLabel', { version: currentVersion })
    : productName;

  return (
    <div className="min-h-screen bg-gray-50">
      {showOnboarding && (
        <OnboardingModal
          onClose={() => setShowOnboarding(false)}
        />
      )}
      {showUpdate && (
        <UpdateModal
          initialInfo={updateInfo}
          edition={isFlocksproActive ? 'flockspro' : 'flocks'}
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
                title={productName}
              >
                <Sparkles className="w-4 h-4 text-gray-600" />
              </div>
            ) : (
              <>
                <div className="flex items-center flex-1 min-w-0">
                  <span className="text-xl font-bold text-gray-900 whitespace-nowrap">{productName}</span>
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
            {navigationSections.map((section) => (
              <div key={section.id} className="mb-4">
                {!collapsed && (
                  <h3 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-gray-400">
                    {section.name}
                  </h3>
                )}
                <div className="space-y-0.5">
                  {section.items.map((item) => {
                    const isActive = isNavigationItemActive(item.href)
                      || (item.href === '/soc' && location.pathname === '/soc');
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
                        {!collapsed && <span className="truncate">{item.name}</span>}
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
                        {t('newVersion')} {formatUpdateVersion(latestVersion) || ''}
                      </span>
                      <span className="inline-flex flex-shrink-0 items-center rounded-full bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white shadow-sm">
                        {t('updateNow')}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-amber-700">
                      {currentVersionLabel}
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
                        {productName} {displayVersion || '...'}
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
                title={hasUpdate ? t('hasNewVersion', { version: formatUpdateVersion(latestVersion) || '' }) : t('versionInfo')}
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
        <header className="border-b border-gray-200 bg-white px-6 py-3">
          <nav className="flex items-center gap-2 overflow-x-auto">
            {workspaceTabs.map((tab) => {
              const isActive = tab.id === currentWorkspace;
              return (
                <Link
                  key={tab.id}
                  to={tab.href}
                  className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${
                    isActive
                      ? 'bg-slate-900 text-white'
                      : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                  }`}
                >
                  <tab.icon className="h-4 w-4" />
                  {tab.name}
                </Link>
              );
            })}
          </nav>
        </header>
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
