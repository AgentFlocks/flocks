import { Link, useLocation, useNavigate, type RouteObject } from 'react-router-dom';
import {
  Home,
  MessageSquarePlus,
  Workflow,
  ListTodo,
  ChevronLeft,
  ChevronRight,
  Layers,
  ShieldCheck,
  ScanSearch,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react';
import type { ComponentType } from 'react';
import { useTranslation } from 'react-i18next';
import { onboardingAPI } from '@/api/onboarding';
import { useTokenPolicyNotice } from '@/hooks/useTokenPolicyNotice';

const SIDEBAR_COLLAPSED_KEY = 'flocks_layout_sidebar_collapsed';

type LazyLayoutModule = { default: ComponentType<any> };

function lazyLayoutComponent<T extends LazyLayoutModule>(
  loader: () => Promise<T>,
  namespaces: readonly string[] = [],
) {
  return lazy(() => recoverLazyLoad(
    Promise.all([loader(), preloadI18nNamespaces(namespaces)]).then(([module]) => module),
  ));
}

function readSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
  } catch {
    return false;
  }
}

const OnboardingModal = lazyLayoutComponent(() => import('@/components/common/OnboardingModal'));
const UpdateModal = lazyLayoutComponent(() => import('@/components/common/UpdateModal'), ['update']);
const NotificationModal = lazyLayoutComponent(() => import('@/components/common/NotificationModal'), ['notification']);
const TokenPolicyNotice = lazyLayoutComponent(() => import('@/components/common/TokenPolicyNotice'), ['notification']);
import { checkUpdate, getCurrentRelease, type VersionInfo } from '@/api/update';
import {
  ackNotification,
  getActiveNotifications,
  getNotificationAckStatus,
  type UserNotification,
} from '@/api/notifications';
import registry, { type ExtensionNavItem } from '@ext/registry';
import ExtensionSlot from '@/components/extensions/ExtensionSlot';
import { useAuth } from '@/contexts/AuthContext';
import { useProductName } from '@/contexts/ProductNameContext';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { getLocalizedReleaseNotes } from '@/utils/releaseNotes';
import { UPDATE_DISMISSED_KEY, buildUpdateDismissalKey, isUpdateDismissed } from '@/utils/updateDismissal';
import { useWebUIContractPages } from '@/hooks/useWebUIContractPages';
import { useSceneSuiteUpdates } from '@/hooks/useSceneSuiteUpdates';
import { useTaskDashboard } from '@/hooks/useTasks';
import { preloadI18nNamespaces } from '@/i18nResources';
import { resolveWebUIContractPageIcon } from '@/utils/webuiContractPageIcons';
import {
  buildWebUIContractWorkspacePageList,
  buildWebUIContractWorkspaceSections,
  getLocalizedWebUIContractTitle,
  workspaceSectionHref,
} from '@/utils/webuiContractWorkspaceSections';
import { useWorkspacePageOrders } from '@/hooks/useWorkspacePageOrders';
import { useLayoutOpenTabs } from '@/hooks/useLayoutOpenTabs';
import { findActiveTabHref, resolveOpenTabs } from '@/utils/layoutTabs';
import {
  AGENT_WORKSPACE,
  AGENT_WORKSPACE_DEFAULT_PATH,
  SCENE_SUITES_PATH,
  isOpenSceneWorkspace,
  readCurrentWorkspace,
  readWorkspacePaths,
  resolveNavRoute,
  saveCurrentWorkspace,
  saveWorkspacePaths,
  sceneNavWorkspace,
  sceneWorkspaceIdOf,
  type NavWorkspaceId,
  type NavWorkspacePaths,
} from '@/utils/navPartitions';
import WorkspaceHeader, { type HeaderWorkspace } from './WorkspaceHeader';
import AccountMenu, { ACCOUNT_MENU_HREFS, type AccountMenuExtraItem } from './AccountMenu';
import SessionHistoryPanel from './SessionHistoryPanel';
import KeepAlivePanes from './KeepAlivePanes';
import { contentRoutes as appContentRoutes } from '@/routes/contentRoutes';
import LazyLoadErrorBoundary from '@/components/common/LazyLoadErrorBoundary';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';
import { recoverLazyLoad } from '@/utils/chunkLoadRecovery';

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 600_000;
const UPDATE_CHECK_INITIAL_DELAY_MS = 250;
// Pane used for routes that are not a nav entry (settings, scene manager).
const TRANSIENT_PANE_HREF = '__current__';

