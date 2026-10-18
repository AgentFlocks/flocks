import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Archive,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Folder,
  FolderInput,
  FolderPlus,
  History,
  Loader2,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Search,
  Share2,
  SquarePen,
  Trash2,
} from 'lucide-react';
import apiClient from '@/api/client';
import { sessionApi } from '@/api/session';
import { useSessions } from '@/hooks/useSessions';
import { useSSE, type SSEEvent } from '@/hooks/useSSE';
import { useAuth } from '@/contexts/AuthContext';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';
import type { Session } from '@/types';
import { sessionPath } from '@/utils/sessionUrl';
import { getSessionDisplayTitle, isRunningSessionStatus, readRunningSessionIds } from '@/utils/sessionStatus';
import { exportSessionJson } from '@/utils/sessionExport';
import ProjectFolderDialog, { pathBasename, type ProjectFolderSummary } from './ProjectFolderDialog';
import { announceProjectsChanged, onProjectsChanged } from '@/utils/projectEvents';

// Sessions outside any registered folder are grouped as "未分类".
const UNGROUPED_ID = 'tasks';
const ARCHIVE_CONCURRENCY = 4;
const HISTORY_PAGE_SIZE = 20;
const HISTORY_LOAD_MORE_SIZE = 50;
// "加载更多" reveals the rest of a list (fetching another page when needed); "收起" goes back to the
// first page, so the compact list is exactly what the panel showed when it opened.
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_RESULT_LIMIT = 30;
const REFRESH_DEBOUNCE_MS = 500;
const REFRESH_EVENTS = new Set(['session.created', 'session.updated', 'session.deleted', 'session.archived', 'session.unarchived']);

interface FolderInfo extends ProjectFolderSummary {
  sessionCount?: number;
  lastActivityAt?: number | null;
  pathStatus?: 'available' | 'missing' | 'unreadable';
  canWrite?: boolean;
  canDelete?: boolean;
  isShared?: boolean;
  isDefault?: boolean;
}

interface MenuState {
  kind: 'session' | 'folder';
  id: string;
  top: number;
  left: number;
  moving?: boolean;
}

function readIdSet(key: string): Set<string> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key) ?? '[]');
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []);
  } catch {
    return new Set();
  }
}

function writeIdSet(key: string, ids: Set<string>): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(Array.from(ids)));
  } catch {
    // Folder layout memory is a convenience only.
  }
}

/** The session a route shows: `/sessions/:id` or the legacy `?session=`. */
export function sessionIdFromLocation(pathname: string, search: string): string | null {
  const match = pathname.match(/^\/sessions\/([^/]+)/);
  if (match) return decodeURIComponent(match[1]);
  if (pathname === '/sessions') return new URLSearchParams(search).get('session');
  return null;
}

function folderLabel(folder: FolderInfo): string {
  return folder.name?.trim() || pathBasename(folder.worktree);
}

