import type {
  EventDetailResponse,
  EventEvidenceCard,
  EventMember,
} from "@/api/securityMonitoringEvents";
import { readableEvidence } from "./EventAssessment";
import { eventCorrelationGroups } from "./correlationDetails";

export const handlingLabels: Record<string, string> = {
  open: "未闭环",
  closed: "已处置",
  ignored: "已忽略",
  contained: "已遏制 · 需跟进",
  ended: "源端关闭",
};

export const investigationLabels: Record<string, string> = {
  stale: "历史版本待核对",
  ready: "已有调查结论",
  pending: "等待调查",
  deferred: "待续查",
  system_wait: "等待服务恢复",
  needs_review: "证据不足",
  retry_exhausted: "调查暂未完成",
  running: "调查中",
};

export const xdrLabels: Record<string, string> = {
  "0": "待处置",
  "10": "处置中",
  "30": "已防护",
  "40": "已处置",
  "50": "已挂起",
  "60": "忽略",
  "70": "已遏制",
};

export const detailRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export const detailRecords = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.filter(
        (item) => item && typeof item === "object" && !Array.isArray(item),
      )
    : [];
export const detailText = (value: unknown, fallback = "来源未提供") =>
  typeof value === "string" || typeof value === "number"
    ? String(value)
    : fallback;
export const detailStrings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
export function uniqueTimeline(value: unknown) {
  const seen = new Set<string>();
  return detailRecords(value).filter((item) => {
    // Legacy snapshots have no reliable alert identity. Only exact repeats
    // can be removed; matching alert names never establishes identity.
    const marker = JSON.stringify(item);
    if (seen.has(marker)) return false;
    seen.add(marker);
    return true;
  });
}
const record = detailRecord,
  records = detailRecords,
  text = detailText,
  strings = detailStrings;
const eventEnded = (event: EventMember) =>
  event.source_closed === true ||
  event.assessment?.source_closed === true ||
  event.assessment?.lifecycle === "monitoring_ended" ||
  event.handling === "ended" ||
  event.assessment?.ended === true;

/** Display wording shared by the selected-event panel and its download. */
export function eventSourceStatus(event: EventMember) {
  const ended =
    event.source_closed === true ||
    event.assessment?.source_closed === true ||
    event.assessment?.lifecycle === "monitoring_ended" ||
    event.handling === "ended";
  const verifiedClosure =
    event.assessment?.source_closure_basis === "source_status";
  const historical = ended && !verifiedClosure && event.deal_status != null;
  const label =
    (xdrLabels[String(event.deal_status)] ||
      (ended ? "当前未核验" : undefined) ||
      handlingLabels[event.handling] ||
      "来源未提供") + (historical ? "（历史记录，当前未核验）" : "");
  const explanation = ended
    ? verifiedClosure
      ? "XDR 已关闭此事件。"
      : "XDR 本轮查询未再返回此事件，已结束本地监测。"
    : event.handling_source === "monitor_verified"
      ? "处置来源：套件写回并回查确认"
      : event.deal_status == null
        ? "尚未取得 XDR 处置状态，按未闭环跟踪。"
        : "处置来源：最近保存的 XDR 状态，非套件执行修复";
  return {
    label,
    explanation,
    closureBasis: ended
      ? verifiedClosure
        ? "XDR 状态已关闭"
        : "XDR 本轮未再返回"
      : "",
  };
}

