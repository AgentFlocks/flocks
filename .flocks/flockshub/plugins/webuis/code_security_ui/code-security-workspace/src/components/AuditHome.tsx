import { useEffect, useMemo, useRef, useState } from "react";
import type { ProjectSummary, ScanSummary } from "../types";
import { Icon } from "../icons";
import { StatusBadge } from "./StatusBadge";
import { registerAuditProject, importAuditProject, deleteAuditProject, readApiFailure } from "../api";
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
  onProjectDeleted,
}: {
  projects: ProjectSummary[];
  scans: ScanSummary[];
  canCreate: boolean;
  onNewAudit: () => void;
  onProjectSelect: (id: string) => void;
  onProjectAdded: (project: ProjectSummary) => void;
  onProjectDeleted?: (id: string) => void;
  hasMore: boolean;
  onLoadMore: () => void;
  loadingMore: boolean;
}) {
  const { language, t } = useCodeSecurityI18n();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const addButtonRef = useRef<HTMLButtonElement>(null);
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const [deletingProject, setDeletingProject] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  useEffect(() => {
    if (!deletingProject) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = deleteDialogRef.current;
    if (dialog?.showModal) dialog.showModal();
    else dialog?.setAttribute("open", "");
    return () => previous?.focus();
  }, [deletingProject]);
  const [adding, setAdding] = useState(false);
  const [sourceKind, setSourceKind] = useState<"local" | "git" | "zip" | "url">("local");
  const [sourceUrl, setSourceUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  useEffect(() => {
    if (!adding) return;
    const dialog = dialogRef.current;
    if (dialog?.showModal) dialog.showModal();
    else dialog?.setAttribute("open", "");
    return () => { addButtonRef.current?.focus(); };
  }, [adding]);
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
  const pages = Math.max(1, Math.ceil(rows.length / 20));
  const current = Math.min(page, pages);
  const pageNumbers = pages <= 7
    ? Array.from({ length: pages }, (_, index) => index + 1)
    : Array.from(new Set([1, pages, ...Array.from({ length: 5 }, (_, index) => Math.max(2, Math.min(current - 2, pages - 5)) + index)]))
        .filter((value) => value <= pages).sort((a, b) => a - b);

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
              ref={addButtonRef}
              aria-haspopup="dialog"
              onClick={() => { setSaveError(""); setAdding(true); }}
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
        <dialog ref={dialogRef} className="cs-add-project-dialog" aria-labelledby="cs-add-project-title"
          onCancel={(event) => { event.preventDefault(); if (!saving) setAdding(false); }}
          onClick={(event) => { if (event.target === event.currentTarget && !saving) setAdding(false); }}>
        <form
          className="cs-register-project"
          onSubmit={async (e) => {
            e.preventDefault();
            if (saving) return;
            setSaving(true);
            setSaveError("");
            try {
              const project = sourceKind === "local"
                ? await registerAuditProject(projectPath, projectName)
                : await importAuditProject(sourceKind, projectName, sourceUrl, branch, file);
              onProjectAdded(project);
              setAdding(false);
              setProjectPath("");
              setProjectName("");
              setSourceUrl(""); setBranch(""); setFile(null);
            } catch (reason) {
              setSaveError(readApiFailure(reason, "无法添加项目").message);
            } finally {
              setSaving(false);
            }
          }}
        >
          <header className="cs-add-project-heading">
            <h2 id="cs-add-project-title">{t("添加项目")}</h2>
            <button type="button" aria-label={t("关闭")} disabled={saving} onClick={() => setAdding(false)}>×</button>
          </header>
          <div className="cs-project-source-tabs" role="tablist" aria-label={t("源码来源")}>
            {([ ["local", "本地目录"], ["git", "Git 仓库"], ["zip", "ZIP 文件"], ["url", "源码 URL"] ] as const).map(([kind, label]) => (
              <button type="button" role="tab" aria-selected={sourceKind === kind} disabled={saving} key={kind}
                onClick={() => { setSourceKind(kind); setSourceUrl(""); setFile(null); setSaveError(""); }}>{t(label)}</button>
            ))}
          </div>
          <div className="cs-add-project-body">
          <p>{t(sourceKind === "local" ? "使用服务器上已存在且已授权的源码目录。" : "源码导入后保存为代码项目，不会自动启动审计。")}</p>
          <label>
            {t("项目名称")}
            <input
              autoFocus
              value={projectName}
              onChange={(e) => setProjectName(e.target.value)}
              placeholder={t("可选")}
            />
          </label>
          {sourceKind === "local" ? <label>
            {t("源码目录")}
            <input
              required
              value={projectPath}
              onChange={(e) => setProjectPath(e.target.value)}
              placeholder={t("服务器上的源码目录绝对路径")}
            />
          </label>
          : sourceKind === "zip" ? <label>
            {t("上传源码 ZIP")}
            <input type="file" accept=".zip,application/zip" required disabled={saving}
              onChange={(event) => { const selected = event.target.files?.[0] || null; setFile(selected); setSaveError(selected && selected.size > 100 * 1024 * 1024 ? t("ZIP 文件不能超过 100 MB") : ""); }} />
            <small>{t("ZIP 最大 100 MB，解压后最大 500 MB。")}</small>
          </label> : <>
            <label>{t(sourceKind === "git" ? "Git 仓库地址" : "源码下载地址")}
              <input required value={sourceUrl} disabled={saving} onChange={(event) => setSourceUrl(event.target.value)}
                placeholder={sourceKind === "git" ? "https://host/team/repo.git 或 git@host:team/repo.git" : "https://host/source.zip"} />
            </label>
            {sourceKind === "git" ? <label>{t("分支或标签")}
              <input value={branch} disabled={saving} onChange={(event) => setBranch(event.target.value)} placeholder={t("留空使用仓库默认分支")} />
              <small>{t("私有仓库使用服务器已配置的 Git 凭据。")}</small>
            </label> : <small>{t("填写可直接下载的 HTTP(S) ZIP 链接，最大 100 MB。")}</small>}
          </>}
          {saveError && <p role="alert">{saveError}</p>}
          </div>
          <div className="cs-add-project-actions">
          <button type="button" className="cs-button cs-button--secondary" disabled={saving} onClick={() => setAdding(false)}>{t("取消")}</button>
          <button
            className="cs-button cs-button--primary"
            type="submit"
            disabled={saving || (sourceKind === "zip" && (!file || file.size > 100 * 1024 * 1024))}
          >
            {t(saving ? sourceKind === "local" ? "正在添加…" : "正在导入…" : "添加项目")}
          </button>
          </div>
        </form>
        </dialog>
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
              <th>{t("源码来源")}</th>
              <th>{t("最近审计时间")}</th>
              <th>{t("最近审计状态")}</th>
              <th>{t("漏洞数量")}</th>
              <th title={t("当前已加载的审计任务数")}>{t("审计次数")}</th>
              <th>{t("操作")}</th>
            </tr>
          </thead>
          <tbody>
            {rows
              .slice((current - 1) * 20, current * 20)
              .map(({ project, latest, count }) => (
                <tr key={project.id}>
                  <td>
                    <button type="button" className="cs-project-name" onClick={() => onProjectSelect(project.id)}>
                      {project.name ||
                        project.worktree.split(/[\\/]/).filter(Boolean).pop()}
                    </button>
                  </td>
                  <td><span className={`cs-source-badge cs-source-badge--${project.sourceKind || "unknown"}`}>{t(({ local: "本地目录", git: "Git 仓库", zip: "ZIP 上传", url: "源码 URL" } as const)[project.sourceKind!] || "未记录")}</span></td>
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
                    <div className="cs-project-row-actions">
                    <button
                      type="button"
                      onClick={() => onProjectSelect(project.id)}
                    >
                      {t("查看项目")}
                    </button>
                    {project.canWrite !== false && project.canDelete !== false && onProjectDeleted && <button type="button" className="cs-project-delete" aria-label={t("删除")} title={t("删除项目")} onClick={() => { setDeleteError(""); setDeletingProject(project); }}><Icon name="trash" width={16} height={16} strokeWidth={1.7} /></button>}
                    </div>
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
          {t("共 {{count}} 个项目 · 每页最多 20 个", { count: rows.length })}{hasMore ? t(" · 审计统计基于已加载记录") : ""}
        </span>
        <nav className="cs-project-pagination" aria-label={t("项目分页")}>
          <button type="button" aria-label={t("上一页")} disabled={current === 1} onClick={() => setPage(current - 1)}>‹</button>
          {pageNumbers.map((number, index) => (
            <span className="cs-page-item" key={number}>
              {index > 0 && number - pageNumbers[index - 1] > 1 && <span className="cs-page-ellipsis" aria-hidden="true">…</span>}
              <button type="button" aria-label={t("第 {{page}} 页", { page: number })} aria-current={number === current ? "page" : undefined} onClick={() => setPage(number)}>{number}</button>
            </span>
          ))}
          <button type="button" aria-label={t("下一页")} disabled={current === pages} onClick={() => setPage(current + 1)}>›</button>
          <span className="cs-page-total">{t("共 {{pages}} 页", { pages })}</span>
          {pages > 7 && <form className="cs-page-jump" onSubmit={(event) => {
            event.preventDefault();
            const input = event.currentTarget.elements.namedItem("page") as HTMLInputElement;
            const value = Number(input.value);
            if (Number.isInteger(value) && value >= 1 && value <= pages) { setPage(value); input.value = ""; }
          }}>
            <input name="page" type="number" min={1} max={pages} required aria-label={t("跳转页码")} placeholder={t("页码")} />
            <button type="submit">{t("跳转")}</button>
          </form>}
        </nav>
      </footer>
      {deletingProject && <dialog ref={deleteDialogRef} className="cs-project-delete-dialog" aria-labelledby="cs-delete-project-title"
        onCancel={(event) => { event.preventDefault(); if (!deleting) setDeletingProject(null); }}>
        <h2 id="cs-delete-project-title">{t("删除项目")}</h2>
        <p>{t("确定删除项目“{{name}}”吗？", { name: deletingProject.name || t("未命名项目") })}</p>
        <p>{t("将永久删除项目及关联审计记录、会话、快照和产物，无法恢复。原始源码目录会保留。")}</p>
        {deleteError && <p role="alert">{deleteError}</p>}
        <div className="cs-add-project-actions">
          <button type="button" className="cs-button cs-button--secondary" disabled={deleting} onClick={() => setDeletingProject(null)}>{t("取消")}</button>
          <button type="button" className="cs-button cs-project-delete-confirm" disabled={deleting} onClick={async () => {
            setDeleting(true); setDeleteError("");
            try { await deleteAuditProject(deletingProject.id); onProjectDeleted?.(deletingProject.id); setDeletingProject(null); }
            catch (error) { setDeleteError(readApiFailure(error, t("删除项目失败")).message); }
            finally { setDeleting(false); }
          }}>{t(deleting ? "正在删除…" : "确定")}</button>
        </div>
      </dialog>}
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
