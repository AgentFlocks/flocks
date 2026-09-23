import { AuditConversation } from "./components/AuditConversation";
import { AuditTaskList } from "./components/AuditTaskList";
import { AuditHome } from "./components/AuditHome";
import {
  useCallback,
  useContext,
  useEffect,
  useInsertionEffect,
  useRef,
  useState,
} from "react";

import { BatchContext, useAuditApi } from "./BatchContext";
import {
  listScans as listGlobalScans,
  deleteScan as deleteGlobalScan,
  listBatchRecords,
  cancelBatchTask,
  deleteBatchTask,
} from "./api";
import { ArtifactInspector } from "./components/ArtifactInspector";
import { DeleteScanDialog } from "./components/DeleteScanDialog";
import { ElapsedTime } from "./components/ElapsedTime";
import { NewAuditDrawer } from "./components/NewAuditDrawer";
import { PhaseWorkspace } from "./components/PhaseWorkspace";
import { isCyberGymValidation, phaseDisplayLabel, phaseGroupId, phaseGroups } from "./phaseGroups";
import { StatusBadge } from "./components/StatusBadge";
import { Icon } from "./icons";
import { useCodeSecurityI18n } from "./i18n";
import { lifecycleLabels, phaseLabels, shortId } from "./labels";
import type {
  AuditEvent,
  ProjectSummary,
  ScanDetail,
  ScanSummary,
} from "./types";
import styles from "./styles";

export interface FinalFindingMetric {
  count: number | null;
  basis: string;
}

interface ScanViewCacheEntry {
  detail: ScanDetail;
  events: AuditEvent[];
  hasOlderEvents: boolean;
  latestSeq: number;
}

const MAX_CACHED_SCAN_VIEWS = 8;

export function deriveFinalFindingMetric(
  detail: ScanDetail,
): FinalFindingMetric {
  if (detail.scan.lifecycle_status !== "completed") {
    return { count: null, basis: "审计完成后确定" };
  }
  if (detail.scan.integrity_status !== "valid") {
    return { count: null, basis: "最终结果不可用" };
  }

  const dynamicExecutionCount = ["completed", "inconclusive"].reduce(
    (total, key) => {
      const value = detail.dynamicValidation?.[key];
      return total + (typeof value === "number" ? value : 0);
    },
    0,
  );
  const usesDynamicResult =
    detail.scan.dynamic_enabled &&
    detail.dynamicValidation?.status !== "skipped" &&
    dynamicExecutionCount > 0;
  const value = usesDynamicResult
    ? detail.findingSummary.dynamic_reproduced
    : detail.findingSummary.total;

  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { count: null, basis: "最终结果不可用" };
  }
  return {
    count: Math.max(0, Math.trunc(value)),
    basis: usesDynamicResult ? "动态验证复现" : "静态验证确认",
  };
}

