import { Link, useLocation, useNavigate, type RouteObject } from 'react-router-dom';
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
  LayoutGrid,
  Boxes,
  ArrowUpCircle,
  RefreshCw,
  Gauge,
  GripVertical,
  Loader2,
  Pencil,
  Plus,
  type LucideIcon,
} from 'lucide-react';
import { useState, useEffect, useCallback, useMemo, useRef, lazy, Suspense } from 'react';
import type {
  ComponentType,
  CSSProperties,
  DragEvent as ReactDragEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import { onboardingAPI } from '@/api/onboarding';
// Modals are only rendered after the user clicks/triggers them; pulling them
// into the eager Layout chunk costs ~1.7k LOC + i18n keys + lucide icons that
// the home page never needs.
const COLLAPSED_NAV_SECTIONS_KEY = 'flocks_layout_collapsed_nav_sections';
const EXPANDED_PRIMARY_NAV_SECTION_KEY = 'flocks_layout_expanded_primary_nav_section';
const WORKSPACE_NAV_SECTION_PREFIX = 'workspace:';
const AI_WORKBENCH_NAV_SECTION_ID = 'aiWorkbench';
const SCENE_NAV_SECTION_ID = 'sceneWorkspaces';
// Pane used for routes that are not a sidebar entry (they are not kept as tabs).
const TRANSIENT_PANE_HREF = '__current__';
const SIDEBAR_WIDTH_KEY = 'flocks_layout_sidebar_width';
const SIDEBAR_DEFAULT_WIDTH = 208;
const SIDEBAR_MIN_WIDTH = 176;
const SIDEBAR_MAX_WIDTH = 520;
const SOC_DASHBOARD_TITLE_KEY = 'soc-dashboard-custom-title-v1';
const SOC_DASHBOARD_TITLE_CHANGED_EVENT = 'soc-dashboard:title-changed';
const SOC_DASHBOARD_TITLE_MAX_LENGTH = 64;

type LazyLayoutModule = { default: ComponentType<any> };

function lazyLayoutComponent<T extends LazyLayoutModule>(
  loader: () => Promise<T>,
  namespaces: readonly string[] = [],
) {
  return lazy(() => recoverLazyLoad(
    Promise.all([
      loader(),
      preloadI18nNamespaces(namespaces),
    ]).then(([module]) => module),
  ));
}

function readCollapsedNavSectionIds(): Set<string> {
  try {
    const rawValue = localStorage.getItem(COLLAPSED_NAV_SECTIONS_KEY);
    if (!rawValue) return new Set();
    const parsedValue: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsedValue)) return new Set();
    return new Set(parsedValue.filter((item): item is string => typeof item === 'string'));
  } catch {
    return new Set();
  }
}

function saveCollapsedNavSectionIds(sectionIds: Set<string>): void {
  try {
    if (sectionIds.size === 0) {
      localStorage.removeItem(COLLAPSED_NAV_SECTIONS_KEY);
      return;
    }
    localStorage.setItem(COLLAPSED_NAV_SECTIONS_KEY, JSON.stringify(Array.from(sectionIds).sort()));
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
}

/**
 * The AI workbench and each scene workspace (SOC) form an accordion: at most
 * one of them shows its second-level menu at a time. `undefined` means nothing
 * has been stored yet, `null` means the user collapsed all of them.
 */
function readExpandedPrimaryNavSectionId(): string | null | undefined {
  try {
    const rawValue = localStorage.getItem(EXPANDED_PRIMARY_NAV_SECTION_KEY);
    if (rawValue === null) return undefined;
    return rawValue === '' ? null : rawValue;
  } catch {
    return undefined;
  }
}

function saveExpandedPrimaryNavSectionId(sectionId: string | null): void {
  try {
    localStorage.setItem(EXPANDED_PRIMARY_NAV_SECTION_KEY, sectionId ?? '');
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
}

function workspaceNavSectionId(workspaceId: string): string {
  return `${WORKSPACE_NAV_SECTION_PREFIX}${workspaceId}`;
}

function clampSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) return SIDEBAR_DEFAULT_WIDTH;
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
}

function estimateSidebarWidthForName(name?: string | null): number {
  const normalized = (name || '').trim();
  if (!normalized) return SIDEBAR_DEFAULT_WIDTH;

  const weightedLength = Array.from(normalized).reduce((total, char) => {
    return total + (char.charCodeAt(0) > 255 ? 1 : 0.58);
  }, 0);

  return clampSidebarWidth(weightedLength * 18 + 112);
}

function readSidebarWidth(): number {
  try {
    const rawValue = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (!rawValue) return SIDEBAR_DEFAULT_WIDTH;
    const parsedValue = Number.parseInt(rawValue, 10);
    return Number.isFinite(parsedValue) ? clampSidebarWidth(parsedValue) : SIDEBAR_DEFAULT_WIDTH;
  } catch {
    return SIDEBAR_DEFAULT_WIDTH;
  }
}

function saveSidebarWidth(width: number): void {
  try {
    localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clampSidebarWidth(width)));
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
}

function readSocDashboardTitle(): string {
  try {
    return localStorage.getItem(SOC_DASHBOARD_TITLE_KEY)?.trim() || '';
  } catch {
    return '';
  }
}

