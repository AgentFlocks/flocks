import type { IconName } from "./icons";
import type { Translator } from "./i18n";

export const phaseLabels: Record<string, string> = {
  snapshot: "准备源码快照",
  threat_modeling: "威胁建模",
  baseline: "基线扫描",
  verification: "静态验证",
  dynamic_validation: "动态验证",
  adjudication: "主智能体裁决",
  targeted_rescan: "定向复扫",
  finalization: "产物封装",
};

export const lifecycleLabels: Record<string, string> = {
  preparing: "准备中",
  running: "运行中",
  cancelling: "正在取消",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export const phaseStatusLabels: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  partial: "部分完成",
  failed: "执行失败",
  cancelled: "已取消",
  skipped: "已跳过",
  not_runnable: "无法动态执行",
};

export const integrityStatusLabels: Record<string, string> = {
  pending: "待校验",
  valid: "校验通过",
  invalid: "校验失败",
};

export const coverageStatusLabels: Record<string, string> = {
  pending: "待评估",
  partial: "部分覆盖",
  blocked: "覆盖受阻",
  complete: "完整覆盖",
  unknown: "未知",
};

export const severityLabels: Record<string, string> = {
  critical: "严重",
  high: "高危",
  medium: "中危",
  low: "低危",
};

export const verdictLabels: Record<string, string> = {
  confirmed: "已确认",
  rejected: "已驳回",
  insufficient_evidence: "证据不足",
};

export function statusIcon(status: string): IconName {
  if (status === "completed") return "check";
  if (status === "failed") return "error";
  if (
    status === "partial" ||
    status === "interrupted" ||
    status === "not_runnable"
  )
    return "warning";
  if (status === "cancelled" || status === "skipped") return "skip";
  if (status === "running" || status === "preparing" || status === "cancelling")
    return "activity";
  return "clock";
}

export function formatDuration(
  milliseconds: number | null | undefined,
  t: Translator,
): string {
  if (milliseconds == null) return "—";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours)
    return t("{{hours}}时{{minutes}}分{{seconds}}秒", {
      hours,
      minutes,
      seconds,
    });
  if (minutes) return t("{{minutes}}分{{seconds}}秒", { minutes, seconds });
  return t("{{seconds}}秒", { seconds });
}

export function formatTime(
  value: string | null | undefined,
  language: string,
): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString(language, { hour12: false });
}

export function formatFileSize(
  bytes: number | null | undefined,
  language: string,
): string {
  const normalizedBytes =
    typeof bytes === "number" && Number.isFinite(bytes)
      ? Math.max(0, bytes)
      : 0;
  const kilobytes = normalizedBytes / 1024;
  const useMegabytes = kilobytes >= 1024;
  const value = useMegabytes ? kilobytes / 1024 : kilobytes;

  return `${value.toLocaleString(language, {
    maximumFractionDigits: 1,
  })} ${useMegabytes ? "MB" : "KB"}`;
}

export function relativeTime(value: string, t: Translator): string {
  const delta = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return t("刚刚");
  if (minutes < 60) return t("{{minutes}}分钟前", { minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("{{hours}}小时前", { hours });
  return t("{{days}}天前", { days: Math.floor(hours / 24) });
}

export function shortId(value?: string | null, length = 10): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}
