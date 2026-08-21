import { useEffect, useMemo, useRef, useState } from "react";

import { getArtifact, getEvidence } from "../api";
import { Icon } from "../icons";
import { shortId } from "../labels";
import type { ArtifactContent, EvidenceContent, ScanDetail } from "../types";
import { StatusBadge } from "./StatusBadge";

const tabs = [
  { id: "overview", label: "概览" },
  { id: "threat_model", label: "威胁模型" },
  { id: "candidate_index", label: "候选问题" },
  { id: "verification_index", label: "独立验证" },
  { id: "dynamic_validation", label: "动态验证" },
  { id: "adjudication", label: "父 Agent 裁决" },
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
  }, [activeTab, detail.scan.scan_id, refreshKey, visible]);

  return (
    <aside
      ref={inspectorRef}
      className={`cs-inspector${open ? " is-open" : ""}`}
      aria-label="中间产物检查器"
      aria-hidden={!wideLayout && !open ? true : undefined}
      aria-modal={!wideLayout && open ? true : undefined}
      role={!wideLayout && open ? "dialog" : undefined}
      inert={!wideLayout && !open ? true : undefined}
    >
      <div className="cs-inspector__header">
        <div>
          <p className="cs-eyebrow">Artifact Inspector</p>
          <h2 ref={titleRef} tabIndex={-1}>
            审计产物
          </h2>
        </div>
        <div className="cs-inspector__actions">
          {activeTab !== "overview" && (
            <button
              className="cs-inspector__refresh"
              type="button"
              onClick={() => setRefreshKey((value) => value + 1)}
              disabled={loading}
            >
              {loading ? "刷新中…" : "刷新"}
            </button>
          )}
          <button
            className="cs-icon-button cs-inspector__close"
            type="button"
            onClick={onClose}
            aria-label="关闭产物检查器"
          >
            <Icon name="close" />
          </button>
        </div>
      </div>
      <div className="cs-artifact-tabs" role="tablist" aria-label="审计产物">
        {tabs.map((tab) => {
          const state =
            tab.id === "overview"
              ? "available"
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
              <span>{tab.label}</span>
              <span className={`cs-artifact-state cs-artifact-state--${state}`}>
                {state === "sealed"
                  ? "已封装"
                  : state === "invalid"
                    ? "校验失败"
                    : state === "pending"
                      ? "等待"
                      : "可查看"}
              </span>
            </button>
          );
        })}
      </div>
      <div className="cs-inspector__body" role="tabpanel">
        {activeTab === "overview" ? (
          <Overview detail={detail} />
        ) : loading && !activeContent ? (
          <InspectorSkeleton />
        ) : error && !activeContent ? (
          <div className="cs-error-state" role="alert">
            <Icon name="warning" />
            <h3>产物暂不可用</h3>
            <p>{error}</p>
            <p>扫描继续运行时，可稍后点击“刷新”读取最新版本。</p>
          </div>
        ) : activeContent ? (
          <>
            {error && (
              <p className="cs-artifact-refresh-error" role="alert">
                刷新失败，仍显示上一次成功读取的内容：{error}
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
  const finalArtifacts = detail.artifacts.filter(
    (artifact) => artifact.state === "sealed",
  );
  return (
    <div className="cs-overview">
      <section>
        <h3>四项独立状态</h3>
        <dl className="cs-state-list">
          <div>
            <dt>执行状态</dt>
            <dd>
              <StatusBadge status={detail.scan.lifecycle_status} />
            </dd>
          </div>
          <div>
            <dt>产物完整性</dt>
            <dd>
              <span className="cs-value-tag">
                {detail.scan.integrity_status}
              </span>
            </dd>
          </div>
          <div>
            <dt>覆盖度</dt>
            <dd>
              <span
                className={`cs-value-tag cs-value-tag--${detail.scan.coverage_status}`}
              >
                {detail.scan.coverage_status}
              </span>
            </dd>
          </div>
          <div>
            <dt>候选问题</dt>
            <dd>{detail.counts.candidates || 0}</dd>
          </div>
        </dl>
        <p className="cs-helper">
          执行完成不等同于不存在漏洞，覆盖度也不等同于产物完整性。
        </p>
        {detail.scan.integrity_status === "invalid" && (
          <div className="cs-callout cs-callout--warning" role="alert">
            <strong>最终产物未通过完整性校验</strong>
            {(detail.scan.integrity_errors || []).map((message) => (
              <span key={message}>{message}</span>
            ))}
          </div>
        )}
      </section>
      <section>
        <h3>快照可信边界</h3>
        <dl className="cs-definition-list">
          <div>
            <dt>源码文件</dt>
            <dd>{detail.target.file_count.toLocaleString("zh-CN")}</dd>
          </div>
          <div>
            <dt>遗漏文件</dt>
            <dd>{detail.target.omitted_file_count.toLocaleString("zh-CN")}</dd>
          </div>
          <div>
            <dt>Revision</dt>
            <dd>
              <code>{shortId(detail.target.source_revision, 14)}</code>
            </dd>
          </div>
          <div>
            <dt>Tree digest</dt>
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
          <h3>已封装产物</h3>
          <div className="cs-download-list">
            {finalArtifacts.map((artifact) => (
              <a
                key={artifact.kind}
                href={artifact.download_url}
                className="cs-download-link"
              >
                <Icon name="download" />
                <span>
                  <strong>{artifact.kind}</strong>
                  <small>
                    {artifact.size_bytes?.toLocaleString("zh-CN")} bytes
                  </small>
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
  if (kind === "dynamic_validation" && !detail.scan.dynamic_enabled) {
    return (
      <div className="cs-empty-state cs-empty-state--compact">
        <Icon name="flask" />
        <h3>动态验证未启用</h3>
        <p>本次审计仅执行静态分析。若需要运行受限探测，请发起新的动态审计。</p>
      </div>
    );
  }
  if (kind === "candidate_index")
    return <CandidateList content={content} scanId={detail.scan.scan_id} />;
  if (kind === "report_markdown" && typeof content === "string") {
    return <pre className="cs-report-preview">{content}</pre>;
  }
  if (content == null || (Array.isArray(content) && !content.length)) {
    return (
      <div className="cs-inline-empty">
        该产物尚未产生。扫描继续运行时会自动更新。
      </div>
    );
  }
  return <StructuredValue value={content} />;
}

function CandidateList({
  content,
  scanId,
}: {
  content: unknown;
  scanId: string;
}) {
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
        <h3>当前阶段尚未发现候选问题</h3>
        <p>扫描仍可能在其他范围继续运行。</p>
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
              <h3 id="evidence-viewer-title">代码证据</h3>
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
              aria-label="关闭代码证据"
            >
              <Icon name="close" />
            </button>
          </div>
          {evidenceLoading ? (
            <InspectorSkeleton />
          ) : evidenceError ? (
            <p className="cs-callout cs-callout--warning" role="alert">
              {evidenceError}
            </p>
          ) : evidence ? (
            <>
              <pre>
                <code>{evidence.excerpt}</code>
              </pre>
              {evidence.truncated && (
                <p className="cs-helper">证据已按 64 KiB 上限截断。</p>
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
                {String(payload.severity || "待定")}
              </span>
              <span className="cs-value-tag">
                {finalFinding ? "最终漏洞" : "候选问题"}
              </span>
            </div>
            <h3>{String(payload.title || "未命名候选问题")}</h3>
            <p>{String(payload.summary || "")}</p>
            <dl>
              <div>
                <dt>验证状态</dt>
                <dd>{String(item.verification_status || "pending")}</dd>
              </div>
              <div>
                <dt>CWE</dt>
                <dd>
                  {Array.isArray(payload.cwe) ? payload.cwe.join(", ") : "—"}
                </dd>
              </div>
            </dl>
            {evidenceRefs.length > 0 && (
              <div className="cs-evidence-links" aria-label="候选问题代码证据">
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
                      查看证据 · {String(reference.relative_path)}:
                      {String(reference.start_line)}
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
                formatScalar(child)
              )}
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p>{formatScalar(value)}</p>;
}

function InspectorSkeleton() {
  return (
    <div className="cs-skeleton-stack" aria-label="正在加载产物">
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

function formatScalar(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}