function saveSocDashboardTitle(title: string): void {
  const normalizedTitle = title.trim();
  try {
    if (normalizedTitle) {
      localStorage.setItem(SOC_DASHBOARD_TITLE_KEY, normalizedTitle);
    } else {
      localStorage.removeItem(SOC_DASHBOARD_TITLE_KEY);
    }
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
  window.dispatchEvent(new CustomEvent(SOC_DASHBOARD_TITLE_CHANGED_EVENT, {
    detail: { title: normalizedTitle || null },
  }));
}

const OnboardingModal = lazyLayoutComponent(() => import('@/components/common/OnboardingModal'));
const UpdateModal = lazyLayoutComponent(() => import('@/components/common/UpdateModal'), ['update']);
const NotificationModal = lazyLayoutComponent(() => import('@/components/common/NotificationModal'), ['notification']);
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
import { useProductName } from '@/contexts/ProductNameContext';
import { getLocalizedReleaseNotes } from '@/utils/releaseNotes';
import { UPDATE_DISMISSED_KEY, buildUpdateDismissalKey, isUpdateDismissed } from '@/utils/updateDismissal';
import { useWebUIContractPages } from '@/hooks/useWebUIContractPages';
import { preloadI18nNamespaces } from '@/i18nResources';
import { resolveWebUIContractPageIcon } from '@/utils/webuiContractPageIcons';
import {
  buildWebUIContractWorkspacePageList,
  getLocalizedWebUIContractTitle,
} from '@/utils/webuiContractWorkspaceSections';
import { useWorkspacePageOrders } from '@/hooks/useWorkspacePageOrders';
import { moveItem, saveNavItemOrder, saveWorkspacePageOrder } from '@/utils/workspaceNavOrder';
import { useLayoutOpenTabs } from '@/hooks/useLayoutOpenTabs';
import { findActiveTabHref, resolveOpenTabs } from '@/utils/layoutTabs';
import {
  AGENT_PARTITION_DEFAULT_PATH,
  SETTINGS_PARTITION_DEFAULT_PATH,
  readPartitionPaths,
  resolveNavPartition,
  savePartitionPaths,
  type NavPartitionId,
  type NavPartitionPaths,
} from '@/utils/navPartitions';
import { useSettingsSectionGroups } from '@/utils/settingsSections';
import PartitionTopBar, { type PartitionTopBarItem } from './PartitionTopBar';
import KeepAlivePanes from './KeepAlivePanes';
import { contentRoutes as appContentRoutes } from '@/routes/contentRoutes';
import { sessionApi } from '@/api/session';
import { useToast } from '@/components/common/Toast';
import LazyLoadErrorBoundary from '@/components/common/LazyLoadErrorBoundary';
import type { WebUIContractWorkspaceListItem } from '@/api/webuiContractPages';
import { recoverLazyLoad } from '@/utils/chunkLoadRecovery';

const UPDATE_CHECK_INTERVAL_MS = 3_600_000;
const UPDATE_CHECK_MIN_GAP_MS = 600_000;
const UPDATE_CHECK_INITIAL_DELAY_MS = 250;
const FLOCKS_LLM_USAGE_URL = 'https://portal.agentflocks.com';

interface LayoutNavItem {
  name: string;
  href: string;
  icon: LucideIcon;
  /** Opens in a new tab instead of routing (Flocks LLM usage portal). */
  external?: boolean;
  /** Workspace page id; present for reorderable workspace pages. */
  pageId?: string;
}

interface LayoutNavSection {
  id?: string;
  name: string;
  /** Top-bar partition this section belongs to. */
  partition: NavPartitionId;
  items: LayoutNavItem[];
  collapsible?: boolean;
  /** Sections in the same accordion group expand one at a time. */
  accordionGroup?: 'primary';
  /** Set for sections that mirror a WebUI contract workspace (e.g. SOC). */
  workspace?: WebUIContractWorkspaceListItem;
  /** Workspace the section's own actions belong to (自定义页面 / 自定义标题).
   *  Kept separate from `workspace`: that one decides where a drag-reorder is
   *  persisted, and it is unset once several scenes share the flat menu. */
  actionsWorkspace?: WebUIContractWorkspaceListItem;
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

function isSocWorkspace(workspace: WebUIContractWorkspaceListItem | null): boolean {
  if (!workspace) return false;
  const id = workspace.id.toLowerCase();
  const title = `${workspace.title} ${workspace.titleEn ?? ''}`.toLowerCase();
  return id === 'soc_ui'
    || id === 'soc_dashboard'
    || id.startsWith('soc_')
    || id.startsWith('soc-')
    || title.includes('soc workspace')
    || workspace.title.includes('SOC 工作区');
}

interface LayoutProps {
  /** Pages rendered inside the layout; defaults to the app routes (tests inject probes). */
  contentRoutes?: RouteObject[];
}

export default function Layout({ contentRoutes = appContentRoutes }: LayoutProps = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(readSidebarWidth);
  const [resizingSidebar, setResizingSidebar] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const isHome = location.pathname === '/';
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const { t, i18n } = useTranslation('nav');
  const { t: tWebUIContractPage } = useTranslation('webuiContractPage');
  const { t: tAuth } = useTranslation('auth');
  const toast = useToast();
  const { productName, proProductName, configuredDisplayName } = useProductName();
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
  const notificationGateReady = flocksproStatusReady
    && (!canManageUpdates || hasCompletedUpdateCheck);
  const canCreateWorkspaceCustomPage = user?.role === 'admin';
  const {
    pages: webuiContractPages,
    workspaces: webuiContractWorkspaces = [],
    loading: webuiContractNavLoading,
  } = useWebUIContractPages();
  const workspacePageOrders = useWorkspacePageOrders();
  const [openTabRecords, setOpenTabRecords] = useLayoutOpenTabs();
  // Tab whose close is in flight: React Router commits the navigation as a
  // transition, so until the route changes it must not be re-added.
  const openTabRecordsRef = useRef(openTabRecords);
  useEffect(() => {
    openTabRecordsRef.current = openTabRecords;
  }, [openTabRecords]);
  const [collapsedNavSectionIds, setCollapsedNavSectionIds] = useState<Set<string>>(readCollapsedNavSectionIds);
  const [expandedPrimaryNavSectionId, setExpandedPrimaryNavSectionId] = useState<string | null | undefined>(
    readExpandedPrimaryNavSectionId,
  );
  const [draggingNavItem, setDraggingNavItem] = useState<{ sectionId: string; key: string } | null>(null);
  const [dragOverNavItemKey, setDragOverNavItemKey] = useState<string | null>(null);
  const [creatingWorkspaceCustomPageSession, setCreatingWorkspaceCustomPageSession] = useState(false);
  const [socTitleDialogOpen, setSocTitleDialogOpen] = useState(false);
  const [socTitleDraft, setSocTitleDraft] = useState(readSocDashboardTitle);
  const hasCustomDisplayName = Boolean(configuredDisplayName?.trim());
  const expandedSidebarWidth = sidebarWidth;
  const sidebarOffsetStyle = {
    '--layout-sidebar-width': `${expandedSidebarWidth}px`,
  } as CSSProperties;

  useEffect(() => {
    if (!accountMenuOpen) return undefined;

    const handlePointerDown = (event: PointerEvent) => {
      if (accountMenuRef.current?.contains(event.target as Node)) return;
      setAccountMenuOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [accountMenuOpen]);

  useEffect(() => {
    if (!hasCustomDisplayName) return;
    const estimatedWidth = estimateSidebarWidthForName(configuredDisplayName);
    setSidebarWidth((currentWidth) => {
      const nextWidth = Math.max(currentWidth, estimatedWidth);
      if (nextWidth !== currentWidth) {
        saveSidebarWidth(nextWidth);
      }
      return nextWidth;
    });
  }, [configuredDisplayName, hasCustomDisplayName]);

  const updateSidebarWidth = useCallback((width: number) => {
    const nextWidth = clampSidebarWidth(width);
    setSidebarWidth(nextWidth);
    saveSidebarWidth(nextWidth);
  }, []);

  const handleSidebarResizePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (collapsed) return;
    event.preventDefault();
    setResizingSidebar(true);

    const target = event.currentTarget;
    const pointerId = event.pointerId;
    try {
      target.setPointerCapture?.(pointerId);
    } catch {
      // Pointer capture is best-effort; dragging still works through window listeners.
    }

    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const handlePointerMove = (moveEvent: PointerEvent) => {
      updateSidebarWidth(moveEvent.clientX);
    };

    const stopResize = () => {
      try {
        target.releasePointerCapture?.(pointerId);
      } catch {
        // The pointer may already be released by the browser.
      }
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      setResizingSidebar(false);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', stopResize);
      window.removeEventListener('pointercancel', stopResize);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', stopResize);
    window.addEventListener('pointercancel', stopResize);
  }, [collapsed, updateSidebarWidth]);

  const handleSidebarResizeKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (collapsed) return;
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    updateSidebarWidth(sidebarWidth + (event.key === 'ArrowRight' ? 16 : -16));
  }, [collapsed, sidebarWidth, updateSidebarWidth]);

  useEffect(() => {
    if (!isHome) return undefined;

    let cancelled = false;
    onboardingAPI.getStatus()
      .then((res) => {
        if (!cancelled && !res.data.completed) {
          setShowOnboarding(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setShowOnboarding(true);
        }
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
    if (!flocksproStatusReady || !canManageUpdates) return;

    const now = Date.now();
    if (checkingUpdateRef.current) return;
    if (!bypassMinGap && now - lastUpdateCheckAtRef.current < UPDATE_CHECK_MIN_GAP_MS) return;

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
    if (!flocksproStatusReady || !canManageUpdates) return undefined;

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
  }, [canManageUpdates, flocksproStatusReady, refreshUpdateStatus]);

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
            ? formatProVersion(
                packageStatus?.bundle_version ||
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
    if (!user?.id) {
      setUpdateNotification(null);
      setUpdateNotificationReady(false);
      return;
    }
    if (!notificationGateReady) return;

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
  }, [i18n.language, notificationGateReady, updateInfo, user?.id]);

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
  const { groups: settingsGroups } = useSettingsSectionGroups();

  const navigation = useMemo<LayoutNavSection[]>(
    () => {
      const enabledWorkspaces = webuiContractWorkspaces.filter((workspace) => workspace.enabled);
      const workspacePageItems = (workspace: WebUIContractWorkspaceListItem): LayoutNavItem[] => (
        workspacePageOrders
          .apply(workspace.id, buildWebUIContractWorkspacePageList(workspace, i18n.language))
          .map((page) => ({
            pageId: page.id,
            name: page.title,
            href: `${workspace.route}/${page.id}`,
            icon: resolveWebUIContractPageIcon(page.icon),
          }))
      );
      const workbenchWorkspaceItems = enabledWorkspaces
        .filter((workspace) => workspace.placement === 'aiWorkbench')
        .flatMap(workspacePageItems);
      // Built-in groups can be reordered by dragging too; the order is stored per group.
      const navItemKey = (item: LayoutNavItem) => item.pageId ?? item.href;
      const orderedGroup = (sectionId: string, items: LayoutNavItem[]) => workspacePageOrders.applyNav(sectionId, items, navItemKey);
      // The SOC workspace partition has one flat menu: every enabled scene
      // workspace contributes its pages at the same level, no group headings.
      const sceneWorkspaces = enabledWorkspaces.filter((workspace) => workspace.placement === 'sceneWorkspace');
      const scenePageItems = sceneWorkspaces.flatMap(workspacePageItems);
      const soleSceneWorkspace = sceneWorkspaces.length === 1 ? sceneWorkspaces[0] : undefined;
      // The SOC actions stay put however many scenes share the menu.
      const socWorkspace = sceneWorkspaces.find((workspace) => isSocWorkspace(workspace));
      const sceneWorkspaceSections: LayoutNavSection[] = scenePageItems.length > 0
        ? [{
          id: SCENE_NAV_SECTION_ID,
          name: '',
          partition: 'scene' as const,
          // Nameless, so no heading renders; still "collapsible" so the entries
          // stay drag-reorderable.
          collapsible: true,
          workspace: soleSceneWorkspace,
          actionsWorkspace: socWorkspace,
          items: soleSceneWorkspace
            ? scenePageItems
            : orderedGroup(SCENE_NAV_SECTION_ID, scenePageItems),
        }]
        : [];
      // Custom pages that do not belong to a workspace sit next to the scene
      // workspaces rather than under the home entry.
      const customPageItems: LayoutNavItem[] = webuiContractPages
        .filter((page) => !page.workspaceId && page.enabled && page.placement === 'home.after' && page.buildStatus === 'ready')
        .map((page) => ({
          name: getLocalizedWebUIContractTitle(page, i18n.language),
          href: page.route,
          icon: resolveWebUIContractPageIcon(page.icon),
        }));
      // The system-settings partition reuses the settings page sections.
      const settingsSections: LayoutNavSection[] = settingsGroups.map((group) => ({
        id: `settings:${group.id}`,
        name: group.name,
        partition: 'settings' as const,
        items: [
          ...group.items.map((item) => ({
            name: item.name,
            href: `/settings/${item.id}`,
            icon: item.icon,
          })),
          // The usage portal lives outside the app but belongs to this menu.
          ...(group.id === 'system'
            ? [{ name: t('flocksLlmUsageQuota'), href: FLOCKS_LLM_USAGE_URL, icon: Gauge, external: true }]
            : []),
        ],
      }));

      return [
        {
          name: '',
          partition: 'agent',
          items: [{ name: t('flocksHome'), href: '/', icon: Home }],
        },
        {
          id: AI_WORKBENCH_NAV_SECTION_ID,
          name: t('aiWorkbench'),
          partition: 'agent',
          collapsible: true,
          accordionGroup: 'primary',
          items: orderedGroup(AI_WORKBENCH_NAV_SECTION_ID, [
            { name: t('sessions'), href: '/sessions', icon: MessageSquare },
            { name: t('workspace'), href: '/workspace', icon: FolderOpen },
            { name: t('tasks'), href: '/tasks', icon: ListTodo },
            { name: t('workflows'), href: '/workflows', icon: Workflow },
            ...workbenchWorkspaceItems,
          ]),
        },
        {
          id: 'agentHub',
          name: t('agentHub'),
          partition: 'agent',
          collapsible: true,
          items: orderedGroup('agentHub', [
            { name: t('agents'), href: '/agents', icon: Bot },
            { name: t('skills'), href: '/skills', icon: BookOpen },
            { name: t('tools'), href: '/tools', icon: Wrench },
            { name: t('deviceIntegration'), href: '/devices', icon: ServerCog },
            { name: t('hub', { productName }), href: '/hub', icon: Archive },
            { name: t('models'), href: '/models', icon: Brain },
            { name: t('channels'), href: '/channels', icon: Radio },
          ]),
        },
        ...sceneWorkspaceSections,
        ...(customPageItems.length > 0
          ? [{ id: 'customPages', name: t('customPages'), partition: 'scene' as const, collapsible: true, items: customPageItems }]
          : []),
        ...settingsSections,
      ];
    },
    [i18n.language, productName, settingsGroups, webuiContractPages, webuiContractWorkspaces, workspacePageOrders, t],
  );

  // Which of the three top-bar partitions is showing. It follows the route, but
  // a partition with no page yet (SOC not installed) can still be selected to
  // show its own — empty — menu.
  const routePartition = resolveNavPartition(location.pathname);
  const [selectedPartition, setSelectedPartition] = useState<NavPartitionId>(routePartition);
  useEffect(() => {
    setSelectedPartition(routePartition);
  }, [routePartition]);

  const visibleNavigation = useMemo(
    () => navigation.filter((section) => section.partition === selectedPartition),
    [navigation, selectedPartition],
  );

  const partitionItems = useMemo<PartitionTopBarItem[]>(() => [
    { id: 'agent', name: t('partitionAgent'), icon: Sparkles },
    { id: 'scene', name: t('partitionScene'), icon: LayoutGrid },
    { id: 'settings', name: t('partitionSettings'), icon: Settings },
  ], [t]);

  // Remember where each partition was left so the top bar returns to it.
  const partitionPathsRef = useRef<NavPartitionPaths>(readPartitionPaths());
  useEffect(() => {
    const next = { ...partitionPathsRef.current, [routePartition]: `${location.pathname}${location.search}` };
    partitionPathsRef.current = next;
    savePartitionPaths(next);
  }, [location.pathname, location.search, routePartition]);

  const partitionTargetPath = useCallback((id: NavPartitionId): string | null => {
    const hrefs = navigation
      .filter((section) => section.partition === id)
      .flatMap((section) => section.items)
      .map((item) => item.href);
    const stored = partitionPathsRef.current[id];
    // Only reuse the stored page while it still exists in that partition.
    if (stored && findActiveTabHref(hrefs, stored.split('?')[0])) return stored;
    if (id === 'agent') return AGENT_PARTITION_DEFAULT_PATH;
    if (id === 'settings') return SETTINGS_PARTITION_DEFAULT_PATH;
    return hrefs[0] ?? null;
  }, [navigation]);

  const selectPartition = useCallback((id: NavPartitionId) => {
    setSelectedPartition(id);
    setSidebarOpen(false);
    if (id === routePartition) return;
    const target = partitionTargetPath(id);
    if (target) navigate(target);
  }, [navigate, partitionTargetPath, routePartition]);

  const displayVersion = isFlocksproActive
    ? updateInfo?.edition === 'flockspro'
      ? formatProVersion(currentProductVersion(updateInfo, true))
      : flocksproVersion || (currentVersion ? formatProVersion(currentVersion) : null)
    : formatUpdateVersion(currentVersion);
  const accountInitial = (user?.username || productName || 'F').trim().charAt(0).toUpperCase();
  const accountRoleLabel = user?.role === 'admin' ? tAuth('admin.roleAdmin') : tAuth('admin.roleMember');
  const hasVisibleUpdate = hasUpdate && canManageUpdates;
  const hasVisibleProductUpdate = hasVisibleUpdate && !hasCustomDisplayName;
  const showFlocksproUpgradeEntry = canManageUpdates;
  const productUpdateTitle = hasVisibleProductUpdate
    ? t('hasNewVersion', { version: formatUpdateVersion(latestVersion) || '' })
    : productName;
  const settingsReturnState = {
    from: {
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
    },
  };
  const isNavItemActive = useCallback((href: string) => (
    location.pathname === href || (href !== '/' && location.pathname.startsWith(`${href}/`))
  ), [location.pathname]);

  // Browser-style tabs above the content: every sidebar entry visited becomes
  // a tab (home, AI workbench pages, agent studio pages, SOC pages alike).
  const navItemsFlat = useMemo(() => navigation.flatMap((section) => section.items), [navigation]);
  const activeTabHref = useMemo(
    () => findActiveTabHref(navItemsFlat.map((item) => item.href), location.pathname),
    [location.pathname, navItemsFlat],
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

  // Every menu entry that has been visited keeps its own pane, in any
  // partition, so coming back to it restores the page as it was left.
  const openTabs = useMemo(() => {
    const tabs = resolveOpenTabs(navItemsFlat, openTabRecords)
      .map((tab) => ({ href: tab.href, path: tab.path, name: tab.name, icon: tab.icon }));
    if (activeTabHref && !tabs.some((tab) => tab.href === activeTabHref)) {
      const item = navItemsFlat.find((entry) => entry.href === activeTabHref);
      if (item) tabs.push({ href: item.href, path: currentLocationPath, name: item.name, icon: item.icon });
    }
    return tabs;
  }, [activeTabHref, currentLocationPath, navItemsFlat, openTabRecords]);

  // One pane per visited entry stays mounted; a route that is not a menu entry
  // gets a transient pane that goes away when it is left.
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
    (href: string) => openTabPathByHref.get(href) ?? href,
    [openTabPathByHref],
  );

  const primaryNavSectionIds = useMemo(
    () => visibleNavigation
      .filter((section) => section.accordionGroup === 'primary' && section.id)
      .map((section) => section.id as string),
    [visibleNavigation],
  );

  // The accordion section that owns the current route (null for home, settings, agent studio...).
  const routePrimaryNavSectionId = useMemo(() => {
    const owner = visibleNavigation.find((section) => (
      section.accordionGroup === 'primary'
      && section.id
      && section.items.some((item) => isNavItemActive(item.href))
    ));
    return owner?.id ?? null;
  }, [isNavItemActive, visibleNavigation]);

  const effectiveExpandedPrimaryNavSectionId = useMemo(() => {
    if (expandedPrimaryNavSectionId === null) return null;
    if (expandedPrimaryNavSectionId !== undefined) {
      if (primaryNavSectionIds.includes(expandedPrimaryNavSectionId)) return expandedPrimaryNavSectionId;
      // A stored workspace section that has not loaded yet: keep everything
      // collapsed instead of flashing the AI workbench open.
      if (webuiContractNavLoading && expandedPrimaryNavSectionId.startsWith(WORKSPACE_NAV_SECTION_PREFIX)) return null;
    }
    // Fall back to the first accordion section of the partition on screen, so
    // switching partitions never lands on an all-collapsed menu.
    return routePrimaryNavSectionId ?? primaryNavSectionIds[0] ?? null;
  }, [expandedPrimaryNavSectionId, primaryNavSectionIds, routePrimaryNavSectionId, webuiContractNavLoading]);

  // Mirrors the explicit (stored) choice so the route effect below can compare
  // against it without re-running on every render.
  const explicitExpandedPrimaryNavSectionRef = useRef(expandedPrimaryNavSectionId);
  useEffect(() => {
    explicitExpandedPrimaryNavSectionRef.current = expandedPrimaryNavSectionId;
  }, [expandedPrimaryNavSectionId]);

  // Entering a route owned by one accordion section opens that section and
  // closes the other: people work in either the AI workbench or a scene
  // workspace such as SOC, rarely both at once. The choice is persisted so
  // leaving for the home page keeps the last group open.
  useEffect(() => {
    if (!routePrimaryNavSectionId) return;
    if (explicitExpandedPrimaryNavSectionRef.current === routePrimaryNavSectionId) return;
    explicitExpandedPrimaryNavSectionRef.current = routePrimaryNavSectionId;
    setExpandedPrimaryNavSectionId(routePrimaryNavSectionId);
    saveExpandedPrimaryNavSectionId(routePrimaryNavSectionId);
  }, [routePrimaryNavSectionId]);

  const togglePrimaryNavSection = useCallback((sectionId: string) => {
    const next = effectiveExpandedPrimaryNavSectionId === sectionId ? null : sectionId;
    explicitExpandedPrimaryNavSectionRef.current = next;
    setExpandedPrimaryNavSectionId(next);
    saveExpandedPrimaryNavSectionId(next);
  }, [effectiveExpandedPrimaryNavSectionId]);

  const toggleNavSection = useCallback((sectionId: string) => {
    setCollapsedNavSectionIds((current) => {
      const next = new Set(current);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      saveCollapsedNavSectionIds(next);
      return next;
    });
  }, []);

  // Second-level menu order is customisable in every group: workspace pages are
  // keyed by page id, built-in entries by href, each stored per group.
  const sectionItemKeys = (section: LayoutNavSection) => section.items.map((item) => item.pageId ?? item.href);

  const reorderSectionItems = useCallback((section: LayoutNavSection, fromKey: string, toKey: string) => {
    if (fromKey === toKey) return;
    const keys = sectionItemKeys(section);
    const fromIndex = keys.indexOf(fromKey);
    const toIndex = keys.indexOf(toKey);
    if (fromIndex < 0 || toIndex < 0) return;
    const nextKeys = moveItem(keys, fromIndex, toIndex);
    if (section.workspace) {
      saveWorkspacePageOrder(section.workspace.id, nextKeys);
    } else if (section.id) {
      saveNavItemOrder(section.id, nextKeys);
    }
  }, []);

  const handleNavItemDragStart = useCallback((event: ReactDragEvent<HTMLDivElement>, sectionId: string, key: string) => {
    setDraggingNavItem({ sectionId, key });
    setDragOverNavItemKey(null);
    try {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', key);
    } catch {
      // Some environments restrict dataTransfer; dragging still works through state.
    }
  }, []);

  const handleNavItemDragOver = useCallback((event: ReactDragEvent<HTMLDivElement>, sectionId: string, key: string) => {
    if (!draggingNavItem || draggingNavItem.sectionId !== sectionId) return;
    event.preventDefault();
    try {
      event.dataTransfer.dropEffect = 'move';
    } catch {
      // Ignore dataTransfer restrictions.
    }
    if (dragOverNavItemKey !== key) {
      setDragOverNavItemKey(key);
    }
  }, [dragOverNavItemKey, draggingNavItem]);

  const handleNavItemDrop = useCallback((event: ReactDragEvent<HTMLDivElement>, section: LayoutNavSection, key: string) => {
    if (!draggingNavItem || draggingNavItem.sectionId !== (section.id ?? '')) return;
    event.preventDefault();
    reorderSectionItems(section, draggingNavItem.key, key);
    setDraggingNavItem(null);
    setDragOverNavItemKey(null);
  }, [draggingNavItem, reorderSectionItems]);

  const handleNavItemDragEnd = useCallback(() => {
    setDraggingNavItem(null);
    setDragOverNavItemKey(null);
  }, []);

  // Keyboard alternative to dragging: Alt+ArrowUp / Alt+ArrowDown moves the focused entry.
  const handleNavItemReorderKeyDown = useCallback((event: ReactKeyboardEvent<HTMLAnchorElement>, section: LayoutNavSection, key: string) => {
    if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
    const keys = sectionItemKeys(section);
    const index = keys.indexOf(key);
    const targetIndex = index + (event.key === 'ArrowUp' ? -1 : 1);
    if (index < 0 || targetIndex < 0 || targetIndex >= keys.length) return;
    event.preventDefault();
    reorderSectionItems(section, key, keys[targetIndex]);
  }, [reorderSectionItems]);

  const handleCreateWorkspaceCustomPage = useCallback(async (workspace: WebUIContractWorkspaceListItem) => {
    if (creatingWorkspaceCustomPageSession) return;
    const workspaceTitle = getLocalizedWebUIContractTitle(workspace, i18n.language);
    setCreatingWorkspaceCustomPageSession(true);
    try {
      const session = await sessionApi.create({
        title: tWebUIContractPage('workspace.customPageSessionTitle', {
          workspace: workspaceTitle,
        }),
      });
      const message = tWebUIContractPage('workspace.socCustomPageInitialMessage', {
        workspaceId: workspace.id,
        workspaceTitle,
        workspaceRoute: workspace.route,
      });
      const displayLabel = tWebUIContractPage('workspace.socCustomPageDisplayLabel');
      setSidebarOpen(false);
      navigate(
        `/sessions?session=${session.id}&message=${encodeURIComponent(message)}&display=${encodeURIComponent(displayLabel)}`,
      );
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : tWebUIContractPage('workspace.customPageCreateError');
      toast.error(tWebUIContractPage('workspace.customPageCreateError'), detail);
    } finally {
      setCreatingWorkspaceCustomPageSession(false);
    }
  }, [
    creatingWorkspaceCustomPageSession,
    i18n.language,
    navigate,
    tWebUIContractPage,
    toast,
  ]);

  const openSocTitleDialog = useCallback(() => {
    setSocTitleDraft(readSocDashboardTitle());
    setSocTitleDialogOpen(true);
  }, []);

  const closeSocTitleDialog = useCallback(() => {
    setSocTitleDialogOpen(false);
  }, []);

  const handleSaveSocTitle = useCallback(() => {
    saveSocDashboardTitle(socTitleDraft);
    setSocTitleDialogOpen(false);
    if (location.pathname === '/contracts/webui/workspaces/soc_ui/soc-dashboard') {
      window.setTimeout(() => window.location.reload(), 0);
    }
  }, [location.pathname, socTitleDraft]);

  const handleResetSocTitle = useCallback(() => {
    setSocTitleDraft('');
    saveSocDashboardTitle('');
    setSocTitleDialogOpen(false);
    if (location.pathname === '/contracts/webui/workspaces/soc_ui/soc-dashboard') {
      window.setTimeout(() => window.location.reload(), 0);
    }
  }, [location.pathname]);

  const openManualUpdateCheck = useCallback(() => {
    setAccountMenuOpen(false);
    setUpdateInfo(null);
    setShowUpdate(true);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 dark:bg-zinc-950 dark:text-zinc-100">
      {/* Modals render lazily — fallback={null} keeps the chunk download
          invisible to the user (they're already triggering an async UI). */}
      <LazyLoadErrorBoundary mode="overlay">
        <Suspense fallback={null}>
          {showOnboarding && (
            <OnboardingModal
              onClose={() => setShowOnboarding(false)}
            />
          )}
          {showUpdate && (
            <UpdateModal
              initialInfo={updateInfo}
              forceInitialCheck={updateInfo === null}
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
      </LazyLoadErrorBoundary>

      {socTitleDialogOpen && (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 px-4"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeSocTitleDialog();
            }
          }}
        >
          <form
            className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-4 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
            onSubmit={(event) => {
              event.preventDefault();
              handleSaveSocTitle();
            }}
          >
            <h2 className="text-base font-semibold text-zinc-950 dark:text-zinc-50">
              {tWebUIContractPage('workspace.customTitleDialogTitle')}
            </h2>
            <label className="mt-4 block text-sm font-medium text-zinc-700 dark:text-zinc-200">
              {tWebUIContractPage('workspace.customTitle')}
              <input
                autoFocus
                value={socTitleDraft}
                onChange={(event) => setSocTitleDraft(event.target.value)}
                maxLength={SOC_DASHBOARD_TITLE_MAX_LENGTH}
                placeholder={tWebUIContractPage('workspace.customTitlePlaceholder')}
                className="mt-2 h-10 w-full rounded-md border border-zinc-200 bg-white px-3 text-sm text-zinc-900 outline-none transition-colors placeholder:text-zinc-400 focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500"
              />
            </label>
            <div className="mt-4 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={handleResetSocTitle}
                className="inline-flex h-9 items-center rounded-md px-3 text-sm font-semibold text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
              >
                {tWebUIContractPage('workspace.customTitleReset')}
              </button>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={closeSocTitleDialog}
                  className="inline-flex h-9 items-center rounded-md border border-zinc-200 px-3 text-sm font-semibold text-zinc-600 transition-colors hover:bg-zinc-50 hover:text-zinc-950 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
                >
                  {tWebUIContractPage('workspace.customTitleCancel')}
                </button>
                <button
                  type="submit"
                  className="inline-flex h-9 items-center rounded-md bg-zinc-950 px-3 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200"
                >
                  {tWebUIContractPage('workspace.customTitleSave')}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}

      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-600 bg-opacity-75 z-40 lg:hidden dark:bg-black dark:bg-opacity-75"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`
          fixed inset-y-0 left-0 z-50 max-w-[calc(100vw-2rem)] bg-zinc-100 border-r border-zinc-200 dark:bg-zinc-950 dark:border-zinc-800
          ${resizingSidebar ? '' : 'transition-all duration-300 ease-in-out'}
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          ${collapsed ? 'w-16' : ''}
        `}
        style={collapsed ? undefined : { width: expandedSidebarWidth }}
      >
        <div className="flex flex-col h-full overflow-visible">
          {/* Logo */}
          <div className={`flex min-h-16 items-center flex-shrink-0 ${collapsed ? 'h-16 justify-center px-2' : 'px-4 py-3'}`}>
            {collapsed ? (
              <div
                className="relative flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
                title={productName}
              >
                <Sparkles className="w-4 h-4 text-zinc-500 dark:text-zinc-300" />
                {hasVisibleProductUpdate && (
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
                  <span
                    className={`min-w-0 text-xl font-bold leading-tight text-zinc-900 dark:text-zinc-50 ${hasCustomDisplayName ? 'whitespace-normal [overflow-wrap:anywhere]' : 'truncate'}`}
                    title={productName}
                  >
                    {productName}
                  </span>
                  {hasVisibleProductUpdate && (
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
            {visibleNavigation.map((section, sectionIndex) => {
              const sectionId = (section.id ?? section.name) || `section-${sectionIndex}`;
              const sectionContentId = `layout-nav-section-${sectionId}`;
              const isPrimarySection = section.accordionGroup === 'primary';
              // In icon-only mode there are no labels to declutter, so every
              // section stays reachable regardless of the accordion state.
              const sectionCollapsed = !collapsed && Boolean(section.collapsible) && (
                isPrimarySection
                  ? effectiveExpandedPrimaryNavSectionId !== sectionId
                  : collapsedNavSectionIds.has(sectionId)
              );
              const sectionWorkspace = section.workspace ?? null;
              const reorderable = Boolean(section.collapsible) && section.items.length > 1;
              const actionsWorkspace = section.actionsWorkspace ?? null;
              const showSocWorkspaceActions = !collapsed && isSocWorkspace(actionsWorkspace);
              return (
                <div key={sectionId} className="mb-6">
                  {!collapsed && section.name && (
                    <h3 className="px-3 mb-2 text-xs font-semibold text-zinc-400 uppercase tracking-wider whitespace-nowrap dark:text-zinc-500">
                      {section.collapsible ? (
                        <button
                          type="button"
                          onClick={() => (isPrimarySection ? togglePrimaryNavSection(sectionId) : toggleNavSection(sectionId))}
                          className="flex h-6 w-full items-center justify-between text-left transition-colors hover:text-zinc-600 focus:outline-none focus-visible:text-zinc-600 dark:hover:text-zinc-300 dark:focus-visible:text-zinc-300"
                          aria-expanded={!sectionCollapsed}
                          aria-controls={sectionContentId}
                        >
                          <span className="min-w-0 truncate">{section.name}</span>
                          <ChevronRight className={`ml-2 h-3.5 w-3.5 shrink-0 transition-transform ${sectionCollapsed ? '' : 'rotate-90'}`} />
                        </button>
                      ) : (
                        section.name
                      )}
                    </h3>
                  )}
                  {collapsed && <div className="mb-1 border-t border-zinc-200 first:border-none dark:border-zinc-800" />}
                  {!sectionCollapsed && (
                    <div id={sectionContentId} className="space-y-0.5">
                      {section.items.map((item) => {
                        const isActive = isNavItemActive(item.href);
                        const itemKey = reorderable ? (item.pageId ?? item.href) : undefined;
                        const isDragging = Boolean(itemKey && draggingNavItem?.sectionId === sectionId && draggingNavItem.key === itemKey);
                        const isDropTarget = Boolean(itemKey && !isDragging && draggingNavItem?.sectionId === sectionId && dragOverNavItemKey === itemKey);
                        const link = item.external ? (
                          <a
                            href={item.href}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={() => setSidebarOpen(false)}
                            title={collapsed ? item.name : undefined}
                            className={`
                              flex items-center rounded-lg transition-all duration-150
                              ${collapsed ? 'justify-center p-2.5' : 'px-3 py-2 text-sm font-medium'}
                              text-zinc-600 hover:bg-white/60 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50
                            `}
                          >
                            <item.icon className={`flex-shrink-0 w-5 h-5 ${collapsed ? '' : 'mr-3'} text-zinc-400 dark:text-zinc-500`} />
                            {!collapsed && <span className="min-w-0 flex-1 truncate">{item.name}</span>}
                          </a>
                        ) : (
                          <Link
                            to={navItemPath(item.href)}
                            draggable={itemKey ? false : undefined}
                            onClick={() => setSidebarOpen(false)}
                            onKeyDown={itemKey ? (event) => handleNavItemReorderKeyDown(event, section, itemKey) : undefined}
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
                                {itemKey && (
                                  <GripVertical
                                    aria-hidden="true"
                                    className="ml-2 h-4 w-4 flex-shrink-0 text-zinc-300 opacity-0 transition-opacity group-hover:opacity-100 dark:text-zinc-600"
                                  />
                                )}
                              </>
                            )}
                          </Link>
                        );
                        if (!itemKey) {
                          return <div key={item.href}>{link}</div>;
                        }
                        return (
                          <div
                            key={item.href}
                            draggable
                            data-nav-item-key={itemKey}
                            data-nav-page-id={item.pageId}
                            onDragStart={(event) => handleNavItemDragStart(event, sectionId, itemKey)}
                            onDragOver={(event) => handleNavItemDragOver(event, sectionId, itemKey)}
                            onDragLeave={() => {
                              if (dragOverNavItemKey === itemKey) setDragOverNavItemKey(null);
                            }}
                            onDrop={(event) => handleNavItemDrop(event, section, itemKey)}
                            onDragEnd={handleNavItemDragEnd}
                            className={`group rounded-lg ${collapsed ? '' : 'cursor-grab active:cursor-grabbing'} ${isDragging ? 'opacity-50' : ''} ${isDropTarget ? 'ring-2 ring-inset ring-zinc-400/70 dark:ring-zinc-500/70' : ''}`}
                          >
                            {link}
                          </div>
                        );
                      })}
                      {showSocWorkspaceActions && actionsWorkspace && (
                        <div className="space-y-0.5 pt-1">
                          {canCreateWorkspaceCustomPage && (
                            <button
                              type="button"
                              onClick={() => void handleCreateWorkspaceCustomPage(actionsWorkspace)}
                              disabled={creatingWorkspaceCustomPageSession}
                              className="flex w-full items-center rounded-lg px-3 py-1.5 text-left text-xs font-medium text-zinc-400 transition-colors hover:bg-white/60 hover:text-zinc-700 disabled:cursor-not-allowed disabled:opacity-60 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
                            >
                              <Plus className="ml-0.5 mr-3.5 h-4 w-4 flex-shrink-0" />
                              <span className="min-w-0 flex-1 truncate">{tWebUIContractPage('workspace.customPage')}</span>
                              {creatingWorkspaceCustomPageSession ? (
                                <Loader2 className="ml-2 h-3.5 w-3.5 shrink-0 animate-spin" />
                              ) : null}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={openSocTitleDialog}
                            className="flex w-full items-center rounded-lg px-3 py-1.5 text-left text-xs font-medium text-zinc-400 transition-colors hover:bg-white/60 hover:text-zinc-700 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
                          >
                            <Pencil className="ml-0.5 mr-3.5 h-4 w-4 flex-shrink-0" />
                            <span className="min-w-0 flex-1 truncate">{tWebUIContractPage('workspace.customTitle')}</span>
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </nav>

          {/* Bottom account entry */}
          <div
            ref={accountMenuRef}
            className={`relative border-t border-zinc-200 flex-shrink-0 dark:border-zinc-800 ${collapsed ? 'p-2' : 'p-3'}`}
          >
            {accountMenuOpen && (
              <div className={`absolute z-50 overflow-hidden rounded-lg border border-zinc-200 bg-white py-1.5 shadow-lg dark:border-zinc-800 dark:bg-zinc-900 ${
                collapsed ? 'bottom-2 left-full ml-2 w-56' : 'bottom-full left-3 right-3 mb-2 min-w-56'
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
                    {proProductName}
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

        {!collapsed && (
          <div
            role="separator"
            aria-label={t('resizeNav')}
            aria-orientation="vertical"
            aria-valuemin={SIDEBAR_MIN_WIDTH}
            aria-valuemax={SIDEBAR_MAX_WIDTH}
            aria-valuenow={expandedSidebarWidth}
            tabIndex={0}
            onPointerDown={handleSidebarResizePointerDown}
            onKeyDown={handleSidebarResizeKeyDown}
            className="absolute inset-y-0 right-0 hidden w-1 cursor-col-resize touch-none outline-none transition-colors hover:bg-zinc-300 focus-visible:bg-zinc-400 lg:block dark:hover:bg-zinc-700 dark:focus-visible:bg-zinc-600"
            title={t('resizeNav')}
          />
        )}

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

      {/* Mobile top menu button */}
      <div className={`lg:hidden fixed top-0 left-0 z-30 flex items-center h-11 px-3 pointer-events-none ${sidebarOpen ? 'hidden' : ''}`}>
        <button
          onClick={() => setSidebarOpen(true)}
          className="pointer-events-auto p-2 text-gray-500 hover:text-gray-700 bg-white rounded-lg shadow-sm border border-gray-200 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-50"
        >
          <Menu className="w-5 h-5" />
        </button>
      </div>

      {/* Main content area */}
      <div
        className={`flex flex-col h-screen ${resizingSidebar ? '' : 'transition-all duration-300'} ${collapsed ? 'lg:pl-16' : 'lg:pl-[var(--layout-sidebar-width)]'}`}
        style={sidebarOffsetStyle}
      >
        <PartitionTopBar
          items={partitionItems}
          activeId={selectedPartition}
          onSelect={selectPartition}
          action={selectedPartition === 'scene'
            ? {
              href: '/scenes/suites',
              name: t('sceneSuiteManager'),
              icon: Boxes,
              active: location.pathname.startsWith('/scenes/'),
            }
            : undefined}
        />
        <main className="relative flex-1 overflow-hidden bg-gray-50 dark:bg-zinc-950">
          <KeepAlivePanes
            panes={keepAlivePanes}
            activeHref={activePaneHref}
            location={location}
            routes={contentRoutes}
          />
        </main>
      </div>
    </div>
  );
}
