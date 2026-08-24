import { useMemo, useState } from "react";

import { formatDuration, formatTime, phaseLabels, shortId } from "../labels";
import type { PhaseRun, ScanDetail, WorkerRun } from "../types";
import { Icon } from "../icons";
import { EventStream } from "./EventStream";
import type { AuditEvent } from "../types";
import { ElapsedTime } from "./ElapsedTime";
import { StatusBadge } from "./StatusBadge";

const PHASE_ORDER = [
  "snapshot",
  "threat_modeling",
  "baseline",
  "verification",
  "dynamic_validation",
  "adjudication",
  "targeted_rescan",
  "finalization",
];

const roleLabels: Record<string, string> = {
  threat_modeler: "威胁建模员",
  baseline: "基线分析员",
  investigator: "定向调查员",
  verifier: "静态验证员",
  prober: "动态探测员",
};

const severityLabels: Record<string, string> = {
  critical: "严重",
  high: "高危",
  medium: "中危",
  low: "低危",
};

const verdictLabels: Record<string, string> = {
  confirmed: "已确认",
  rejected: "已驳回",
  insufficient_evidence: "证据不足",
};

const sourceActivityLabels: Array<[string, string]> = [
  ["inventory", "清单"],
  ["search", "搜索"],
  ["read", "读取"],
];

const artifactStateLabels: Record<string, string> = {
  pending: "等待生成",
  partial: "生成中",
  available: "可查看",
  sealed: "已封装",
  invalid: "校验失败",
};

