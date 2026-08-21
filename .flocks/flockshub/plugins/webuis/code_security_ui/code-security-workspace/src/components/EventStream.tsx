import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import { formatTime, phaseLabels, shortId } from "../labels";
import type { AuditEvent } from "../types";

const ROW_HEIGHT = 72;
const VIEWPORT_HEIGHT = 360;

export function EventStream({
  events,
  hasOlder,
  loadingOlder,
  onLoadOlder,
}: {
  events: AuditEvent[];
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => Promise<void>;
}) {
  const [level, setLevel] = useState("all");
  const [phase, setPhase] = useState("all");
  const [worker, setWorker] = useState("all");
  const [autoFollow, setAutoFollow] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const previousFirstSeq = useRef(events[0]?.seq);
  const previousLastSeq = useRef(events[events.length - 1]?.seq);
  const layoutFirstSeq = useRef(events[0]?.seq);
  const previousScrollHeight = useRef(0);
  const viewport = useRef<HTMLDivElement>(null);
  const phaseOptions = useMemo(
    () => uniqueEventValues(events, "phase"),
    [events],
  );
  const workerOptions = useMemo(
    () => uniqueEventValues(events, "work_unit_id", "worker", "batch_id"),
    [events],
  );
  const filtered = useMemo(
    () =>
      events.filter(
        (event) =>
          (level === "all" || event.level === level) &&
          (phase === "all" || event.summary.phase === phase) &&
          (worker === "all" || eventWorker(event) === worker),
      ),
    [events, level, phase, worker],
  );

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
      viewport.current.scrollTop = viewport.current.scrollHeight;
    } else {
      setUnseen((value) => value + added);
    }
  }, [autoFollow, events]);

  useLayoutEffect(() => {
    const element = viewport.current;
    const firstSeq = events[0]?.seq;
    if (!element) return;
    if (
      layoutFirstSeq.current !== undefined &&
      firstSeq !== undefined &&
      firstSeq < layoutFirstSeq.current
    ) {
      element.scrollTop += element.scrollHeight - previousScrollHeight.current;
    }
    layoutFirstSeq.current = firstSeq;
    previousScrollHeight.current = element.scrollHeight;
  }, [events, filtered.length]);

  const virtual = filtered.length > 100;
  const start = virtual
    ? Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - 4)
    : 0;
  const end = virtual
    ? Math.min(
        filtered.length,
        start + Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + 8,
      )
    : filtered.length;
  const visible = filtered.slice(start, end);

  const returnToLatest = () => {
    setAutoFollow(true);
    setUnseen(0);
    if (viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  };

  return (
    <section className="cs-events" aria-labelledby="event-title">
      <div className="cs-subsection-heading cs-events__heading">
        <div>
          <h3 id="event-title">实时事件</h3>
          <span>已加载 {events.length} 条可信事件</span>
        </div>
        <div className="cs-event-filters">
          <label>
            <span className="cs-visually-hidden">按阶段筛选事件</span>
            <select
              value={phase}
              onChange={(event) => setPhase(event.target.value)}
            >
              <option value="all">全部阶段</option>
              {phaseOptions.map((value) => (
                <option key={value} value={value}>
                  {phaseLabels[value] || value}
                </option>
              ))}
            </select>
          </label>
          {workerOptions.length > 0 && (
            <label>
              <span className="cs-visually-hidden">按工作单元筛选事件</span>
              <select
                value={worker}
                onChange={(event) => setWorker(event.target.value)}
              >
                <option value="all">全部 Worker</option>
                {workerOptions.map((value) => (
                  <option key={value} value={value}>
                    {shortId(value, 14)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            <span className="cs-visually-hidden">按级别筛选事件</span>
            <select
              value={level}
              onChange={(event) => setLevel(event.target.value)}
            >
              <option value="all">全部级别</option>
              <option value="info">信息</option>
              <option value="warning">警告</option>
              <option value="error">错误</option>
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
          {loadingOlder ? "正在加载更早事件…" : "加载更早事件"}
        </button>
      )}
      <div
        ref={viewport}
        className="cs-event-viewport"
        style={{ height: VIEWPORT_HEIGHT }}
        onScroll={(event) => {
          const element = event.currentTarget;
          setScrollTop(element.scrollTop);
          const atBottom =
            element.scrollHeight - element.scrollTop - element.clientHeight <
            24;
          setAutoFollow(atBottom);
          if (atBottom) setUnseen(0);
        }}
        tabIndex={0}
        aria-label="审计事件列表"
      >
        {virtual ? (
          <div
            className="cs-event-virtual"
            style={{ height: filtered.length * ROW_HEIGHT }}
          >
            {visible.map((event, index) => (
              <EventRow
                key={event.seq}
                event={event}
                style={{
                  transform: `translateY(${(start + index) * ROW_HEIGHT}px)`,
                }}
              />
            ))}
          </div>
        ) : (
          visible.map((event) => <EventRow key={event.seq} event={event} />)
        )}
        {!filtered.length && (
          <p className="cs-inline-empty">当前筛选下还没有事件。</p>
        )}
      </div>
      {!autoFollow && unseen > 0 && (
        <button
          className="cs-new-events"
          type="button"
          onClick={returnToLatest}
        >
          有 {unseen} 条新事件 · 回到最新
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

function EventRow({
  event,
  style,
}: {
  event: AuditEvent;
  style?: CSSProperties;
}) {
  const phase =
    typeof event.summary.phase === "string" ? event.summary.phase : "";
  return (
    <article
      className={`cs-event-row cs-event-row--${event.level}`}
      style={style}
    >
      <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
      <span className="cs-event-row__marker" aria-hidden="true" />
      <div>
        <strong>{event.title}</strong>
        <p>
          {phaseLabels[phase] || phase || event.type} · seq {event.seq}
        </p>
      </div>
      <button
        type="button"
        className="cs-copy-button"
        aria-label={`复制事件 ${event.seq} 摘要`}
        onClick={() =>
          navigator.clipboard?.writeText(
            `${event.title}\n${JSON.stringify(event.summary)}`,
          )
        }
      >
        {shortId(String(event.seq), 8)}
      </button>
    </article>
  );
}
