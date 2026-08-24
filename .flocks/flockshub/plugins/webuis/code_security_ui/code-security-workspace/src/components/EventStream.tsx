import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import { Icon } from "../icons";
import { useCodeSecurityI18n, type Translator } from "../i18n";
import { formatTime, phaseLabels, phaseStatusLabels, shortId } from "../labels";
import type { AuditEvent } from "../types";

const ROW_HEIGHT = 72;
const VIEWPORT_HEIGHT = 360;

interface GroupedEvent {
  event: AuditEvent;
  count: number;
  oldestCreatedAt: string;
  signature: string | null;
}

export function EventStream({
  events,
  selectedPhase,
  hasOlder,
  loading,
  loadingOlder,
  onLoadOlder,
}: {
  events: AuditEvent[];
  selectedPhase?: string;
  hasOlder: boolean;
  loading: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => Promise<void>;
}) {
  const { t } = useCodeSecurityI18n();
  const [level, setLevel] = useState("all");
  const [phase, setPhase] = useState(selectedPhase || "all");
  const [worker, setWorker] = useState("all");
  const [autoFollow, setAutoFollow] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const previousFirstSeq = useRef(events[0]?.seq);
  const previousLastSeq = useRef(events[events.length - 1]?.seq);
  const layoutLastSeq = useRef(events[events.length - 1]?.seq);
  const previousScrollHeight = useRef(0);
  const viewport = useRef<HTMLDivElement>(null);
  const phaseOptions = useMemo(() => {
    const values = uniqueEventValues(events, "phase");
    if (selectedPhase && !values.includes(selectedPhase)) {
      values.push(selectedPhase);
      values.sort();
    }
    return values;
  }, [events, selectedPhase]);
  const workerOptions = useMemo(
    () => uniqueEventValues(events, "work_unit_id", "worker", "batch_id"),
    [events],
  );
  const filtered = useMemo(
    () =>
      events
        .filter(
          (event) =>
            (level === "all" || event.level === level) &&
            (phase === "all" || event.summary.phase === phase) &&
            (worker === "all" || eventWorker(event) === worker),
        )
        .sort(compareEventsNewestFirst),
    [events, level, phase, worker],
  );
  const grouped = useMemo(() => groupRepeatedProgress(filtered), [filtered]);

  useEffect(() => {
    const firstSeq = events[0]?.seq;
    const lastSeq = events[events.length - 1]?.seq;
    const prepended =
      previousFirstSeq.current !== undefined &&
      firstSeq !== undefined &&
      firstSeq < previousFirstSeq.current;
    const added = events.filter(
      (event) =>
        previousLastSeq.current === undefined ||
        event.seq > previousLastSeq.current,
    ).length;
    previousFirstSeq.current = firstSeq;
    previousLastSeq.current = lastSeq;
    if (prepended) return;
    if (!added) return;
    if (autoFollow && viewport.current) {
      viewport.current.scrollTop = 0;
    } else {
      setUnseen((value) => value + added);
    }
  }, [autoFollow, events]);

  useLayoutEffect(() => {
    const element = viewport.current;
    const lastSeq = events[events.length - 1]?.seq;
    if (!element) return;
    if (
      !autoFollow &&
      layoutLastSeq.current !== undefined &&
      lastSeq !== undefined &&
      lastSeq > layoutLastSeq.current
    ) {
      element.scrollTop += element.scrollHeight - previousScrollHeight.current;
    }
    layoutLastSeq.current = lastSeq;
    previousScrollHeight.current = element.scrollHeight;
  }, [autoFollow, events, grouped.length]);

  const virtual = grouped.length > 100;
  const start = virtual
    ? Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 4)
    : 0;
  const end = virtual
    ? Math.min(
        grouped.length,
        start + Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + 8,
      )
    : grouped.length;
  const visible = grouped.slice(start, end);
  const phaseFilterLabel = t(
    phase === "all" ? "全部阶段" : phaseLabels[phase] || phase,
  );

  const returnToLatest = () => {
    setAutoFollow(true);
    setUnseen(0);
    if (viewport.current) viewport.current.scrollTop = 0;
  };

  return (
    <section
      className="cs-events"
      aria-labelledby="event-title"
      aria-busy={loading}
    >
      <div className="cs-subsection-heading cs-events__heading">
        <div>
          <h3 id="event-title">{t("阶段事件")}</h3>
          <span>
            {loading
              ? t("{{phase}} · 正在加载事件…", { phase: phaseFilterLabel })
              : grouped.length < filtered.length
                ? t(
                    "{{phase}} · 显示 {{groups}} 组（{{filtered}} 条）/ {{total}} 条",
                    {
                      phase: phaseFilterLabel,
                      groups: grouped.length,
                      filtered: filtered.length,
                      total: events.length,
                    },
                  )
                : t("{{phase}} · 显示 {{visible}} / {{total}} 条", {
                    phase: phaseFilterLabel,
                    visible: filtered.length,
                    total: events.length,
                  })}
          </span>
        </div>
        <div className="cs-event-filters">
          <label className="cs-event-filter cs-event-filter--phase">
            <span className="cs-visually-hidden">{t("按阶段筛选事件")}</span>
            <select
              value={phase}
              onChange={(event) => setPhase(event.target.value)}
            >
              <option value="all">{t("全部阶段")}</option>
              {phaseOptions.map((value) => (
                <option key={value} value={value}>
                  {t(phaseLabels[value] || value)}
                </option>
              ))}
            </select>
          </label>
          {workerOptions.length > 0 && (
            <label className="cs-event-filter cs-event-filter--worker">
              <span className="cs-visually-hidden">
                {t("按工作单元筛选事件")}
              </span>
              <select
                value={worker}
                onChange={(event) => setWorker(event.target.value)}
              >
                <option value="all">{t("全部工作单元")}</option>
                {workerOptions.map((value) => (
                  <option key={value} value={value}>
                    {shortId(value, 14)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="cs-event-filter cs-event-filter--level">
            <span className="cs-visually-hidden">{t("按级别筛选事件")}</span>
            <select
              value={level}
              onChange={(event) => setLevel(event.target.value)}
            >
              <option value="all">{t("全部级别")}</option>
              <option value="info">{t("信息")}</option>
              <option value="warning">{t("警告")}</option>
              <option value="error">{t("错误")}</option>
            </select>
          </label>
        </div>
      </div>
      {hasOlder && (
        <button
          className="cs-load-older"
          type="button"
          onClick={() => void onLoadOlder()}
          disabled={loadingOlder}
        >
          {t(loadingOlder ? "正在加载更早事件…" : "加载更早事件")}
        </button>
      )}
      <div
        ref={viewport}
        className="cs-event-viewport"
        style={{ height: VIEWPORT_HEIGHT }}
        onScroll={(event) => {
          const element = event.currentTarget;
          setScrollTop(element.scrollTop);
          const atLatest = element.scrollTop < 24;
          setAutoFollow(atLatest);
          if (atLatest) setUnseen(0);
        }}
        tabIndex={0}
        aria-label={t("审计事件列表")}
      >
        {virtual ? (
          <div
            className="cs-event-virtual"
            style={{ height: grouped.length * ROW_HEIGHT }}
          >
            {visible.map((group, index) => (
              <EventRow
                key={group.event.seq}
                group={group}
                style={{
                  transform: `translateY(${(start + index) * ROW_HEIGHT}px)`,
                }}
              />
            ))}
          </div>
        ) : (
          visible.map((group) => (
            <EventRow key={group.event.seq} group={group} />
          ))
        )}
        {!filtered.length && (
          <p className="cs-inline-empty" role={loading ? "status" : undefined}>
            {t(
              loading
                ? "正在加载审计事件…"
                : "当前阶段与筛选条件下还没有事件。",
            )}
          </p>
        )}
      </div>
      {!autoFollow && unseen > 0 && (
        <button
          className="cs-new-events"
          type="button"
          onClick={returnToLatest}
        >
          {t("有 {{count}} 条新事件 · 回到最新", { count: unseen })}
        </button>
      )}
    </section>
  );
}

function uniqueEventValues(events: AuditEvent[], ...keys: string[]): string[] {
  const values = new Set<string>();
  events.forEach((event) => {
    for (const key of keys) {
      const value = event.summary[key];
      if (typeof value === "string" && value) {
        values.add(value);
        break;
      }
    }
  });
  return [...values].sort();
}

function eventWorker(event: AuditEvent): string {
  for (const key of ["work_unit_id", "worker", "batch_id"]) {
    const value = event.summary[key];
    if (typeof value === "string" && value) return value;
  }
  return "";
}

function compareEventsNewestFirst(left: AuditEvent, right: AuditEvent): number {
  const timeDifference =
    Date.parse(right.created_at) - Date.parse(left.created_at);
  return timeDifference || right.seq - left.seq;
}

function groupRepeatedProgress(events: AuditEvent[]): GroupedEvent[] {
  const groups: GroupedEvent[] = [];
  events.forEach((event) => {
    const signature = progressSignature(event);
    const previous = groups[groups.length - 1];
    if (signature && previous?.signature === signature) {
      previous.count += 1;
      previous.oldestCreatedAt = event.created_at;
      return;
    }
    groups.push({
      event,
      count: 1,
      oldestCreatedAt: event.created_at,
      signature,
    });
  });
  return groups;
}

function progressSignature(event: AuditEvent): string | null {
  if (event.type !== "phase.progress" || event.level !== "info") return null;
  const statusCounts = Object.entries(
    numericRecord(event.summary.status_counts),
  ).sort(([left], [right]) => left.localeCompare(right));
  const workers = Array.isArray(event.summary.workers)
    ? event.summary.workers
        .flatMap((value) => {
          if (!value || typeof value !== "object") return [];
          const record = value as Record<string, unknown>;
          const id =
            typeof record.work_unit_id === "string" ? record.work_unit_id : "";
          const status = typeof record.status === "string" ? record.status : "";
          const session =
            typeof record.session_id === "string" ? record.session_id : "";
          const candidate =
            typeof record.candidate_id === "string" ? record.candidate_id : "";
          return id && status
            ? [`${id}\u0000${status}\u0000${session}\u0000${candidate}`]
            : [];
        })
        .sort()
    : [];
  return JSON.stringify({
    type: event.type,
    title: event.title,
    phase: event.summary.phase,
    batch: eventWorker(event),
    status: event.summary.status,
    statusCounts,
    workers,
  });
}

function EventRow({
  group,
  style,
}: {
  group: GroupedEvent;
  style?: CSSProperties;
}) {
  const { language, t } = useCodeSecurityI18n();
  const { event } = group;
  const phase =
    typeof event.summary.phase === "string" ? event.summary.phase : "";
  const metric = eventMetric(event, t);
  const title = t(event.title.replace(/父 Agent\s*/g, "主智能体"));
  const formattedTime = (value: string) => formatTime(value, language);
  return (
    <article
      className={`cs-event-row cs-event-row--${event.level}`}
      style={style}
    >
      <time dateTime={event.created_at}>{formattedTime(event.created_at)}</time>
      <span className="cs-event-row__marker" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>
          <span>{t(phaseLabels[phase] || phase || event.type)}</span>
          {group.count > 1 && (
            <span
              className="cs-event-row__group"
              aria-label={t(
                "已合并 {{count}} 条相同状态事件，时间范围 {{start}} 至 {{end}}",
                {
                  count: group.count,
                  start: formattedTime(group.oldestCreatedAt),
                  end: formattedTime(event.created_at),
                },
              )}
            >
              {t("合并 {{count}} 条 · {{start}}–{{end}}", {
                count: group.count,
                start: formattedTime(group.oldestCreatedAt),
                end: formattedTime(event.created_at),
              })}
            </span>
          )}
        </p>
      </div>
      <span className="cs-event-row__metric" aria-label={metric.label}>
        {metric.text}
      </span>
      <button
        type="button"
        className="cs-copy-button"
        aria-label={
          group.count > 1
            ? t("复制合并事件摘要，共 {{count}} 条", { count: group.count })
            : t("复制事件 {{seq}} 摘要", { seq: event.seq })
        }
        title={
          group.count > 1
            ? t("复制合并的 {{count}} 条事件摘要", { count: group.count })
            : t("复制事件 #{{seq}} 摘要", { seq: event.seq })
        }
        onClick={() =>
          navigator.clipboard?.writeText(
            `${title}\n${
              group.count > 1
                ? `${t("合并 {{count}} 条相同状态事件：{{start}}–{{end}}", {
                    count: group.count,
                    start: formattedTime(group.oldestCreatedAt),
                    end: formattedTime(event.created_at),
                  })}\n`
                : ""
            }${JSON.stringify(event.summary)}`,
          )
        }
      >
        <Icon name="copy" />
      </button>
    </article>
  );
}

function eventMetric(
  event: AuditEvent,
  t: Translator,
): { text: string; label: string } {
  const statusCounts = numericRecord(event.summary.status_counts);
  const totalWorkers = Object.values(statusCounts).reduce(
    (total, count) => total + count,
    0,
  );
  if (totalWorkers > 0) {
    const failed = statusCounts.failed || 0;
    const completed = statusCounts.completed || 0;
    const running = statusCounts.running || 0;
    const cancelled = statusCounts.cancelled || 0;
    if (failed > 0)
      return {
        text: t("{{count}} 失败", { count: failed }),
        label: t("{{count}} 个工作单元执行失败", { count: failed }),
      };
    if (completed > 0) {
      return {
        text: t("{{completed}}/{{total}} 已完成", {
          completed,
          total: totalWorkers,
        }),
        label: t("{{total}} 个工作单元中 {{completed}} 个已完成", {
          total: totalWorkers,
          completed,
        }),
      };
    }
    if (running > 0)
      return {
        text: t("{{count}} 运行中", { count: running }),
        label: t("{{count}} 个工作单元正在运行", { count: running }),
      };
    if (cancelled > 0)
      return {
        text: t("{{count}} 已取消", { count: cancelled }),
        label: t("{{count}} 个工作单元已取消", { count: cancelled }),
      };
  }

  const launchedWorkers = numericValue(event.summary.launched_workers);
  if (launchedWorkers !== null) {
    return {
      text: t("{{count}} 已启动", { count: launchedWorkers }),
      label: t("{{count}} 个工作单元已启动", { count: launchedWorkers }),
    };
  }

  const counts = numericRecord(event.summary.counts);
  const candidates = counts.candidates;
  const verifications = counts.verifications;
  if (candidates !== undefined && verifications) {
    return {
      text: t("{{verified}}/{{candidates}} 已验证", {
        verified: verifications,
        candidates,
      }),
      label: t("{{candidates}} 个候选漏洞中已有 {{verified}} 个验证记录", {
        candidates,
        verified: verifications,
      }),
    };
  }
  if (candidates !== undefined) {
    return {
      text: t("{{count}} 候选漏洞", { count: candidates }),
      label: t("{{count}} 个候选漏洞", { count: candidates }),
    };
  }

  const findingCount = numericValue(event.summary.finding_count);
  if (findingCount !== null) {
    return {
      text: t("{{count}} 漏洞", { count: findingCount }),
      label: t("{{count}} 个漏洞", { count: findingCount }),
    };
  }

  const status =
    typeof event.summary.status === "string" ? event.summary.status : "";
  if (status && phaseStatusLabels[status]) {
    return {
      text: t(phaseStatusLabels[status]),
      label: t(phaseStatusLabels[status]),
    };
  }

  const levelLabels = { info: "信息", warning: "警告", error: "错误" };
  return {
    text: t(levelLabels[event.level]),
    label: t("{{level}}事件", { level: t(levelLabels[event.level]) }),
  };
}

function numericRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, number] =>
        typeof entry[1] === "number" && Number.isFinite(entry[1]),
    ),
  );
}

function numericValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
