import { CheckCircle2, CircleDot, ShieldAlert } from "lucide-react";
import type {
  EventAssessmentStatus,
  EventMember,
} from "@/api/securityMonitoringEvents";

export const assessmentLabels: Record<EventAssessmentStatus, string> = {
  risk_open: "有风险 · 处置中",
  risk_closed: "有风险 · 已处置",
  benign: "无风险 · 待忽略",
  insufficient: "证据不足 · 待观察",
  ended: "源端关闭",
  source_closed_risk: "源端关闭 · 曾确认风险",
  source_closed_benign: "源端关闭 · 无风险",
  source_closed_unknown: "源端关闭 · 未定性",
};

export function assessmentStatus(member: EventMember): EventAssessmentStatus {
  if (member.assessment?.source_closed || member.source_closed) {
    const verdict =
      member.assessment?.source_closed_verdict ||
      member.source_closed_verdict ||
      member.assessment?.historical_verdict;
    return verdict === "risk"
      ? "source_closed_risk"
      : verdict === "benign"
        ? "source_closed_benign"
        : "source_closed_unknown";
  }
  if (member.assessment) return member.assessment.status;
  // Legacy rows lack the evidence-backed classification. Never promote a risk
  // label or an old verdict to a confirmed risk or a confirmed benign result.
  return "insufficient";
}

export function AssessmentBadge({
  status,
  label,
}: {
  status: EventAssessmentStatus;
  label?: string;
}) {
  const color =
    status === "ended" || status === "source_closed_unknown"
      ? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
      : status === "benign" ||
          status === "risk_closed" ||
          status === "source_closed_benign"
        ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
        : status === "risk_open" || status === "source_closed_risk"
          ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
          : "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${color}`}
    >
      {status === "risk_open" || status === "source_closed_risk" ? (
        <ShieldAlert size={12} />
      ) : status === "insufficient" ||
        status === "ended" ||
        status === "source_closed_unknown" ? (
        <CircleDot size={12} />
      ) : (
        <CheckCircle2 size={12} />
      )}
      {label || assessmentLabels[status]}
    </span>
  );
}

/** Keep the original in technical details; presentation removes only opaque IDs. */
export function readableEvidence(value: string): string {
  return value
    .replace(/\[?evidence[-_]\d+\]?/gi, "")
    .replace(
      /(?:告警编号|告警\s*ID|事件编号|事件\s*ID)\s*[:：]\s*(?:(?:alert|incident)-[a-z0-9-]+[、,，;；\s]*)+/gi,
      "",
    )
    .replace(/\b(?:alert|incident)-[a-f0-9]{8}-[a-f0-9-]+\b/gi, "")
    .replace(/(?:^|[；;])\s*[；;：:]\s*/g, "；")
    .replace(/^\s*[；;：:、，,]+|[；;：:、，,]+\s*$/g, "")
    .replace(/ {2,}/g, " ")
    .trim();
}
