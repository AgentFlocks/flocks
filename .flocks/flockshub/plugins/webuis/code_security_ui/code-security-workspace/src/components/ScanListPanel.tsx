import { useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../icons";
import { lifecycleLabels, phaseLabels, relativeTime } from "../labels";
import type { ScanSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

const SCAN_ROW_HEIGHT = 106;
const SCAN_OVERSCAN = 5;
const PREFETCH_DELAY_MS = 180;

export function ScanListPanel({
  scans,
  selectedId,
  onSelect,
  onNewAudit,
  canCreate,
  open,
  onClose,
  hasMore,
  loadingMore,
  onLoadMore,
  onDelete,
  onPrefetch,
}: {
  scans: ScanSummary[];
  selectedId: string | null;
  onSelect: (scanId: string) => void;
  onNewAudit: () => void;
  canCreate: boolean;
  open: boolean;
  onClose: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => Promise<void>;
  onDelete: (scan: ScanSummary, opener: HTMLButtonElement) => void;
  onPrefetch?: (scanId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [overlayLayout, setOverlayLayout] = useState(
    () => window.matchMedia?.("(max-width: 1023px)").matches ?? false,
  );
  const [mobileLayout, setMobileLayout] = useState(
    () => window.matchMedia?.("(max-width: 767px)").matches ?? false,
  );
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);
  const panelRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const listRef = useRef<HTMLElement>(null);
  const prefetchTimerRef = useRef<number | null>(null);
  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return scans.filter((scan) => {
      const matchesStatus =
        status === "all" || scan.lifecycle_status === status;
      const matchesQuery =
        !term ||
        scan.display_name.toLocaleLowerCase().includes(term) ||
        scan.scan_id.toLocaleLowerCase().includes(term);
      return matchesStatus && matchesQuery;
    });
  }, [query, scans, status]);
  const virtual = filtered.length > 100;
  const start = virtual
    ? Math.max(0, Math.floor(scrollTop / SCAN_ROW_HEIGHT) - SCAN_OVERSCAN)
    : 0;
  const end = virtual
    ? Math.min(
        filtered.length,
        start + Math.ceil(viewportHeight / SCAN_ROW_HEIGHT) + SCAN_OVERSCAN * 2,
      )
    : filtered.length;
  const visible = filtered.slice(start, end);

  const cancelScheduledPrefetch = () => {
    if (prefetchTimerRef.current === null) return;
    window.clearTimeout(prefetchTimerRef.current);
    prefetchTimerRef.current = null;
  };

  const schedulePrefetch = (scanId: string) => {
    cancelScheduledPrefetch();
    prefetchTimerRef.current = window.setTimeout(() => {
      prefetchTimerRef.current = null;
      onPrefetch?.(scanId);
    }, PREFETCH_DELAY_MS);
  };

  useEffect(
    () => () => {
      if (prefetchTimerRef.current !== null)
        window.clearTimeout(prefetchTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia("(max-width: 1023px)");
    const mobile = window.matchMedia("(max-width: 767px)");
    const update = () => {
      setOverlayLayout(media.matches);
      setMobileLayout(mobile.matches);
    };
    update();
    media.addEventListener?.("change", update);
    mobile.addEventListener?.("change", update);
    return () => {
      media.removeEventListener?.("change", update);
      mobile.removeEventListener?.("change", update);
    };
  }, []);

  useEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() =>
      setViewportHeight(list.clientHeight),
    );
    observer.observe(list);
    setViewportHeight(list.clientHeight || 600);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setScrollTop(0);
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [query, status]);

  useEffect(() => {
    if (!overlayLayout || mobileLayout || !open) return undefined;
    window.setTimeout(() => titleRef.current?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled])",
        ),
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === titleRef.current)
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileLayout, onClose, open, overlayLayout]);

  const hidden = overlayLayout && (!open || mobileLayout);
  const modal = overlayLayout && open && !mobileLayout;

  return (
    <aside
      ref={panelRef}
      className={`cs-scan-panel${open ? " is-open" : ""}`}
      aria-label="审计历史"
      aria-hidden={hidden ? true : undefined}
      aria-modal={modal ? true : undefined}
      role={modal ? "dialog" : undefined}
      inert={hidden ? true : undefined}
    >
      <div className="cs-panel-heading">
        <div>
          <p className="cs-eyebrow">代码安全</p>
          <h2 ref={titleRef} tabIndex={-1}>
            审计记录
          </h2>
        </div>
        <div className="cs-panel-heading__actions">
          <span className="cs-count">{scans.length}</span>
          <button
            className="cs-icon-button cs-scan-panel__close"
            type="button"
            onClick={onClose}
            aria-label="关闭审计列表"
          >
            <Icon name="close" />
          </button>
        </div>
      </div>
      {canCreate && (
        <button
          className="cs-button cs-button--secondary cs-button--full"
          type="button"
          onClick={onNewAudit}
        >
          <Icon name="plus" />
          新建审计
        </button>
      )}
      <label className="cs-search">
        <span className="cs-visually-hidden">搜索审计记录</span>
        <Icon name="search" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索目标或 scan_id"
        />
      </label>
      <label className="cs-filter-label">
        <span>状态筛选</span>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="running">运行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
          <option value="interrupted">已中断</option>
        </select>
      </label>
      <nav
        ref={listRef}
        className="cs-scan-list"
        aria-label="扫描列表"
        onScroll={(event) => {
          const element = event.currentTarget;
          setScrollTop(element.scrollTop);
          if (
            hasMore &&
            !loadingMore &&
            element.scrollHeight - element.scrollTop - element.clientHeight <
              120
          ) {
            void onLoadMore();
          }
        }}
      >
        <div
          className={virtual ? "cs-scan-list__virtual" : "cs-scan-list__items"}
          style={
            virtual ? { height: filtered.length * SCAN_ROW_HEIGHT } : undefined
          }
        >
          {visible.map((scan, index) => {
            const canDelete = [
              "completed",
              "failed",
              "cancelled",
              "interrupted",
            ].includes(scan.lifecycle_status);
            return (
              <div
                key={scan.scan_id}
                className={`cs-scan-item${selectedId === scan.scan_id ? " is-selected" : ""}${virtual ? " is-virtual" : ""}`}
                style={
                  virtual
                    ? {
                        transform: `translateY(${(start + index) * SCAN_ROW_HEIGHT}px)`,
                      }
                    : undefined
                }
              >
                <button
                  type="button"
                  className="cs-scan-item__select"
                  onPointerEnter={() => schedulePrefetch(scan.scan_id)}
                  onPointerLeave={cancelScheduledPrefetch}
                  onFocus={() => schedulePrefetch(scan.scan_id)}
                  onBlur={cancelScheduledPrefetch}
                  onClick={() => {
                    onSelect(scan.scan_id);
                    if (overlayLayout) onClose();
                  }}
                  aria-label={`${scan.display_name} · ${lifecycleLabels[scan.lifecycle_status] || scan.lifecycle_status}`}
                  aria-current={
                    selectedId === scan.scan_id ? "page" : undefined
                  }
                >
                  <span className="cs-scan-item__top">
                    <strong>{scan.display_name}</strong>
                  </span>
                  <span className="cs-scan-item__meta">
                    {scan.lifecycle_status === "completed"
                      ? scan.final_finding_count == null
                        ? "最终漏洞待确认"
                        : `${scan.final_finding_count} 个最终漏洞`
                      : phaseLabels[scan.current_phase || ""] || "等待阶段信息"}
                    {scan.dynamic_enabled && (
                      <span className="cs-mode-tag">动态</span>
                    )}
                  </span>
                  <span className="cs-scan-item__bottom">
                    <code title={scan.scan_id}>
                      {scan.scan_id.slice(0, 16)}
                    </code>
                    <time dateTime={scan.created_at}>
                      {relativeTime(scan.created_at)}
                    </time>
                  </span>
                </button>
                <span className="cs-scan-item__actions">
                  <StatusBadge status={scan.lifecycle_status} />
                  {canCreate && (
                    <button
                      type="button"
                      className="cs-scan-item__delete"
                      disabled={!canDelete}
                      aria-label={
                        canDelete
                          ? `删除审计 ${scan.display_name}`
                          : `审计 ${scan.display_name} 仍在运行，需先取消后才能删除`
                      }
                      title={
                        canDelete
                          ? "删除审计"
                          : "请先取消审计，待其停止后再删除"
                      }
                      onClick={(event) => onDelete(scan, event.currentTarget)}
                    >
                      <Icon name="trash" />
                    </button>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        {!filtered.length && (
          <p className="cs-inline-empty">没有匹配的审计记录</p>
        )}
        {hasMore && (
          <button
            type="button"
            className="cs-load-more-scans"
            onClick={() => void onLoadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? "正在加载…" : "加载更多审计记录"}
          </button>
        )}
      </nav>
    </aside>
  );
}