interface NavItem {
  /** Stable id so an extension registry can hide a default entry (`hideDefault`). */
  id?: string;
  name: string;
  href: string;
  icon: LucideIcon;
  pageId?: string;
  /** Starts a new chat instead of routing to a stored path. */
  action?: 'new-chat';
  count?: number;
  accent?: boolean;
}

interface NavSection {
  id: string;
  name?: string;
  items: NavItem[];
  collapsible?: boolean;
}

interface LayoutProps {
  contentRoutes?: RouteObject[];
}

function formatUpdateVersion(version?: string | null): string | null {
  const raw = (version || '').trim();
  if (!raw) return null;
  return /^(pro-)?v/i.test(raw) ? raw : `v${raw}`;
}

function currentProductVersion(info: VersionInfo): string | null {
  return info.current_version || null;
}

function latestProductVersion(info: VersionInfo): string | null {
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

/** Scene workspaces the switcher offers, in the order the API returns them. */
function sceneHeaderIcon(workspace: WebUIContractWorkspaceListItem): LucideIcon {
  const id = workspace.id.toLowerCase();
  if (id.includes('soc')) return ShieldCheck;
  if (id.includes('code') || id.includes('audit') || id.includes('security')) return ScanSearch;
  return Layers;
}

// Sidebar group each default entry sat in before the agent hub moved into the
// account menu. A private build's `hideDefault` may still name a whole group.
const LEGACY_NAV_GROUPS = new Map<string, string>([
  ['flocksHome', 'aiWorkbench'],
  ['sessions', 'aiWorkbench'],
  ['workspace', 'aiWorkbench'],
  ['tasks', 'aiWorkbench'],
  ['workflows', 'aiWorkbench'],
  ['agents', 'agentHub'],
  ['skills', 'agentHub'],
  ['tools', 'agentHub'],
  ['deviceIntegration', 'agentHub'],
  ['hub', 'agentHub'],
  ['models', 'agentHub'],
  ['channels', 'agentHub'],
]);

const hidesDefault = (id?: string) => Boolean(id) && registry.hideDefault?.(id as string) === true;
const isHiddenDefault = (id?: string) => hidesDefault(id) || hidesDefault(id ? LEGACY_NAV_GROUPS.get(id) : undefined);

/** Group an extension's sidebar entry joined in earlier releases. */
function legacyExtensionGroup(item: ExtensionNavItem): string {
  if (item.section === 'home' || item.section === 'aiWorkbench') return 'aiWorkbench';
  if (item.section === 'sceneWorkspaces') return 'customPages';
  return 'agentHub';
}

export default function Layout({ contentRoutes = appContentRoutes }: LayoutProps = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const confirm = useConfirm();
  const { t, i18n } = useTranslation('nav');
  const { t: tAuth } = useTranslation('auth');
  const { productName } = useProductName();

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readSidebarCollapsed);
  const isHome = location.pathname === '/';
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [updateState, setUpdateState] = useState<'idle' | 'pending' | 'visible'>('idle');
  // Whether the open dialog was asked for explicitly. Inferring it from
  // "we have no cached version info" was fragile: a background refresh landing
  // while the dialog was open filled the cache and silently turned the forced
  // check off under it.
  const [updateCheckForced, setUpdateCheckForced] = useState(false);
  const tokenPolicy = useTokenPolicyNotice(user?.id, showOnboarding || updateState === 'visible');
  const handleUpdatePresented = useCallback(() => {
    if (document.visibilityState === 'visible') setUpdateState('visible');
  }, []);
  const requestUpdate = useCallback(() => {
    setUpdateState((state) => (state === 'visible' ? state : 'pending'));
  }, []);
  const closeUpdate = useCallback(() => {
    setUpdateState('idle');
    setUpdateCheckForced(false);
  }, []);

  const [hasUpdate, setHasUpdate] = useState(false);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [updateInfo, setUpdateInfo] = useState<VersionInfo | null>(null);
  const [currentRelease, setCurrentRelease] = useState<{ userId: string; info: VersionInfo | null } | null>(null);
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
  const canManageUpdates = user?.role === 'admin';
  const notificationGateReady = !canManageUpdates || hasCompletedUpdateCheck;
  const currentReleaseReady = !!user?.id && currentRelease?.userId === user.id;

  const {
    pages: webuiContractPages,
    workspaces: webuiContractWorkspaces = [],
    loading: webuiContractNavLoading,
    error: webuiContractNavError,
  } = useWebUIContractPages();
  const viewingScenes = location.pathname === '/scenes' || location.pathname.startsWith('/scenes/');
  const { hasUnseenSuites } = useSceneSuiteUpdates({ viewingScenes, userId: user?.id });
  const workspacePageOrders = useWorkspacePageOrders();
  const { counts: taskCounts } = useTaskDashboard();
  const [openTabRecords, setOpenTabRecords] = useLayoutOpenTabs();
  const openTabRecordsRef = useRef(openTabRecords);
  useEffect(() => {
    openTabRecordsRef.current = openTabRecords;
  }, [openTabRecords]);
  const [collapsedNavSectionIds, setCollapsedNavSectionIds] = useState<Set<string>>(new Set());

  // Entries an extension registered, admin-only ones dropped for members, in
  // registry order. Empty in a stock build.
  const extensionNavItems = useMemo(() => {
    const label = (item: ExtensionNavItem) => {
      const ns = item.ns || 'nav';
      return i18n.exists?.(item.labelKey, { ns }) === false
        ? item.labelKey
        : t(item.labelKey, { ns, productName });
    };
    const toItem = (item: ExtensionNavItem): AccountMenuExtraItem => ({
      id: item.id,
      name: label(item),
      href: item.href,
      icon: item.icon,
      highlight: item.highlight,
      badge: item.badge,
    });
    const visible = registry.navItems
      .filter((item) => !item.adminOnly || user?.role === 'admin')
      .filter((item) => !isHiddenDefault(item.id))
      .filter((item) => item.placement === 'accountMenu' || !hidesDefault(legacyExtensionGroup(item)))
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const sidebar = visible.filter((item) => (item.placement ?? 'sidebar') === 'sidebar');
    return {
      accountMenu: visible.filter((item) => item.placement === 'accountMenu').map(toItem),
      // The agent hub moved into the account menu, so sidebar entries for it
      // join the integrations group there; the other sections are part of the
      // Agent workspace's own menu.
      integrations: sidebar.filter((item) => (item.section ?? 'agentHub') === 'agentHub').map(toItem),
      agentWorkspace: sidebar.filter((item) => (item.section ?? 'agentHub') !== 'agentHub').map(toItem),
    };
  }, [i18n, productName, t, user?.role]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((value) => {
      const next = !value;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch {
        // Collapse memory is a convenience only.
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!isHome) return undefined;
    let cancelled = false;
    onboardingAPI.getStatus()
      .then((res) => {
        if (!cancelled && !res.data.completed) setShowOnboarding(true);
      })
      .catch(() => {
        if (!cancelled) setShowOnboarding(true);
      });
    return () => {
      cancelled = true;
    };
  }, [isHome]);

  const handleOpenOnboarding = useCallback(() => setShowOnboarding(true), []);

  useEffect(() => {
    window.addEventListener('flocks:open-onboarding', handleOpenOnboarding);
    return () => window.removeEventListener('flocks:open-onboarding', handleOpenOnboarding);
  }, [handleOpenOnboarding]);

  const refreshUpdateStatus = useCallback(async (bypassMinGap = false) => {
    if (!canManageUpdates) return;

    const now = Date.now();
    if (checkingUpdateRef.current) return;
    if (!bypassMinGap && now - lastUpdateCheckAtRef.current < UPDATE_CHECK_MIN_GAP_MS) return;

    checkingUpdateRef.current = true;
    lastUpdateCheckAtRef.current = now;

    try {
      const info = await checkUpdate(i18n.language, registry.updateChannel);
      setUpdateInfo(info);

      const displayCurrentVersion = currentProductVersion(info);
      const displayLatestVersion = latestProductVersion(info);

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
          requestUpdate();
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
  }, [canManageUpdates, i18n.language, requestUpdate]);

  useEffect(() => {
    if (!canManageUpdates) return undefined;

    const initialCheckTimerId = window.setTimeout(() => {
      refreshUpdateStatus(true);
    }, UPDATE_CHECK_INITIAL_DELAY_MS);

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
      window.clearTimeout(initialCheckTimerId);
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [canManageUpdates, refreshUpdateStatus]);

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
    const fetchKey = `${user.id}:${i18n.language}`;
    if (lastNotificationFetchKeyRef.current === fetchKey) return;
    const previousFetchKey = lastNotificationFetchKeyRef.current;
    lastNotificationFetchKeyRef.current = fetchKey;
    setBackendNotificationsReady(false);

    let cancelled = false;
    void getActiveNotifications(i18n.language)
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
  }, [i18n.language, user?.id]);

  useEffect(() => {
    const userId = user?.id;
    if (!userId) {
      setCurrentRelease(null);
      return;
    }

    let cancelled = false;
    void getCurrentRelease()
      .then((info) => {
        if (!cancelled) setCurrentRelease({ userId, info });
      })
      .catch(() => {
        if (!cancelled) setCurrentRelease({ userId, info: null });
      });

    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  useEffect(() => {
    if (!user?.id) {
      setUpdateNotification(null);
      setUpdateNotificationReady(false);
      return;
    }
    if (!notificationGateReady || !currentReleaseReady) return;

    setUpdateNotificationReady(false);
    const notification = buildUpdateNotification(currentRelease?.info ?? null, i18n.language);
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
  }, [currentRelease, currentReleaseReady, i18n.language, notificationGateReady, user?.id]);

  const allNotifications = updateNotification
    ? [...notifications, updateNotification].sort((a, b) => a.priority - b.priority)
    : notifications;
  const visibleNotifications = tokenPolicy.ready && !tokenPolicy.notice && backendNotificationsReady && currentReleaseReady && updateNotificationReady && !showOnboarding && updateState === 'idle' && allNotifications.length > 0
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

  // ── Workspaces ────────────────────────────────────────────────────────────
  const sceneWorkspaces = useMemo(
    () => webuiContractWorkspaces.filter((workspace) => workspace.placement === 'sceneWorkspace'),
    [webuiContractWorkspaces],
  );
  const openSceneWorkspaces = useMemo(
    () => sceneWorkspaces.filter(isOpenSceneWorkspace),
    [sceneWorkspaces],
  );
  const headerWorkspaces = useMemo<HeaderWorkspace[]>(() => [
    { id: AGENT_WORKSPACE, name: t('agentWorkspace'), icon: Sparkles },
    ...openSceneWorkspaces.map((workspace) => ({
      id: sceneNavWorkspace(workspace.id),
      name: getLocalizedWebUIContractTitle(workspace, i18n.language),
      icon: sceneHeaderIcon(workspace),
    })),
  ], [i18n.language, openSceneWorkspaces, t]);

  const navStateReady = !webuiContractNavLoading
    || webuiContractWorkspaces.length > 0
    || webuiContractPages.length > 0;

  // The workspace whose menu the sidebar shows. Settings and the scene manager
  // keep the workspace they were opened from.
  const routeNav = resolveNavRoute(location.pathname, navStateReady ? webuiContractWorkspaces : null);
  const [rememberedWorkspace, setRememberedWorkspace] = useState<NavWorkspaceId>(readCurrentWorkspace);
  const routeWorkspace = routeNav.kind === 'workspace' ? routeNav.workspace : null;
  const currentWorkspace = routeWorkspace ?? rememberedWorkspace;
  useEffect(() => {
    if (!routeWorkspace || !navStateReady) return;
    // A scene route only "counts" once we know that scene is still open.
    const sceneId = sceneWorkspaceIdOf(routeWorkspace);
    if (sceneId && !openSceneWorkspaces.some((workspace) => workspace.id === sceneId)) return;
    setRememberedWorkspace(routeWorkspace);
    saveCurrentWorkspace(routeWorkspace);
  }, [navStateReady, openSceneWorkspaces, routeWorkspace]);

  // Last path visited inside each workspace, so switching back lands there.
  const workspacePathsRef = useRef<NavWorkspacePaths>(readWorkspacePaths());
  useEffect(() => {
    if (!routeWorkspace || !navStateReady || viewingScenes) return;
    const next = { ...workspacePathsRef.current, [routeWorkspace]: `${location.pathname}${location.search}` };
    workspacePathsRef.current = next;
    saveWorkspacePaths(next);
  }, [location.pathname, location.search, navStateReady, routeWorkspace, viewingScenes]);

  const newChat = useCallback(() => {
    setSidebarOpen(false);
    navigate('/sessions', { state: { skipLastSelectedSessionRestore: true } });
  }, [navigate]);

  // ── Sidebar sections for the current workspace ───────────────────────────
  const agentSection = useMemo<NavSection>(() => {
    const workbenchItems: NavItem[] = webuiContractWorkspaces
      .filter((workspace) => workspace.placement === 'aiWorkbench' && workspace.enabled)
      .flatMap((workspace) => workspacePageOrders
        .apply(workspace.id, buildWebUIContractWorkspacePageList(workspace, i18n.language)
          .filter((page) => page.enabled && page.buildStatus === 'ready'))
        .map((page) => ({
          pageId: page.id,
          name: page.title,
          href: `${workspace.route}/${page.id}`,
          icon: resolveWebUIContractPageIcon(page.icon),
        })));
    const customItems: NavItem[] = webuiContractPages
      .filter((page) => !page.workspaceId && page.enabled && page.placement === 'home.after' && page.buildStatus === 'ready')
      .map((page) => ({
        name: getLocalizedWebUIContractTitle(page, i18n.language),
        href: page.route,
        icon: resolveWebUIContractPageIcon(page.icon),
      }));
    const items: NavItem[] = [
      { id: 'flocksHome', name: t('flocksHome'), href: '/', icon: Home },
      // Keeps the `sessions` id: it is the entry that leads to /sessions.
      { id: 'sessions', name: t('newChat'), href: '/sessions', icon: MessageSquarePlus, action: 'new-chat', accent: true },
      { id: 'tasks', name: t('tasks'), href: '/tasks', icon: ListTodo, count: taskCounts ? (taskCounts.running ?? 0) + (taskCounts.queued ?? 0) : undefined },
      { id: 'workflows', name: t('workflows'), href: '/workflows', icon: Workflow },
      // Workbench workspace pages sat in the aiWorkbench group too.
      ...(hidesDefault('aiWorkbench') ? [] : workbenchItems),
      ...(hidesDefault('customPages') ? [] : customItems),
      ...extensionNavItems.agentWorkspace,
    ];
    return {
      id: 'agent',
      items: items.filter((item) => !isHiddenDefault(item.id)),
    };
  }, [extensionNavItems, i18n.language, taskCounts, t, webuiContractPages, webuiContractWorkspaces, workspacePageOrders]);

  const sceneSections = useMemo(() => {
    const map = new Map<string, NavSection[]>();
    for (const workspace of openSceneWorkspaces) {
      const readyPages = (pages: WebUIContractWorkspaceListItem['pages']) => pages.filter((page) => page.enabled && page.buildStatus === 'ready');
      const groups: NavSection[] = (workspace.sections?.length ?? 0) > 0
        ? buildWebUIContractWorkspaceSections(workspace, i18n.language).reduce<NavSection[]>((result, section) => {
          const pages = workspacePageOrders.apply(`${workspace.id}:${section.id}`, readyPages(section.pages));
          if (section.flat) {
            // One item named by the section; consecutive flat sections share a header-less group.
            const page = pages.find((item) => item.id === section.defaultPageId) ?? pages[0];
            if (!page) return result;
            const item: NavItem = {
              pageId: page.id,
              name: section.label,
              href: workspaceSectionHref(workspace.route, section, page.id),
              icon: resolveWebUIContractPageIcon(section.icon || page.icon),
            };
            const last = result[result.length - 1];
            if (last && !last.name) last.items.push(item);
            else result.push({ id: `scene:${workspace.id}:${section.id}`, items: [item] });
            return result;
          }
          result.push({
            id: `scene:${workspace.id}:${section.id}`,
            name: section.label,
            collapsible: true,
            items: pages.map((page) => ({
              pageId: page.id,
              name: page.title,
              href: workspaceSectionHref(workspace.route, section, page.id),
              icon: resolveWebUIContractPageIcon(page.icon),
            } as NavItem)),
          });
          return result;
        }, [])
        : [{
          id: `scene:${workspace.id}`,
          items: workspacePageOrders
            .apply(workspace.id, readyPages(buildWebUIContractWorkspacePageList(workspace, i18n.language)))
            .map((page) => ({
              pageId: page.id,
              name: page.title,
              href: `${workspace.route}/${page.id}`,
              icon: resolveWebUIContractPageIcon(page.icon),
            } as NavItem)),
        }];
      map.set(sceneNavWorkspace(workspace.id), groups.filter((group) => group.items.length > 0));
    }
    return map;
  }, [i18n.language, openSceneWorkspaces, workspacePageOrders]);

  const visibleSections = useMemo<NavSection[]>(() => {
    if (currentWorkspace === AGENT_WORKSPACE) return [agentSection];
    return sceneSections.get(currentWorkspace) ?? [];
  }, [agentSection, currentWorkspace, sceneSections]);

  // ── Keep-alive panes: every visited nav href stays mounted ────────────────
  const allNavItems = useMemo<NavItem[]>(() => {
    const items = [...agentSection.items];
    for (const groups of sceneSections.values()) for (const group of groups) items.push(...group.items);
    return items;
  }, [agentSection, sceneSections]);
  const navItemsFlat = useMemo(() => {
    // Account-menu destinations keep their pane alive too, though they are not
    // in the sidebar.
    const accountHrefs = [...ACCOUNT_MENU_HREFS, ...extensionNavItems.integrations.map((item) => item.href)];
    const seen = new Set<string>();
    const items: NavItem[] = [];
    for (const item of [...allNavItems, ...accountHrefs.map((href) => ({ name: href, href, icon: Home }))]) {
      if (seen.has(item.href)) continue;
      seen.add(item.href);
      items.push(item);
    }
    return items;
  }, [allNavItems, extensionNavItems]);

  const isNavItemActive = useCallback((href: string) => {
    const active = findActiveTabHref(navItemsFlat.map((item) => item.href), location.pathname, location.search);
    return active === href;
  }, [location.pathname, location.search, navItemsFlat]);

  const activeTabHref = useMemo(
    () => findActiveTabHref(navItemsFlat.map((item) => item.href), location.pathname, location.search),
    [location.pathname, location.search, navItemsFlat],
  );
  const currentLocationPath = `${location.pathname}${location.search}`;

  useEffect(() => {
    if (!activeTabHref) return;
    const stored = openTabRecordsRef.current;
    const existing = stored.find((record) => record.href === activeTabHref);
    if (existing && existing.path === currentLocationPath) return;
    setOpenTabRecords(existing
      ? stored.map((record) => (record.href === activeTabHref ? { ...record, path: currentLocationPath } : record))
      : [...stored, { href: activeTabHref, path: currentLocationPath }]);
  }, [activeTabHref, currentLocationPath, setOpenTabRecords]);

  const openTabs = useMemo(() => {
    const tabs = resolveOpenTabs(navItemsFlat, openTabRecords).map((tab) => ({ href: tab.href, path: tab.path }));
    if (activeTabHref && !tabs.some((tab) => tab.href === activeTabHref)) {
      tabs.push({ href: activeTabHref, path: currentLocationPath });
    }
    return tabs;
  }, [activeTabHref, currentLocationPath, navItemsFlat, openTabRecords]);

  const keepAlivePanes = useMemo(() => {
    const panes = openTabs.map((tab) => ({ href: tab.href }));
    return activeTabHref ? panes : [...panes, { href: TRANSIENT_PANE_HREF }];
  }, [activeTabHref, openTabs]);
  const activePaneHref = activeTabHref ?? TRANSIENT_PANE_HREF;
  const openTabPathByHref = useMemo(() => {
    const map = new Map<string, string>();
    for (const tab of openTabs) map.set(tab.href, tab.path);
    return map;
  }, [openTabs]);
  const navItemPath = useCallback(
    (href: string) => href === '/workspace' ? '/workspace?tab=files' : openTabPathByHref.get(href) ?? href,
    [openTabPathByHref],
  );

  // A scene whose workspace closed (uninstalled, disabled or license lapsed)
  // cannot stay on screen: fall back to its suite manager.
  const routeSceneId = routeWorkspace ? sceneWorkspaceIdOf(routeWorkspace) : null;
  useEffect(() => {
    if (!navStateReady || webuiContractNavError || viewingScenes || !routeSceneId) return;
    if (!openSceneWorkspaces.some((workspace) => workspace.id === routeSceneId)) {
      navigate(SCENE_SUITES_PATH, { replace: true });
    }
  }, [navStateReady, navigate, openSceneWorkspaces, routeSceneId, viewingScenes, webuiContractNavError]);

  const selectWorkspace = useCallback((workspaceId: NavWorkspaceId) => {
    setSidebarOpen(false);
    if (workspaceId === currentWorkspace && !viewingScenes) return;
    const stored = workspacePathsRef.current[workspaceId];
    if (stored) {
      navigate(stored);
      return;
    }
    if (workspaceId === AGENT_WORKSPACE) {
      navigate(AGENT_WORKSPACE_DEFAULT_PATH);
      return;
    }
    const sceneId = sceneWorkspaceIdOf(workspaceId);
    const workspace = openSceneWorkspaces.find((item) => item.id === sceneId);
    if (workspace) navigate(workspace.route);
  }, [currentWorkspace, navigate, openSceneWorkspaces, viewingScenes]);

  const toggleNavSection = useCallback((sectionId: string) => {
    setCollapsedNavSectionIds((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) next.delete(sectionId);
      else next.add(sectionId);
      return next;
    });
  }, []);

  const openManualUpdateCheck = useCallback(() => {
    setUpdateInfo(null);
    setUpdateCheckForced(true);
    requestUpdate();
  }, [requestUpdate]);

  const handleLogout = useCallback(async () => {
    const ok = await confirm({
      title: t('logout'),
      description: t('logoutConfirm'),
      confirmText: t('logout'),
    });
    if (ok) void logout();
  }, [confirm, logout, t]);

  const openShortcuts = useCallback(() => {
    void confirm({
      title: t('shortcutsTitle'),
      description: t('shortcutsBody'),
      confirmText: t('gotIt'),
      hideCancel: true,
    });
  }, [confirm, t]);

  const displayVersion = formatUpdateVersion(currentVersion);
  const accountInitial = (user?.username || productName || 'F').trim().charAt(0).toUpperCase();
  const accountRoleLabel = user?.role === 'admin' ? tAuth('admin.roleAdmin') : tAuth('admin.roleMember');
  const hasVisibleUpdate = hasUpdate && canManageUpdates;
  const headerUpdate = hasVisibleUpdate
    ? { title: t('hasNewVersion', { version: formatUpdateVersion(latestVersion) || '' }), onClick: requestUpdate }
    : null;

  const navLinkClass = (active: boolean) => `flex items-center rounded-lg transition-colors ${collapsed ? 'justify-center p-2.5' : 'gap-3 px-3 py-2 text-sm font-medium'} ${
    active
      ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
      : 'text-zinc-600 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
  }`;

  const renderNavItem = (item: NavItem) => {
    const active = isNavItemActive(item.href);
    const iconClass = `h-5 w-5 shrink-0 ${
      item.accent ? 'text-red-500' : active ? 'text-zinc-700 dark:text-zinc-100' : 'text-zinc-400 dark:text-zinc-500'
    }`;
    if (item.action === 'new-chat') {
      return (
        <button
          key={item.href}
          type="button"
          onClick={newChat}
          title={collapsed ? item.name : undefined}
          data-tip={collapsed ? item.name : undefined}
          className={`w-full ${navLinkClass(false)} ${item.accent ? 'text-red-600 dark:text-red-400' : ''}`}
        >
          <item.icon aria-hidden="true" className={iconClass} />
          {!collapsed && <span className="min-w-0 flex-1 truncate text-left">{item.name}</span>}
        </button>
      );
    }
    return (
      <Link
        key={item.href}
        to={navItemPath(item.href)}
        onClick={() => setSidebarOpen(false)}
        aria-current={active ? 'page' : undefined}
        title={collapsed ? item.name : undefined}
        data-tip={collapsed ? item.name : undefined}
        className={navLinkClass(active)}
      >
        <item.icon aria-hidden="true" className={iconClass} />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 truncate">{item.name}</span>
            {item.count ? <span className="shrink-0 rounded-full bg-zinc-200 px-1.5 text-xs text-zinc-500 dark:bg-zinc-700 dark:text-zinc-300">{item.count}</span> : null}
          </>
        )}
      </Link>
    );
  };

  const sidebar = (
    <div className="flex h-full flex-col overflow-hidden">
      <nav className={`flex-1 overflow-y-auto overflow-x-hidden py-3 ${collapsed ? 'px-2' : 'px-3'}`}>
        {visibleSections.map((section) => {
          const sectionCollapsed = !collapsed && Boolean(section.collapsible) && collapsedNavSectionIds.has(section.id);
          return (
            <div key={section.id} className="mb-4">
              {!collapsed && section.name && (
                <h3 className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                  {section.collapsible ? (
                    <button type="button" onClick={() => toggleNavSection(section.id)} aria-expanded={!sectionCollapsed}
                      className="flex h-6 w-full items-center justify-between hover:text-zinc-600 dark:hover:text-zinc-300">
                      <span className="min-w-0 truncate">{section.name}</span>
                      <ChevronRight aria-hidden="true" className={`ml-2 h-3.5 w-3.5 shrink-0 transition-transform ${sectionCollapsed ? '' : 'rotate-90'}`} />
                    </button>
                  ) : section.name}
                </h3>
              )}
              {!sectionCollapsed && <div className="space-y-0.5">{section.items.map(renderNavItem)}</div>}
            </div>
          );
        })}
      </nav>
      {currentWorkspace === AGENT_WORKSPACE && (
        <SessionHistoryPanel collapsed={collapsed} onExpandSidebar={() => setCollapsed(false)} onNavigate={() => setSidebarOpen(false)} />
      )}
      <AccountMenu
        collapsed={collapsed}
        username={user?.username || productName}
        roleLabel={accountRoleLabel}
        initial={accountInitial}
        version={displayVersion}
        versionContext={{ version: displayVersion, currentVersion, latestVersion, hasUpdate: hasVisibleUpdate }}
        hubName={t('hub', { productName })}
        isHidden={isHiddenDefault}
        integrationItems={extensionNavItems.integrations}
        extensionItems={extensionNavItems.accountMenu}
        onNavigate={() => setSidebarOpen(false)}
        onCheckUpdate={openManualUpdateCheck}
        onLogout={() => void handleLogout()}
      />
    </div>
  );

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-zinc-950 dark:text-zinc-100">
      <LazyLoadErrorBoundary mode="overlay">
        <Suspense fallback={null}>
          {showOnboarding && <OnboardingModal onClose={() => setShowOnboarding(false)} />}
          {tokenPolicy.notice && !showOnboarding && updateState !== 'visible' && (
            <TokenPolicyNotice onClose={tokenPolicy.close} onPresented={tokenPolicy.onPresented} />
          )}
        </Suspense>
        {/* One boundary per modal. Sharing a single Suspense meant that whenever
            one of these chunks started loading, the fallback replaced the others
            -- so a notification arriving while the update dialog was open blanked
            the dialog out from under the reader. */}
        <Suspense fallback={null}>
          {updateState !== 'idle' && (updateState === 'visible' || (tokenPolicy.ready && !tokenPolicy.notice && !showOnboarding)) && (
            <UpdateModal
              initialInfo={updateInfo}
              forceInitialCheck={updateCheckForced || updateInfo === null}
              canUpgrade={canManageUpdates}
              onPresented={handleUpdatePresented}
              onClose={closeUpdate}
              onDismiss={closeUpdate}
            />
          )}
        </Suspense>
        <Suspense fallback={null}>
          <ExtensionSlot
            name="layout.notifications"
            context={{
              notifications: visibleNotifications,
              acknowledgingIds: acknowledgingNotificationIds,
              onAcknowledge: closeVisibleNotification,
              onClose: closeVisibleNotification,
              onDismissForever: dismissVisibleNotificationForever,
            }}
          >
            {visibleNotifications.length > 0 && (
              <NotificationModal
                notifications={visibleNotifications}
                acknowledgingIds={acknowledgingNotificationIds}
                onAcknowledge={closeVisibleNotification}
                onClose={closeVisibleNotification}
                onDismissForever={dismissVisibleNotificationForever}
              />
            )}
          </ExtensionSlot>
        </Suspense>
      </LazyLoadErrorBoundary>

      <WorkspaceHeader
        productName={productName}
        update={headerUpdate}
        workspaces={headerWorkspaces}
        currentWorkspace={navStateReady ? currentWorkspace : null}
        onSelectWorkspace={selectWorkspace}
        sceneManager={{ href: SCENE_SUITES_PATH, hasNew: hasUnseenSuites, active: viewingScenes }}
        notifications={notifications}
        onOpenNotification={(notification) => {
          setUpdateNotification(notification.id === updateNotification?.id ? notification : updateNotification);
          if (notification.id.startsWith('whats-new-')) requestUpdate();
        }}
        taskCounts={taskCounts ? { running: taskCounts.running ?? 0, queued: taskCounts.queued ?? 0 } : null}
        onOpenShortcuts={openShortcuts}
        onOpenMobileNav={() => setSidebarOpen(true)}
        mobileNavOpen={sidebarOpen}
      />

      {sidebarOpen && <div className="fixed inset-0 top-14 z-30 bg-zinc-900/40 lg:hidden" onClick={() => setSidebarOpen(false)} />}

      {/* Off-canvas on narrow screens: `invisible` while closed keeps its links
          out of the tab order (visibility flips after the slide-out). */}
      <aside
        className={`fixed bottom-0 left-0 top-14 z-40 max-w-[calc(100vw-2rem)] border-r border-zinc-200 bg-zinc-100 transition-[transform,visibility] dark:border-zinc-800 dark:bg-zinc-950 lg:translate-x-0 ${collapsed ? 'w-16' : 'w-64'} ${sidebarOpen ? 'translate-x-0' : '-translate-x-full invisible lg:visible'}`}
      >
        {sidebar}
        <button
          type="button"
          onClick={toggleCollapsed}
          // Same handle in both states, centred on the sidebar edge so it stays put while pages scroll.
          className="absolute -right-3 top-1/2 hidden h-9 w-6 -translate-y-1/2 items-center justify-center rounded-lg border border-zinc-200 bg-white text-zinc-500 shadow-sm transition-colors hover:text-zinc-800 lg:flex dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          aria-label={collapsed ? t('expandNav') : t('collapseNav')}
          title={collapsed ? t('expandNav') : t('collapseNav')}
        >
          {collapsed ? <ChevronRight aria-hidden="true" className="h-4 w-4" /> : <ChevronLeft aria-hidden="true" className="h-4 w-4" />}
        </button>
      </aside>

      <div className={`flex h-screen flex-col pt-14 transition-[padding] ${collapsed ? 'lg:pl-16' : 'lg:pl-64'}`}>
        <main className="relative flex-1 overflow-hidden bg-gray-50 dark:bg-zinc-950">
          <KeepAlivePanes panes={keepAlivePanes} activeHref={activePaneHref} location={location} routes={contentRoutes} />
        </main>
      </div>
    </div>
  );
}