export default function Page() {
  const [search, setSearch] = useState(window.location.search);
  useWorkspaceStyles();
  useEffect(() => {
    const changed = () => setSearch(window.location.search);
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  const params = new URLSearchParams(search);
  const batchId = params.get("batch_id") || "";
  const taskId = params.get("task_id") || "";
  const scope = batchId && taskId ? { batchId, taskId } : null;
  return (
    <BatchContext.Provider value={scope}>
      <WorkspacePage key={`${batchId}:${taskId}`} />
    </BatchContext.Provider>
  );
}

function WorkspacePage() {
  const scope = useContext(BatchContext);
  const {
    cancelScan,
    getEarlierEvents,
    getEvents,
    getRecentEvents,
    getScan,
    listProjects,
    downloadUrl,
  } = useAuditApi();
  const { t } = useCodeSecurityI18n();
  const user = useSdkUser();
  const canCreate = user?.role === "admin" && !scope;
  const initialParams = new URLSearchParams(window.location.search);
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [removedProjectIds, setRemovedProjectIds] = useState<Set<string>>(() => new Set());
  const [batchRecords, setBatchRecords] = useState<ScanSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialParams.get("scan_id"),
  );
  const [detail, setDetail] = useState<ScanDetail | null>(null);
  const WorkbenchPanel = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__?.AuditWorkbenchPanel;
  const [workbenchTask, setWorkbenchTask] = useState<string | null>(null);
  const [requestedPhase, setRequestedPhase] = useState<{ id: string }>();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingEvents, setLoadingEvents] = useState(true);
  const [showSkeleton, setShowSkeleton] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<
    "connected" | "reconnecting" | "failed"
  >("connected");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(
    Boolean(initialParams.get("artifact")),
  );
  const [activeArtifact, setActiveArtifact] = useState(
    initialParams.get("artifact") || "overview",
  );
  const [liveMessage, setLiveMessage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [scanCursor, setScanCursor] = useState<string | null>(null);
  const [loadingMoreScans, setLoadingMoreScans] = useState(false);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlderEvents, setLoadingOlderEvents] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ScanSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const latestSeqRef = useRef(0);
  const selectedIdRef = useRef<string | null>(selectedId);
  const refreshTasksRef = useRef(new Map<string, Promise<void>>());
  const refreshQueuedRef = useRef(new Set<string>());
  const drawerOpenerRef = useRef<HTMLElement | null>(null);
  const inspectorOpenerRef = useRef<HTMLElement | null>(null);
  const loadingMoreScansRef = useRef(false);
  const listRefreshTimerRef = useRef<number | null>(null);
  const queuedScanRefreshRef = useRef(new Set<string>());
  const deletedScanIdsRef = useRef(new Set<string>());
  const deleteOpenerRef = useRef<HTMLButtonElement | null>(null);
  const scanViewCacheRef = useRef(new Map<string, ScanViewCacheEntry>());
  const initialLoadTasksRef = useRef(
    new Map<string, Promise<{ detail: ScanDetail; hasMore: boolean }>>(),
  );
  const authoritativeDetailsRef = useRef(new Map<string, ScanDetail>());
  const prefetchTaskRef = useRef<Promise<void> | null>(null);
  const hasPresentedDetailRef = useRef(false);

  const openDrawer = useCallback(() => {
    drawerOpenerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setDrawerOpen(true);
  }, []);

  const showOverview = useCallback(
    (view: "home" | "audits" | "project", projectId?: string) => {
      const url = new URL(window.location.href);
      for (const key of [
        "scan_id",
        "batch_id",
        "task_id",
        "artifact",
        "view",
        "project_id",
      ])
        url.searchParams.delete(key);
      if (view !== "home") url.searchParams.set("view", view);
      if (view === "project" && projectId)
        url.searchParams.set("project_id", projectId);
      url.hash = "";
      selectedIdRef.current = null;
      setSelectedId(null);
      setDetail(null);
      setEvents([]);
      setError("");
      setLoading(false);
      setInspectorOpen(false);
      setActiveArtifact("overview");
      window.history.pushState({}, "", url);
      // Synchronize both the outer batch scope and the host router.
      window.dispatchEvent(new PopStateEvent("popstate"));
    },
    [],
  );

  const returnHome = useCallback(() => showOverview("home"), [showOverview]);
  const returnAuditList = useCallback(
    () => showOverview("audits"),
    [showOverview],
  );

  const openProject = useCallback(
    (projectId: string) => showOverview("project", projectId),
    [showOverview],
  );

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
    window.setTimeout(() => drawerOpenerRef.current?.focus(), 0);
  }, []);

  const openInspector = useCallback(() => {
    inspectorOpenerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setInspectorOpen(true);
  }, []);

  const closeInspector = useCallback(() => {
    setInspectorOpen(false);
    window.setTimeout(() => inspectorOpenerRef.current?.focus(), 0);
  }, []);

  const openDeleteDialog = useCallback(
    (scan: ScanSummary, opener: HTMLButtonElement) => {
      deleteOpenerRef.current = opener;
      setDeleteError("");
      setDeleteTarget(scan);
    },
    [],
  );

  const closeDeleteDialog = useCallback(() => {
    if (deleting) return;
    setDeleteTarget(null);
    setDeleteError("");
    window.setTimeout(() => deleteOpenerRef.current?.focus(), 0);
  }, [deleting]);

  const applySelection = useCallback(
    (scanId: string, updateHistory: boolean) => {
      const cached = scanViewCacheRef.current.get(scanId);
      if (cached) rememberScanView(scanViewCacheRef.current, scanId, cached);
      selectedIdRef.current = scanId;
      setSelectedId(scanId);
      setDetail(cached?.detail || null);
      setEvents(cached?.events || []);
      setError("");
      setLoading(!cached);
      setLoadingEvents(!cached);
      setInspectorOpen(false);
      setHasOlderEvents(cached?.hasOlderEvents || false);
      setLoadingOlderEvents(false);
      latestSeqRef.current = cached?.latestSeq || 0;
      if (updateHistory) {
        const params = new URLSearchParams(window.location.search);
        params.set("scan_id", scanId);
        params.set("view", "audits");
        window.history.pushState(
          {},
          "",
          `${window.location.pathname}?${params.toString()}`,
        );
        window.dispatchEvent(new PopStateEvent("popstate"));
      }
    },
    [],
  );

  const selectScan = useCallback(
    (scanId: string) => {
      if (scanId === selectedIdRef.current) return;
      applySelection(scanId, true);
    },
    [applySelection],
  );

  const replaceSelection = useCallback(
    (scanId: string) => {
      applySelection(scanId, false);
      const params = new URLSearchParams(window.location.search);
      params.set("scan_id", scanId);
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}?${params.toString()}`,
      );
    },
    [applySelection],
  );

  const loadSelected = useCallback(
    (scanId: string, afterSeq = 0) => {
      const load = async () => {
        const detailRequest = getScan(scanId).catch((reason) => {
          if (reason?.response?.status === 404) {
            deletedScanIdsRef.current.add(scanId);
            authoritativeDetailsRef.current.delete(scanId);
            scanViewCacheRef.current.delete(scanId);
            setScans(current => current.filter(scan => scan.scan_id !== scanId));
            if (selectedIdRef.current === scanId) { setDetail(null); setEvents([]); }
          }
          throw reason;
        }).then((nextDetail) => {
          const displayDetail =
            authoritativeDetailsRef.current.get(scanId) || nextDetail;
          if (!scope && !deletedScanIdsRef.current.has(scanId)) {
            setScans((current) =>
              mergeScans(current, [summaryFromDetail(displayDetail)]),
            );
          }
          if (selectedIdRef.current === scanId && !deletedScanIdsRef.current.has(scanId)) {
            hasPresentedDetailRef.current = true;
            setDetail(displayDetail);
            setLoading(false);
          }
          return nextDetail;
        });
        const eventsRequest = afterSeq
          ? getEvents(scanId, afterSeq)
          : getRecentEvents(scanId);
        const [nextDetail, page] = await Promise.all([
          detailRequest,
          eventsRequest,
        ]);
        if (deletedScanIdsRef.current.has(scanId)) {
          return { detail: nextDetail, hasMore: false };
        }
        const resolvedDetail =
          authoritativeDetailsRef.current.get(scanId) || nextDetail;
        const eventItems: AuditEvent[] = page.items;
        const previous = scanViewCacheRef.current.get(scanId);
        const nextEvents = mergeAuditEvents(
          afterSeq ? previous?.events || [] : [],
          eventItems,
        );
        const nextHasOlderEvents = afterSeq
          ? previous?.hasOlderEvents || false
          : page.hasMore;
        const deliveredSeq = eventItems.length
          ? eventItems[eventItems.length - 1].seq
          : afterSeq;
        const nextLatestSeq = Math.max(previous?.latestSeq || 0, deliveredSeq);
        rememberScanView(scanViewCacheRef.current, scanId, {
          detail: resolvedDetail,
          events: nextEvents,
          hasOlderEvents: nextHasOlderEvents,
          latestSeq: nextLatestSeq,
        });
        if (selectedIdRef.current !== scanId)
          return { detail: nextDetail, hasMore: false };
        hasPresentedDetailRef.current = true;
        setDetail(resolvedDetail);
        setLoading(false);
        setLoadingEvents(false);
        if (!afterSeq) setHasOlderEvents(page.hasMore);
        latestSeqRef.current = Math.max(latestSeqRef.current, deliveredSeq);
        setEvents(nextEvents);
        if (eventItems.length) {
          setLiveMessage(
            t("{{phase}}新增 {{count}} 条事件。", {
              phase: t(
                phaseLabels[resolvedDetail.scan.current_phase || ""] ||
                  "代码审计",
              ),
              count: eventItems.length,
            }),
          );
        }
        return {
          detail: resolvedDetail,
          hasMore: Boolean(
            (afterSeq && page.hasMore) ||
            deliveredSeq !== Number(resolvedDetail.scan.latest_event_seq || 0),
          ),
        };
      };

      if (afterSeq) return load();
      const existing = initialLoadTasksRef.current.get(scanId);
      if (existing) return existing;
      let running: Promise<{ detail: ScanDetail; hasMore: boolean }>;
      running = load().finally(() => {
        if (initialLoadTasksRef.current.get(scanId) === running)
          initialLoadTasksRef.current.delete(scanId);
      });
      initialLoadTasksRef.current.set(scanId, running);
      return running;
    },
    [t],
  );

  const prefetchScan = useCallback(
    (scanId: string) => {
      if (
        scanId === selectedIdRef.current ||
        scanViewCacheRef.current.has(scanId) ||
        prefetchTaskRef.current
      )
        return;
      let running: Promise<void>;
      running = loadSelected(scanId)
        .then(() => undefined)
        .catch(() => undefined)
        .finally(() => {
          if (prefetchTaskRef.current === running)
            prefetchTaskRef.current = null;
        });
      prefetchTaskRef.current = running;
    },
    [loadSelected],
  );

  const refreshChangedScan = useCallback(
    async (scanId: string) => {
      refreshQueuedRef.current.add(scanId);
      const existing = refreshTasksRef.current.get(scanId);
      if (existing) return existing;
      const drain = async () => {
        while (
          refreshQueuedRef.current.delete(scanId) &&
          selectedIdRef.current === scanId
        ) {
          const beforeSeq = latestSeqRef.current;
          const result = await loadSelected(scanId, beforeSeq);
          if (result.hasMore && latestSeqRef.current > beforeSeq)
            refreshQueuedRef.current.add(scanId);
        }
      };
      let running: Promise<void>;
      running = drain().finally(() => {
        if (refreshTasksRef.current.get(scanId) === running)
          refreshTasksRef.current.delete(scanId);
      });
      refreshTasksRef.current.set(scanId, running);
      return running;
    },
    [loadSelected],
  );

  const reloadList = useCallback(async (reset = false) => {
    const ordinary = listGlobalScans().then((page) => {
      const items = page.items.filter(
        (scan) => !deletedScanIdsRef.current.has(scan.scan_id),
      );
      setScans((current) => reset || !page.nextCursor ? items : mergeScans(current, items));
      setScanCursor(page.nextCursor);
      return items;
    });
    const batches =
      user?.role === "admin" ? listBatchRecords() : Promise.resolve([]);
    const [visibleItems, batchItems] = await Promise.all([ordinary, batches]);
    setBatchRecords(
      batchItems
        .filter((item) => !deletedScanIdsRef.current.has(item.scan_id))
        .map((item) => ({
          ...item,
          display_name: `${t("任务")} ${item.task_id}`,
        })),
    );
    return scope
      ? batchItems
          .filter(
            (item) =>
              item.batch_id === scope.batchId &&
              item.task_id === scope.taskId &&
              item.audit_scan_id,
          )
          .map((item) => ({ ...item, scan_id: item.audit_scan_id! }))
      : visibleItems;
  }, []);

  useEffect(() => {
    if (selectedId || scope) return;
    void reloadList(true).catch((reason) => setError(reason?.message || t("无法加载更多审计记录")));
  }, [selectedId, scope, reloadList, t]);

  const loadMoreScans = useCallback(async () => {
    if (!scanCursor || loadingMoreScansRef.current) return;
    loadingMoreScansRef.current = true;
    setLoadingMoreScans(true);
    try {
      const page = await listGlobalScans(scanCursor);
      const visibleItems = page.items.filter(
        (scan) => !deletedScanIdsRef.current.has(scan.scan_id),
      );
      setScans((current) => mergeScans(current, visibleItems));
      setScanCursor(page.nextCursor);
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          t("无法加载更多审计记录"),
      );
    } finally {
      loadingMoreScansRef.current = false;
      setLoadingMoreScans(false);
    }
  }, [scanCursor, t]);

  const scheduleListRefresh = useCallback((scanId: string) => {
    queuedScanRefreshRef.current.add(scanId);
    if (listRefreshTimerRef.current !== null) return;
    listRefreshTimerRef.current = window.setTimeout(() => {
      listRefreshTimerRef.current = null;
      const scanIds = [...queuedScanRefreshRef.current];
      queuedScanRefreshRef.current.clear();
      Promise.all(scanIds.map((item) => getScan(item).catch(() => null))).then(
        (details) => {
          const summaries = details
            .filter((item): item is ScanDetail => item !== null)
            .map(summaryFromDetail)
            .filter((scan) => !deletedScanIdsRef.current.has(scan.scan_id));
          if (summaries.length)
            setScans((current) => mergeScans(current, summaries));
        },
      );
    }, 250);
  }, []);

  useEffect(
    () => () => {
      if (listRefreshTimerRef.current !== null)
        window.clearTimeout(listRefreshTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    const onPopState = () => {
      const params = new URLSearchParams(window.location.search);
      if (
        (params.get("batch_id") || "") !== (scope?.batchId || "") ||
        (params.get("task_id") || "") !== (scope?.taskId || "")
      )
        return;
      const scanId = params.get("scan_id");
      if (scanId && scanId !== selectedIdRef.current)
        applySelection(scanId, false);
      if (!scanId && !scope) {
        selectedIdRef.current = null;
        setSelectedId(null);
        setDetail(null);
        setLoading(false);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [applySelection]);

  useEffect(() => {
    let disposed = false;
    const timer = window.setTimeout(() => setShowSkeleton(true), 180);
    reloadList()
      .then((nextScans) => {
        if (disposed) return;
        const currentSelection = selectedIdRef.current;
        const candidate =
          currentSelection || (scope ? nextScans[0]?.scan_id : null) || null;
        if (candidate && candidate !== currentSelection)
          replaceSelection(candidate);
        if (!candidate) setLoading(false);
      })
      .catch((reason) => {
        setError(
          reason?.response?.data?.detail?.message ||
            reason?.message ||
            t("无法加载代码审计工作区"),
        );
        setLoading(false);
      });
    if (!scope) {
      setProjectsLoading(true);
      listProjects()
        .then((next) => {
          if (!disposed) setProjects(next);
        })
        .catch((reason) =>
          setError(
            reason?.response?.data?.detail?.message ||
              reason?.message ||
              t("无法加载可审计项目列表"),
          ),
        )
        .finally(() => {
          if (!disposed) setProjectsLoading(false);
        });
    }
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [canCreate, reloadList, replaceSelection, t]);

  useEffect(() => {
    if (!selectedId) return;
    const cached = scanViewCacheRef.current.get(selectedId);
    if (cached) {
      setLoading(false);
      setLoadingEvents(false);
      window.setTimeout(() => titleRef.current?.focus(), 0);

    } else {
      setLoading(true);
      setLoadingEvents(true);
    }
    loadSelected(selectedId, cached?.latestSeq || 0)
      .then(({ hasMore }) => {
        if (selectedIdRef.current === selectedId && hasMore) {
          refreshChangedScan(selectedId).catch(() =>
            setConnection("reconnecting"),
          );
        }
        if (selectedIdRef.current === selectedId && !cached) {
          window.setTimeout(() => titleRef.current?.focus(), 0);
        }
      })
      .catch((reason) => {
        if (selectedIdRef.current === selectedId) {
          setLoadingEvents(false);
          setError(
            reason?.response?.data?.detail?.message ||
              reason?.message ||
              t("无法加载扫描详情"),
          );
        }
      })
      .finally(() => {
        if (selectedIdRef.current === selectedId) setLoading(false);
      });
  }, [loadSelected, refreshChangedScan, selectedId, t]);

  useEffect(() => {
    if (scope || typeof EventSource === "undefined") return undefined;
    const source = new EventSource("/api/event", { withCredentials: true });
    source.onopen = () => setConnection("connected");
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event.type !== "code-security.scan.changed") return;
        const properties = event.properties || {};
        const changedScanId = String(properties.scanId || "");
        if (!changedScanId) return;
        const activeScanId = selectedIdRef.current;
        if (changedScanId !== activeScanId) {
          scheduleListRefresh(changedScanId);
          return;
        }
        if (Number(properties.latestEventSeq || 0) <= latestSeqRef.current)
          return;
        refreshChangedScan(changedScanId).catch(() =>
          setConnection("reconnecting"),
        );
      } catch {
        return;
      }
    };
    source.onerror = () => setConnection("reconnecting");
    return () => source.close();
  }, [refreshChangedScan, scheduleListRefresh]);

  useEffect(() => {
    if (!scope && user?.role !== "admin") return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await reloadList();
        if (disposed) return;
        const id = selectedIdRef.current;
        if (id) await refreshChangedScan(id);
        else if (scope && next[0]) replaceSelection(next[0].scan_id);
        if (!disposed) setConnection("connected");
      } catch {
        if (!disposed) setConnection("reconnecting");
      } finally {
        if (!disposed) timer = setTimeout(poll, 2000);
      }
    };
    timer = setTimeout(poll, 2000);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [
    scope?.batchId,
    scope?.taskId,
    reloadList,
    refreshChangedScan,
    replaceSelection,
  ]);

  const loadOlderEvents = async () => {
    const scanId = selectedIdRef.current;
    const beforeSeq = events[0]?.seq;
    if (!scanId || !beforeSeq || loadingOlderEvents) return;
    setLoadingOlderEvents(true);
    try {
      const page = await getEarlierEvents(scanId, beforeSeq);
      if (selectedIdRef.current !== scanId) return;
      setEvents((current) => {
        const nextEvents = mergeAuditEvents(page.items, current);
        const cached = scanViewCacheRef.current.get(scanId);
        if (cached) {
          rememberScanView(scanViewCacheRef.current, scanId, {
            ...cached,
            events: nextEvents,
            hasOlderEvents: page.hasMore,
          });
        }
        return nextEvents;
      });
      setHasOlderEvents(page.hasMore);
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          t("无法加载更早的可信事件"),
      );
    } finally {
      if (selectedIdRef.current === scanId) setLoadingOlderEvents(false);
    }
  };

  useEffect(() => {
    if (connection === "connected" || !selectedId) return undefined;
    const timer = window.setInterval(() => {
      refreshChangedScan(selectedId)
        .then(() => setConnection("connected"))
        .catch(() => setConnection("failed"));
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [connection, refreshChangedScan, selectedId]);

  const changeArtifact = (artifact: string) => {
    setActiveArtifact(artifact);
    const params = new URLSearchParams(window.location.search);
    params.set("artifact", artifact);
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}?${params.toString()}`,
    );
  };

  const handleCancel = async () => {
    if (
      !detail ||
      detail.scan.scan_id !== selectedIdRef.current ||
      !window.confirm(t("确定取消本次审计吗？已产生的中间数据会保留。"))
    )
      return;
    setCancelling(true);
    try {
      const nextDetail = await cancelScan(detail.scan.scan_id);
      const scanId = nextDetail.scan.scan_id;
      authoritativeDetailsRef.current.set(scanId, nextDetail);
      const cached = scanViewCacheRef.current.get(scanId);
      if (cached) {
        rememberScanView(scanViewCacheRef.current, scanId, {
          ...cached,
          detail: nextDetail,
        });
      }
      if (selectedIdRef.current === scanId) setDetail(nextDetail);
      await reloadList();
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          t("取消审计失败"),
      );
    } finally {
      setCancelling(false);
    }
  };

  const handleCreated = (nextDetail: ScanDetail) => {
    setDrawerOpen(false);
    selectScan(nextDetail.scan.scan_id);
    reloadList().catch((reason) =>
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          t("审计已创建，但扫描列表刷新失败"),
      ),
    );
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError("");
    try {
      if (target.batch_id && target.task_id) {
        await deleteBatchTask(target.batch_id, target.task_id);
        deletedScanIdsRef.current.add(target.scan_id);
        setBatchRecords((items) =>
          items.filter((item) => item.scan_id !== target.scan_id),
        );
        setDeleteTarget(null);
        setLiveMessage(
          t("已删除 {{name}} 的审计记录。", { name: target.display_name }),
        );
        if (
          scope?.batchId === target.batch_id &&
          scope?.taskId === target.task_id
        ) {
          const next = records.find((item) => item.scan_id !== target.scan_id);
          if (next) selectRecord(next.scan_id);
          else {
            window.history.replaceState({}, "", window.location.pathname);
            window.dispatchEvent(new PopStateEvent("popstate"));
          }
        } else
          window.setTimeout(
            () =>
              document
                .querySelector<HTMLElement>(".cs-audit-open")
                ?.focus(),
            0,
          );
        return;
      }
      await deleteGlobalScan(target.scan_id);
      deletedScanIdsRef.current.add(target.scan_id);
      authoritativeDetailsRef.current.delete(target.scan_id);
      scanViewCacheRef.current.delete(target.scan_id);
      initialLoadTasksRef.current.delete(target.scan_id);
      const remaining = scans.filter((scan) => scan.scan_id !== target.scan_id);
      setScans(remaining);
      setDeleteTarget(null);
      setLiveMessage(
        t("已删除 {{name}} 的审计记录。", { name: target.display_name }),
      );

      if (!scope && selectedIdRef.current === target.scan_id) {
        refreshQueuedRef.current.delete(target.scan_id);
        queuedScanRefreshRef.current.delete(target.scan_id);
        const nextScan = remaining[0];
        if (nextScan) {
          replaceSelection(nextScan.scan_id);
        } else {
          selectedIdRef.current = null;
          setSelectedId(null);
          setDetail(null);
          setEvents([]);
          setLoading(false);
          setLoadingEvents(false);
          setInspectorOpen(false);
          latestSeqRef.current = 0;
          const params = new URLSearchParams(window.location.search);
          params.delete("scan_id");
          params.delete("artifact");
          const query = params.toString();
          window.history.replaceState(
            {},
            "",
            `${window.location.pathname}${query ? `?${query}` : ""}`,
          );
        }
      } else {
        window.setTimeout(
          () =>
            document
              .querySelector<HTMLElement>(".cs-audit-open")
              ?.focus(),
          0,
        );
      }
    } catch (reason: any) {
      setDeleteError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          t("删除审计失败"),
      );
    } finally {
      setDeleting(false);
    }
  };

  if (
    loading &&
    !detail &&
    !hasPresentedDetailRef.current &&
    !selectedId &&
    !scope
  ) {
    return showSkeleton ? (
      <WorkspaceSkeleton />
    ) : (
      <main className="code-security-workspace" aria-busy="true">
        <span className="cs-visually-hidden">
          {t("正在加载代码审计工作区")}
        </span>
      </main>
    );
  }

  const batchScanIds = new Set(batchRecords.map((item) => item.scan_id));
  const records = mergeScans(scans, batchRecords).filter((record) => !record.workspace_ref || !removedProjectIds.has(record.workspace_ref));
  const selectedRecord = scope
    ? batchRecords.find(
        (item) =>
          item.batch_id === scope.batchId && item.task_id === scope.taskId,
      )
    : undefined;
  const selectedRecordId = selectedRecord?.scan_id || selectedId;
  const selectRecord = (recordId: string) => {
    const record = records.find((item) => item.scan_id === recordId);
    if (!record) return;
    if (!record.batch_id && !scope) {
      selectScan(recordId);
      return;
    }
    const params = new URLSearchParams({ view: "audits" });
    if (record.batch_id && record.task_id) {
      params.set("batch_id", record.batch_id);
      params.set("task_id", record.task_id);
      if (record.audit_scan_id) params.set("scan_id", record.audit_scan_id);
    } else params.set("scan_id", recordId);
    window.history.pushState({}, "", `${window.location.pathname}?${params}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };

  const projectId = initialParams.get("project_id");
  const selectedProject = projects.find((project) => project.id === projectId);
  const taskProjectId =
    detail?.scan.workspace_ref ||
    records.find((record) => record.scan_id === selectedRecordId)
      ?.workspace_ref ||
    projectId;
  const taskProject = projects.find((project) => project.id === taskProjectId);
  const finalFindingMetric = detail ? deriveFinalFindingMetric(detail) : null;
  const executionPhase = { phase: detail?.scan.current_phase || "" };
  const executionGroup = phaseGroups.find(group => group.id === phaseGroupId(executionPhase));
  const executionLabel = executionPhase.phase
    ? `${executionGroup ? `${t(executionGroup.label)} / ` : ""}${t(phaseDisplayLabel(executionPhase, detail ?? undefined))}`
    : t("等待阶段信息");

  return (
    <main
      className={`code-security-workspace cs-workbench${!selectedId && !scope ? " cs-home-view" : " cs-task-view"}`}
    >
      <div
        className="cs-live-region"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {liveMessage}
      </div>
      {!selectedId && !scope && error && (
        <div className="cs-page-error" role="alert">
          {error}
        </div>
      )}
      {!selectedId && !scope && initialParams.get("view") === "project" ? (
        selectedProject ? (
          <AuditTaskList
            canManage={user?.role === "admin"}
            onDelete={openDeleteDialog}
            onPrefetch={(id) => {
              if (!scope && !batchScanIds.has(id)) prefetchScan(id);
            }}
            key={selectedProject.id}
            project={selectedProject}
            scans={records.filter(
              (record) => record.workspace_ref === selectedProject.id,
            )}
            onSelect={selectRecord}
            onHome={returnHome}
            onNewAudit={openDrawer}
            canCreate={canCreate}
            hasMore={Boolean(scanCursor)}
            loadingMore={loadingMoreScans}
            onLoadMore={loadMoreScans}
          />
        ) : (
          <section className="cs-project-home">
            <button className="cs-home-link" type="button" onClick={returnHome}>
              {t("← 代码审计首页")}
            </button>
            <p role="status">
              {t(
                projectsLoading
                  ? "正在加载项目…"
                  : "项目不存在或当前不可访问。",
              )}
            </p>
          </section>
        )
      ) : !selectedId && !scope && initialParams.get("view") === "audits" ? (
        <AuditTaskList
          canManage={user?.role === "admin"}
          onDelete={openDeleteDialog}
          onPrefetch={(id) => {
            if (!scope && !batchScanIds.has(id)) prefetchScan(id);
          }}
          scans={records}
          onSelect={selectRecord}
          onHome={returnHome}
          onNewAudit={openDrawer}
          canCreate={canCreate}
          hasMore={Boolean(scanCursor)}
          loadingMore={loadingMoreScans}
          onLoadMore={loadMoreScans}
        />
      ) : !selectedId && !scope ? (
        <AuditHome
          projects={projects}
          scans={records}
          canCreate={canCreate}
          onNewAudit={openDrawer}
          onProjectSelect={openProject}
          hasMore={Boolean(scanCursor)}
          onLoadMore={loadMoreScans}
          loadingMore={loadingMoreScans}
          onProjectDeleted={(id) => {
            setRemovedProjectIds((current) => new Set([...current, id]));
            setProjects((current) => current.filter((project) => project.id !== id));
            setScans((current) => current.filter((scan) => scan.workspace_ref !== id));
            setBatchRecords((current) => current.filter((scan) => scan.workspace_ref !== id));
          }}
          onProjectAdded={(project) =>
            setProjects((current) => [
              ...current.filter((p) => p.id !== project.id),
              project,
            ])
          }
        />
      ) : null}

      <section className="cs-main-column" hidden={!selectedId && !scope}>
        <nav className="cs-breadcrumbs" aria-label={t("页面导航")}>
          <button type="button" className="cs-home-link" onClick={returnHome}>
            {t("首页")}
          </button>
          <span aria-hidden="true">/</span>
          <button
            type="button"
            className="cs-home-link"
            onClick={() =>
              taskProject ? openProject(taskProject.id) : returnAuditList()
            }
          >
            {t("返回任务列表")}
          </button>
        </nav>
        {connection !== "connected" && (
          <div
            className={`cs-connection cs-connection--${connection}`}
            role="status"
          >
            <Icon name="warning" />
            <span>
              {connection === "failed"
                ? t("实时连接仍未恢复，当前内容可继续查看。")
                : t("实时连接已中断，正在重连。当前内容仍可查看。")}
            </span>
            {connection === "failed" && selectedId && (
              <button
                type="button"
                onClick={() => loadSelected(selectedId, latestSeqRef.current)}
              >
                {t("立即重试")}
              </button>
            )}
          </div>
        )}
        {error && (
          <div className="cs-page-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError("")}>
              {t("关闭")}
            </button>
          </div>
        )}
        {!detail ? (
          loading ? (
            <ScanDetailSkeleton />
          ) : scope ? (
            <section className="cs-empty" role="status">
              <h2>
                {t("任务")} {scope.taskId}
              </h2>
              <p>
                {t(
                  lifecycleLabels[
                    selectedRecord?.lifecycle_status || "preparing"
                  ] || "准备中",
                )}
              </p>
              {selectedRecord?.failure_summary && (
                <p>{selectedRecord.failure_summary}</p>
              )}
              {selectedRecord &&
                ["preparing", "running"].includes(
                  selectedRecord.lifecycle_status,
                ) && (
                  <button
                    className="cs-button"
                    disabled={cancelling}
                    onClick={async () => {
                      setCancelling(true);
                      try {
                        await cancelBatchTask(scope.batchId, scope.taskId);
                      } catch (reason: any) {
                        setError(reason.message);
                        setCancelling(false);
                      }
                    }}
                  >
                    {t(cancelling ? "正在取消" : "取消任务")}
                  </button>
                )}
            </section>
          ) : (
            <EmptyWorkspace canCreate={canCreate} onNewAudit={openDrawer} />
          )
        ) : (
          <>
            <header className="cs-scan-header">
              <div className="cs-scan-title">
                <div className="cs-scan-title__line">
                  <h1 ref={titleRef} tabIndex={-1}>
                    {detail.target.display_name}
                  </h1>
                  <StatusBadge status={detail.scan.lifecycle_status} />
                  <span className="cs-mode-tag">
                    {isCyberGymValidation({ phase: "dynamic_validation" }, detail)
                      ? t("CyberGym 审计")
                      : detail.scan.dynamic_enabled ? t("动态审计") : t("静态审计")}
                  </span>
                </div>
                <div className="cs-header-meta">
                  <span>
                    {t("当前执行")}：{executionLabel}
                  </span>
                  <ElapsedTime
                    startedAt={detail.scan.started_at}
                    finishedAt={detail.scan.finished_at}
                    initialMs={detail.scan.elapsed_ms}
                    running={["preparing", "running", "cancelling"].includes(
                      detail.scan.lifecycle_status,
                    )}
                  />
                  <code title={detail.scan.scan_id}>
                    {shortId(detail.scan.scan_id, 18)}
                  </code>
                  <code title={detail.target.tree_digest}>
                    tree {shortId(detail.target.tree_digest, 10)}
                  </code>
                </div>
              </div>
              <div className="cs-header-actions">
                {detail.scan.can_cancel && (
                  <button
                    className="cs-button cs-button--danger"
                    type="button"
                    onClick={handleCancel}
                    disabled={cancelling}
                  >
                    {cancelling ? t("正在取消…") : t("取消审计")}
                  </button>
                )}
                {detail.scan.lifecycle_status === "completed" &&
                  detail.scan.integrity_status === "valid" && (
                    <a
                      className="cs-button cs-button--secondary"
                      href={downloadUrl(detail.scan.scan_id, "report.md")}
                    >
                      <Icon name="download" />
                      {t("下载报告")}
                    </a>
                  )}
                <button
                  className="cs-button cs-button--secondary cs-inspector-trigger"
                  type="button"
                  onClick={openInspector}
                >
                  <Icon name="panel" />
                  {t("查看产物")}
                </button>
                <button type="button" className="cs-button cs-button--secondary" aria-expanded={workbenchTask === detail.scan.scan_id} onClick={() => setWorkbenchTask(detail.scan.scan_id)}>
                  <Icon name="panel" />{t("工作台")}
                </button>
              </div>
            </header>
            <div className="cs-audit-metrics" aria-label={t("审计指标")}>
              <div
                className={`cs-metric-findings${(finalFindingMetric?.count ?? 0) > 0 ? " has-findings" : ""}`}
              >
                <span>{t("漏洞数量")}</span>
                <strong>{finalFindingMetric?.count ?? "—"}</strong>
                <small>
                  {t(finalFindingMetric?.basis || "审计完成后确定")}
                </small>
              </div>
              <div>
                <span>{t("候选问题")}</span>
                <strong>{detail.counts.candidates ?? "—"}</strong>
                <small>{t("不等同于已确认漏洞")}</small>
              </div>
              <div>
                <span>{t("快照文件")}</span>
                <strong>{detail.target.file_count}</strong>
                <small>{t("当前审计范围")}</small>
              </div>
              <div>
                <span>{t("执行记录")}</span>
                <strong>{detail.phaseRuns.filter(phase => phase.status !== "skipped").length}</strong>
                <small>{t("包含子阶段与各轮记录")}</small>
              </div>
              <div>
                <span>{t("覆盖状态")}</span>
                <strong className="cs-metric-text">
                  {t(
                    (
                      {
                        complete: "完整",
                        partial: "部分覆盖",
                        pending: "待评估",
                        unknown: "未知",
                      } as Record<string, string>
                    )[detail.coverageSummary.completeness] ||
                      detail.coverageSummary.completeness,
                  )}
                </strong>
                <small>{t("以证据覆盖结果为准")}</small>
              </div>
            </div>
            {detail.scan.failure_summary && (
              <section
                className="cs-failure-card"
                aria-labelledby="failure-title"
              >
                <Icon name="error" />
                <div>
                  <h2 id="failure-title">{t("审计未正常完成")}</h2>
                  <p>{detail.scan.failure_summary}</p>
                  <code>{detail.scan.failure_code}</code>
                </div>
              </section>
            )}
            <PhaseWorkspace
              key={detail.scan.scan_id}
              requestedPhase={requestedPhase}
              detail={detail}
              onOpenArtifacts={(kind) => { if (kind) changeArtifact(kind); openInspector(); }}
              scanId={detail.scan.scan_id}
              phases={detail.phaseRuns}
              events={events}
              workers={detail.workers || []}
              currentPhase={detail.scan.current_phase}
              snapshotBoundary={detail.target}
              artifactBundle={{
                artifacts: detail.artifacts,
                integrityStatus: detail.scan.integrity_status,
              }}
              dynamicValidationStatus={String(
                detail.dynamicValidation?.status || "",
              )}
              finalFindingCount={finalFindingMetric?.count}
              finalFindingBasis={finalFindingMetric?.basis}
              hasOlderEvents={hasOlderEvents}
              loadingEvents={loadingEvents}
              loadingOlderEvents={loadingOlderEvents}
              onLoadOlderEvents={loadOlderEvents}
            />
            {WorkbenchPanel && <WorkbenchPanel
              key={`workbench-${detail.scan.scan_id}`}
              open={workbenchTask === detail.scan.scan_id}
              title={t("审计工作台")} closeLabel={t("关闭工作台")} resizeLabel={t("调整工作台宽度")}
              onClose={() => setWorkbenchTask(null)}
            >
            <AuditConversation
              key={`conversation-${detail.scan.scan_id}`}
              detail={detail}
              onPhase={(id) => {
                setRequestedPhase({ id });
                document
                  .querySelector(".cs-execution")
                  ?.scrollIntoView?.({ block: "start" });
              }}
              onArtifact={(kind) => {
                changeArtifact(kind);
                openInspector();
              }}
            />
            </WorkbenchPanel>}
          </>
        )}
      </section>

      {detail && (
        <ArtifactInspector
          key={detail.scan.scan_id}
          drawerOnly
          detail={detail}
          activeTab={activeArtifact}
          onTabChange={changeArtifact}
          open={inspectorOpen}
          onClose={closeInspector}
        />
      )}
      {detail && inspectorOpen && (
        <button
          type="button"
          className="cs-inspector-scrim"
          aria-label={t("关闭产物检查器")}
          onClick={closeInspector}
        />
      )}
      <NewAuditDrawer
        open={drawerOpen}
        projects={projects}
        initialProjectId={
          !selectedId && initialParams.get("view") === "project"
            ? selectedProject?.id
            : undefined
        }
        onClose={closeDrawer}
        onCreated={handleCreated}
      />
      <DeleteScanDialog
        scan={deleteTarget}
        deleting={deleting}
        error={deleteError}
        onClose={closeDeleteDialog}
        onConfirm={handleDelete}
      />
    </main>
  );
}

function summaryFromDetail(detail: ScanDetail): ScanSummary {
  const finalFindingMetric = deriveFinalFindingMetric(detail);
  return {
    scan_id: detail.scan.scan_id,
    display_name: detail.target.display_name,
    lifecycle_status: detail.scan.lifecycle_status,
    current_phase: detail.scan.current_phase,
    dynamic_enabled: detail.scan.dynamic_enabled,
    created_at: detail.scan.created_at,
    finished_at: detail.scan.finished_at,
    failure_summary: detail.scan.failure_summary,
    workspace_ref: detail.scan.workspace_ref,
    candidate_count: detail.counts.candidates,
    final_finding_count: finalFindingMetric.count,
    final_finding_basis: finalFindingMetric.basis,
  };
}

function mergeScans(
  current: ScanSummary[],
  incoming: ScanSummary[],
): ScanSummary[] {
  const items = new Map(current.map((scan) => [scan.scan_id, scan]));
  incoming.forEach((scan) => items.set(scan.scan_id, scan));
  return [...items.values()].sort(
    (left, right) =>
      right.created_at.localeCompare(left.created_at) ||
      right.scan_id.localeCompare(left.scan_id),
  );
}

function mergeAuditEvents(
  current: AuditEvent[],
  incoming: AuditEvent[],
): AuditEvent[] {
  return Array.from(
    new Map([...current, ...incoming].map((item) => [item.seq, item])).values(),
  ).sort((left, right) => left.seq - right.seq);
}

function rememberScanView(
  cache: Map<string, ScanViewCacheEntry>,
  scanId: string,
  entry: ScanViewCacheEntry,
) {
  cache.delete(scanId);
  cache.set(scanId, entry);
  while (cache.size > MAX_CACHED_SCAN_VIEWS) {
    const oldest = cache.keys().next().value;
    if (typeof oldest !== "string") break;
    cache.delete(oldest);
  }
}

function isTerminalScan(detail: ScanDetail): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(
    detail.scan.lifecycle_status,
  );
}

function useSdkUser(): any {
  const sdk = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
  return sdk?.useCurrentUser ? sdk.useCurrentUser() : null;
}

function EmptyWorkspace({
  canCreate,
  onNewAudit,
}: {
  canCreate: boolean;
  onNewAudit: () => void;
}) {
  const { t } = useCodeSecurityI18n();
  return (
    <section className="cs-empty-state">
      <span className="cs-empty-state__icon">
        <Icon name="shield" />
      </span>
      <h1>{t("还没有代码审计")}</h1>
      <p>
        {t(
          "创建一次基于不可变源码快照的安全审计，查看威胁模型、验证过程和最终报告。",
        )}
      </p>
      {canCreate ? (
        <button
          className="cs-button cs-button--secondary"
          type="button"
          onClick={onNewAudit}
        >
          <Icon name="plus" />
          {t("新建审计")}
        </button>
      ) : (
        <p className="cs-helper">{t("当前版本仅允许管理员启动审计。")}</p>
      )}
    </section>
  );
}

function WorkspaceSkeleton() {
  const { t } = useCodeSecurityI18n();
  return (
    <main
      className="code-security-workspace cs-workspace-skeleton"
      aria-label={t("正在加载代码审计工作区")}
    >
      <aside>
        <span />
        <span />
        <span />
        <span />
      </aside>
      <section>
        <span />
        <span />
        <span />
        <span />
      </section>
      <aside>
        <span />
        <span />
        <span />
      </aside>
    </main>
  );
}

function ScanDetailSkeleton() {
  const { t } = useCodeSecurityI18n();
  return (
    <section
      className="cs-skeleton-stack cs-detail-skeleton"
      aria-label={t("正在加载扫描详情")}
      aria-busy="true"
    >
      <span />
      <span />
      <span />
      <span />
    </section>
  );
}

function useWorkspaceStyles() {
  useInsertionEffect(() => {
    const element = document.createElement("style");
    element.dataset.flocksCodeSecurityWorkspace = "true";
    element.textContent = styles;
    document.head.append(element);
    return () => element.remove();
  }, []);
}
