import { useMemo, useState } from "react";
import type { ProjectSummary, ScanSummary } from "../types";
import { StatusBadge } from "./StatusBadge";
import { registerAuditProject, readApiFailure } from "../api";
import { useCodeSecurityI18n } from "../i18n";

export function AuditHome({
  projects,
  scans,
  canCreate,
  onNewAudit,
  onProjectSelect,
  hasMore,
  onLoadMore,
  loadingMore,
  onProjectAdded,
}: {
  projects: ProjectSummary[];
  scans: ScanSummary[];
  canCreate: boolean;
  onNewAudit: () => void;
  onProjectSelect: (id: string) => void;
  onProjectAdded: (project: ProjectSummary) => void;
  hasMore: boolean;
  onLoadMore: () => void;
  loadingMore: boolean;
}) {
  const { language, t } = useCodeSecurityI18n();
  const [adding, setAdding] = useState(false);
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const rows = useMemo(
    () =>
      projects
        .map((project) => {
          const audits = scans.filter(
            (scan) => scan.workspace_ref === project.id,
          );
          const latest = [...audits].sort((a, b) =>
            b.created_at.localeCompare(a.created_at),
          )[0];
          return { project, latest, count: audits.length };
        })
        .filter(
          ({ project, latest }) =>
            `${project.name || ""} ${project.worktree}`
              .toLowerCase()
              .includes(query.toLowerCase()) &&
            (status === "all" ||
              (status === "none"
                ? !latest
                : latest?.lifecycle_status === status)),
        ),
    [projects, scans, query, status],
  );
  const pages = Math.max(1, Math.ceil(rows.length / 10));
  const current = Math.min(page, pages);
  return (
    <section className="cs-project-home">
      <header>
        <div>
          <h1>{t("Flocks 代码安全审计工作台")}</h1>
          <p>{t("管理代码项目，查看审计进展与结果。")}</p>
        </div>
        {canCreate && (
          <div className="cs-home-actions">
            <button
              type="button"
              className="cs-button cs-button--secondary"
              aria-expanded={adding}
              onClick={() => setAdding(!adding)}
            >
              {t("添加项目")}
            </button>
            <button
              type="button"
              className="cs-button cs-button--primary"
              onClick={onNewAudit}
            >
              {t("发起审计")}
            </button>
          </div>
        )}
      </header>
      {adding && (
        <form
          className="cs-register-project"
          onSubmit={async (e) => {
            e.preventDefault();
            if (saving) return;
            setSaving(true);
            setSaveError("");
            try {
              const project = await registerAuditProject(
                projectPath,
                projectName,
              );
              onProjectAdded(project);
              setAdding(false);
              setProjectPath("");
              setProjectName("");
            } catch (reason) {
              setSaveError(readApiFailure(reason, "无法添加项目").message);
            } finally {
              setSaving(false);
            }
          }}
        >
          <h2>{t("登记已有代码目录")}</h2>
          <p>{t("使用服务器上已存在且已授权的源码目录。")}</p>
          <label>
            {t("项目名称")}
            <input
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder={t("可选")}
            />
          </label>
          <label>
            {t("源码目录")}
            <input
              required
              value={projectPath}
              onChange={(e) => setProjectPath(e.target.value)}
              placeholder={t("服务器上的源码目录绝对路径")}
            />
          </label>
          {saveError && <p role="alert">{saveError}</p>}
          <button
            className="cs-button cs-button--primary"
            type="submit"
            disabled={saving}
          >
            {saving ? "正在添加…" : "添加项目"}
          </button>
        </form>
      )}
      <div className="cs-project-toolbar">
        <h2>
          {t("代码项目")}
          <small>{projects.length}</small>
        </h2>
        <div>
          <select
            aria-label={t("筛选项目审计状态")}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">{t("全部状态")}</option>
            <option value="running">{t("运行中")}</option>
            <option value="completed">{t("已完成")}</option>
            <option value="failed">{t("失败")}</option>
            <option value="none">{t("暂无审计")}</option>
          </select>
          <input
            type="search"
            aria-label={t("搜索代码项目")}
            placeholder={t("搜索项目名称或路径")}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>
      <div className="cs-project-table-scroll">
        <table className="cs-project-table">
          <thead>
            <tr>
              <th>{t("项目名称")}</th>
              <th>{t("最近审计")}</th>
              <th>{t("审计状态")}</th>
              <th>{t("漏洞数量")}</th>
              <th>{t("已加载审计")}</th>
              <th>{t("操作")}</th>
            </tr>
          </thead>
          <tbody>
            {rows
              .slice((current - 1) * 10, current * 10)
              .map(({ project, latest, count }) => (
                <tr key={project.id}>
                  <td>
                    <strong>
                      {project.name ||
                        project.worktree.split(/[\\/]/).filter(Boolean).pop()}
                    </strong>
                    <small title={project.worktree}>{project.worktree}</small>
                  </td>
                  <td>
                    {latest
                      ? new Date(latest.created_at).toLocaleString(language, {
                          year: "numeric",
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </td>
                  <td>
                    {latest ? (
                      <StatusBadge status={latest.lifecycle_status} />
                    ) : (
                      "暂无审计"
                    )}
                  </td>
                  <td>{latest?.final_finding_count ?? "—"}</td>
                  <td>{count}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => onProjectSelect(project.id)}
                    >
                      {t("查看项目")}
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      {!rows.length && (
        <div className="cs-home-empty">
          <h3>{projects.length ? "没有匹配的项目" : "还没有代码项目"}</h3>
          <p>
            {projects.length
              ? "尝试其他关键词或状态。"
              : "在项目管理中登记源码目录后，即可发起审计。"}
          </p>
        </div>
      )}
      <footer>
        <span>
          共 {rows.length} 个项目{hasMore ? " · 审计统计基于已加载记录" : ""}
        </span>
        <div>
          <button
            type="button"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          >
            {t("上一页")}
          </button>
          <span>
            {current} / {pages}
          </span>
          <button
            type="button"
            disabled={current === pages}
            onClick={() => setPage(current + 1)}
          >
            {t("下一页")}
          </button>
        </div>
      </footer>
      {hasMore && (
        <button
          className="cs-home-link"
          type="button"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? "正在加载…" : "加载更多审计记录"}
        </button>
      )}
    </section>
  );
}
