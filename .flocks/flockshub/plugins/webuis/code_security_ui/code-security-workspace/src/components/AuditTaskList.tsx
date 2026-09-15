import { useEffect, useRef, useState } from "react";
import { useCodeSecurityI18n } from "../i18n";
import { phaseLabels } from "../labels";
import type { ProjectSummary, ScanSummary } from "../types";
import { Icon } from "../icons";
import { StatusBadge } from "./StatusBadge";

export function AuditTaskList({
  scans,
  canManage,
  onDelete,
  onPrefetch,
  project,
  onSelect,
  onHome,
  onNewAudit,
  canCreate,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  scans: ScanSummary[];
  canManage?: boolean;
  onDelete?: (scan: ScanSummary, opener: HTMLButtonElement) => void;
  onPrefetch?: (id: string) => void;
  project?: ProjectSummary;
  onSelect: (id: string) => void;
  onHome: () => void;
  onNewAudit: () => void;
  canCreate: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => Promise<void>;
}) {
  const { t, language } = useCodeSecurityI18n();
  const prefetchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const cancelPrefetch = () => clearTimeout(prefetchTimer.current);
  const schedulePrefetch = (id: string) => {
    cancelPrefetch();
    prefetchTimer.current = setTimeout(() => onPrefetch?.(id), 150);
  };
  useEffect(() => cancelPrefetch, []);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const filtered = scans.filter(
    (scan) =>
      `${scan.display_name} ${scan.scan_id}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()) &&
      (status === "all" || scan.lifecycle_status === status),
  );
  return (
    <section className="cs-project-home cs-audit-list-page">
      <button type="button" className="cs-home-link" onClick={onHome}>
        {t("← 代码审计首页")}
      </button>
      <header>
        <div>
          {project && <p className="cs-eyebrow">{t("项目详情")}</p>}
          <h1>
            {project
              ? project.name ||
                project.worktree.split(/[\\/]/).filter(Boolean).pop()
              : t("审计列表")}
          </h1>
          <p>
            {project
              ? t("查看此项目的审计记录，选择任务查看执行过程与结果。")
              : t("查看所有审计任务的执行进度与结果。")}
          </p>
          {project && <p className="cs-project-path">{project.worktree}</p>}
        </div>
        {canCreate && (
          <button
            className="cs-button cs-button--primary"
            type="button"
            onClick={onNewAudit}
          >
            {t("发起审计")}
          </button>
        )}
      </header>
      <div className="cs-project-toolbar">
        <span>
          {project && <strong>{t("项目审计记录")} · </strong>}
          {t("已加载 {{count}} 个审计任务", { count: scans.length })}
        </span>
        <div>
          <select
            aria-label={t("筛选审计任务状态")}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="all">{t("全部状态")}</option>
            <option value="preparing">{t("准备中")}</option>
            <option value="running">{t("运行中")}</option>
            <option value="completed">{t("已完成")}</option>
            <option value="failed">{t("执行失败")}</option>
            <option value="cancelled">{t("已取消")}</option>
            <option value="cancelling">{t("正在取消")}</option>
            <option value="interrupted">{t("已中断")}</option>
          </select>
          <input
            type="search"
            aria-label={t("搜索审计任务")}
            placeholder={t("搜索任务名称或编号")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="cs-project-table-scroll">
        <table className="cs-project-table">
          <thead>
            <tr>
              <th>{t("审计任务")}</th>
              <th>{t("审计状态")}</th>
              <th>{t("当前阶段")}</th>
              <th>{t("漏洞数量")}</th>
              <th>{t("创建时间")}</th>
              <th>{t("操作")}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((scan) => (
              <tr key={scan.scan_id}>
                <td>
                  <strong>{scan.display_name}</strong>
                  <small>{scan.scan_id}</small>
                </td>
                <td>
                  <StatusBadge status={scan.lifecycle_status} />
                </td>
                <td>
                  {t(phaseLabels[scan.current_phase || ""] || "等待阶段信息")}
                </td>
                <td>{scan.final_finding_count ?? "—"}</td>
                <td>
                  {new Date(scan.created_at).toLocaleString(language, {
                    year: "numeric",
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </td>
                <td>
                  <button
                    type="button"
                    className="cs-audit-open"
                    aria-label={t("查看审计 {{name}}", {
                      name: scan.display_name,
                    })}
                    onPointerEnter={() => schedulePrefetch(scan.scan_id)}
                    onPointerLeave={cancelPrefetch}
                    onFocus={() => schedulePrefetch(scan.scan_id)}
                    onBlur={cancelPrefetch}
                    onClick={() => {
                      cancelPrefetch();
                      onSelect(scan.scan_id);
                    }}
                  >
                    {t("查看审计")}
                  </button>
                  {canManage && onDelete && (
                    <button
                      type="button"
                      className="cs-record-delete"
                      disabled={
                        ![
                          "completed",
                          "failed",
                          "cancelled",
                          "interrupted",
                        ].includes(scan.lifecycle_status)
                      }
                      aria-label={t(
                        [
                          "completed",
                          "failed",
                          "cancelled",
                          "interrupted",
                        ].includes(scan.lifecycle_status)
                          ? "删除审计 {{name}}"
                          : "审计 {{name}} 仍在运行，需先取消后才能删除",
                        { name: scan.display_name },
                      )}
                      onClick={(event) => onDelete(scan, event.currentTarget)}
                    >
                      <Icon name="trash" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!filtered.length && (
        <div className="cs-home-empty">
          <h3>{t(scans.length ? "没有匹配的审计任务" : "还没有审计任务")}</h3>
          <p>
            {t(
              scans.length
                ? "尝试其他关键词或状态。"
                : "发起一次代码审计，执行过程和结果会显示在这里。",
            )}
          </p>
        </div>
      )}
      {hasMore && (
        <footer>
          <span>{t("筛选结果基于已加载的任务。")}</span>
          <button
            type="button"
            disabled={loadingMore}
            onClick={() => void onLoadMore()}
          >
            {t(loadingMore ? "正在加载…" : "加载更多审计记录")}
          </button>
        </footer>
      )}
    </section>
  );
}
