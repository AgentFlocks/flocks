import { useState } from "react";
import { readableEvidence } from "./EventAssessment";

export const timelinePartialNotice =
  "部分取证仅返回部分记录，以下时间范围不代表完整历史，不能据此排除风险。";

export function timelineTime(
  item: Record<string, unknown>,
  formatTime: (value: unknown) => string,
) {
  if (!("first_time" in item) && !("end_time" in item))
    return formatTime(item.time);
  const first = item.first_time,
    end = item.end_time;
  const hasFirst = first != null && first !== "",
    hasEnd = end != null && end !== "";
  if (hasFirst && hasEnd)
    return first === end
      ? formatTime(first)
      : `${formatTime(first)} — ${formatTime(end)}`;
  if (hasFirst) return `首次 ${formatTime(first)}（最近时间未提供）`;
  if (hasEnd) return `最近 ${formatTime(end)}（首次时间未提供）`;
  return formatTime(item.time);
}

export function EventTimeline({
  timeline,
  otherTimeline,
  grouped,
  retainedRisk,
  truncated,
  formatTime,
}: {
  timeline: Record<string, unknown>[];
  otherTimeline: Record<string, unknown>[];
  grouped: boolean;
  retainedRisk: boolean;
  truncated: boolean;
  formatTime: (value: unknown) => string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!timeline.length && !otherTimeline.length && !truncated) return null;
  return (
    <section aria-label="事件发生时间线">
      <h3 className="mb-2 text-sm font-semibold">事件发生时间线</h3>
      {truncated && (
        <p className="mb-3 text-xs leading-6 text-amber-700">
          部分来源时间受数量上限影响，当前时间线可能不完整。
        </p>
      )}
      {timeline.some((item) => item.partial === true) && (
        <p className="mb-3 text-xs leading-6 text-amber-700">
          {timelinePartialNotice}
        </p>
      )}
      {grouped && (
        <p className="mb-3 text-xs leading-6 text-slate-500">
          {retainedRisk
            ? "以下为此前确认风险的告警时间；补充核查另行记录。"
            : "同一告警合并展示首次至最近发生的时间；简要列出源、目的 IP，完整信息见证据详情。"}
        </p>
      )}
      {!!timeline.length && (
        <ol
          aria-label="告警时间线"
          className="ml-1 space-y-3 border-l border-sky-200 pl-5 dark:border-sky-800"
        >
          {(expanded ? timeline : timeline.slice(0, 5)).map((item, index) => (
            <li key={String(item.id || index)} className="relative">
              <span className="absolute -left-[25px] top-1.5 h-2 w-2 rounded-full bg-emerald-500" />
              <time className="text-xs font-medium text-sky-700 dark:text-sky-300">
                {timelineTime(item, formatTime)}
              </time>
              <p className="mt-1 break-words text-sm leading-6">
                {readableEvidence(
                  String(item.text || item.name || "来源未提供行为描述"),
                )}
              </p>
              {typeof item.boundary === "string" && item.boundary && (
                <p className="mt-1 break-words text-xs leading-5 text-slate-500">
                  {item.boundary}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
      {timeline.length > 5 && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="mt-3 rounded px-1 py-1 text-xs font-medium text-sky-700 hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-950"
        >
          {expanded ? "收起时间线" : `展开全部 ${timeline.length} 条`}
        </button>
      )}
      {!!otherTimeline.length && (
        <details className="mt-3 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900">
          <summary className="cursor-pointer font-medium">
            其他来源时间（{otherTimeline.length} 条）
          </summary>
          <ul className="mt-2 space-y-2">
            {otherTimeline.map((item, index) => (
              <li key={index}>
                {timelineTime(item, formatTime)} ·{" "}
                {String(item.text || "来源未提供描述")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
