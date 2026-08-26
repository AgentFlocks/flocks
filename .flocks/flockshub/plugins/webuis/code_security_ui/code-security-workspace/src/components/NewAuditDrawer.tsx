import { useEffect, useMemo, useRef, useState } from "react";

import { createIdempotencyKey, createScan, readApiFailure } from "../api";
import { Icon } from "../icons";
import { useCodeSecurityI18n } from "../i18n";
import type { NewAuditValues, ProjectSummary, ScanDetail } from "../types";

const DRAFT_KEY = "code-security-new-audit-draft-v1";
const EMPTY_VALUES: NewAuditValues = {
  workspaceId: "",
  targetPath: ".",
  model: "",
  includePaths: ".",
  excludePatterns: "",
  maxFileBytes: 1_048_576,
  copySource: true,
  dynamicEnabled: false,
  dynamicConfirmed: false,
  coveragePolicy: "evidence_backed_partial",
  verificationVotes: 1,
};
const CREATE_ERROR_MESSAGES: Record<string, string> = {
  unsafe_target_scope:
    "目标目录属于 Flocks 运行数据，不能作为审计目标。请选择源码项目目录。",
  target_not_directory: "目标目录不存在或不是文件夹，请检查相对路径。",
  target_not_authorized: "目标目录不在所选工作区内，请重新选择。",
  workspace_not_found: "所选工作区不可用，请刷新后重新选择。",
};

