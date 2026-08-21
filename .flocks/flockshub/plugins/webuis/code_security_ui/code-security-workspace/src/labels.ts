import type { IconName } from "./icons";

export const phaseLabels: Record<string, string> = {
  snapshot: "准备源码快照",
  threat_modeling: "威胁建模",
  baseline: "基线扫描",
  verification: "独立验证",
  dynamic_validation: "动态验证",
  adjudication: "父 Agent 裁决",
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
};

export function statusIcon(status: string): IconName {
  if (status === "completed") return "check";
  if (status === "failed") return "error";
  if (status === "partial" || status === "interrupted") return "warning";
  if (status === "cancelled" || status === "skipped") return "skip";
  if (status === "running" || status === "preparing" || status === "cancelling")
    return "activity";
  return "clock";
}

export function formatDuration(milliseconds?: number | null): string {
  if (milliseconds == null) return "—";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}时${minutes}分${seconds}秒`;
  if (minutes) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

export function formatTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}

export function relativeTime(value: string): string {
  const delta = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${Math.floor(hours / 24)}天前`;
}

export function shortId(value?: string | null, length = 10): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}
