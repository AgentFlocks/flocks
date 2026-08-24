import { useEffect, useMemo, useRef, useState } from "react";

import { getArtifact, getEvidence } from "../api";
import { Icon } from "../icons";
import { useCodeSecurityI18n, type Translator } from "../i18n";
import {
  coverageStatusLabels,
  formatFileSize,
  integrityStatusLabels,
  severityLabels,
  shortId,
  verdictLabels,
} from "../labels";
import type { ArtifactContent, EvidenceContent, ScanDetail } from "../types";
import { StatusBadge } from "./StatusBadge";

const tabs = [
  { id: "overview", label: "概览" },
  { id: "threat_model", label: "威胁模型" },
  { id: "candidate_index", label: "候选漏洞" },
  { id: "verification_index", label: "静态验证" },
  { id: "dynamic_validation", label: "动态验证" },
  { id: "adjudication", label: "主智能体裁决" },
  { id: "coverage", label: "覆盖度" },
  { id: "report_markdown", label: "最终报告" },
];

export function ArtifactInspector({
  detail,
  activeTab,
  onTabChange,
  open,
  onClose,
}: {
  detail: ScanDetail;
  activeTab: string;
  onTabChange: (tab: string) => void;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useCodeSecurityI18n();
  const [content, setContent] = useState<{
    scanId: string;
    artifact: ArtifactContent;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [wideLayout, setWideLayout] = useState(
    () => window.matchMedia?.("(min-width: 1440px)").matches ?? false,
  );
  const inspectorRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const states = useMemo(
    () =>
      new Map(
        detail.artifacts.map((artifact) => [artifact.kind, artifact.state]),
      ),
    [detail.artifacts],
  );
  const visible = wideLayout || open;
  const dynamicValidationDisabled =
    activeTab === "dynamic_validation" && !detail.scan.dynamic_enabled;
  const activeState =
    activeTab === "overview"
      ? "available"
      : dynamicValidationDisabled
        ? "disabled"
        : states.get(activeTab) || "pending";
  const blockedByIntegrity = activeState === "invalid";
  const activeContent =
    content?.scanId === detail.scan.scan_id &&
    content.artifact.kind === activeTab
      ? content.artifact
      : null;

  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const media = window.matchMedia("(min-width: 1440px)");
    const update = () => setWideLayout(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    if (wideLayout || !open) return undefined;
    window.setTimeout(() => titleRef.current?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !inspectorRef.current) return;
      const focusable = Array.from(
        inspectorRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), a[href]",
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
  }, [onClose, open, wideLayout]);

  useEffect(() => {
    if (activeTab === "overview") {
      setContent(null);
      setLoading(false);
      return;
    }
    if (!visible) return;
    if (dynamicValidationDisabled) {
      setContent(null);
      setLoading(false);
      setError("");
      return;
    }
    if (blockedByIntegrity) {
      setContent(null);
      setLoading(false);
      setError("该产物未通过完整性校验，不能作为可信产物展示。");
      return;
    }
    const scanId = detail.scan.scan_id;
    let cancelled = false;
    setLoading(true);
    setError("");
    getArtifact(scanId, activeTab)
      .then((value) => {
        if (!cancelled) setContent({ scanId, artifact: value });
      })
      .catch((reason) => {
        if (!cancelled) {
          setContent((current) =>
            current?.scanId === scanId && current.artifact.kind === activeTab
              ? current
              : null,
          );
          setError(
            reason?.response?.data?.detail?.message ||
              reason?.message ||
              "暂时无法读取该产物",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    activeTab,
    blockedByIntegrity,
    detail.scan.scan_id,
    dynamicValidationDisabled,
    refreshKey,
    visible,
  ]);

  return (
    <aside
      ref={inspectorRef}
      className={`cs-inspector${open ? " is-open" : ""}`}
      aria-label={t("中间产物检查器")}
      aria-hidden={!wideLayout && !open ? true : undefined}
      aria-modal={!wideLayout && open ? true : undefined}
      role={!wideLayout && open ? "dialog" : undefined}
      inert={!wideLayout && !open ? true : undefined}
    >
      <div className="cs-inspector__header">
        <div>
          <p className="cs-eyebrow">{t("产物检查器")}</p>
          <h2 ref={titleRef} tabIndex={-1}>
            {t("审计产物")}
          </h2>
        </div>
        <div className="cs-inspector__actions">
          {activeTab !== "overview" &&
            !blockedByIntegrity &&
            !dynamicValidationDisabled && (
              <button
                className="cs-inspector__refresh"
                type="button"
                onClick={() => setRefreshKey((value) => value + 1)}
                disabled={loading}
              >
                {t(loading ? "刷新中…" : "刷新")}
              </button>
            )}
          <button
            className="cs-icon-button cs-inspector__close"
            type="button"
            onClick={onClose}
            aria-label={t("关闭产物检查器")}
          >
            <Icon name="close" />
          </button>
        </div>
      </div>
      <div
        className="cs-artifact-tabs"
        role="tablist"
        aria-label={t("审计产物")}
      >
        {tabs.map((tab) => {
          const state =
            tab.id === "overview"
              ? "available"
              : tab.id === "dynamic_validation" && !detail.scan.dynamic_enabled
                ? "disabled"
                : states.get(tab.id) || "pending";
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={activeTab === tab.id ? "is-selected" : ""}
              onClick={() => onTabChange(tab.id)}
            >
              <span>{t(tab.label)}</span>
              <span className={`cs-artifact-state cs-artifact-state--${state}`}>
                {t(
                  state === "sealed"
                    ? "已完成"
                    : state === "invalid"
                      ? "校验失败"
                      : state === "disabled"
                        ? "未启用"
                        : state === "pending"
                          ? "等待中"
                          : "可查看",
                )}
              </span>
            </button>
          );
        })}
      </div>
      <div className="cs-inspector__body" role="tabpanel">
        {activeTab === "overview" ? (
          <Overview detail={detail} />
        ) : dynamicValidationDisabled ? (
          <DynamicValidationDisabled />
        ) : loading && !activeContent ? (
          <InspectorSkeleton />
        ) : error && !activeContent ? (
          <div className="cs-error-state" role="alert">
            <Icon name="warning" />
            <h3>{t(blockedByIntegrity ? "产物未通过校验" : "产物暂不可用")}</h3>
            <p>{t(error)}</p>
            <p>
              {t(
                blockedByIntegrity
                  ? "结构化中间数据仍可从其他标签查看；最终报告需要重新发起审计后生成。"
                  : "扫描继续运行时，可稍后点击“刷新”读取最新版本。",
              )}
            </p>
          </div>
        ) : activeContent ? (
          <>
            {error && (
              <p className="cs-artifact-refresh-error" role="alert">
                {t("刷新失败，仍显示上一次成功读取的内容：{{error}}", {
                  error: t(error),
                })}
              </p>
            )}
            <ArtifactBody
              kind={activeTab}
              content={activeContent.content}
              detail={detail}
            />
          </>
        ) : (
          <InspectorSkeleton />
        )}
      </div>
    </aside>
  );
}

function Overview({ detail }: { detail: ScanDetail }) {
  const { language, t } = useCodeSecurityI18n();
  const finalArtifacts = detail.artifacts.filter(
    (artifact) => artifact.state === "sealed",
  );
  return (
    <div className="cs-overview">
      <section>
        <h3>{t("执行状态")}</h3>
        <dl className="cs-state-list">
          <div>
            <dt>{t("执行状态")}</dt>
            <dd>
              <StatusBadge status={detail.scan.lifecycle_status} />
            </dd>
          </div>
          <div>
            <dt>{t("产物完整性")}</dt>
            <dd>
              <span className="cs-value-tag">
                {t(
                  integrityStatusLabels[detail.scan.integrity_status] || "未知",
                )}
              </span>
            </dd>
          </div>
          <div>
            <dt>{t("覆盖度")}</dt>
            <dd>
              <span
                className={`cs-value-tag cs-value-tag--${detail.scan.coverage_status}`}
              >
                {t(coverageStatusLabels[detail.scan.coverage_status] || "未知")}
              </span>
            </dd>
          </div>
          <div>
            <dt>{t("候选漏洞")}</dt>
            <dd>{detail.counts.candidates || 0}</dd>
          </div>
        </dl>
        <p className="cs-helper">
          {t("执行完成不等同于不存在漏洞，覆盖度也不等同于产物完整性。")}
        </p>
        {detail.scan.integrity_status === "invalid" && (
          <div className="cs-callout cs-callout--warning" role="alert">
            <strong>{t("最终产物未通过完整性校验")}</strong>
            {(detail.scan.integrity_errors || []).map((message) => (
              <span key={message}>{formatIntegrityError(message, t)}</span>
            ))}
          </div>
        )}
      </section>
      <section>
        <h3>{t("快照可信边界")}</h3>
        <dl className="cs-definition-list">
          <div>
            <dt>{t("源码文件")}</dt>
            <dd>{detail.target.file_count.toLocaleString(language)}</dd>
          </div>
          <div>
            <dt>{t("遗漏文件")}</dt>
            <dd>{detail.target.omitted_file_count.toLocaleString(language)}</dd>
          </div>
          <div>
            <dt>{t("版本")}</dt>
            <dd>
              <code>{shortId(detail.target.source_revision, 14)}</code>
            </dd>
          </div>
          <div>
            <dt>{t("目录摘要")}</dt>
            <dd>
              <code title={detail.target.tree_digest}>
                {shortId(detail.target.tree_digest, 18)}
              </code>
            </dd>
          </div>
        </dl>
      </section>
      {finalArtifacts.length > 0 && (
        <section>
          <h3>{t("已完成产物")}</h3>
          <div className="cs-download-list">
            {finalArtifacts.map((artifact) => (
              <a
                key={artifact.kind}
                href={artifact.download_url}
                className="cs-download-link"
              >
                <Icon name="download" />
                <span>
                  <strong>
                    {t(
                      tabs.find((tab) => tab.id === artifact.kind)?.label ||
                        artifact.kind,
                    )}
                  </strong>
                  <small>{formatFileSize(artifact.size_bytes, language)}</small>
                </span>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function ArtifactBody({
  kind,
  content,
  detail,
}: {
  kind: string;
  content: unknown;
  detail: ScanDetail;
}) {
  const { t } = useCodeSecurityI18n();
  if (kind === "dynamic_validation" && !detail.scan.dynamic_enabled) {
    return <DynamicValidationDisabled />;
  }
  if (kind === "candidate_index")
    return <CandidateList content={content} scanId={detail.scan.scan_id} />;
  if (kind === "report_markdown" && typeof content === "string") {
    return <MarkdownReport content={content} />;
  }
  if (content == null || (Array.isArray(content) && !content.length)) {
    return (
      <div className="cs-inline-empty">
        {t("该产物尚未产生。扫描继续运行时会自动更新。")}
      </div>
    );
  }
  return <StructuredValue value={content} />;
}

function DynamicValidationDisabled() {
  const { t } = useCodeSecurityI18n();
  return (
    <div className="cs-empty-state cs-empty-state--compact">
      <Icon name="flask" />
      <h3>{t("动态验证未启用")}</h3>
      <p>
        {t("本次审计仅执行静态分析。若需要运行受限探测，请发起新的动态审计。")}
      </p>
    </div>
  );
}

function MarkdownReport({ content }: { content: string }) {
  const { t } = useCodeSecurityI18n();
  const Markdown = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__?.Markdown;
  if (!Markdown) {
    return <pre className="cs-report-fallback">{content}</pre>;
  }
  return (
    <article className="cs-report-markdown" aria-label={t("最终审计报告")}>
      <Markdown content={content} />
    </article>
  );
}

function CandidateList({
  content,
  scanId,
}: {
  content: unknown;
  scanId: string;
}) {
  const { t } = useCodeSecurityI18n();
  const candidates = Array.isArray(content) ? content : [];
  const [evidence, setEvidence] = useState<EvidenceContent | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState("");
  const evidenceRequestRef = useRef(0);
  useEffect(() => {
    evidenceRequestRef.current += 1;
    setEvidence(null);
    setEvidenceError("");
    setEvidenceLoading(false);
  }, [scanId]);
  const openEvidence = async (evidenceId: string) => {
    const requestId = evidenceRequestRef.current + 1;
    evidenceRequestRef.current = requestId;
    setEvidenceLoading(true);
    setEvidenceError("");
    try {
      const value = await getEvidence(scanId, evidenceId);
      if (evidenceRequestRef.current === requestId) setEvidence(value);
    } catch (reason: any) {
      if (evidenceRequestRef.current === requestId) {
        setEvidenceError(
          reason?.response?.data?.detail?.message ||
            reason?.message ||
            "无法读取代码证据",
        );
      }
    } finally {
      if (evidenceRequestRef.current === requestId) setEvidenceLoading(false);
    }
  };
  const closeEvidence = () => {
    evidenceRequestRef.current += 1;
    setEvidence(null);
    setEvidenceError("");
    setEvidenceLoading(false);
  };
  if (!candidates.length) {
    return (
      <div className="cs-empty-state cs-empty-state--compact">
        <Icon name="search" />
        <h3>{t("当前阶段尚未发现候选漏洞")}</h3>
        <p>{t("扫描仍可能在其他范围继续运行。")}</p>
      </div>
    );
  }
  return (
    <div className="cs-candidate-list">
      {(evidence || evidenceLoading || evidenceError) && (
        <section
          className="cs-evidence-viewer"
          aria-labelledby="evidence-viewer-title"
        >
          <div className="cs-subsection-heading">
            <div>
              <h3 id="evidence-viewer-title">{t("代码证据")}</h3>
              {evidence && (
                <code>
                  {evidence.relative_path}:{evidence.start_line}-
                  {evidence.end_line}
                </code>
              )}
            </div>
            <button
              className="cs-icon-button"
              type="button"
              onClick={closeEvidence}
              aria-label={t("关闭代码证据")}
            >
              <Icon name="close" />
            </button>
          </div>
          {evidenceLoading ? (
            <InspectorSkeleton />
          ) : evidenceError ? (
            <p className="cs-callout cs-callout--warning" role="alert">
              {t(evidenceError)}
            </p>
          ) : evidence ? (
            <>
              <pre>
                <code>{evidence.excerpt}</code>
              </pre>
              {evidence.truncated && (
                <p className="cs-helper">{t("证据已按 64 KiB 上限截断。")}</p>
              )}
            </>
          ) : null}
        </section>
      )}
      {candidates.map((raw, index) => {
        const item = asRecord(raw);
        const payload = asRecord(item.payload);
        const finalFinding = item.final_finding === true;
        const evidenceRefs = Array.isArray(item.evidence) ? item.evidence : [];
        return (
          <article
            key={String(item.candidate_id || index)}
            className="cs-candidate-card"
          >
            <div className="cs-candidate-card__top">
              <span
                className={`cs-severity cs-severity--${payload.severity || "unknown"}`}
              >
                {t(
                  severityLabels[
                    String(payload.severity || "").toLowerCase()
                  ] || String(payload.severity || "待定"),
                )}
              </span>
              <span className="cs-value-tag">
                {t(finalFinding ? "漏洞" : "候选漏洞")}
              </span>
            </div>
            <h3>{String(payload.title || t("未命名候选漏洞"))}</h3>
            <p>{String(payload.summary || "")}</p>
            <dl>
              <div>
                <dt>{t("验证状态")}</dt>
                <dd>
                  {t(
                    verdictLabels[
                      String(item.verification_status || "pending")
                    ] || String(item.verification_status || "pending"),
                  )}
                </dd>
              </div>
              <div>
                <dt>CWE</dt>
                <dd>
                  {Array.isArray(payload.cwe) ? payload.cwe.join(", ") : "—"}
                </dd>
              </div>
            </dl>
            {evidenceRefs.length > 0 && (
              <div
                className="cs-evidence-links"
                aria-label={t("候选漏洞代码证据")}
              >
                {evidenceRefs.map((rawEvidence, evidenceIndex) => {
                  const reference = asRecord(rawEvidence);
                  return (
                    <button
                      key={String(reference.evidence_id || evidenceIndex)}
                      type="button"
                      onClick={() =>
                        openEvidence(String(reference.evidence_id))
                      }
                    >
                      {t("查看证据 · {{path}}:{{start}}-{{end}}", {
                        path: String(reference.relative_path),
                        start: String(reference.start_line),
                        end: String(reference.end_line || reference.start_line),
                      })}
                    </button>
                  );
                })}
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

function StructuredValue({ value }: { value: unknown }) {
  const { t } = useCodeSecurityI18n();
  if (Array.isArray(value)) {
    return (
      <div className="cs-structured-list">
        {value.map((item, index) => (
          <StructuredValue key={index} value={item} />
        ))}
      </div>
    );
  }
  if (value && typeof value === "object") {
    return (
      <dl className="cs-structured-object">
        {Object.entries(value).map(([key, child]) => (
          <div key={key}>
            <dt>{humanize(key)}</dt>
            <dd>
              {child && typeof child === "object" ? (
                <StructuredValue value={child} />
              ) : (
                formatScalar(child, t)
              )}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p>{formatScalar(value, t)}</p>;
}

function InspectorSkeleton() {
  const { t } = useCodeSecurityI18n();
  return (
    <div className="cs-skeleton-stack" aria-label={t("正在加载产物")}>
      <span />
      <span />
      <span />
    </div>
  );
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, any>)
    : {};
}

function humanize(value: string): string {
  return value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ");
}

function formatIntegrityError(message: string, t: Translator): string {
  const missingPrefix = "Required sealed artifacts are missing:";
  if (message.startsWith(missingPrefix)) {
    return t("完整性清单未封装以下必需产物：{{artifacts}}。", {
      artifacts: message.slice(missingPrefix.length).trim(),
    });
  }
  if (message === "Completed scan output is missing")
    return t("已完成扫描的产物目录不存在。");
  if (message === "Scan manifest is missing")
    return t("产物完整性清单不存在。");
  return message;
}

function formatScalar(value: unknown, t: Translator): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return t(value ? "是" : "否");
  return String(value);
}
