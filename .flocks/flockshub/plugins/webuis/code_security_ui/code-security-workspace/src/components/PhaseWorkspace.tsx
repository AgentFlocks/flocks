import { useMemo, useRef, useState } from "react";

import { getArtifact, getEvidence, readApiFailure } from "../api";
import { useCodeSecurityI18n } from "../i18n";
import {
  formatDuration,
  formatFileSize,
  formatTime,
  phaseLabels,
  shortId,
} from "../labels";
import type {
  AuditEvent,
  EvidenceContent,
  PhaseRun,
  ScanDetail,
  WorkerRun,
} from "../types";
import { Icon } from "../icons";
import { EventStream } from "./EventStream";
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
  sealed: "已完成",
  invalid: "校验失败",
};

export function PhaseWorkspace({
  scanId,
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
  scanId?: string;
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
  const { language, t } = useCodeSecurityI18n();
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
  const selectedAdjudicationRound = numberValue(
    selected?.phase === "adjudication"
      ? selected.summary?.adjudication_round
      : undefined,
  );
  const eventsWithPhase = useMemo(() => {
    const phaseByRunId = new Map(
      sorted.map((phase) => [phase.phase_run_id, phase.phase]),
    );
    return events.map((event) => {
      if (typeof event.summary.phase === "string" || !event.phase_run_id) {
        return event;
      }
      const phase = phaseByRunId.get(event.phase_run_id);
      return phase ? { ...event, summary: { ...event.summary, phase } } : event;
    });
  }, [events, sorted]);

  return (
    <section className="cs-execution" aria-labelledby="execution-title">
      <div className="cs-section-heading">
        <div>
          <h2 id="execution-title">{t("阶段与实时事件")}</h2>
        </div>
        <div
          className="cs-final-findings"
          aria-label={
            finalFindingCount === null
              ? t("漏洞数，{{basis}}", { basis: t(finalFindingBasis) })
              : t("漏洞数 {{count}} 个，{{basis}}", {
                  count: finalFindingCount,
                  basis: t(finalFindingBasis),
                })
          }
        >
          <div>
            <span>{t("漏洞数")}</span>
            <small>{t(finalFindingBasis)}</small>
          </div>
          <strong className="cs-tabular">
            {finalFindingCount === null ? "—" : finalFindingCount}
            {finalFindingCount !== null && <small>{t("个")}</small>}
          </strong>
        </div>
      </div>

      <div className="cs-phase-rail" role="tablist" aria-label={t("审计阶段")}>
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
                context={t("{{phase}}阶段", {
                  phase: t(phaseLabels[phase.phase] || phase.phase),
                })}
              />
              <strong>{t(phaseLabels[phase.phase] || phase.phase)}</strong>
              <span className="cs-tabular">
                {workerTotal
                  ? t("{{done}}/{{total}} 个工作单元 · ", {
                      done: workerDone,
                      total: workerTotal,
                    })
                  : ""}
                {phase.started_at ? (
                  <ElapsedTime
                    startedAt={phase.started_at}
                    finishedAt={phase.finished_at}
                    initialMs={phase.duration_ms || 0}
                    running={phase.status === "running"}
                    prefix=""
                  />
                ) : (
                  formatDuration(phase.duration_ms, t)
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
              <span className="cs-kicker">{t("当前查看")}</span>
              <h3>{t(phaseLabels[selected.phase] || selected.phase)}</h3>
            </div>
            <StatusBadge
              status={
                selected.phase === "dynamic_validation" &&
                dynamicValidationStatus === "not_runnable"
                  ? "not_runnable"
                  : selected.status
              }
              context={t("阶段状态")}
            />
          </div>
          <dl className="cs-metric-grid">
            <div>
              <dt>{t("开始时间")}</dt>
              <dd>{formatTime(selected.started_at, language)}</dd>
            </div>
            <div>
              <dt>{t("结束时间")}</dt>
              <dd>{formatTime(selected.finished_at, language)}</dd>
            </div>
            <div>
              <dt>{t("阶段耗时")}</dt>
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
                  formatDuration(selected.duration_ms, t)
                )}
              </dd>
            </div>
            <div>
              <dt>
                {t(
                  selected.phase === "snapshot"
                    ? "快照大小"
                    : selected.phase === "finalization"
                      ? "已完成产物"
                      : selected.phase === "adjudication"
                        ? "裁决轮次"
                        : "工作单元",
                )}
              </dt>
              <dd className="cs-tabular">
                {selected.phase === "snapshot"
                  ? snapshotBoundary
                    ? formatFileSize(snapshotBoundary.total_bytes, language)
                    : "—"
                  : selected.phase === "finalization"
                    ? t("{{count}} 个", { count: sealedArtifactCount })
                    : selected.phase === "adjudication"
                      ? selectedAdjudicationRound
                        ? t("第 {{round}} 轮", {
                            round: selectedAdjudicationRound,
                          })
                        : "—"
                      : (selected.worker_count ?? "—")}
              </dd>
            </div>
          </dl>
          {selected.status === "skipped" && (
            <p className="cs-callout cs-callout--muted">
              {t("启动审计时未启用动态验证。")}
            </p>
          )}
          {selected.status === "partial" && (
            <p className="cs-callout cs-callout--warning">
              {t("该阶段仅部分完成，请结合覆盖度与限制项判断结果。")}
            </p>
          )}
        </article>
      ) : (
        <div className="cs-inline-empty">
          {t("阶段信息将在快照创建后出现。")}
        </div>
      )}

      {selected?.phase === "snapshot" ? (
        snapshotBoundary ? (
          <SnapshotBoundary boundary={snapshotBoundary} />
        ) : (
          <div className="cs-inline-empty">
            {t("快照可信边界将在源码快照创建后出现。")}
          </div>
        )
      ) : selected?.phase === "finalization" ? (
        artifactBundle ? (
          <ArtifactBundleSummary bundle={artifactBundle} />
        ) : (
          <div className="cs-inline-empty">
            {t("封装结果将在最终产物生成后出现。")}
          </div>
        )
      ) : selected?.phase === "adjudication" ? (
        <AdjudicationSummary
          scanId={scanId}
          phase={selected}
          workers={workers}
        />
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
          events={eventsWithPhase}
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

interface RejectedCandidate {
  candidateId: string;
  reason: string;
}

type CandidateEvidenceState =
  | { status: "loading" }
  | { status: "loaded"; items: EvidenceContent[] }
  | { status: "error"; message: string };

interface RescanDirective {
  reason: string;
  paths: string[];
  questions: string[];
}

function AdjudicationSummary({
  scanId,
  phase,
  workers,
}: {
  scanId?: string;
  phase: PhaseRun;
  workers: WorkerRun[];
}) {
  const { t } = useCodeSecurityI18n();
  const summary = phase.summary || {};
  const round = numberValue(summary.adjudication_round);
  const action =
    summary.action === "finalize" || summary.action === "targeted_rescan"
      ? summary.action
      : null;
  const acceptedCandidateIds = stringArray(summary.accepted_candidate_ids);
  const rejectedCandidates = rejectedCandidateArray(
    summary.rejected_candidates,
  );
  const rescan = rescanDirective(summary.rescan);
  const candidateTitles = new Map<string, string>();
  const candidateRationales = new Map<string, string>();
  workers.forEach((worker) => {
    worker.candidate_summaries?.forEach((candidate) => {
      if (candidate.title) {
        candidateTitles.set(candidate.candidate_id, candidate.title);
      }
      if (candidate.rationale) {
        candidateRationales.set(candidate.candidate_id, candidate.rationale);
      }
    });
  });
  const [candidateEvidence, setCandidateEvidence] = useState<
    Record<string, CandidateEvidenceState>
  >({});
  const candidateIndexRequestRef = useRef<Promise<unknown> | null>(null);
  const evidenceRequestRef = useRef(new Map<string, Promise<void>>());

  const loadCandidateIndex = () => {
    if (!scanId) {
      return Promise.reject(new Error("当前扫描任务缺少标识，无法读取证据。"));
    }
    if (!candidateIndexRequestRef.current) {
      candidateIndexRequestRef.current = getArtifact(scanId, "candidate_index")
        .then((artifact) => artifact.content)
        .catch((reason) => {
          candidateIndexRequestRef.current = null;
          throw reason;
        });
    }
    return candidateIndexRequestRef.current;
  };

  const loadCandidateEvidence = (candidateId: string) => {
    const existingRequest = evidenceRequestRef.current.get(candidateId);
    if (existingRequest) return existingRequest;

    setCandidateEvidence((current) => ({
      ...current,
      [candidateId]: { status: "loading" },
    }));
    const request = loadCandidateIndex()
      .then((content) => evidenceIdsForCandidate(content, candidateId))
      .then((evidenceIds) =>
        scanId
          ? Promise.all(
              evidenceIds.map((evidenceId) => getEvidence(scanId, evidenceId)),
            )
          : [],
      )
      .then((items) => {
        setCandidateEvidence((current) => ({
          ...current,
          [candidateId]: { status: "loaded", items },
        }));
      })
      .catch((reason) => {
        evidenceRequestRef.current.delete(candidateId);
        setCandidateEvidence((current) => ({
          ...current,
          [candidateId]: {
            status: "error",
            message: readApiFailure(reason, "无法加载候选漏洞证据").message,
          },
        }));
      });
    evidenceRequestRef.current.set(candidateId, request);
    return request;
  };

  const candidateLabel = (candidateId: string) =>
    candidateTitles.get(candidateId) || shortId(candidateId, 28);
  const candidateRationale = (candidateId: string) =>
    candidateRationales.get(candidateId) || "";
  const candidateCount =
    acceptedCandidateIds.length + rejectedCandidates.length;
  const failureReason = stringValue(summary.reason);

  return (
    <section
      className="cs-phase-summary cs-adjudication-summary"
      aria-labelledby="adjudication-summary-title"
    >
      <div className="cs-subsection-heading cs-phase-summary__heading">
        <div>
          <h3 id="adjudication-summary-title">
            <Icon name="shield" />
            {t("裁决内容与结果")}
          </h3>
          <span>
            {t(
              action === "finalize"
                ? "主智能体已完成{{round}}裁决并形成最终结论"
                : action === "targeted_rescan"
                  ? "主智能体已完成{{round}}裁决并要求补充验证"
                  : phase.status === "failed"
                    ? "主智能体未能提交有效裁决"
                    : "主智能体正在审阅候选漏洞与验证结论",
              {
                round: round ? t("第 {{round}} 轮", { round }) : "",
              },
            )}
          </span>
        </div>
      </div>

      {action === "finalize" ? (
        <>
          <dl className="cs-phase-summary__grid">
            <div>
              <dt>{t("裁决结果")}</dt>
              <dd>{t("完成审计定稿")}</dd>
            </div>
            <div>
              <dt>{t("裁决对象")}</dt>
              <dd className="cs-tabular">
                {t("{{count}} 个候选漏洞", { count: candidateCount })}
              </dd>
            </div>
            <div>
              <dt>{t("接受")}</dt>
              <dd className="cs-tabular">
                {t("{{count}} 个", { count: acceptedCandidateIds.length })}
              </dd>
            </div>
            <div>
              <dt>{t("驳回")}</dt>
              <dd className="cs-tabular">
                {t("{{count}} 个", { count: rejectedCandidates.length })}
              </dd>
            </div>
          </dl>
          <div className="cs-adjudication-summary__content">
            <DecisionCandidateList
              title="纳入漏洞"
              emptyText="没有候选漏洞被纳入最终报告。"
              candidateIds={acceptedCandidateIds}
              candidateLabel={candidateLabel}
              candidateRationale={candidateRationale}
              evidenceByCandidate={candidateEvidence}
              onLoadEvidence={loadCandidateEvidence}
              tone="accepted"
            />
            <DecisionCandidateList
              title="已驳回的候选漏洞"
              emptyText="没有候选漏洞被驳回。"
              rejectedCandidates={rejectedCandidates}
              candidateLabel={candidateLabel}
              candidateRationale={candidateRationale}
              evidenceByCandidate={candidateEvidence}
              onLoadEvidence={loadCandidateEvidence}
              tone="rejected"
            />
          </div>
        </>
      ) : action === "targeted_rescan" && rescan ? (
        <div className="cs-adjudication-summary__rescan">
          <div>
            <span className="cs-kicker">{t("裁决结果")}</span>
            <strong>{t("需要定向复扫后再形成最终结论")}</strong>
          </div>
          <div>
            <h4>{t("裁决依据")}</h4>
            <p>{rescan.reason}</p>
          </div>
          <div>
            <h4>{t("复扫范围")}</h4>
            <ul className="cs-adjudication-summary__paths">
              {rescan.paths.map((path) => (
                <li key={path}>
                  <code>{path}</code>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4>{t("待确认问题")}</h4>
            <ol>
              {rescan.questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ol>
          </div>
        </div>
      ) : (
        <p
          className={`cs-phase-summary__note${phase.status === "failed" ? " cs-phase-summary__note--danger" : ""}`}
        >
          {failureReason && phase.status === "failed"
            ? failureReason
            : t(
                phase.status === "failed"
                  ? "裁决执行失败，请结合阶段事件查看失败原因。"
                  : phase.status === "running"
                    ? "裁决提交后，这里将显示接受、驳回或定向复扫的具体内容。"
                    : "当前任务没有保存结构化裁决详情，可在审计产物面板中查看完整裁决记录。",
              )}
        </p>
      )}
    </section>
  );
}

function DecisionCandidateList({
  title,
  emptyText,
  candidateIds = [],
  rejectedCandidates = [],
  candidateLabel,
  candidateRationale = () => "",
  evidenceByCandidate = {},
  onLoadEvidence,
  tone,
}: {
  title: string;
  emptyText: string;
  candidateIds?: string[];
  rejectedCandidates?: RejectedCandidate[];
  candidateLabel: (candidateId: string) => string;
  candidateRationale?: (candidateId: string) => string;
  evidenceByCandidate?: Record<string, CandidateEvidenceState>;
  onLoadEvidence?: (candidateId: string) => Promise<void>;
  tone: "accepted" | "rejected";
}) {
  const { t } = useCodeSecurityI18n();
  const items = candidateIds.length
    ? candidateIds.map((candidateId) => ({ candidateId, reason: "" }))
    : rejectedCandidates;
  const accepted = tone === "accepted";
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(
    null,
  );
  const toggleEvidence = (candidateId: string) => {
    if (expandedCandidateId === candidateId) {
      setExpandedCandidateId(null);
      return;
    }
    setExpandedCandidateId(candidateId);
    void onLoadEvidence?.(candidateId);
  };

  return (
    <section
      className={`cs-adjudication-group cs-adjudication-group--${tone}`}
      aria-label={t("{{title}}，{{count}} 个", {
        title: t(title),
        count: items.length,
      })}
    >
      <header className="cs-adjudication-group__header">
        <div>
          <span className="cs-adjudication-group__icon">
            <Icon name={accepted ? "check" : "error"} />
          </span>
          <div>
            <h4>{t(title)}</h4>
            <p>
              {t(
                accepted
                  ? "经主智能体裁决，纳入最终报告"
                  : "经主智能体裁决，不纳入最终报告",
              )}
            </p>
          </div>
        </div>
        <strong className="cs-tabular">
          {items.length}
          <small>{t("个")}</small>
        </strong>
      </header>
      {items.length ? (
        <ul>
          {items.map((item, index) => {
            const expanded = expandedCandidateId === item.candidateId;
            const evidencePanelId = `${tone}-evidence-${index + 1}`;
            const label = candidateLabel(item.candidateId);
            const disclosureLabel = t(
              accepted
                ? expanded
                  ? "收起证据"
                  : "查看证据"
                : expanded
                  ? "收起依据"
                  : "查看依据",
            );
            return (
              <li
                key={item.candidateId}
                className={expanded ? "is-expanded" : undefined}
              >
                <button
                  type="button"
                  className="cs-adjudication-candidate__heading cs-adjudication-candidate__toggle"
                  aria-controls={evidencePanelId}
                  aria-expanded={expanded}
                  aria-label={t("{{candidate}}，{{action}}", {
                    candidate: label,
                    action: disclosureLabel,
                  })}
                  onClick={() => toggleEvidence(item.candidateId)}
                >
                  <span
                    className="cs-adjudication-candidate__index"
                    aria-hidden="true"
                  >
                    {index + 1}
                  </span>
                  <span className="cs-adjudication-candidate__copy">
                    <strong>{label}</strong>
                    <code>{item.candidateId}</code>
                  </span>
                  <span className="cs-adjudication-candidate__actions">
                    <span className="cs-adjudication-candidate__status">
                      {t(accepted ? "已纳入" : "已驳回")}
                    </span>
                    <span className="cs-adjudication-candidate__disclosure">
                      {disclosureLabel}
                      <Icon name="chevron" />
                    </span>
                  </span>
                </button>
                {expanded && (
                  <CandidateEvidencePanel
                    id={evidencePanelId}
                    candidateLabel={label}
                    decisionReason={item.reason}
                    rationale={candidateRationale(item.candidateId)}
                    state={evidenceByCandidate[item.candidateId]}
                    tone={tone}
                    onRetry={() => void onLoadEvidence?.(item.candidateId)}
                  />
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p>{t(emptyText)}</p>
      )}
    </section>
  );
}

function CandidateEvidencePanel({
  id,
  candidateLabel,
  decisionReason,
  rationale,
  state,
  tone,
  onRetry,
}: {
  id: string;
  candidateLabel: string;
  decisionReason: string;
  rationale: string;
  state?: CandidateEvidenceState;
  tone: "accepted" | "rejected";
  onRetry: () => void;
}) {
  const { t } = useCodeSecurityI18n();
  const accepted = tone === "accepted";
  const showRationale = !!rationale && rationale !== decisionReason;
  return (
    <div
      id={id}
      className="cs-adjudication-evidence"
      role="region"
      aria-label={t(
        accepted ? "{{candidate}}的纳入证据" : "{{candidate}}的驳回详情",
        { candidate: candidateLabel },
      )}
    >
      {decisionReason && (
        <section className="cs-adjudication-evidence__decision cs-adjudication-evidence__decision--rejected">
          <h5>{t("驳回依据")}</h5>
          <p>{decisionReason}</p>
        </section>
      )}
      {showRationale && (
        <section className="cs-adjudication-evidence__rationale">
          <h5>{t("独立验证结论")}</h5>
          <p>{rationale}</p>
        </section>
      )}
      <section className="cs-adjudication-evidence__code">
        <h5>
          {t(accepted ? "代码证据" : "相关代码证据")}
          {state?.status === "loaded" && (
            <span className="cs-tabular">
              {t("{{count}} 条", { count: state.items.length })}
            </span>
          )}
        </h5>
        {!state || state.status === "loading" ? (
          <p className="cs-adjudication-evidence__message" role="status">
            {t(accepted ? "正在加载纳入证据…" : "正在加载驳回详情…")}
          </p>
        ) : state.status === "error" ? (
          <div className="cs-adjudication-evidence__error" role="alert">
            <p>{t(state.message)}</p>
            <button type="button" onClick={onRetry}>
              {t("重新加载")}
            </button>
          </div>
        ) : state.items.length ? (
          <div className="cs-adjudication-evidence__items">
            {state.items.map((evidence, index) => (
              <article key={evidence.evidence_id}>
                <header>
                  <strong>{t("证据 {{index}}", { index: index + 1 })}</strong>
                  <code>
                    {evidence.relative_path}:{evidence.start_line}-
                    {evidence.end_line}
                  </code>
                </header>
                <pre
                  tabIndex={0}
                  aria-label={t(
                    "代码证据 {{index}}，{{path}} 第 {{start}} 至 {{end}} 行",
                    {
                      index: index + 1,
                      path: evidence.relative_path,
                      start: evidence.start_line,
                      end: evidence.end_line,
                    },
                  )}
                >
                  <code>{evidence.excerpt}</code>
                </pre>
                {evidence.truncated && <small>{t("证据内容已截断。")}</small>}
              </article>
            ))}
          </div>
        ) : (
          <p className="cs-adjudication-evidence__message">
            {t("该候选漏洞没有可展示的代码证据，可在审计产物中查看完整记录。")}
          </p>
        )}
      </section>
    </div>
  );
}

function evidenceIdsForCandidate(
  content: unknown,
  candidateId: string,
): string[] {
  if (!Array.isArray(content)) return [];
  const candidate = content.find(
    (item) =>
      !!item &&
      typeof item === "object" &&
      stringValue((item as Record<string, unknown>).candidate_id) ===
        candidateId,
  );
  if (!candidate || typeof candidate !== "object") return [];
  const evidence = (candidate as Record<string, unknown>).evidence;
  if (!Array.isArray(evidence)) return [];
  return Array.from(
    new Set(
      evidence.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const evidenceId = stringValue(
          (item as Record<string, unknown>).evidence_id,
        );
        return evidenceId ? [evidenceId] : [];
      }),
    ),
  );
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.trunc(value)
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && !!item)
    : [];
}

function rejectedCandidateArray(value: unknown): RejectedCandidate[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const candidateId = stringValue(record.candidate_id);
    if (!candidateId) return [];
    return [{ candidateId, reason: stringValue(record.reason) }];
  });
}

function rescanDirective(value: unknown): RescanDirective | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const reason = stringValue(record.reason);
  const paths = stringArray(record.paths);
  const questions = stringArray(record.questions);
  return reason && paths.length && questions.length
    ? { reason, paths, questions }
    : null;
}

function SnapshotBoundary({ boundary }: { boundary: ScanDetail["target"] }) {
  const { language, t } = useCodeSecurityI18n();
  return (
    <section
      className="cs-phase-summary cs-snapshot-boundary"
      aria-labelledby="snapshot-boundary-title"
    >
      <div className="cs-subsection-heading cs-phase-summary__heading">
        <div>
          <h3 id="snapshot-boundary-title">
            <Icon name="shield" />
            {t("快照可信边界")}
          </h3>
          <span>{t("后续审计仅基于此不可变源码快照")}</span>
        </div>
      </div>
      <dl className="cs-phase-summary__grid">
        <div>
          <dt>{t("纳入文件")}</dt>
          <dd className="cs-tabular">
            {boundary.file_count.toLocaleString(language)}
          </dd>
        </div>
        <div>
          <dt>{t("遗漏文件")}</dt>
          <dd className="cs-tabular">
            {boundary.omitted_file_count.toLocaleString(language)}
          </dd>
        </div>
        <div>
          <dt>{t("版本")}</dt>
          <dd>
            <code title={boundary.source_revision || undefined}>
              {shortId(boundary.source_revision, 18)}
            </code>
          </dd>
        </div>
        <div>
          <dt>{t("目录摘要")}</dt>
          <dd>
            <code title={boundary.tree_digest}>
              {shortId(boundary.tree_digest, 18)}
            </code>
          </dd>
        </div>
      </dl>
      <p className="cs-phase-summary__note">
        {t(
          "工作区后续发生的文件变化不会影响本次审计结果，所有智能体共享同一份固定内容与版本标识。",
        )}
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
  const { language, t } = useCodeSecurityI18n();
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
            {t("封装结果")}
          </h3>
          <span>
            {t(
              bundle.integrityStatus === "valid"
                ? "最终产物已经固定并通过摘要校验"
                : bundle.integrityStatus === "invalid"
                  ? "最终产物未通过完整性校验"
                  : "正在生成并校验最终产物",
            )}
          </span>
        </div>
      </div>
      <dl className="cs-phase-summary__grid">
        <div>
          <dt>{t("已完成")}</dt>
          <dd className="cs-tabular">
            {t("{{count}} 个", { count: sealedArtifacts.length })}
          </dd>
        </div>
        <div>
          <dt>{t("产物大小")}</dt>
          <dd className="cs-tabular">
            {sealedArtifacts.length
              ? formatFileSize(totalBytes, language)
              : "—"}
          </dd>
        </div>
        <div>
          <dt>{t("完整性")}</dt>
          <dd>
            <span
              className={`cs-artifact-state cs-artifact-state--${integrityState}`}
            >
              {t(integrityLabel)}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("最终报告")}</dt>
          <dd>
            <span
              className={`cs-artifact-state cs-artifact-state--${finalReport?.state || "pending"}`}
            >
              {t(artifactStateLabels[finalReport?.state || "pending"])}
            </span>
          </dd>
        </div>
      </dl>
      <p
        className={`cs-phase-summary__note${bundle.integrityStatus === "invalid" ? " cs-phase-summary__note--danger" : ""}`}
      >
        {t(
          bundle.integrityStatus === "valid"
            ? "报告、SARIF、漏洞清单、覆盖度和审计清单等产物已完成，可从审计产物面板查看或下载。"
            : bundle.integrityStatus === "invalid"
              ? "当前封装结果不可作为可信最终输出，请结合审计产物面板中的校验信息重新发起审计。"
              : "产物仍在生成中，完整性校验完成后会自动更新封装状态。",
        )}
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
  const { language, t } = useCodeSecurityI18n();
  return (
    <section className="cs-workers" aria-labelledby="workers-title">
      <div className="cs-subsection-heading">
        <div>
          <h3 id="workers-title">{t("工作单元")}</h3>
          <span>{t("{{count}} 个", { count: workers.length })}</span>
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
            const coverageCounts = worker.coverage?.counts;
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
                    <strong>{t(roleLabels[worker.role] || worker.role)}</strong>
                    <code title={worker.work_unit_id}>
                      {shortId(worker.work_unit_id, 18)}
                    </code>
                  </div>
                  <StatusBadge
                    status={displayStatus}
                    context={t("工作单元状态")}
                  />
                </div>

                {candidate && (
                  <section
                    className="cs-worker-candidate"
                    aria-label={t("验证对象")}
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
                            {t(
                              severityLabels[candidate.severity] ||
                                candidate.severity,
                            )}
                          </span>
                        )}
                        <span
                          className={`cs-worker-verdict cs-worker-verdict--${verdict}`}
                        >
                          {t(verdictLabels[verdict] || "等待结论")}
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
                    <dt>{t("开始")}</dt>
                    <dd>{formatTime(worker.started_at, language)}</dd>
                  </div>
                  <div>
                    <dt>{t("耗时")}</dt>
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
                        formatDuration(worker.elapsed_ms, t)
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("范围")}</dt>
                    <dd>
                      {isFullSnapshot
                        ? t("全量源码快照")
                        : t("{{count}} 个路径", { count: worker.path_count })}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("源码访问记录")}</dt>
                    <dd>
                      {sourceActivityCount
                        ? t("{{count}} 条", { count: sourceActivityCount })
                        : "—"}
                    </dd>
                  </div>
                </dl>

                {sourceActivityCount > 0 && (
                  <div
                    className="cs-worker-activity"
                    aria-label={t("源码访问记录统计")}
                  >
                    {sourceActivityLabels.map(([operation, label]) => (
                      <span key={operation}>
                        {t(label)}{" "}
                        <strong>{activityCounts[operation] || 0}</strong>
                      </span>
                    ))}
                  </div>
                )}

                {coverageCounts && (
                  <div
                    className="cs-worker-activity"
                    aria-label={t("覆盖证明统计")}
                  >
                    <span>
                      {t("已分配")}{" "}
                      <strong>{coverageCounts.assigned || 0}</strong>
                    </span>
                    <span>
                      {t("完整读取")}{" "}
                      <strong>{coverageCounts.read_complete || 0}</strong>
                    </span>
                    <span>
                      {t("未检查")}{" "}
                      <strong>{coverageCounts.unexamined || 0}</strong>
                    </span>
                    <span>
                      {t("失败")} <strong>{coverageCounts.failed || 0}</strong>
                    </span>
                  </div>
                )}

                {worker.recent_rejection && (
                  <div
                    className="cs-worker-activity"
                    aria-label={t("最近一次提交拒绝")}
                  >
                    <span>
                      {t("错误码")} {" "}
                      <strong>
                        {worker.recent_rejection.error_code || "—"}
                      </strong>
                    </span>
                    <span>
                      {t("违规项")} {" "}
                      <strong>
                        {worker.recent_rejection.violation_count}
                      </strong>
                    </span>
                    <span>
                      {worker.recent_rejection.retryable
                        ? t("可在当前执行中修正")
                        : t("不可重试")}
                    </span>
                  </div>
                )}

                {!candidate && worker.candidate_ids.length > 0 && (
                  <div
                    className="cs-worker-tags"
                    aria-label={t("关联候选漏洞 ID")}
                  >
                    {worker.candidate_ids.map((candidateId) => (
                      <code key={candidateId} title={candidateId}>
                        {shortId(candidateId, 18)}
                      </code>
                    ))}
                  </div>
                )}

                {candidate?.rationale && (
                  <details className="cs-worker-details cs-worker-rationale">
                    <summary>{t("查看验证结论")}</summary>
                    <div
                      className="cs-worker-rationale__content"
                      role="region"
                      aria-label={t("验证结论详情")}
                      tabIndex={0}
                    >
                      <p>{candidate.rationale}</p>
                    </div>
                    {candidate.rationale_truncated && (
                      <small>{t("理由已截取显示。")}</small>
                    )}
                  </details>
                )}

                {worker.paths.length > 0 && !isFullSnapshot && (
                  <details className="cs-worker-details cs-worker-paths">
                    <summary>{t("查看分配范围")}</summary>
                    <ul>
                      {worker.paths.map((path) => (
                        <li key={path}>
                          <code>{path}</code>
                        </li>
                      ))}
                    </ul>
                    {worker.paths_truncated && (
                      <p>
                        {t("仅显示前 {{count}} 条路径。", {
                          count: worker.paths.length,
                        })}
                      </p>
                    )}
                  </details>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="cs-inline-empty">{t("该阶段没有工作单元。")}</p>
      )}
    </section>
  );
}
