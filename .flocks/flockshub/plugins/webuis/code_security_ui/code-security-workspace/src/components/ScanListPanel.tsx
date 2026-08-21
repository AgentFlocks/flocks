import { useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "../icons";
import { phaseLabels, relativeTime } from "../labels";
import type { ScanSummary } from "../types";
import { StatusBadge } from "./StatusBadge";

export function ScanListPanel({
  scans,
  selectedId,
  onSelect,
  onNewAudit,
  canCreate,
  open,
  onClose,
}: {
  scans: ScanSummary[];
  selectedId: string | null;
  onSelect: (scanId: string) => void;
  onNewAudit: () => void;
  canCreate: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [overlayLayout, setOverlayLayout] = useState(
    () => window.matchMedia?.("(max-width: 1023px)").matches ?? false,
  );
  const [mobileLayout, setMobileLayout] = useState(
    () => window.matchMedia?.("(max-width: 767px)").matches ?? false,
  );
  const panelRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
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
          className="cs-button cs-button--primary cs-button--full"
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
      <nav className="cs-scan-list" aria-label="扫描列表">
        {filtered.map((scan) => (
          <button
            key={scan.scan_id}
            type="button"
            className={`cs-scan-item${selectedId === scan.scan_id ? " is-selected" : ""}`}
            onClick={() => {
              onSelect(scan.scan_id);
              if (overlayLayout) onClose();
            }}
            aria-current={selectedId === scan.scan_id ? "page" : undefined}
          >
            <span className="cs-scan-item__top">
              <strong>{scan.display_name}</strong>
              <StatusBadge status={scan.lifecycle_status} />
            </span>
            <span className="cs-scan-item__meta">
              {scan.lifecycle_status === "completed"
                ? `${scan.candidate_count || 0} 个候选`
                : phaseLabels[scan.current_phase || ""] || "等待阶段信息"}
              {scan.dynamic_enabled && (
                <span className="cs-mode-tag">动态</span>
              )}
            </span>
            <span className="cs-scan-item__bottom">
              <code title={scan.scan_id}>{scan.scan_id.slice(0, 16)}</code>
              <time dateTime={scan.created_at}>
                {relativeTime(scan.created_at)}
              </time>
            </span>
          </button>
        ))}
        {!filtered.length && (
          <p className="cs-inline-empty">没有匹配的审计记录</p>
        )}
      </nav>
    </aside>
  );
}