/** A title that scrolls into view on hover when it does not fit. */
function ScrollingTitle({ text }: { text: string }) {
  const viewportRef = useRef<HTMLSpanElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);
  const animationRef = useRef<Animation | null>(null);

  const start = useCallback(() => {
    const viewport = viewportRef.current;
    const inner = textRef.current;
    if (!viewport || !inner || typeof inner.animate !== 'function') return;
    const distance = inner.scrollWidth - viewport.clientWidth;
    if (distance < 2) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    animationRef.current?.cancel();
    animationRef.current = inner.animate(
      [{ transform: 'translateX(0)' }, { transform: `translateX(-${distance}px)` }],
      { duration: Math.max(1800, (distance / 30) * 1000), delay: 500, easing: 'linear', fill: 'forwards' },
    );
  }, []);

  const stop = useCallback(() => {
    animationRef.current?.cancel();
    animationRef.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  return (
    <span ref={viewportRef} className="block min-w-0 overflow-hidden text-ellipsis whitespace-nowrap" onMouseEnter={start} onMouseLeave={stop} onFocus={start} onBlur={stop}>
      <span ref={textRef} className="inline-block whitespace-nowrap">{text}</span>
    </span>
  );
}

interface SessionHistoryPanelProps {
  /** Sidebar shows icons only: the panel becomes one button. */
  collapsed: boolean;
  onExpandSidebar: () => void;
  /** Called after a navigation, so the mobile drawer can close. */
  onNavigate: () => void;
}

export default function SessionHistoryPanel({ collapsed, onExpandSidebar, onNavigate }: SessionHistoryPanelProps) {
  const { t } = useTranslation('nav');
  const { user } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const location = useLocation();
  const navigate = useNavigate();
  const selectedSessionId = sessionIdFromLocation(location.pathname, location.search);
  // The open session as of now, for code that resumes after an await: the user may have moved on.
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const collapsedKey = `flocks:history:collapsed-folders:${user?.id ?? 'anonymous'}`;
  const pinnedKey = `flocks:history:pinned-folders:${user?.id ?? 'anonymous'}`;

  const [folders, setFolders] = useState<FolderInfo[]>([]);
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => readIdSet(collapsedKey));
  const [pinnedFolders, setPinnedFolders] = useState<Set<string>>(() => readIdSet(pinnedKey));
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [folderDialog, setFolderDialog] = useState<{ mode: 'create' | 'rename'; folder?: FolderInfo } | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Session[] | null>(null);
  const [creatingIn, setCreatingIn] = useState<string | null>(null);
  const [visibleCounts, setVisibleCounts] = useState<Record<string, number>>({});
  const [selecting, setSelecting] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(() => new Set());
  const [archivingChecked, setArchivingChecked] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const refreshTimerRef = useRef<number | null>(null);

  useEffect(() => writeIdSet(collapsedKey, collapsedFolders), [collapsedFolders, collapsedKey]);
  useEffect(() => writeIdSet(pinnedKey, pinnedFolders), [pinnedFolders, pinnedKey]);

  const folderIds = useMemo(() => [UNGROUPED_ID, ...folders.map((folder) => folder.id)], [folders]);
  const {
    sessions,
    loading,
    refetch,
    updateSessionTitle,
    removeSession,
    hasMoreByProject = {},
    loadingMoreProjectIds = new Set<string>(),
    loadMore,
  } = useSessions('', {
    projectIds: folderIds,
    pageSize: HISTORY_PAGE_SIZE,
    loadMorePageSize: HISTORY_LOAD_MORE_SIZE,
  });
  const compactCount = HISTORY_PAGE_SIZE;

  // The open session can sort past the loaded pages; read it on its own so it stays listed
  // (and its folder is known) without loading every older page.
  const [olderOpenSession, setOlderOpenSession] = useState<Session | null>(null);
  // Bumped by refreshes so a renamed or moved open session is read again.
  const [olderOpenSessionVersion, setOlderOpenSessionVersion] = useState(0);
  const openSessionInPage = Boolean(selectedSessionId && sessions.some((session) => session.id === selectedSessionId));
  useEffect(() => {
    if (!selectedSessionId || openSessionInPage) {
      setOlderOpenSession(null);
      return undefined;
    }
    let cancelled = false;
    sessionApi.get(selectedSessionId)
      .then((response) => {
        const session = response as unknown as Session;
        // Archived sessions and sub-agent sessions are not rows of this list.
        if (!cancelled) setOlderOpenSession(session.status === 'archived' || session.parentID ? null : session);
      })
      .catch(() => {
        if (!cancelled) setOlderOpenSession(null);
      });
    return () => {
      cancelled = true;
    };
  }, [olderOpenSessionVersion, openSessionInPage, selectedSessionId]);
  const listedSessions = useMemo(
    () => (olderOpenSession && !sessions.some((session) => session.id === olderOpenSession.id) ? [...sessions, olderOpenSession] : sessions),
    [olderOpenSession, sessions],
  );
  // Handlers that await a confirmation read the list as it is afterwards, not as it was when they started.
  const listedSessionsRef = useRef(listedSessions);
  listedSessionsRef.current = listedSessions;

  const fetchFolders = useCallback(async () => {
    try {
      const response = await apiClient.get('/api/project');
      const list = Array.isArray(response.data) ? (response.data as FolderInfo[]) : [];
      setFolders(list.filter((folder) => folder.id !== UNGROUPED_ID && !folder.isDefault));
    } catch {
      // Keep the last known folders; the next refresh tries again.
    }
  }, []);

  useEffect(() => {
    void fetchFolders();
    // The composer's project picker can create projects too.
    return onProjectsChanged('sidebar', () => { void fetchFolders(); });
  }, [fetchFolders]);

  const refreshAll = useCallback(async () => {
    setOlderOpenSessionVersion((version) => version + 1);
    await Promise.allSettled([fetchFolders(), refetch()]);
  }, [fetchFolders, refetch]);

  // Live updates: one shared event stream; bursts become one refresh.
  const handleEvent = useCallback((event: SSEEvent) => {
    if (event.type === 'session.status') {
      const sessionId = event.properties?.sessionID;
      if (typeof sessionId !== 'string') return;
      const running = isRunningSessionStatus(event.properties?.status);
      setRunningIds((current) => {
        if (current.has(sessionId) === running) return current;
        const next = new Set(current);
        if (running) next.add(sessionId);
        else next.delete(sessionId);
        return next;
      });
      return;
    }
    if (!REFRESH_EVENTS.has(event.type)) return;
    if (event.type === 'session.updated' && event.properties?.id && event.properties?.title) {
      updateSessionTitle(event.properties.id, event.properties.title);
    }
    if (refreshTimerRef.current !== null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void refreshAll();
    }, REFRESH_DEBOUNCE_MS);
  }, [refreshAll, updateSessionTitle]);

  const loadRunning = useCallback(async () => {
    try {
      const response = await apiClient.get('/api/session/status');
      setRunningIds(readRunningSessionIds(response.data));
    } catch {
      // Running dots are a hint only.
    }
  }, []);

  useSSE({
    url: '/api/event',
    onEvent: handleEvent,
    onReconnect: () => {
      void loadRunning();
      void refreshAll();
    },
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  useEffect(() => {
    void loadRunning();
    return () => {
      if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    };
  }, [loadRunning]);

  // Search is title-only and flat; results carry their folder name.
  useEffect(() => {
    const query = searchQuery.trim();
    if (!searchOpen || !query) {
      setSearchResults(null);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void sessionApi.list({ view: 'list', manager: true, roots: true, limit: SEARCH_RESULT_LIMIT, search: query })
        .then((items) => {
          if (!cancelled) setSearchResults(items.filter((item) => !item.parentID));
        })
        .catch(() => {
          if (!cancelled) setSearchResults([]);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchOpen, searchQuery]);

  const groups = useMemo(() => {
    const byFolder = new Map<string, Session[]>();
    const known = new Set(folders.map((folder) => folder.id));
    for (const session of listedSessions) {
      const folderId = session.effectiveProjectID || session.projectID;
      const key = known.has(folderId) ? folderId : UNGROUPED_ID;
      const list = byFolder.get(key) ?? [];
      list.push(session);
      byFolder.set(key, list);
    }
    const ordered = [...folders].sort((a, b) => {
      const pinDiff = Number(pinnedFolders.has(b.id)) - Number(pinnedFolders.has(a.id));
      if (pinDiff !== 0) return pinDiff;
      const activity = (b.lastActivityAt ?? 0) - (a.lastActivityAt ?? 0);
      return activity !== 0 ? activity : folderLabel(a).localeCompare(folderLabel(b));
    });
    return {
      folders: ordered.map((folder) => ({ folder, sessions: byFolder.get(folder.id) ?? [] })),
      ungrouped: byFolder.get(UNGROUPED_ID) ?? [],
    };
  }, [folders, listedSessions, pinnedFolders]);

  const closeMenu = useCallback(() => setMenu(null), []);

  useEffect(() => {
    if (!menu) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target as Element | null)?.closest?.('[data-history-menu]')) setMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenu(null);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [menu]);

  const openMenu = (kind: MenuState['kind'], id: string, trigger: HTMLElement) => {
    const rect = trigger.getBoundingClientRect();
    setMenu((current) => (current?.kind === kind && current.id === id
      ? null
      : { kind, id, top: Math.max(8, Math.min(rect.top, window.innerHeight - 280)), left: Math.min(rect.right + 6, window.innerWidth - 232) }));
  };

  const openSession = (sessionId: string) => {
    navigate(sessionPath(sessionId));
    onNavigate();
  };

  const newChat = () => {
    navigate('/sessions', { state: { skipLastSelectedSessionRestore: true } });
    onNavigate();
  };

  /** Leave `sessionId` for a new chat, unless another session has been opened meanwhile. */
  const leaveSession = (sessionId: string | null | undefined) => {
    if (sessionId && mountedRef.current && selectedSessionIdRef.current === sessionId) newChat();
  };

  const newSessionInFolder = async (folderId: string) => {
    if (creatingIn) return;
    setCreatingIn(folderId);
    try {
      const created = await sessionApi.create({ title: 'New Session', projectID: folderId });
      setCollapsedFolders((current) => {
        const next = new Set(current);
        next.delete(folderId);
        return next;
      });
      void refreshAll();
      openSession(created.id);
    } catch (err: any) {
      toast.error(t('history.createSessionFailed'), err?.response?.data?.detail ?? err?.message);
    } finally {
      setCreatingIn(null);
    }
  };

  const submitRename = async () => {
    if (!renaming) return;
    const title = renaming.value.trim();
    const target = listedSessions.find((session) => session.id === renaming.id);
    setRenaming(null);
    if (!title || !target || title === target.title) return;
    try {
      await sessionApi.update(target.id, { title });
      updateSessionTitle(target.id, title);
      setOlderOpenSession((current) => (current?.id === target.id ? { ...current, title } : current));
      toast.success(t('history.renamed'));
    } catch (err: any) {
      toast.error(t('history.renameFailed'), err?.message);
    }
  };

  const archiveSession = async (session: Session) => {
    const ok = await confirm({
      title: t('history.archiveTitle'),
      description: t('history.archiveDescription', { title: getSessionDisplayTitle(session) }),
      confirmText: t('history.archive'),
    });
    if (!ok) return;
    try {
      await sessionApi.archive(session.id);
    } catch (err: any) {
      toast.error(t('history.archiveFailed'), err?.message);
      return;
    }
    removeSession(session.id);
    leaveSession(session.id);
    toast.success(t('history.archived'));
    void fetchFolders();
  };

  const moveSession = async (session: Session, folder: FolderInfo) => {
    closeMenu();
    try {
      await sessionApi.moveToProject(session.id, folder.id);
      toast.success(t('history.moved', { folder: folderLabel(folder) }));
      void refreshAll();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(t('history.moveFailed'), typeof detail === 'string' ? detail : err?.message);
    }
  };

  const toggleShare = async (session: Session) => {
    try {
      if (session.isShared) await sessionApi.unshareLocal(session.id);
      else await sessionApi.shareLocal(session.id);
      toast.success(t(session.isShared ? 'history.unshared' : 'history.shared'));
      void refetch();
    } catch (err: any) {
      toast.error(t('history.shareFailed'), err?.message);
    }
  };

  const exportSession = async (session: Session) => {
    try {
      await exportSessionJson(session.id, session.title);
    } catch (err: any) {
      toast.error(t('history.downloadFailed'), err?.message);
    }
  };

  const togglePin = (folderId: string) => {
    closeMenu();
    setPinnedFolders((current) => {
      const next = new Set(current);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  };

  const toggleFolder = (folderId: string) => {
    setCollapsedFolders((current) => {
      const next = new Set(current);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  };

  const copyFolderPath = async (folder: FolderInfo) => {
    closeMenu();
    try {
      await navigator.clipboard.writeText(folder.worktree);
      toast.success(t('history.pathCopied'));
    } catch (err: any) {
      toast.error(t('history.copyFailed'), err?.message);
    }
  };

  const toggleFolderShare = async (folder: FolderInfo) => {
    closeMenu();
    try {
      await apiClient.post(`/api/project/${folder.id}/${folder.isShared ? 'unshare-local' : 'share-local'}`);
      toast.success(t(folder.isShared ? 'history.unshared' : 'history.shared'));
      void refreshAll();
    } catch (err: any) {
      toast.error(t('history.shareFailed'), err?.message);
    }
  };

  const removeFolder = async (folder: FolderInfo) => {
    closeMenu();
    const ok = await confirm({
      title: t('history.removeFolderTitle'),
      description: t('history.removeFolderDescription', { folder: folderLabel(folder) }),
      confirmText: t('history.removeFolderConfirm'),
    });
    if (!ok) return;
    try {
      await apiClient.delete(`/api/project/${folder.id}`, { params: { keepSessions: true } });
      setPinnedFolders((current) => {
        const next = new Set(current);
        next.delete(folder.id);
        return next;
      });
      toast.success(t('history.folderRemoved'));
      announceProjectsChanged('sidebar');
      void refreshAll();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(t('history.removeFolderFailed'), typeof detail === 'string' ? detail : err?.message);
    }
  };

  /** Folder of `sessionId` (null when none is open or it has no folder); undefined when it could not be read. */
  const openSessionFolder = async (sessionId: string | null): Promise<string | null | undefined> => {
    if (!sessionId) return null;
    const known = listedSessionsRef.current.find((session) => session.id === sessionId);
    if (known) return known.effectiveProjectID || known.projectID || null;
    try {
      const session = (await sessionApi.get(sessionId)) as unknown as Session;
      return session.effectiveProjectID || session.projectID || null;
    } catch {
      return undefined;
    }
  };

  const deleteFolder = async (folder: FolderInfo) => {
    closeMenu();
    const ok = await confirm({
      title: t('history.deleteFolderTitle'),
      description: t('history.deleteFolderDescription', { folder: folderLabel(folder) }),
      confirmText: t('history.deleteFolder'),
      variant: 'danger',
    });
    if (!ok) return;
    try {
      // Whether the open session lives in this folder, read now (its own lookup may have finished
      // while the confirmation was open) and before the delete archives it.
      const checkedSession = selectedSessionIdRef.current;
      const openFolder = await openSessionFolder(checkedSession);
      // Same as the former workbench "删除项目": the project goes, its sessions are archived (restorable).
      await apiClient.delete(`/api/project/${folder.id}`);
      setPinnedFolders((current) => {
        const next = new Set(current);
        next.delete(folder.id);
        return next;
      });
      // Leave the session open now if the delete took it: the one checked above, or one the user
      // opened while the delete was out (the list still shows its folder from before the delete).
      const openNow = selectedSessionIdRef.current;
      const listedNow = listedSessionsRef.current.find((session) => session.id === openNow);
      const folderNow =
        openNow === checkedSession
          ? openFolder
          : listedNow
            ? listedNow.effectiveProjectID || listedNow.projectID || null
            : undefined;
      if (folderNow === folder.id) leaveSession(openNow);
      else if (folderNow === undefined && openNow) {
        // Its folder is unknown: leave it if the delete archived it (an archived session reads as
        // 404) — but only while it is still the open one.
        try {
          const session = (await sessionApi.get(openNow)) as unknown as Session;
          if (session.status === 'archived' || (session.effectiveProjectID || session.projectID) === folder.id) {
            leaveSession(openNow);
          }
        } catch (err: any) {
          if (err?.response?.status === 404) leaveSession(openNow);
        }
      }
      toast.success(t('history.folderDeleted'));
      announceProjectsChanged('sidebar');
      void refreshAll();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(t('history.deleteFolderFailed'), typeof detail === 'string' ? detail : err?.message);
    }
  };

  /** Keep the open session reachable even when it sorts past the visible page. */
  const visibleRows = (sectionId: string, list: Session[], compactCount: number): Session[] => {
    const visibleCount = visibleCounts[sectionId] ?? compactCount;
    if (list.length <= visibleCount) return list;
    const head = list.slice(0, visibleCount);
    const selected = list.find((session) => session.id === selectedSessionId);
    return selected && !head.includes(selected) ? [...head, selected] : head;
  };

  const exitSelecting = () => {
    setSelecting(false);
    setCheckedIds(new Set());
  };

  const toggleChecked = (sessionId: string) => {
    setCheckedIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  };

  const archiveChecked = async () => {
    const targets = listedSessions.filter((session) => checkedIds.has(session.id));
    if (targets.length === 0 || archivingChecked) return;
    const ok = await confirm({
      title: t('history.archiveSelectedTitle'),
      description: t('history.archiveSelectedDescription', { count: targets.length }),
      confirmText: t('history.archive'),
    });
    if (!ok) return;
    setArchivingChecked(true);
    const failed: string[] = [];
    const archived = new Set<string>();
    const queue = [...targets];
    // A few requests at a time: much faster than one by one for a long selection,
    // without flooding the server with one request per session at once.
    const archiveNext = async () => {
      for (let session = queue.shift(); session; session = queue.shift()) {
        try {
          await sessionApi.archive(session.id);
          archived.add(session.id);
          removeSession(session.id);
        } catch {
          failed.push(getSessionDisplayTitle(session));
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(ARCHIVE_CONCURRENCY, targets.length) }, archiveNext));
    setArchivingChecked(false);
    // Leave the open session when it was archived, whatever happened to the others.
    const openNow = selectedSessionIdRef.current;
    if (openNow && archived.has(openNow)) leaveSession(openNow);
    if (failed.length) toast.error(t('history.archiveFailed'), failed.join('、'));
    else toast.success(t('history.archivedCount', { count: targets.length }));
    exitSelecting();
    void fetchFolders();
  };

  const iconButtonClass = 'grid h-7 w-7 shrink-0 place-items-center rounded-lg text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus';
  const rowMenuButtonClass = 'grid h-6 w-6 shrink-0 place-items-center rounded text-ui-secondary opacity-0 transition-all hover:bg-ui-hover hover:text-ui-text focus:opacity-100 group-hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus';
  // Reveal actions only for the hovered folder header, or for keyboard focus.
  const folderActionButtonClass = 'pointer-events-none grid h-6 w-6 shrink-0 place-items-center rounded text-ui-secondary opacity-0 transition-all hover:bg-ui-hover hover:text-ui-text group-hover/folder:pointer-events-auto group-hover/folder:opacity-100 group-hover/folder:disabled:opacity-50 focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus';

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-1 border-t border-ui-border py-2 [&_svg]:stroke-[1.75]">
        <button type="button" onClick={onExpandSidebar} className={iconButtonClass} title={t('history.title')} aria-label={t('history.title')} data-tip={t('history.title')}>
          <History className="h-[18px] w-[18px]" />
        </button>
        <button type="button" onClick={newChat} className={iconButtonClass} title={t('history.newChat')} aria-label={t('history.newChat')} data-tip={t('history.newChat')}>
          <SquarePen className="h-[18px] w-[18px]" />
        </button>
      </div>
    );
  }

  const renderSessionRow = (session: Session, inFolder: boolean) => {
    const selected = selectedSessionId === session.id;
    const running = runningIds.has(session.id);
    if (renaming?.id === session.id) {
      return (
        <div key={session.id} className={inFolder ? 'pl-7 pr-1' : 'px-1'}>
          <input
            autoFocus
            value={renaming.value}
            maxLength={100}
            onChange={(event) => setRenaming({ id: session.id, value: event.target.value })}
            onKeyDown={(event) => {
              if (event.key === 'Enter') { event.preventDefault(); void submitRename(); }
              if (event.key === 'Escape') { event.preventDefault(); setRenaming(null); }
            }}
            onBlur={() => void submitRename()}
            className="h-8 w-full rounded-lg border border-ui-control bg-ui-surface px-2 text-sm text-ui-text shadow-ui-input outline-none focus:ring-2 focus:ring-ui-focus"
          />
        </div>
      );
    }
    if (selecting) {
      const checked = checkedIds.has(session.id);
      return (
        <label key={session.id} className={`flex h-8 cursor-pointer items-center gap-2 rounded-lg px-2 text-sm transition-colors ${inFolder ? 'ml-7' : ''} ${checked ? 'bg-ui-selected font-medium text-ui-text' : 'font-normal text-ui-text hover:bg-ui-hover hover:text-ui-text'}`}>
          <input type="checkbox" checked={checked} onChange={() => toggleChecked(session.id)} className="h-3.5 w-3.5 shrink-0 accent-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus" />
          <span className="min-w-0 flex-1 truncate" title={getSessionDisplayTitle(session)}>{getSessionDisplayTitle(session)}</span>
        </label>
      );
    }
    return (
      <div key={session.id} className={`group flex items-center rounded-lg transition-colors ${inFolder ? 'ml-7' : ''} ${selected ? 'bg-ui-selected' : 'hover:bg-ui-hover'}`}>
        <button type="button" onClick={() => openSession(session.id)} title={getSessionDisplayTitle(session)}
          className={`flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg px-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus ${selected ? 'font-medium text-ui-text' : 'font-normal text-ui-text group-hover:text-ui-text'}`}>
          {running && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-red-500" />}
          <ScrollingTitle text={getSessionDisplayTitle(session)} />
        </button>
        <button type="button" aria-label={t('history.sessionActions', { title: getSessionDisplayTitle(session) })}
          onClick={(event) => openMenu('session', session.id, event.currentTarget)}
          className={`${rowMenuButtonClass} mr-1 ${menu?.kind === 'session' && menu.id === session.id ? 'opacity-100' : ''}`}>
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  };

  const renderListFooter = (
    sectionId: string,
    list: Session[],
    compactCount: number,
    remoteMore: boolean,
    indent: boolean,
  ) => {
    const visibleCount = visibleCounts[sectionId] ?? compactCount;
    const hiddenLoaded = list.length > visibleCount;
    const canLoad = hiddenLoaded || remoteMore;
    const canCollapse = visibleCount > compactCount && list.length > compactCount;
    const loadingMore = loadingMoreProjectIds.has(sectionId);
    if (!canLoad && !canCollapse) return null;
    const linkClass = 'flex items-center gap-1 rounded px-2 py-1 text-xs text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus';
    return (
      <div className={`flex items-center gap-1 ${indent ? 'ml-7' : ''}`}>
        {canLoad && (
          <button type="button" disabled={loadingMore} className={linkClass}
            onClick={() => {
              const nextCount = Math.min(visibleCount, list.length) + HISTORY_LOAD_MORE_SIZE;
              setVisibleCounts((current) => ({ ...current, [sectionId]: nextCount }));
              if (remoteMore && list.length < nextCount) loadMore?.(sectionId);
            }}>
            {loadingMore && <Loader2 className="h-3 w-3 animate-spin" />}{t('history.loadMore')}
          </button>
        )}
        {canCollapse && (
          <button type="button" className={linkClass} onClick={() => setVisibleCounts((current) => ({ ...current, [sectionId]: compactCount }))}>
            {t('history.collapse')}
          </button>
        )}
      </div>
    );
  };

  const activeFolder = menu?.kind === 'folder' ? folders.find((folder) => folder.id === menu.id) : undefined;
  const activeSession = menu?.kind === 'session' ? listedSessions.find((session) => session.id === menu.id) : undefined;
  const moveTargets = folders.filter((folder) => folder.canWrite !== false && folder.pathStatus === 'available');

  return (
    <section className="flex min-h-0 flex-1 flex-col pt-2 [&_svg]:stroke-[1.75]" aria-label={t('history.title')}>
      <div className="flex items-center gap-0.5 px-2 pb-1.5">
        <span className="flex-1 text-xs font-medium uppercase tracking-wide text-ui-secondary">{t('history.title')}</span>
        <button type="button" onClick={() => { setSearchOpen((open) => !open); setTimeout(() => searchInputRef.current?.focus(), 0); }} className={iconButtonClass} title={t('history.search')} aria-label={t('history.search')} aria-expanded={searchOpen}>
          <Search className="h-4 w-4" />
        </button>
        <button type="button" onClick={() => setFolderDialog({ mode: 'create' })} className={iconButtonClass} title={t('history.newFolder')} aria-label={t('history.newFolder')}>
          <FolderPlus className="h-4 w-4" />
        </button>
        <button type="button" onClick={() => (selecting ? exitSelecting() : setSelecting(true))} className={`${iconButtonClass} ${selecting ? 'bg-ui-selected text-ui-text' : ''}`}
          title={t('history.selectMode')} aria-label={t('history.selectMode')} aria-pressed={selecting}>
          <CheckSquare className="h-4 w-4" />
        </button>
      </div>

      {searchOpen && (
        <div className="px-3 pb-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ui-secondary" />
            <input ref={searchInputRef} value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Escape') { setSearchOpen(false); setSearchQuery(''); } }}
              placeholder={t('history.searchPlaceholder')} aria-label={t('history.searchPlaceholder')}
              className="h-8 w-full rounded-lg border border-ui-control bg-ui-surface pl-8 pr-2 text-sm text-ui-text shadow-ui-input outline-none placeholder:text-ui-muted focus:ring-2 focus:ring-ui-focus" />
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {searchResults !== null ? (
          searchResults.length > 0 ? searchResults.map((session) => (
            <button key={session.id} type="button" onClick={() => { openSession(session.id); setSearchOpen(false); setSearchQuery(''); }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm font-normal text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-400" />
              <span className="min-w-0 flex-1 truncate">{getSessionDisplayTitle(session)}</span>
            </button>
          )) : <p className="px-3 py-6 text-center text-xs text-ui-muted">{t('history.noResults')}</p>
        ) : loading && sessions.length === 0 ? (
          <div className="flex items-center gap-2 px-3 py-6 text-xs text-ui-muted"><Loader2 className="h-4 w-4 animate-spin" />{t('history.loading')}</div>
        ) : (
          <>
            {groups.folders.map(({ folder, sessions: folderSessions }) => {
              const isCollapsed = collapsedFolders.has(folder.id);
              const pinned = pinnedFolders.has(folder.id);
              const remoteMore = folderSessions.length < (folder.sessionCount ?? 0) || hasMoreByProject[folder.id];
              return (
                <div key={folder.id}>
                  <div className="group/folder flex items-center gap-0.5 rounded-lg px-1 transition-colors hover:bg-ui-hover">
                    <button type="button" onClick={() => toggleFolder(folder.id)} aria-expanded={!isCollapsed}
                      className="flex h-8 min-w-0 flex-1 items-center gap-1.5 rounded-lg text-left text-[13px] font-normal text-ui-text hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">
                      {isCollapsed ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ui-secondary" /> : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ui-secondary" />}
                      <Folder className="h-3.5 w-3.5 shrink-0 text-ui-secondary" />
                      <span className={`min-w-0 truncate ${pinned ? 'font-medium' : ''}`}>{folderLabel(folder)}</span>
                      {pinned && <Pin className="h-3 w-3 shrink-0 text-ui-secondary" />}
                    </button>
                    <button type="button" aria-label={t('history.folderActions', { folder: folderLabel(folder) })} title={t('history.folderActions', { folder: folderLabel(folder) })} onClick={(event) => openMenu('folder', folder.id, event.currentTarget)} className={folderActionButtonClass}>
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" disabled={creatingIn === folder.id} aria-label={t('history.newSessionIn', { folder: folderLabel(folder) })} title={t('history.newSessionIn', { folder: folderLabel(folder) })} onClick={() => void newSessionInFolder(folder.id)} className={folderActionButtonClass}>
                      {creatingIn === folder.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageSquarePlus className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                  {!isCollapsed && (
                    <div className="space-y-0.5 py-0.5">
                      {visibleRows(folder.id, folderSessions, compactCount).map((session) => renderSessionRow(session, true))}
                      {folderSessions.length === 0 && <p className="ml-7 px-2 py-1 text-xs text-ui-muted">{t('history.emptyFolder')}</p>}
                      {renderListFooter(folder.id, folderSessions, compactCount, Boolean(remoteMore), true)}
                    </div>
                  )}
                </div>
              );
            })}
            <div className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-ui-secondary">{t('history.ungrouped')}</div>
            <div className="space-y-0.5">
              {visibleRows(UNGROUPED_ID, groups.ungrouped, compactCount).map((session) => renderSessionRow(session, false))}
              {groups.ungrouped.length === 0 && <p className="px-2 py-1 text-xs text-ui-muted">{t('history.emptyUngrouped')}</p>}
              {renderListFooter(UNGROUPED_ID, groups.ungrouped, compactCount, Boolean(hasMoreByProject[UNGROUPED_ID]), false)}
            </div>
          </>
        )}
      </div>

      {menu && activeSession && createPortal(
        <div data-history-menu className="fixed z-[65] w-56 overflow-hidden rounded-xl border border-ui-border bg-ui-surface p-1 text-ui-text shadow-ui-panel [&_svg]:stroke-[1.75]" style={{ top: menu.top, left: menu.left }}>
          {menu.moving ? (
            <>
              <button type="button" onClick={() => setMenu({ ...menu, moving: false })} className="mb-1 flex w-full items-center gap-1.5 border-b border-ui-border px-2 py-1.5 text-xs font-medium text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">
                <ChevronRight className="h-3.5 w-3.5 rotate-180" />{t('history.moveTo')}
              </button>
              <div className="max-h-56 overflow-y-auto">
                {moveTargets.length === 0 ? <p className="px-2 py-3 text-center text-xs text-ui-muted">{t('history.noMoveTargets')}</p> : moveTargets.map((folder) => {
                  const current = (activeSession.effectiveProjectID || activeSession.projectID) === folder.id;
                  return (
                    <button key={folder.id} type="button" disabled={current} onClick={() => void moveSession(activeSession, folder)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">
                      <Folder className="h-3.5 w-3.5 shrink-0 text-ui-secondary" /><span className="min-w-0 flex-1 truncate">{folderLabel(folder)}</span>
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            <>
              <button type="button" onClick={() => { setRenaming({ id: activeSession.id, value: activeSession.title }); closeMenu(); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Pencil className="h-4 w-4 text-ui-secondary" />{t('history.rename')}</button>
              <button type="button" onClick={() => { void exportSession(activeSession); closeMenu(); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Download className="h-4 w-4 text-ui-secondary" />{t('history.downloadJson')}</button>
              <button type="button" disabled={moveTargets.length === 0} onClick={() => setMenu({ ...menu, moving: true })} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><FolderInput className="h-4 w-4 text-ui-secondary" />{t('history.move')}</button>
              <button type="button" onClick={() => { void toggleShare(activeSession); closeMenu(); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Share2 className="h-4 w-4 text-ui-secondary" />{activeSession.isShared ? t('history.unshare') : t('history.share')}</button>
              <div className="my-1 border-t border-ui-border" />
              <button type="button" onClick={() => { closeMenu(); void archiveSession(activeSession); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-red-600 transition-colors hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus dark:text-red-300 dark:hover:bg-red-500/10"><Archive className="h-4 w-4" />{t('history.archive')}</button>
            </>
          )}
        </div>,
        document.body,
      )}

      {menu && activeFolder && createPortal(
        <div data-history-menu className="fixed z-[65] w-52 overflow-hidden rounded-xl border border-ui-border bg-ui-surface p-1 text-ui-text shadow-ui-panel [&_svg]:stroke-[1.75]" style={{ top: menu.top, left: menu.left }}>
          <button type="button" onClick={() => { setFolderDialog({ mode: 'rename', folder: activeFolder }); closeMenu(); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Pencil className="h-4 w-4 text-ui-secondary" />{t('history.rename')}</button>
          <button type="button" onClick={() => togglePin(activeFolder.id)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">{pinnedFolders.has(activeFolder.id) ? <PinOff className="h-4 w-4 text-ui-secondary" /> : <Pin className="h-4 w-4 text-ui-secondary" />}{pinnedFolders.has(activeFolder.id) ? t('history.unpin') : t('history.pin')}</button>
          <button type="button" onClick={() => void copyFolderPath(activeFolder)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Copy className="h-4 w-4 text-ui-secondary" />{t('history.copyPath')}</button>
          <button type="button" onClick={() => void toggleFolderShare(activeFolder)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><Share2 className="h-4 w-4 text-ui-secondary" />{activeFolder.isShared ? t('history.unshare') : t('history.share')}</button>
          <div className="my-1 border-t border-ui-border" />
          <button type="button" onClick={() => void removeFolder(activeFolder)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus"><FolderInput className="h-4 w-4 text-ui-secondary" />{t('history.removeFolder')}</button>
          <button type="button" disabled={activeFolder.canDelete === false} onClick={() => void deleteFolder(activeFolder)} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus dark:text-red-300 dark:hover:bg-red-500/10"><Trash2 className="h-4 w-4" />{t('history.deleteFolder')}</button>
        </div>,
        document.body,
      )}

      {selecting && (
        <div className="flex items-center gap-2 border-t border-ui-border px-3 py-2">
          <span className="min-w-0 flex-1 truncate text-xs text-ui-secondary">{t('history.selectedCount', { count: checkedIds.size })}</span>
          <button type="button" onClick={exitSelecting} className="h-7 rounded-lg px-2.5 text-xs text-ui-secondary transition-colors hover:bg-ui-hover hover:text-ui-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ui-focus">{t('history.cancel')}</button>
          <button type="button" disabled={checkedIds.size === 0 || archivingChecked} onClick={() => void archiveChecked()}
            className="inline-flex h-7 items-center gap-1 rounded-lg bg-red-600 px-2.5 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ui-focus">
            {archivingChecked ? <Loader2 className="h-3 w-3 animate-spin" /> : <Archive className="h-3 w-3" />}{t('history.archive')}
          </button>
        </div>
      )}

      {folderDialog && (
        <ProjectFolderDialog
          mode={folderDialog.mode}
          project={folderDialog.folder}
          onClose={() => setFolderDialog(null)}
          onSaved={(saved) => {
            setFolderDialog(null);
            setFolders((current) => {
              const next = current.filter((folder) => folder.id !== saved.id);
              return [{ ...saved }, ...next];
            });
            announceProjectsChanged('sidebar');
            void refreshAll();
          }}
        />
      )}
    </section>
  );
}