export function PhaseWorkspace({
  phases,
  events,
  workers,
  currentPhase,
  snapshotBoundary,
  artifactBundle,
  dynamicValidationStatus,
  finalFindingCount = null,
  finalFindingBasis = "审计完成后确定",
  hasOlderEvents = false,
  loadingEvents = false,
  loadingOlderEvents = false,
  onLoadOlderEvents = async () => undefined,
}: {
  phases: PhaseRun[];
  events: AuditEvent[];
  workers: WorkerRun[];
  currentPhase?: string | null;
  snapshotBoundary?: ScanDetail["target"];
  artifactBundle?: {
    artifacts: ScanDetail["artifacts"];
    integrityStatus: string;
  };
  dynamicValidationStatus?: string;
  finalFindingCount?: number | null;
  finalFindingBasis?: string;
  hasOlderEvents?: boolean;
  loadingEvents?: boolean;
  loadingOlderEvents?: boolean;
  onLoadOlderEvents?: () => Promise<void>;
}) {
  const sorted = useMemo(
    () =>
      [...phases].sort((a, b) => {
        const byTime = (a.started_at || a.created_at || "").localeCompare(
          b.started_at || b.created_at || "",
        );
        return (
          byTime ||
          PHASE_ORDER.indexOf(a.phase) - PHASE_ORDER.indexOf(b.phase) ||
          a.ordinal - b.ordinal
        );
      }),
    [phases],
  );
  const defaultId =
    sorted.find(
      (phase) => phase.phase === currentPhase && phase.status === "running",
    )?.phase_run_id ||
    [...sorted].reverse().find((phase) => phase.status !== "pending")
      ?.phase_run_id ||
    sorted[0]?.phase_run_id;
  const [selectedId, setSelectedId] = useState<string | undefined>(defaultId);
  const selected =
    sorted.find((phase) => phase.phase_run_id === selectedId) ||
    sorted.find((phase) => phase.phase_run_id === defaultId);
  const sealedArtifactCount =
    artifactBundle?.artifacts.filter((artifact) => artifact.state === "sealed")
      .length || 0;
  const selectedHasOperationalDetails =
    selected?.phase !== "snapshot" && selected?.phase !== "finalization";

  return (
    <section className="cs-execution" aria-labelledby="execution-title">
      <div className="cs-section-heading">
        <div>
          <h2 id="execution-title">阶段与实时事件</h2>
        </div>
        <div
          className="cs-final-findings"
          aria-label={
            finalFindingCount === null
              ? `最终漏洞数，${finalFindingBasis}`
              : `最终漏洞数 ${finalFindingCount} 个，${finalFindingBasis}`
          }
        >
          <div>
            <span>最终漏洞数</span>
            <small>{finalFindingBasis}</small>
          </div>
          <strong className="cs-tabular">
            {finalFindingCount === null ? "—" : finalFindingCount}
            {finalFindingCount !== null && <small>个</small>}
          </strong>
        </div>
      </div>

      <div className="cs-phase-rail" role="tablist" aria-label="审计阶段">
        {sorted.map((phase) => {
          const displayStatus =
            phase.phase === "dynamic_validation" &&
            dynamicValidationStatus === "not_runnable"
              ? "not_runnable"
              : phase.status;
          const workerDone = phase.worker_status_counts?.completed || 0;
          const workerTotal =
            phase.worker_count ||
            Object.values(phase.worker_status_counts || {}).reduce(
              (sum, value) => sum + value,
              0,
            );
          return (
            <button
              key={phase.phase_run_id}
              type="button"
              role="tab"
              aria-selected={selected?.phase_run_id === phase.phase_run_id}
              className={`cs-phase-step${selected?.phase_run_id === phase.phase_run_id ? " is-selected" : ""}`}
              onClick={() => setSelectedId(phase.phase_run_id)}
            >
              <StatusBadge
                status={displayStatus}
                context={`${phaseLabels[phase.phase] || phase.phase}阶段`}
              />
              <strong>{phaseLabels[phase.phase] || phase.phase}</strong>
              <span className="cs-tabular">
                {workerTotal ? `${workerDone}/${workerTotal} Worker · ` : ""}
                {phase.started_at ? (
                  <ElapsedTime
                    startedAt={phase.started_at}
                    finishedAt={phase.finished_at}
                    initialMs={phase.duration_ms || 0}
                    running={phase.status === "running"}
                    prefix=""
                  />
                ) : (
                  formatDuration(phase.duration_ms)
                )}
              </span>
            </button>
          );
        })}
      </div>

      {selected ? (
        <article className="cs-current-phase" role="tabpanel">
          <div className="cs-current-phase__header">
            <div>
              <span className="cs-kicker">当前查看</span>
              <h3>{phaseLabels[selected.phase] || selected.phase}</h3>
            </div>
            <StatusBadge
              status={
                selected.phase === "dynamic_validation" &&
                dynamicValidationStatus === "not_runnable"
                  ? "not_runnable"
                  : selected.status
              }
              context="阶段状态"
            />
          </div>
          <dl className="cs-metric-grid">
            <div>
              <dt>开始时间</dt>
              <dd>{formatTime(selected.started_at)}</dd>
            </div>
            <div>
              <dt>结束时间</dt>
              <dd>{formatTime(selected.finished_at)}</dd>
            </div>
            <div>
              <dt>阶段耗时</dt>
              <dd className="cs-tabular">
                {selected.started_at ? (
                  <ElapsedTime
                    startedAt={selected.started_at}
                    finishedAt={selected.finished_at}
                    initialMs={selected.duration_ms || 0}
                    running={selected.status === "running"}
                    prefix=""
                  />
                ) : (
                  formatDuration(selected.duration_ms)
                )}
              </dd>
            </div>
            <div>
              <dt>
                {selected.phase === "snapshot"
                  ? "快照大小"
                  : selected.phase === "finalization"
                    ? "已封装产物"
                    : "Worker"}
              </dt>
              <dd className="cs-tabular">
                {selected.phase === "snapshot"
                  ? snapshotBoundary
                    ? formatFileSize(snapshotBoundary.total_bytes)
                    : "—"
                  : selected.phase === "finalization"
                    ? `${sealedArtifactCount} 个`
                    : (selected.worker_count ?? "—")}
              </dd>
            </div>
          </dl>
          {selected.status === "skipped" && (
            <p className="cs-callout cs-callout--muted">
              启动审计时未启用动态验证。
            </p>
          )}
          {selected.status === "partial" && (
            <p className="cs-callout cs-callout--warning">
              该阶段仅部分完成，请结合覆盖度与限制项判断结果。
            </p>
          )}
        </article>
      ) : (
        <div className="cs-inline-empty">阶段信息将在快照创建后出现。</div>
      )}

      {selected?.phase === "snapshot" ? (
        snapshotBoundary ? (
          <SnapshotBoundary boundary={snapshotBoundary} />
        ) : (
          <div className="cs-inline-empty">
            快照可信边界将在源码快照创建后出现。
          </div>
        )
      ) : selected?.phase === "finalization" ? (
        artifactBundle ? (
          <ArtifactBundleSummary bundle={artifactBundle} />
        ) : (
          <div className="cs-inline-empty">
            封装结果将在最终产物生成后出现。
          </div>
        )
      ) : (
        <WorkerList
          dynamicValidationStatus={dynamicValidationStatus}
          workers={
            selected
              ? workers.filter((worker) => worker.phase === selected.phase)
              : workers
          }
        />
      )}
      {selectedHasOperationalDetails && (
        <EventStream
          key={selected?.phase_run_id || "all-events"}
          events={events}
          selectedPhase={selected?.phase}
          hasOlder={hasOlderEvents}
          loading={loadingEvents}
          loadingOlder={loadingOlderEvents}
          onLoadOlder={onLoadOlderEvents}
        />
      )}
    </section>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes.toLocaleString("zh-CN")} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

function SnapshotBoundary({ boundary }: { boundary: ScanDetail["target"] }) {
  return (
    <section
      className="cs-phase-summary cs-snapshot-boundary"
      aria-labelledby="snapshot-boundary-title"
    >
      <div className="cs-subsection-heading cs-phase-summary__heading">
        <div>
          <h3 id="snapshot-boundary-title">
            <Icon name="shield" />
            快照可信边界
          </h3>
          <span>后续审计仅基于此不可变源码快照</span>
        </div>
      </div>
      <dl className="cs-phase-summary__grid">
        <div>
          <dt>纳入文件</dt>
          <dd className="cs-tabular">
            {boundary.file_count.toLocaleString("zh-CN")}
          </dd>
        </div>
        <div>
          <dt>遗漏文件</dt>
          <dd className="cs-tabular">
            {boundary.omitted_file_count.toLocaleString("zh-CN")}
          </dd>
        </div>
        <div>
          <dt>Revision</dt>
          <dd>
            <code title={boundary.source_revision || undefined}>
              {shortId(boundary.source_revision, 18)}
            </code>
          </dd>
        </div>
        <div>
          <dt>Tree digest</dt>
          <dd>
            <code title={boundary.tree_digest}>
              {shortId(boundary.tree_digest, 18)}
            </code>
          </dd>
        </div>
      </dl>
      <p className="cs-phase-summary__note">
        工作区后续发生的文件变化不会影响本次审计结果，所有 Agent
        共享同一份固定内容与版本标识。
      </p>
    </section>
  );
}

function ArtifactBundleSummary({
  bundle,
}: {
  bundle: {
    artifacts: ScanDetail["artifacts"];
    integrityStatus: string;
  };
}) {
  const sealedArtifacts = bundle.artifacts.filter(
    (artifact) => artifact.state === "sealed",
  );
  const totalBytes = sealedArtifacts.reduce(
    (sum, artifact) => sum + (artifact.size_bytes || 0),
    0,
  );
  const finalReport = bundle.artifacts.find(
    (artifact) => artifact.kind === "report_markdown",
  );
  const integrityState =
    bundle.integrityStatus === "valid"
      ? "sealed"
      : bundle.integrityStatus === "invalid"
        ? "invalid"
        : "pending";
  const integrityLabel =
    bundle.integrityStatus === "valid"
      ? "校验通过"
      : bundle.integrityStatus === "invalid"
        ? "校验失败"
        : "等待校验";

  return (
    <section
      className={`cs-phase-summary cs-artifact-bundle cs-artifact-bundle--${integrityState}`}
      aria-labelledby="artifact-bundle-title"
    >
      <div className="cs-subsection-heading cs-phase-summary__heading">
        <div>
          <h3 id="artifact-bundle-title">
            <Icon name="report" />
            封装结果
          </h3>
          <span>
            {bundle.integrityStatus === "valid"
              ? "最终产物已经固定并通过摘要校验"
              : bundle.integrityStatus === "invalid"
                ? "最终产物未通过完整性校验"
                : "正在生成并校验最终产物"}
          </span>
        </div>
      </div>
      <dl className="cs-phase-summary__grid">
        <div>
          <dt>已封装</dt>
          <dd className="cs-tabular">{sealedArtifacts.length} 个</dd>
        </div>
        <div>
          <dt>已封装大小</dt>
          <dd className="cs-tabular">
            {sealedArtifacts.length ? formatFileSize(totalBytes) : "—"}
          </dd>
        </div>
        <div>
          <dt>完整性</dt>
          <dd>
            <span
              className={`cs-artifact-state cs-artifact-state--${integrityState}`}
            >
              {integrityLabel}
            </span>
          </dd>
        </div>
        <div>
          <dt>最终报告</dt>
          <dd>
            <span
              className={`cs-artifact-state cs-artifact-state--${finalReport?.state || "pending"}`}
            >
              {artifactStateLabels[finalReport?.state || "pending"]}
            </span>
          </dd>
        </div>
      </dl>
      <p
        className={`cs-phase-summary__note${bundle.integrityStatus === "invalid" ? " cs-phase-summary__note--danger" : ""}`}
      >
        {bundle.integrityStatus === "valid"
          ? "报告、SARIF、漏洞清单、覆盖度和审计清单等产物已封装，可从审计产物面板查看或下载。"
          : bundle.integrityStatus === "invalid"
            ? "当前封装结果不可作为可信最终输出，请结合审计产物面板中的校验信息重新发起审计。"
            : "产物仍在生成中，完整性校验完成后会自动更新封装状态。"}
      </p>
    </section>
  );
}

function WorkerList({
  workers,
  dynamicValidationStatus,
}: {
  workers: WorkerRun[];
  dynamicValidationStatus?: string;
}) {
  return (
    <section className="cs-workers" aria-labelledby="workers-title">
      <div className="cs-subsection-heading">
        <div>
          <h3 id="workers-title">Worker 工作单元</h3>
          <span>{workers.length} 个</span>
        </div>
      </div>
      {workers.length ? (
        <div className="cs-worker-list">
          {workers.map((worker) => {
            const candidate =
              worker.role === "verifier"
                ? worker.candidate_summaries?.[0]
                : undefined;
            const activityCounts = worker.activity_counts || {};
            const sourceActivityCount = sourceActivityLabels.reduce(
              (total, [operation]) => total + (activityCounts[operation] || 0),
              0,
            );
            const isFullSnapshot =
              worker.paths.length === 1 && worker.paths[0] === ".";
            const verdict = candidate?.verdict || "pending";
            const displayStatus =
              worker.role === "prober" &&
              worker.status === "completed" &&
              dynamicValidationStatus === "not_runnable"
                ? "not_runnable"
                : worker.status === "running" && !worker.started_at
                  ? "pending"
                  : worker.status;

            return (
              <article key={worker.work_unit_id} className="cs-worker-card">
                <div className="cs-worker-card__heading">
                  <div>
                    <strong>{roleLabels[worker.role] || worker.role}</strong>
                    <code title={worker.work_unit_id}>
                      {shortId(worker.work_unit_id, 18)}
                    </code>
                  </div>
                  <StatusBadge status={displayStatus} context="Worker 状态" />
                </div>

                {candidate && (
                  <section
                    className="cs-worker-candidate"
                    aria-label="验证对象"
                  >
                    <div className="cs-worker-candidate__heading">
                      <h4>
                        {candidate.title || shortId(candidate.candidate_id, 24)}
                      </h4>
                      <div className="cs-worker-candidate__meta">
                        {candidate.severity && (
                          <span
                            className={`cs-severity cs-severity--${candidate.severity}`}
                          >
                            {severityLabels[candidate.severity] ||
                              candidate.severity}
                          </span>
                        )}
                        <span
                          className={`cs-worker-verdict cs-worker-verdict--${verdict}`}
                        >
                          {verdictLabels[verdict] || "等待结论"}
                        </span>
                      </div>
                    </div>
                    <code title={candidate.candidate_id}>
                      {shortId(candidate.candidate_id, 24)}
                    </code>
                  </section>
                )}

                <dl>
                  <div>
                    <dt>开始</dt>
                    <dd>{formatTime(worker.started_at)}</dd>
                  </div>
                  <div>
                    <dt>耗时</dt>
                    <dd className="cs-tabular">
                      {worker.started_at ? (
                        <ElapsedTime
                          startedAt={worker.started_at}
                          finishedAt={worker.finished_at}
                          initialMs={worker.elapsed_ms || 0}
                          running={worker.status === "running"}
                          prefix=""
                        />
                      ) : (
                        formatDuration(worker.elapsed_ms)
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>范围</dt>
                    <dd>
                      {isFullSnapshot
                        ? "全量源码快照"
                        : `${worker.path_count} 个路径`}
                    </dd>
                  </div>
                  <div>
                    <dt>源码访问记录</dt>
                    <dd>
                      {sourceActivityCount ? `${sourceActivityCount} 条` : "—"}
                    </dd>
                  </div>
                </dl>

                {sourceActivityCount > 0 && (
                  <div
                    className="cs-worker-activity"
                    aria-label="源码访问记录统计"
                  >
                    {sourceActivityLabels.map(([operation, label]) => (
                      <span key={operation}>
                        {label}{" "}
                        <strong>{activityCounts[operation] || 0}</strong>
                      </span>
                    ))}
                  </div>
                )}

                {!candidate && worker.candidate_ids.length > 0 && (
                  <div className="cs-worker-tags" aria-label="关联候选 ID">
                    {worker.candidate_ids.map((candidateId) => (
                      <code key={candidateId} title={candidateId}>
                        {shortId(candidateId, 18)}
                      </code>
                    ))}
                  </div>
                )}

                {candidate?.rationale && (
                  <details className="cs-worker-details cs-worker-rationale">
                    <summary>查看验证结论</summary>
                    <div
                      className="cs-worker-rationale__content"
                      role="region"
                      aria-label="验证结论详情"
                      tabIndex={0}
                    >
                      <p>{candidate.rationale}</p>
                    </div>
                    {candidate.rationale_truncated && (
                      <small>理由已截取显示。</small>
                    )}
                  </details>
                )}

                {worker.paths.length > 0 && !isFullSnapshot && (
                  <details className="cs-worker-details cs-worker-paths">
                    <summary>查看分配范围</summary>
                    <ul>
                      {worker.paths.map((path) => (
                        <li key={path}>
                          <code>{path}</code>
                        </li>
                      ))}
                    </ul>
                    {worker.paths_truncated && (
                      <p>仅显示前 {worker.paths.length} 条路径。</p>
                    )}
                  </details>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="cs-inline-empty">该阶段没有 Worker 工作单元。</p>
      )}
    </section>
  );
}