/** The screen and readable download share the same current/historical evidence selection. */
export function eventDetailView(detail: EventDetailResponse) {
  const result = record(detail.investigation?.result);
  const state =
    detail.event.investigation?.state || detail.investigation?.state || "";
  const retainedRisk =
    detail.event.assessment?.confirmed_risk_retained === true ||
    detail.overview?.confirmed_risk_retained === true;
  const confirmedRisk =
    retainedRisk ||
    (detail.event.assessment?.verdict === "risk" &&
      detail.event.assessment?.evidence_supported === true);
  const historical =
    !retainedRisk &&
    (state === "stale" ||
      (detail.event.investigation?.is_current === false &&
        state !== "pending" &&
        Object.keys(result).length > 0));
  const history = record(
    detail.investigation?.historical_result ||
      (historical && detail.investigation?.state !== "stale" ? result : null),
  );
  const story = historical
    ? {}
    : record(detail.story || result.story || detail.event.story);
  const overview = historical ? {} : record(detail.overview);
  const basics = record(overview.basics || story.basics || detail.event.basics);
  const basicLabels: Record<string, string> = {
    name: "事件名称",
    device: "监测设备",
    alert_time: "告警时间",
    start_time: "事件首次发生",
    end_time: "事件最近发生",
    host: "关联主机",
    severity: "来源风险等级",
    src_ip: "源 IP",
    dst_ip: "目标 IP",
    src_port: "源端口",
    dst_port: "目标端口",
    url: "请求 URL",
    method: "请求方法",
    domain: "域名",
    virus_name: "病毒名称",
    file_name: "文件名称",
    file_path: "文件路径",
    file_md5: "文件 MD5",
    process_name: "进程",
    command_line: "命令行",
  };
  const evidence = records(overview.evidence || story.key_evidence);
  const packets = records(story.packets);
  const confirmedStory = retainedRisk ? record(detail.confirmed_story) : {};
  const timelineStory = retainedRisk ? confirmedStory : story;
  const timelineOverview = retainedRisk ? confirmedStory : overview;
  const hasAlertTimeline =
    Array.isArray(timelineOverview.alert_timeline) ||
    Array.isArray(timelineStory.alert_timeline);
  let timeline = uniqueTimeline(
    hasAlertTimeline
      ? timelineOverview.alert_timeline || timelineStory.alert_timeline
      : timelineOverview.timeline || timelineStory.timeline,
  );
  if (!retainedRisk && !timeline.length)
    timeline = uniqueTimeline(detail.source_event_timeline);
  const otherTimeline = hasAlertTimeline
    ? uniqueTimeline(
        timelineOverview.other_timeline || timelineStory.other_timeline,
      )
    : [];
  const timelineTruncated =
    timelineOverview.timeline_truncated === true ||
    timelineStory.timeline_truncated === true;
  const historicalEvidenceCards = records(
    confirmedStory.evidence_cards,
  ) as unknown as EventEvidenceCard[];
  const currentEvidenceCards = records(
    overview.evidence_cards || story.evidence_cards,
  ) as unknown as EventEvidenceCard[];
  const sourceCard = detail.source_event_evidence;
  const hasEvidenceCards =
    retainedRisk ||
    !!sourceCard ||
    Array.isArray(overview.evidence_cards) ||
    Array.isArray(story.evidence_cards);
  const evidenceCards = retainedRisk
    ? historicalEvidenceCards
    : [...(sourceCard ? [sourceCard] : []), ...currentEvidenceCards];
  const originalGaps = Array.isArray(overview.gaps)
    ? strings(overview.gaps)
    : Array.isArray(overview.missing_evidence)
      ? strings(overview.missing_evidence)
      : [...strings(story.gaps), ...strings(result.gaps)];
  const gaps = [
    ...new Set(
      (retainedRisk
        ? [
            ...(strings(overview.missing_evidence).length
              ? strings(overview.findings)
                  .slice(0, 1)
                  .map((line) => `已有（此前核实）：${line}`)
              : []),
            ...strings(overview.missing_evidence),
          ]
        : [...originalGaps, ...strings(overview.missing_evidence)]
      ).filter((gap) => !/调查尚未|尚未形成.*结论|调查未完成/.test(gap)),
    ),
  ];
  const recheckState = !eventEnded(detail.event)
    ? text(overview.recheck_state || detail.event.assessment?.recheck_state, "")
    : "";
  const recheckReason = text(
    overview.recheck_reason || detail.event.assessment?.recheck_reason,
    "",
  );
  const originalExplanation = text(
    historical
      ? detail.event.investigation?.reason ||
          "原事件已更新，旧调查结论仅供历史查看；当前事件仍需重新核对。"
      : result.reason || detail.event.reason || story.conclusion,
    "尚未形成有证据支持的调查结论。",
  );
  const explanation =
    readableEvidence(text(overview.conclusion, originalExplanation)) ||
    "尚未形成可读的调查结论。";
  const canShowOperatorCopy =
    state === "ready" &&
    !historical &&
    result.confidence === "high" &&
    detail.event.assessment?.is_current === true &&
    detail.event.assessment?.evidence_supported === true &&
    detail.event.assessment.verdict !== "unknown";
  const operatorSummary = canShowOperatorCopy
    ? readableEvidence(
        text(overview.operator_summary || story.operator_summary, ""),
      )
    : "";
  const operatorExplanation = canShowOperatorCopy
    ? strings(overview.operator_explanation || story.operator_explanation)
        .map(readableEvidence)
        .filter(Boolean)
        .slice(0, 3)
    : [];
  const conciseSummary = readableEvidence(text(overview.summary, ""));
  const findings = strings(overview.findings)
    .map(readableEvidence)
    .filter(Boolean)
    .slice(0, 2);
  const missingEvidence = strings(overview.missing_evidence)
    .map(readableEvidence)
    .filter(Boolean)
    .slice(0, 3);
  const correlationGroups = eventCorrelationGroups(detail, {
    historical,
    retainedRisk,
  });
  const currentCorrelationIds = correlationGroups
    .filter((group) => group.scope === "current")
    .flatMap((group) => [
      ...group.results.map((row) => row.evidence_id).filter(Boolean),
      ...(group.coveredEvidenceIds || []),
    ]);
  const otherQueries = strings(story.other_queries).filter(
    (line) => !currentCorrelationIds.some((id) => line.startsWith(`${id} ·`)),
  );
  return {
    state,
    retainedRisk,
    confirmedRisk,
    historical,
    history,
    story,
    overview,
    basics,
    basicLabels,
    timeline,
    otherTimeline,
    timelineTruncated,
    hasAlertTimeline,
    evidence,
    packets,
    hasEvidenceCards,
    evidenceCards,
    gaps,
    recheckState,
    recheckReason,
    explanation,
    operatorSummary,
    operatorExplanation,
    conciseSummary,
    findings,
    missingEvidence,
    correlationGroups,
    otherQueries,
  };
}
