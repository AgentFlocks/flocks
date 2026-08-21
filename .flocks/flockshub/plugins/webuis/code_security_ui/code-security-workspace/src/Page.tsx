import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelScan,
  getEarlierEvents,
  getEvents,
  getRecentEvents,
  getScan,
  listProjects,
  listScans,
} from "./api";
import { ArtifactInspector } from "./components/ArtifactInspector";
import { ElapsedTime } from "./components/ElapsedTime";
import { NewAuditDrawer } from "./components/NewAuditDrawer";
import { PhaseWorkspace } from "./components/PhaseWorkspace";
import { ScanListPanel } from "./components/ScanListPanel";
import { StatusBadge } from "./components/StatusBadge";
import { Icon } from "./icons";
import { phaseLabels, shortId } from "./labels";
import type {
  AuditEvent,
  ProjectSummary,
  ScanDetail,
  ScanSummary,
} from "./types";
import styles from "./styles";

export default function Page() {
  const user = useSdkUser();
  const canCreate = user?.role === "admin";
  const initialParams = new URLSearchParams(window.location.search);
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialParams.get("scan_id"),
  );
  const [detail, setDetail] = useState<ScanDetail | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showSkeleton, setShowSkeleton] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState<
    "connected" | "reconnecting" | "failed"
  >("connected");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [activeArtifact, setActiveArtifact] = useState(
    initialParams.get("artifact") || "overview",
  );
  const [liveMessage, setLiveMessage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [scanPanelOpen, setScanPanelOpen] = useState(false);
  const [scanCursor, setScanCursor] = useState<string | null>(null);
  const [loadingMoreScans, setLoadingMoreScans] = useState(false);
  const [hasOlderEvents, setHasOlderEvents] = useState(false);
  const [loadingOlderEvents, setLoadingOlderEvents] = useState(false);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const latestSeqRef = useRef(0);
  const selectedIdRef = useRef<string | null>(selectedId);
  const refreshTasksRef = useRef(new Map<string, Promise<void>>());
  const refreshQueuedRef = useRef(new Set<string>());
  const drawerOpenerRef = useRef<HTMLElement | null>(null);
  const inspectorOpenerRef = useRef<HTMLElement | null>(null);
  const scanPanelOpenerRef = useRef<HTMLElement | null>(null);
  const loadingMoreScansRef = useRef(false);
  const listRefreshTimerRef = useRef<number | null>(null);
  const queuedScanRefreshRef = useRef(new Set<string>());

  const openDrawer = useCallback(() => {
    drawerOpenerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setDrawerOpen(true);
  }, []);

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

  const openScanPanel = useCallback(() => {
    scanPanelOpenerRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setScanPanelOpen(true);
  }, []);

  const closeScanPanel = useCallback(() => {
    setScanPanelOpen(false);
    window.setTimeout(() => scanPanelOpenerRef.current?.focus(), 0);
  }, []);

  const applySelection = useCallback(
    (scanId: string, updateHistory: boolean) => {
      selectedIdRef.current = scanId;
      setSelectedId(scanId);
      setDetail(null);
      setEvents([]);
      setError("");
      setLoading(true);
      setInspectorOpen(false);
      setHasOlderEvents(false);
      setLoadingOlderEvents(false);
      latestSeqRef.current = 0;
      if (updateHistory) {
        const params = new URLSearchParams(window.location.search);
        params.set("scan_id", scanId);
        window.history.pushState(
          {},
          "",
          `${window.location.pathname}?${params.toString()}`,
        );
      }
    },
    [],
  );

  const selectScan = useCallback(
    (scanId: string) => applySelection(scanId, true),
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

  const loadSelected = useCallback(async (scanId: string, afterSeq = 0) => {
    const [nextDetail, page] = await Promise.all([
      getScan(scanId),
      afterSeq ? getEvents(scanId, afterSeq) : getRecentEvents(scanId),
    ]);
    const eventItems: AuditEvent[] = page.items;
    setScans((current) => mergeScans(current, [summaryFromDetail(nextDetail)]));
    if (selectedIdRef.current !== scanId)
      return { detail: nextDetail, hasMore: false };
    setDetail(nextDetail);
    if (!afterSeq) setHasOlderEvents(page.hasMore);
    const deliveredSeq = eventItems.length
      ? eventItems[eventItems.length - 1].seq
      : afterSeq;
    latestSeqRef.current = Math.max(latestSeqRef.current, deliveredSeq);
    setEvents((current) => {
      const merged = afterSeq ? [...current, ...eventItems] : eventItems;
      return Array.from(
        new Map(merged.map((item) => [item.seq, item])).values(),
      ).sort((a, b) => a.seq - b.seq);
    });
    if (eventItems.length) {
      setLiveMessage(
        `${phaseLabels[nextDetail.scan.current_phase || ""] || "代码审计"}新增 ${eventItems.length} 条事件。`,
      );
    }
    return {
      detail: nextDetail,
      hasMore: Boolean(
        (afterSeq && page.hasMore) ||
        deliveredSeq !== Number(nextDetail.scan.latest_event_seq || 0),
      ),
    };
  }, []);

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
          const result = await loadSelected(scanId, latestSeqRef.current);
          if (result.hasMore) refreshQueuedRef.current.add(scanId);
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

  const reloadList = useCallback(async () => {
    const page = await listScans();
    setScans((current) => mergeScans(current, page.items));
    setScanCursor(page.nextCursor);
    return page.items;
  }, []);

  const loadMoreScans = useCallback(async () => {
    if (!scanCursor || loadingMoreScansRef.current) return;
    loadingMoreScansRef.current = true;
    setLoadingMoreScans(true);
    try {
      const page = await listScans(scanCursor);
      setScans((current) => mergeScans(current, page.items));
      setScanCursor(page.nextCursor);
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          "无法加载更多审计记录",
      );
    } finally {
      loadingMoreScansRef.current = false;
      setLoadingMoreScans(false);
    }
  }, [scanCursor]);

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
            .map(summaryFromDetail);
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
      const scanId = new URLSearchParams(window.location.search).get("scan_id");
      if (scanId && scanId !== selectedIdRef.current)
        applySelection(scanId, false);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [applySelection]);

  useEffect(() => {
    const timer = window.setTimeout(() => setShowSkeleton(true), 180);
    Promise.all([reloadList(), listProjects()])
      .then(([nextScans, nextProjects]) => {
        setProjects(nextProjects);
        const currentSelection = selectedIdRef.current;
        const candidate = currentSelection || nextScans[0]?.scan_id || null;
        if (candidate && candidate !== currentSelection)
          replaceSelection(candidate);
        if (!candidate) setLoading(false);
      })
      .catch((reason) => {
        setError(
          reason?.response?.data?.detail?.message ||
            reason?.message ||
            "无法加载代码审计工作区",
        );
        setLoading(false);
      });
    return () => window.clearTimeout(timer);
  }, [reloadList, replaceSelection]);

  useEffect(() => {
    if (!selectedId) return;
    setLoading(true);
    loadSelected(selectedId)
      .then(({ hasMore }) => {
        if (selectedIdRef.current === selectedId && hasMore) {
          refreshChangedScan(selectedId).catch(() =>
            setConnection("reconnecting"),
          );
        }
        if (selectedIdRef.current === selectedId) {
          window.setTimeout(() => titleRef.current?.focus(), 0);
        }
      })
      .catch((reason) => {
        if (selectedIdRef.current === selectedId)
          setError(
            reason?.response?.data?.detail?.message ||
              reason?.message ||
              "无法加载扫描详情",
          );
      })
      .finally(() => {
        if (selectedIdRef.current === selectedId) setLoading(false);
      });
  }, [loadSelected, refreshChangedScan, selectedId]);

  useEffect(() => {
    if (!selectedId || typeof EventSource === "undefined") return undefined;
    const source = new EventSource("/api/event", { withCredentials: true });
    source.onopen = () => setConnection("connected");
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event.type !== "code-security.scan.changed") return;
        const properties = event.properties || {};
        const changedScanId = String(properties.scanId || "");
        if (!changedScanId) return;
        if (changedScanId !== selectedId) {
          scheduleListRefresh(changedScanId);
          return;
        }
        if (Number(properties.latestEventSeq || 0) <= latestSeqRef.current)
          return;
        refreshChangedScan(selectedId).catch(() =>
          setConnection("reconnecting"),
        );
      } catch {
        return;
      }
    };
    source.onerror = () => setConnection("reconnecting");
    return () => source.close();
  }, [refreshChangedScan, scheduleListRefresh, selectedId]);

  const loadOlderEvents = async () => {
    const scanId = selectedIdRef.current;
    const beforeSeq = events[0]?.seq;
    if (!scanId || !beforeSeq || loadingOlderEvents) return;
    setLoadingOlderEvents(true);
    try {
      const page = await getEarlierEvents(scanId, beforeSeq);
      if (selectedIdRef.current !== scanId) return;
      setEvents((current) =>
        Array.from(
          new Map(
            [...page.items, ...current].map((item) => [item.seq, item]),
          ).values(),
        ).sort((a, b) => a.seq - b.seq),
      );
      setHasOlderEvents(page.hasMore);
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          "无法加载更早的可信事件",
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
      !window.confirm("确定取消本次审计吗？已产生的中间数据会保留。")
    )
      return;
    setCancelling(true);
    try {
      const nextDetail = await cancelScan(detail.scan.scan_id);
      if (selectedIdRef.current === nextDetail.scan.scan_id)
        setDetail(nextDetail);
      await reloadList();
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail?.message ||
          reason?.message ||
          "取消审计失败",
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
          "审计已创建，但扫描列表刷新失败",
      ),
    );
  };

  if (loading && !detail) {
    return (
      <>
        <style>{styles}</style>
        {showSkeleton ? (
          <WorkspaceSkeleton />
        ) : (
          <main className="code-security-workspace" aria-busy="true">
            <span className="cs-visually-hidden">正在加载代码审计工作区</span>
          </main>
        )}
      </>
    );
  }

  return (
    <main className="code-security-workspace">
      <style>{styles}</style>
      <div
        className="cs-live-region"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {liveMessage}
      </div>
      <ScanListPanel
        scans={scans}
        selectedId={selectedId}
        onSelect={selectScan}
        onNewAudit={openDrawer}
        canCreate={canCreate}
        open={scanPanelOpen}
        onClose={closeScanPanel}
        hasMore={Boolean(scanCursor)}
        loadingMore={loadingMoreScans}
        onLoadMore={loadMoreScans}
      />
      {scanPanelOpen && (
        <button
          type="button"
          className="cs-scan-panel-scrim"
          aria-label="关闭审计列表"
          onClick={closeScanPanel}
        />
      )}

      <section className="cs-main-column">
        {connection !== "connected" && (
          <div
            className={`cs-connection cs-connection--${connection}`}
            role="status"
          >
            <Icon name="warning" />
            <span>
              {connection === "failed"
                ? "实时连接仍未恢复，当前内容可继续查看。"
                : "实时连接已中断，正在重连。当前内容仍可查看。"}
            </span>
            {connection === "failed" && selectedId && (
              <button
                type="button"
                onClick={() => loadSelected(selectedId, latestSeqRef.current)}
              >
                立即重试
              </button>
            )}
          </div>
        )}
        {error && (
          <div className="cs-page-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError("")}>
              关闭
            </button>
          </div>
        )}
        {!detail ? (
          <EmptyWorkspace canCreate={canCreate} onNewAudit={openDrawer} />
        ) : (
          <>
            <header className="cs-scan-header">
              <div className="cs-mobile-scan-select">
                <label htmlFor="mobile-scan-select">切换审计</label>
                <select
                  id="mobile-scan-select"
                  value={detail.scan.scan_id}
                  onChange={(event) => selectScan(event.target.value)}
                >
                  {scans.map((scan) => (
                    <option key={scan.scan_id} value={scan.scan_id}>
                      {scan.display_name} · {scan.lifecycle_status}
                    </option>
                  ))}
                </select>
                {scanCursor && (
                  <button
                    type="button"
                    className="cs-button cs-button--secondary cs-mobile-load-more"
                    onClick={loadMoreScans}
                    disabled={loadingMoreScans}
                  >
                    {loadingMoreScans ? "正在加载…" : "加载更多审计记录"}
                  </button>
                )}
              </div>
              <button
                className="cs-icon-button cs-scan-drawer-trigger"
                type="button"
                onClick={openScanPanel}
                aria-label="打开审计列表"
              >
                <Icon name="panel" />
              </button>
              <div className="cs-scan-title">
                <div className="cs-scan-title__line">
                  <h1 ref={titleRef} tabIndex={-1}>
                    {detail.target.display_name}
                  </h1>
                  <StatusBadge status={detail.scan.lifecycle_status} />
                  <span className="cs-mode-tag">
                    {detail.scan.dynamic_enabled ? "动态审计" : "静态审计"}
                  </span>
                </div>
                <div className="cs-header-meta">
                  <span>
                    {phaseLabels[detail.scan.current_phase || ""] ||
                      "等待阶段信息"}
                  </span>
                  <ElapsedTime
                    startedAt={detail.scan.started_at}
                    finishedAt={detail.scan.finished_at}
                    initialMs={detail.scan.elapsed_ms}
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
                {canCreate && (
                  <button
                    className="cs-button cs-button--primary cs-header-new-audit"
                    type="button"
                    onClick={openDrawer}
                  >
                    <Icon name="plus" />
                    新建审计
                  </button>
                )}
                {detail.scan.can_cancel && (
                  <button
                    className="cs-button cs-button--danger"
                    type="button"
                    onClick={handleCancel}
                    disabled={cancelling}
                  >
                    {cancelling ? "正在取消…" : "取消审计"}
                  </button>
                )}
                {detail.scan.lifecycle_status === "completed" &&
                  detail.scan.integrity_status === "valid" && (
                    <a
                      className="cs-button cs-button--primary"
                      href={`/api/code-security/v1/scans/${detail.scan.scan_id}/downloads/report.md`}
                    >
                      <Icon name="download" />
                      下载报告
                    </a>
                  )}
                <button
                  className="cs-button cs-button--secondary cs-inspector-trigger"
                  type="button"
                  onClick={openInspector}
                >
                  <Icon name="panel" />
                  查看产物
                </button>
              </div>
            </header>
            {detail.scan.failure_summary && (
              <section
                className="cs-failure-card"
                aria-labelledby="failure-title"
              >
                <Icon name="error" />
                <div>
                  <h2 id="failure-title">审计未正常完成</h2>
                  <p>{detail.scan.failure_summary}</p>
                  <code>{detail.scan.failure_code}</code>
                </div>
              </section>
            )}
            <PhaseWorkspace
              key={detail.scan.scan_id}
              phases={detail.phaseRuns}
              events={events}
              workers={detail.workers || []}
              currentPhase={detail.scan.current_phase}
              hasOlderEvents={hasOlderEvents}
              loadingOlderEvents={loadingOlderEvents}
              onLoadOlderEvents={loadOlderEvents}
            />
          </>
        )}
      </section>

      {detail && (
        <ArtifactInspector
          key={detail.scan.scan_id}
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
          aria-label="关闭产物检查器"
          onClick={closeInspector}
        />
      )}
      <NewAuditDrawer
        open={drawerOpen}
        projects={projects}
        onClose={closeDrawer}
        onCreated={handleCreated}
      />
    </main>
  );
}

