import { useRef, useState } from "react";
import { ChevronDown, Download } from "lucide-react";
import type { EventDetailResponse } from "@/api/securityMonitoringEvents";
import {
  eventDetailJson,
  eventDetailMarkdown,
  saveEventDetail,
} from "./eventDetailExport";

export function EventDetailDownload({
  detail,
  enabled,
  timezone,
  formatTime,
}: {
  detail: EventDetailResponse | null;
  enabled: boolean;
  timezone: string;
  formatTime: (value: unknown) => string;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const [error, setError] = useState("");
  const download = (format: "md" | "json") => {
    if (!enabled || !detail) return;
    if (menu.current) menu.current.open = false;
    setError("");
    try {
      const exportedAt = new Date();
      const content =
        format === "md"
          ? eventDetailMarkdown(detail, timezone, formatTime, exportedAt)
          : eventDetailJson(detail, timezone, exportedAt);
      saveEventDetail(content, detail, format, exportedAt);
    } catch {
      setError("详情下载失败，请重试。");
    }
  };
  return (
    <div className="relative flex items-center gap-1">
      <button
        type="button"
        disabled={!enabled}
        onClick={() => download("md")}
        className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-xs font-medium text-sky-700 hover:bg-sky-50 disabled:opacity-40 dark:text-sky-300 dark:hover:bg-sky-950"
        title="下载当前所选原事件的 Markdown 详情"
      >
        <Download size={16} />
        <span>下载详情</span>
      </button>
      <details ref={menu} className="relative">
        <summary
          aria-label="选择详情下载格式"
          aria-disabled={!enabled}
          onClick={(event) => {
            if (!enabled) event.preventDefault();
          }}
          className={`flex list-none rounded-lg p-1.5 text-sky-700 [&::-webkit-details-marker]:hidden dark:text-sky-300 ${enabled ? "cursor-pointer hover:bg-sky-50 dark:hover:bg-sky-950" : "opacity-40"}`}
        >
          <ChevronDown size={14} />
        </summary>
        <div className="absolute right-0 top-full z-20 mt-2 min-w-40 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
          <button
            type="button"
            disabled={!enabled}
            onClick={() => download("json")}
            className="w-full rounded px-3 py-2 text-left text-xs text-slate-700 hover:bg-sky-50 disabled:opacity-40 dark:text-slate-200 dark:hover:bg-sky-950"
          >
            下载脱敏 JSON
          </button>
        </div>
      </details>
      {error && (
        <p
          role="alert"
          className="absolute right-0 top-full mt-2 w-44 rounded-lg bg-amber-50 p-2 text-xs text-amber-800 shadow"
        >
          {error}
        </p>
      )}
    </div>
  );
}
