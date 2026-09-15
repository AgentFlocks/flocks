import { useEffect, useRef, useState } from "react";
import { useAuditApi } from "../BatchContext";
import { readApiFailure } from "../api";
import { useCodeSecurityI18n } from "../i18n";
import {
  coverageStatusLabels,
  formatDuration,
  severityLabels,
} from "../labels";
import type { ScanDetail } from "../types";
import { StatusBadge } from "./StatusBadge";
import { StructuredValue } from "./ArtifactInspector";

type Finding = {
  findingId: string;
  title: string;
  summary: string;
  severity: { level: string };
  validation: {
    conclusion?: string;
    staticConclusion?: string;
    dynamicConclusion?: string;
    summary?: string;
    limitations?: string[];
  };
  rootCause?: { summary: string };
  remediation?: unknown;
  codeEvidence?: {
    id: string;
    path: string;
    startLine: number;
    endLine: number;
    code: string;
  }[];
};

export function GeneratedReport({ detail }: { detail: ScanDetail }) {
  const { t } = useCodeSecurityI18n();
  const { getArtifact, downloadUrl } = useAuditApi();
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const detailRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const state = detail.artifacts.find(
    (artifact) => artifact.kind === "findings",
  )?.state;
  const blocked =
    detail.scan.integrity_status === "invalid" || state === "invalid";
  useEffect(() => {
    let cancelled = false;
    setFindings(null);
    setError("");
    if (blocked || !state || state === "pending") return;
    getArtifact(detail.scan.scan_id, "findings")
      .then((artifact) => {
        if (cancelled) return;
        const content = artifact.content as { findings?: Finding[] } | null;
        if (artifact.state === "invalid") throw new Error(t("产物未通过校验"));
        if (!Array.isArray(content?.findings))
          throw new Error(t("漏洞清单格式不可用"));
        setFindings(content.findings);
        setSelectedId((current) =>
          content.findings!.some((item) => item.findingId === current)
            ? current
            : "",
        );
      })
      .catch((reason) => {
        if (!cancelled)
          setError(readApiFailure(reason, t("暂时无法读取该产物")).message);
      });
    return () => {
      cancelled = true;
    };
  }, [detail.scan.scan_id, state, blocked, refresh, getArtifact, t]);
  const selected = findings?.find(
    (finding) => finding.findingId === selectedId,
  );
  useEffect(() => {
    if (!selected || !detailRef.current) return;
    const dialog = detailRef.current;
    dialog.showModal();
    return () => {
      dialog.close();
      triggerRef.current?.focus({ preventScroll: true });
    };
  }, [selected]);
  const verified = findings?.filter(
    (finding) => finding.validation.dynamicConclusion === "reproduced",
  ).length;
  const high = findings?.filter((finding) =>
    ["critical", "high"].includes(finding.severity.level),
  ).length;
  return (
    <section className="cs-final-report" aria-label={t("审计报告")}>
      <header className="cs-final-report-header">
        <div>
          <h2>
            {t("审计报告")}{" "}
            <StatusBadge status={detail.scan.lifecycle_status} />
          </h2>
          <p>
            {detail.target.display_name} · <code>{detail.scan.scan_id}</code>
          </p>
        </div>
        <div className="cs-final-report-actions">
          <button
            type="button"
            onClick={() => setRefresh((value) => value + 1)}
          >
            {t("刷新")}
          </button>
          {detail.scan.integrity_status === "valid" &&
            detail.artifacts
              .filter(
                (artifact) =>
                  artifact.kind === "sarif" &&
                  artifact.state === "sealed",
              )
              .map((artifact) => (
                <a
                  key={artifact.kind}
                  href={downloadUrl(
                    detail.scan.scan_id,
                    "report.sarif",
                  )}
                >
                  {t("导出 SARIF")}
                </a>
              ))}
        </div>
      </header>
      {!detail.scan.dynamic_enabled && (
        <p className="cs-final-report-notice">
          {t("本次为静态审计，已确认漏洞不代表已通过动态复现。")}
        </p>
      )}
      <div className="cs-final-report-metrics">
        {[
          ["报告漏洞", findings?.length],
          ["高危及严重", high],
          ["动态复现", verified],
          ["审计耗时", formatDuration(detail.scan.elapsed_ms, t)],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{t(String(label))}</span>
            <strong>{value ?? "—"}</strong>
          </div>
        ))}
      </div>
      {blocked ? (
        <p role="alert">{t("产物未通过校验")}</p>
      ) : error ? (
        <p role="alert">{error}</p>
      ) : !findings ? (
        <p role="status">
          {t(
            !state || state === "pending"
              ? "报告生成后将在此展示。"
              : "正在读取审计报告…",
          )}
        </p>
      ) : (
        <>
          <section className="cs-final-report-panel">
            <h3>
              {t("漏洞清单")} <small>{findings.length}</small>
            </h3>
            {findings.length ? (
              <div className="cs-final-report-table">
                <table>
                  <thead>
                    <tr>
                      {["漏洞", "严重程度", "验证状态", "操作"].map((label) => (
                        <th key={label}>{t(label)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {findings.map((finding) => (
                      <tr
                        key={finding.findingId}
                        className={
                          selectedId === finding.findingId ? "is-selected" : ""
                        }
                      >
                        <td>
                          {finding.title}
                          <small>{finding.findingId}</small>
                        </td>
                        <td>
                          <span
                            className={`cs-severity cs-severity--${finding.severity.level}`}
                          >
                            {t(
                              severityLabels[finding.severity.level] ||
                                finding.severity.level,
                            )}
                          </span>
                        </td>
                        <td>
                          {t(
                            finding.validation.dynamicConclusion ===
                              "reproduced"
                              ? "已复现"
                              : finding.validation.dynamicConclusion ===
                                  "not_reproduced"
                                ? "未复现"
                                : finding.validation.dynamicConclusion ===
                                    "inconclusive"
                                  ? "动态验证未定"
                                  : (finding.validation.staticConclusion ||
                                        finding.validation.conclusion) ===
                                      "confirmed"
                                    ? "静态确认"
                                    : "待确认",
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            aria-haspopup="dialog"
                            onClick={(event) => {
                              triggerRef.current = event.currentTarget;
                              setSelectedId(finding.findingId);
                            }}
                          >
                            {t("查看详情")}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p>{t("未发现可报告的漏洞。")}</p>
            )}
          </section>
          {selected && (
            <dialog
              className="cs-finding-drawer"
              ref={detailRef}
              aria-label={t("漏洞详情")}
              onCancel={() => setSelectedId("")}
              onClose={() => setSelectedId("")}
              onClick={(event) => {
                if (event.target === event.currentTarget) setSelectedId("");
              }}
            >
              <header className="cs-finding-drawer-header">
                <h3>{selected.title}</h3>
                <button type="button" onClick={() => setSelectedId("")}>{t("关闭")}</button>
              </header>
              <div className="cs-final-report-detail">
                <p>{selected.summary}</p>
                {selected.rootCause?.summary && (
                  <>
                    <h4>{t("根因分析")}</h4>
                    <p>{selected.rootCause.summary}</p>
                  </>
                )}
                {selected.validation.summary && (
                  <>
                    <h4>{t("验证结论")}</h4>
                    <p>{selected.validation.summary}</p>
                  </>
                )}
                {selected.codeEvidence?.map((evidence) => (
                  <div key={evidence.id}>
                    <h4>
                      <code>
                        {evidence.path}:{evidence.startLine}-{evidence.endLine}
                      </code>
                    </h4>
                    <pre tabIndex={0}>
                      <code>{evidence.code}</code>
                    </pre>
                  </div>
                ))}
                {selected.remediation != null && (
                  <>
                    <h4>{t("修复建议")}</h4>
                    <StructuredValue value={selected.remediation} />
                  </>
                )}
                {!!selected.validation.limitations?.length && (
                  <>
                    <h4>{t("验证限制")}</h4>
                    <ul>
                      {selected.validation.limitations.map((item, index) => (
                        <li key={index}>{item}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </dialog>
          )}
        </>
      )}
      <section className="cs-final-report-panel">
        <h3>{t("审计范围与版本")}</h3>
        <dl className="cs-final-report-metadata">
          {[
            ["审计对象", detail.target.display_name],
            ["快照文件", detail.target.file_count],
            ["源码版本", detail.target.source_revision || "—"],
            ["快照指纹", detail.target.tree_digest],
            [
              "覆盖状态",
              t(
                coverageStatusLabels[detail.coverageSummary.completeness] ||
                  detail.coverageSummary.completeness,
              ),
            ],
            ["待补充项", detail.coverageSummary.deferred_count],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{t(String(label))}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </section>
  );
}
