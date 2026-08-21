import { useEffect, useMemo, useRef, useState } from "react";

import { createIdempotencyKey, createScan } from "../api";
import { Icon } from "../icons";
import type { NewAuditValues, ProjectSummary, ScanDetail } from "../types";

const DRAFT_KEY = "code-security-new-audit-draft-v1";
const EMPTY_VALUES: NewAuditValues = {
  workspaceId: "",
  targetPath: ".",
  model: "",
  includePaths: ".",
  excludePatterns: "",
  maxFileBytes: 1_048_576,
  dynamicEnabled: false,
  dynamicConfirmed: false,
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
    if (dirty && !window.confirm("表单中有尚未提交的内容，确定关闭吗？"))
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
      const detail = reason?.response?.data?.detail;
      setSubmitError(
        `${detail?.message || reason?.message || "创建审计失败"}${detail?.requestId ? ` · Request ID: ${detail.requestId}` : ""}`,
      );
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
        aria-label="关闭新建审计"
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
            <p className="cs-eyebrow">不可变源码快照</p>
            <h2 id="new-audit-title" ref={titleRef} tabIndex={-1}>
              新建代码审计
            </h2>
          </div>
          <button
            className="cs-icon-button"
            type="button"
            onClick={requestClose}
            aria-label="关闭新建审计"
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
              <h3>请修正以下问题</h3>
              {submitError && <p>{submitError}</p>}
              <ul>
                {Object.entries(errors)
                  .filter(([, value]) => value)
                  .map(([key, value]) => (
                    <li key={key}>
                      <a href={`#audit-${key}`}>{value}</a>
                    </li>
                  ))}
              </ul>
            </div>
          )}

          <fieldset>
            <legend>目标</legend>
            <label className="cs-field" htmlFor="audit-workspaceId">
              <span>
                工作区 <b aria-hidden="true">*</b>
              </span>
              <select
                id="audit-workspaceId"
                value={values.workspaceId}
                onChange={(event) => set("workspaceId", event.target.value)}
                aria-invalid={Boolean(errors.workspaceId)}
              >
                <option value="">请选择工作区</option>
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
                目标目录 <b aria-hidden="true">*</b>
              </span>
              <input
                id="audit-targetPath"
                value={values.targetPath}
                onChange={(event) => set("targetPath", event.target.value)}
                onBlur={validatePath}
                aria-invalid={Boolean(errors.targetPath)}
                aria-describedby="audit-target-help"
              />
              <small id="audit-target-help">
                相对于所选工作区，例如 <code>packages/api</code>。
              </small>
              {errors.targetPath && (
                <small role="alert">{errors.targetPath}</small>
              )}
            </label>
          </fieldset>

          <fieldset>
            <legend>范围</legend>
            <label className="cs-field" htmlFor="audit-includePaths">
              <span>包含路径</span>
              <textarea
                id="audit-includePaths"
                value={values.includePaths}
                onChange={(event) => set("includePaths", event.target.value)}
                rows={3}
              />
              <small>每行一个快照相对路径。</small>
            </label>
            <label className="cs-field" htmlFor="audit-excludePatterns">
              <span>排除模式</span>
              <textarea
                id="audit-excludePatterns"
                value={values.excludePatterns}
                onChange={(event) => set("excludePatterns", event.target.value)}
                rows={3}
              />
              <small>每行一个 glob；留空使用插件默认排除规则。</small>
            </label>
            <details className="cs-advanced">
              <summary>高级设置</summary>
              <label className="cs-field" htmlFor="audit-maxFileBytes">
                <span>单文件上限（字节）</span>
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
            </details>
          </fieldset>

          <fieldset>
            <legend>模型</legend>
            <label className="cs-field" htmlFor="audit-model">
              <span>固定模型</span>
              <input
                id="audit-model"
                value={values.model}
                onChange={(event) => set("model", event.target.value)}
                placeholder="留空使用系统默认模型"
                aria-invalid={Boolean(errors.model)}
              />
              <small>
                可选；格式为 <code>provider/model</code>。
              </small>
              {errors.model && <small role="alert">{errors.model}</small>}
            </label>
          </fieldset>

          <fieldset>
            <legend>验证方式</legend>
            <label className="cs-toggle" htmlFor="audit-dynamicEnabled">
              <span>
                <strong>动态验证</strong>
                <small>默认关闭；开启后执行受限 Docker 探测。</small>
              </span>
              <input
                id="audit-dynamicEnabled"
                type="checkbox"
                checked={values.dynamicEnabled}
                onChange={(event) => setDynamicEnabled(event.target.checked)}
              />
            </label>
            {values.dynamicEnabled && (
              <div className="cs-dynamic-confirm">
                <Icon name="flask" />
                <div>
                  <h3>动态验证将在本地 Docker 中构建并运行受限探测</h3>
                  <p>
                    无网络 · 无主机挂载 · 只读根文件系统 · 无 capabilities ·
                    资源受限 · 仅使用本地已有镜像
                  </p>
                  <p>
                    需要 Docker CLI、可用的本地 daemon，以及快照中受支持的
                    Dockerfile。
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
                      我理解动态验证会执行快照中的受限代码，并同意继续。
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
              取消
            </button>
            <button
              className="cs-button cs-button--primary"
              type="submit"
              disabled={submitting}
            >
              {submitting
                ? "正在创建不可变快照…"
                : values.dynamicEnabled
                  ? "启动动态审计"
                  : "启动静态审计"}
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