function summaryFromDetail(detail: ScanDetail): ScanSummary {
  return {
    scan_id: detail.scan.scan_id,
    display_name: detail.target.display_name,
    lifecycle_status: detail.scan.lifecycle_status,
    current_phase: detail.scan.current_phase,
    dynamic_enabled: detail.scan.dynamic_enabled,
    created_at: detail.scan.created_at,
    finished_at: detail.scan.finished_at,
    failure_summary: detail.scan.failure_summary,
    candidate_count: detail.counts.candidates,
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
  return (
    <section className="cs-empty-state">
      <span className="cs-empty-state__icon">
        <Icon name="shield" />
      </span>
      <h1>还没有代码审计</h1>
      <p>
        创建一次基于不可变源码快照的安全审计，查看威胁模型、验证过程和最终报告。
      </p>
      {canCreate ? (
        <button
          className="cs-button cs-button--primary"
          type="button"
          onClick={onNewAudit}
        >
          <Icon name="plus" />
          新建审计
        </button>
      ) : (
        <p className="cs-helper">当前版本仅允许管理员启动审计。</p>
      )}
    </section>
  );
}

function WorkspaceSkeleton() {
  return (
    <main
      className="code-security-workspace cs-workspace-skeleton"
      aria-label="正在加载代码审计工作区"
    >
      <style>{styles}</style>
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