export function NewAuditDrawer({
  open,
  projects,
  onClose,
  onCreated,
}: {
  open: boolean;
  projects: ProjectSummary[];
  onClose: () => void;
  onCreated: (detail: ScanDetail) => void;
}) {
  const { t } = useCodeSecurityI18n();
  const baselineRef = useRef<NewAuditValues | null>(null);
  const wasOpenRef = useRef(false);
  const attemptRef = useRef<{ signature: string; key: string } | null>(null);
  const [values, setValues] = useState<NewAuditValues>(() => {
    const draft = readDraft();
    baselineRef.current = draft;
    return draft;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const dirty = JSON.stringify(values) !== JSON.stringify(baselineRef.current);
  const availableProjects = useMemo(
    () =>
      projects.filter(
        (project) =>
          project.pathStatus === "available" && project.canWrite !== false,
      ),
    [projects],
  );

  function requestClose() {
    if (dirty && !window.confirm(t("表单中有尚未提交的内容，确定关闭吗？")))
      return;
    onClose();
  }

  useEffect(() => {
    if (!open) return;
    if (
      !availableProjects.some((project) => project.id === values.workspaceId)
    ) {
      const workspaceId = availableProjects[0]?.id || "";
      setValues((current) => {
        const next = { ...current, workspaceId };
        baselineRef.current = {
          ...(baselineRef.current || EMPTY_VALUES),
          workspaceId,
        };
        return next;
      });
    }
    window.setTimeout(() => titleRef.current?.focus(), 0);
  }, [availableProjects, open, values.workspaceId]);

  useEffect(() => {
    if (open && !wasOpenRef.current) {
      setValues((current) => ({ ...current, dynamicConfirmed: false }));
      setErrors((current) => ({ ...current, dynamicConfirmed: "" }));
    }
    wasOpenRef.current = open;
  }, [open]);

  useEffect(() => {
    if (!open) return;
    try {
      sessionStorage.setItem(
        DRAFT_KEY,
        JSON.stringify({ ...values, dynamicConfirmed: false }),
      );
    } catch {
      // A blocked session store must not make the audit form unusable.
    }
  }, [open, values]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        requestClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, a[href]",
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
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [dirty, onClose, open]);

  if (!open) return null;

  const set = <K extends keyof NewAuditValues>(
    key: K,
    value: NewAuditValues[K],
  ) => {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: "" }));
    setSubmitError("");
  };
  const setDynamicEnabled = (enabled: boolean) => {
    setValues((current) => ({
      ...current,
      dynamicEnabled: enabled,
      dynamicConfirmed: enabled ? current.dynamicConfirmed : false,
    }));
    setErrors((current) => ({
      ...current,
      dynamicEnabled: "",
      dynamicConfirmed: "",
    }));
    setSubmitError("");
  };
  const setCopySource = (enabled: boolean) => {
    setValues((current) => ({
      ...current,
      copySource: enabled,
      dynamicEnabled: enabled ? current.dynamicEnabled : false,
      dynamicConfirmed: enabled ? current.dynamicConfirmed : false,
    }));
    setErrors((current) => ({
      ...current,
      dynamicEnabled: "",
      dynamicConfirmed: "",
    }));
    setSubmitError("");
  };
  const validatePath = () => {
    const path = values.targetPath.trim();
    const invalid =
      path.startsWith("/") || path.includes("..") || path.includes("\\");
    setErrors((current) => ({
      ...current,
      targetPath: invalid ? "目标目录必须是工作区内的相对路径。" : "",
    }));
    return !invalid;
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const nextErrors: Record<string, string> = {};
    if (!values.workspaceId) nextErrors.workspaceId = "请选择工作区。";
    if (!validatePath())
      nextErrors.targetPath = "目标目录必须是工作区内的相对路径。";
    if (values.model && !values.model.includes("/"))
      nextErrors.model = "固定模型必须使用 provider/model 格式。";
    if (values.dynamicEnabled && !values.dynamicConfirmed)
      nextErrors.dynamicConfirmed = "请确认已理解动态验证的执行边界。";
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) {
      window.setTimeout(() => errorRef.current?.focus(), 0);
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const signature = JSON.stringify(values);
      if (attemptRef.current?.signature !== signature) {
        attemptRef.current = { signature, key: createIdempotencyKey() };
      }
      const detail = await createScan(values, attemptRef.current.key);
      try {
        sessionStorage.removeItem(DRAFT_KEY);
      } catch {
        // The scan was created; draft cleanup is best-effort only.
      }
      baselineRef.current = EMPTY_VALUES;
      setValues(EMPTY_VALUES);
      attemptRef.current = null;
      onCreated(detail);
    } catch (reason: any) {
      const failure = readApiFailure(reason, t("创建审计失败"));
      const message =
        (failure.code && CREATE_ERROR_MESSAGES[failure.code]) ||
        failure.message;
      const field =
        failure.code === "workspace_not_found"
          ? "workspaceId"
          : failure.code?.startsWith("target_") ||
              failure.code === "unsafe_target_scope"
            ? "targetPath"
            : null;
      if (field) {
        setErrors((current) => ({ ...current, [field]: message }));
        setSubmitError(
          failure.requestId
            ? t("请求 ID：{{id}}", { id: failure.requestId })
            : "",
        );
      } else {
        setSubmitError(
          `${t(message)}${
            failure.requestId
              ? ` · ${t("请求 ID：{{id}}", { id: failure.requestId })}`
              : ""
          }`,
        );
      }
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="cs-drawer-layer" role="presentation">
      <button
        className="cs-drawer-scrim"
        type="button"
        onClick={requestClose}
        aria-label={t("关闭新建审计")}
      />
      <section
        ref={dialogRef}
        className="cs-new-audit"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-audit-title"
      >
        <header>
          <div>
            <p className="cs-eyebrow">
              {t(values.copySource ? "不可变源码快照" : "直接源码审计")}
            </p>
            <h2 id="new-audit-title" ref={titleRef} tabIndex={-1}>
              {t("新建代码审计")}
            </h2>
          </div>
          <button
            className="cs-icon-button"
            type="button"
            onClick={requestClose}
            aria-label={t("关闭新建审计")}
          >
            <Icon name="close" />
          </button>
        </header>
        <form onSubmit={submit} noValidate>
          {(Object.values(errors).some(Boolean) || submitError) && (
            <div
              className="cs-form-errors"
              role="alert"
              tabIndex={-1}
              ref={errorRef}
            >
              <h3>{t("请修正以下问题")}</h3>
              {submitError && <p>{submitError}</p>}
              <ul>
                {Object.entries(errors)
                  .filter(([, value]) => value)
                  .map(([key, value]) => (
                    <li key={key}>
                      <a href={`#audit-${key}`}>{t(value)}</a>
                    </li>
                  ))}
              </ul>
            </div>
          )}

          <fieldset>
            <legend>{t("目标")}</legend>
            <label className="cs-field" htmlFor="audit-workspaceId">
              <span>
                {t("工作区")} <b aria-hidden="true">*</b>
              </span>
              <select
                id="audit-workspaceId"
                value={values.workspaceId}
                onChange={(event) => set("workspaceId", event.target.value)}
                aria-invalid={Boolean(errors.workspaceId)}
              >
                <option value="">{t("请选择工作区")}</option>
                {availableProjects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name || project.worktree}
                  </option>
                ))}
              </select>
              {errors.workspaceId && (
                <small role="alert">{errors.workspaceId}</small>
              )}
            </label>
            <label className="cs-field" htmlFor="audit-targetPath">
              <span>
                {t("目标目录")} <b aria-hidden="true">*</b>
              </span>
              <input
                id="audit-targetPath"
                value={values.targetPath}
                onChange={(event) => set("targetPath", event.target.value)}
                onBlur={validatePath}
                aria-invalid={Boolean(errors.targetPath)}
                aria-describedby={`audit-target-help${errors.targetPath ? " audit-targetPath-error" : ""}`}
              />
              <small id="audit-target-help">
                {t("相对于所选工作区，例如")} <code>packages/api</code>。
              </small>
              {errors.targetPath && (
                <small id="audit-targetPath-error" role="alert">
                  {errors.targetPath}
                </small>
              )}
            </label>
          </fieldset>

          <fieldset>
            <legend>{t("范围")}</legend>
            <label className="cs-field" htmlFor="audit-includePaths">
              <span>{t("包含路径")}</span>
              <textarea
                id="audit-includePaths"
                value={values.includePaths}
                onChange={(event) => set("includePaths", event.target.value)}
                rows={3}
              />
              <small>{t("每行一个快照相对路径。")}</small>
            </label>
            <label className="cs-field" htmlFor="audit-excludePatterns">
              <span>{t("排除模式")}</span>
              <textarea
                id="audit-excludePatterns"
                value={values.excludePatterns}
                onChange={(event) => set("excludePatterns", event.target.value)}
                rows={3}
              />
              <small>{t("每行一个 glob；留空使用插件默认排除规则。")}</small>
            </label>
            <details className="cs-advanced">
              <summary>{t("高级设置")}</summary>
              <label className="cs-toggle" htmlFor="audit-copySource">
                <span>
                  <strong>{t("复制源码到只读快照")}</strong>
                  <small>
                    {t(
                      values.copySource
                        ? "默认开启；审计使用固定的源码副本。"
                        : "已关闭；直接读取源目录，文件变化会导致校验失败，且不能启用动态验证。",
                    )}
                  </small>
                </span>
                <input
                  id="audit-copySource"
                  type="checkbox"
                  checked={values.copySource}
                  onChange={(event) => setCopySource(event.target.checked)}
                />
              </label>
              <label className="cs-field" htmlFor="audit-maxFileBytes">
                <span>{t("单文件上限（字节）")}</span>
                <input
                  id="audit-maxFileBytes"
                  type="number"
                  min={1}
                  max={50 * 1024 * 1024}
                  value={values.maxFileBytes}
                  onChange={(event) =>
                    set("maxFileBytes", Number(event.target.value))
                  }
                />
              </label>
              <label className="cs-field" htmlFor="audit-coveragePolicy">
                <span>{t("覆盖策略")}</span>
                <select
                  id="audit-coveragePolicy"
                  value={values.coveragePolicy}
                  onChange={(event) =>
                    set(
                      "coveragePolicy",
                      event.target.value as NewAuditValues["coveragePolicy"],
                    )
                  }
                >
                  <option value="evidence_backed_partial">
                    {t("可信部分覆盖（默认）")}
                  </option>
                  <option value="exhaustive">{t("穷尽覆盖")}</option>
                </select>
                <small>
                  {t("穷尽覆盖会阻止仍有未检查文件或阻塞问题的工作单元完成。")}
                </small>
              </label>
              <div className="cs-field">
                <label htmlFor="audit-verificationVotes">
                  {t("独立复核票数")}
                </label>
                <select
                  id="audit-verificationVotes"
                  value={values.verificationVotes}
                  onChange={(event) =>
                    set(
                      "verificationVotes",
                      Number(
                        event.target.value,
                      ) as NewAuditValues["verificationVotes"],
                    )
                  }
                  aria-describedby="audit-verificationVotes-help"
                >
                  {[1, 2, 3, 4, 5].map((count) => (
                    <option key={count} value={count}>
                      {count}
                    </option>
                  ))}
                </select>
                <small id="audit-verificationVotes-help">
                  {t("每个候选漏洞独立复核；多票时按严格多数决裁定。")}
                </small>
              </div>
            </details>
          </fieldset>

          <fieldset>
            <legend>{t("模型")}</legend>
            <label className="cs-field" htmlFor="audit-model">
              <span>{t("固定模型")}</span>
              <input
                id="audit-model"
                value={values.model}
                onChange={(event) => set("model", event.target.value)}
                placeholder={t("留空使用系统默认模型")}
                aria-invalid={Boolean(errors.model)}
              />
              <small>
                {t("可选；格式为")} <code>provider/model</code>。
              </small>
              {errors.model && <small role="alert">{errors.model}</small>}
            </label>
          </fieldset>

          <fieldset>
            <legend>{t("验证方式")}</legend>
            <label className="cs-toggle" htmlFor="audit-dynamicEnabled">
              <span>
                <strong>{t("动态验证")}</strong>
                <small>{t("默认关闭；开启后执行受限 Docker 探测。")}</small>
              </span>
              <input
                id="audit-dynamicEnabled"
                type="checkbox"
                checked={values.dynamicEnabled}
                disabled={!values.copySource}
                onChange={(event) => setDynamicEnabled(event.target.checked)}
              />
            </label>
            {!values.copySource && (
              <small>
                {t("直接源码审计不支持动态验证；重新开启源码复制后可用。")}
              </small>
            )}
            {values.dynamicEnabled && (
              <div className="cs-dynamic-confirm">
                <Icon name="flask" />
                <div>
                  <h3>{t("动态验证将在本地 Docker 中构建并运行受限探测")}</h3>
                  <p>
                    {t(
                      "无网络 · 无主机挂载 · 只读根文件系统 · 无 capabilities · 资源受限 · 仅使用本地已有镜像",
                    )}
                  </p>
                  <p>
                    {t(
                      "需要 Docker CLI、可用的本地 daemon，以及快照中受支持的 Dockerfile。",
                    )}
                  </p>
                  <label htmlFor="audit-dynamicConfirmed">
                    <input
                      id="audit-dynamicConfirmed"
                      type="checkbox"
                      checked={values.dynamicConfirmed}
                      onChange={(event) =>
                        set("dynamicConfirmed", event.target.checked)
                      }
                      aria-invalid={Boolean(errors.dynamicConfirmed)}
                    />
                    <span>
                      {t("我理解动态验证会执行快照中的受限代码，并同意继续。")}
                    </span>
                  </label>
                  {errors.dynamicConfirmed && (
                    <small role="alert">{errors.dynamicConfirmed}</small>
                  )}
                </div>
              </div>
            )}
          </fieldset>

          <footer>
            <button
              className="cs-button cs-button--secondary"
              type="button"
              onClick={requestClose}
            >
              {t("取消")}
            </button>
            <button
              className="cs-button cs-button--primary"
              type="submit"
              disabled={submitting}
            >
              {t(
                submitting
                  ? values.copySource
                    ? "正在创建不可变快照…"
                    : "正在准备直接源码审计…"
                  : values.dynamicEnabled
                    ? "启动动态审计"
                    : "启动静态审计",
              )}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function readDraft(): NewAuditValues {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null");
    return parsed && typeof parsed === "object"
      ? { ...EMPTY_VALUES, ...parsed, dynamicConfirmed: false }
      : EMPTY_VALUES;
  } catch {
    return EMPTY_VALUES;
  }
}
