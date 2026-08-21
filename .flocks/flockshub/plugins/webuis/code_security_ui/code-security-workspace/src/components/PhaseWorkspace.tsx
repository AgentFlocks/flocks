import { useMemo, useState } from "react";

import { formatDuration, formatTime, phaseLabels, shortId } from "../labels";
import type { PhaseRun, WorkerRun } from "../types";
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
  verifier: "独立验证员",
  prober: "动态探测员",
};

export function PhaseWorkspace({
  phases,
  events,
  workers,
  currentPhase,
  hasOlderEvents = false,
  loadingOlderEvents = false,
  onLoadOlderEvents = async () => undefined,
}: {
  phases: PhaseRun[];
  events: AuditEvent[];
  workers: WorkerRun[];
  currentPhase?: string | null;
  hasOlderEvents?: boolean;
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

  return (
    <section className="cs-execution" aria-labelledby="execution-title">
      <div className="cs-section-heading">
        <div>
          <p className="cs-eyebrow">可信执行过程</p>
          <h2 id="execution-title">阶段与实时事件</h2>
        </div>
        <p>不使用不可解释的总体百分比</p>
      </div>

      <div className="cs-phase-rail" role="tablist" aria-label="审计阶段">
        {sorted.map((phase) => {
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
                status={phase.status}
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
            <StatusBadge status={selected.status} context="阶段状态" />
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
                    prefix=""
                  />
                ) : (
                  formatDuration(selected.duration_ms)
                )}
              </dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>{selected.worker_count ?? "—"}</dd>
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

      <WorkerList
        workers={workers.filter((worker) => worker.phase === selected?.phase)}
      />
      <DurationTable phases={sorted} />
      <EventStream
        events={events}
        hasOlder={hasOlderEvents}
        loadingOlder={loadingOlderEvents}
        onLoadOlder={onLoadOlderEvents}
      />
    </section>
  );
}

function WorkerList({ workers }: { workers: WorkerRun[] }) {
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
          {workers.map((worker) => (
            <article key={worker.work_unit_id} className="cs-worker-card">
              <div className="cs-worker-card__heading">
                <div>
                  <strong>{roleLabels[worker.role] || worker.role}</strong>
                  <code title={worker.work_unit_id}>
                    {shortId(worker.work_unit_id, 18)}
                  </code>
                </div>
                <StatusBadge status={worker.status} context="Worker 状态" />
              </div>
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
                        prefix=""
                      />
                    ) : (
                      formatDuration(worker.elapsed_ms)
                    )}
                  </dd>
                </div>
                <div>
                  <dt>范围</dt>
                  <dd>{worker.path_count} 个路径</dd>
                </div>
                <div>
                  <dt>关联候选</dt>
                  <dd>{worker.candidate_ids.length || "—"}</dd>
                </div>
              </dl>
              {worker.candidate_ids.length > 0 && (
                <div className="cs-worker-tags" aria-label="关联候选 ID">
                  {worker.candidate_ids.map((candidateId) => (
                    <code key={candidateId} title={candidateId}>
                      {shortId(candidateId, 18)}
                    </code>
                  ))}
                </div>
              )}
              {worker.paths.length > 0 && (
                <details className="cs-worker-paths">
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
          ))}
        </div>
      ) : (
        <p className="cs-inline-empty">该阶段没有 Worker 工作单元。</p>
      )}
    </section>
  );
}

function DurationTable({ phases }: { phases: PhaseRun[] }) {
  return (
    <section className="cs-duration" aria-labelledby="duration-title">
      <div className="cs-subsection-heading">
        <h3 id="duration-title">阶段耗时</h3>
        <span>精确值</span>
      </div>
      <div className="cs-table-wrap">
        <table>
          <thead>
            <tr>
              <th>阶段</th>
              <th>状态</th>
              <th>开始</th>
              <th>结束</th>
              <th>耗时</th>
              <th>Worker</th>
            </tr>
          </thead>
          <tbody>
            {phases.map((phase) => (
              <tr key={phase.phase_run_id}>
                <th scope="row">
                  {phaseLabels[phase.phase] || phase.phase}
                  {phase.ordinal > 1 ? ` #${phase.ordinal}` : ""}
                </th>
                <td>
                  <StatusBadge status={phase.status} />
                </td>
                <td>{formatTime(phase.started_at)}</td>
                <td>{formatTime(phase.finished_at)}</td>
                <td className="cs-tabular">
                  {phase.started_at ? (
                    <ElapsedTime
                      startedAt={phase.started_at}
                      finishedAt={phase.finished_at}
                      initialMs={phase.duration_ms || 0}
                      prefix=""
                    />
                  ) : (
                    formatDuration(phase.duration_ms)
                  )}
                </td>
                <td>{phase.worker_count ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
