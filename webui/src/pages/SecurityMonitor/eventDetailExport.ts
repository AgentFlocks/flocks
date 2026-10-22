import type {
  EventDetailResponse,
  EventEvidenceCard,
} from "@/api/securityMonitoringEvents";
import {
  assessmentLabels,
  assessmentStatus,
  readableEvidence,
} from "./EventAssessment";
import {
  detailRecord as record,
  detailRecords as records,
  detailText as text,
  eventDetailView,
  eventSourceStatus,
  investigationLabels,
} from "./eventDetailView";
import { timelinePartialNotice, timelineTime } from "./EventTimeline";
import { correlationLine } from "./correlationDetails";

const markdown = (value: unknown): string =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/([\\`*_{}\[\]#|])/g, "\\$1")
    .replace(/\r?\n/g, "  \n");
const valueText = (value: unknown): string =>
  Array.isArray(value)
    ? value.map(valueText).join("、")
    : value && typeof value === "object"
      ? JSON.stringify(value)
      : text(value);
const code = (value: unknown) => {
  const body =
    typeof value === "string"
      ? value
      : (JSON.stringify(value, null, 2) ?? "来源未提供");
  const fence = "`".repeat(
    Math.max(
      3,
      ...Array.from(body.matchAll(/`+/g), (match) => match[0].length + 1),
    ),
  );
  return `${fence}\n${body}\n${fence}`;
};

export function eventEvidenceScope(detail: EventDetailResponse) {
  const view = eventDetailView(detail);
  if (view.retainedRisk)
    return detail.confirmed_evidence_scope === "previous_activity" ||
      detail.event.assessment?.risk_scope === "previous_activity"
      ? "此前活动已确认风险的证据；不代表新增活动已核查。"
      : "保留此前确认风险的证据；补充核查单独记录。";
  return view.historical
    ? "当前事件已更新；旧调查结论仅供历史参考，未作为当前事件取证。"
    : "当前事件版本的已保存取证。";
}

function evidenceMarkdown(
  card: EventEvidenceCard,
  index: number,
  formatTime: (value: unknown) => string,
) {
  const lines = [
    `### ${index + 1}. ${markdown(card.summary)}`,
    "",
    `来源：${markdown(card.source)}`,
  ];
  const fields = card.fields || [];
  const alertId =
    card.alert_id ||
    fields.find((field) => ["alert_id", "alert_ids"].includes(field.key))
      ?.value;
  if (alertId) lines.push(`告警编号：${markdown(valueText(alertId))}`);
  for (const field of fields) {
    if (["alert_id", "alert_ids"].includes(field.key)) continue;
    const value = /(?:_time|_at)$/.test(field.key)
      ? (Array.isArray(field.value) ? field.value : [field.value])
          .map(formatTime)
          .join("、")
      : valueText(field.value);
    lines.push(`- ${markdown(field.label)}：${markdown(value)}`);
  }
  if (!fields.some((field) => field.key === "alert_time") && card.time != null)
    lines.push(`发生时间：${markdown(formatTime(card.time))}`);
  if (card.boundary) lines.push("", markdown(card.boundary));
  if (card.partial)
    lines.push("", "该查询仅返回部分记录；未取得的内容不能据此排除风险。");
  for (const packet of card.packets || [])
    lines.push(
      "",
      `**${markdown(packet.label)}**${packet.truncated ? "（已截断）" : ""}`,
      "",
      code(packet.text || "（来源明确返回空正文）"),
    );
  const provenance = card.records?.length
    ? card.records
    : [
        {
          evidence_id: card.evidence_id,
          source: card.source,
          query: card.query,
        },
      ];
  lines.push("", "查询来源：");
  for (const item of provenance) {
    lines.push(
      `- ${markdown(item.evidence_id || "未编号查询")} · ${markdown(item.source || card.source)}`,
    );
    if (item.query) lines.push("", code(item.query));
  }
  return lines.join("\n");
}

export function eventDetailMarkdown(
  detail: EventDetailResponse,
  timezone: string,
  formatTime: (value: unknown) => string,
  exportedAt = new Date(),
) {
  const view = eventDetailView(detail);
  const event = detail.event;
  const sourceStatus = eventSourceStatus(event);
  const lines = [
    `# 事件详情 · ${markdown(event.name)}`,
    "",
    `导出时间：${markdown(formatTime(exportedAt.toISOString()))}（${markdown(exportedAt.toISOString())}）`,
    `显示时区：${markdown(timezone)}；未带时区的来源时间保留原值。`,
    `原始事件 ID：${markdown(event.id || event.key)}`,
    `监测设备：${markdown(event.device_name || event.device)}`,
    `关联主机：${markdown(event.host || "来源未提供")}`,
    `状态：${markdown(event.assessment?.label || assessmentLabels[assessmentStatus(event)])}`,
    `XDR 状态：${markdown(sourceStatus.label)}`,
    markdown(sourceStatus.explanation),
    ...(sourceStatus.closureBasis
      ? [`源端关闭依据：${markdown(sourceStatus.closureBasis)}`]
      : []),
    `证据范围：${markdown(eventEvidenceScope(detail))}`,
    "",
    "## 调查结论",
    "",
    markdown(view.conciseSummary || view.operatorSummary || view.explanation),
  ];
  if (view.recheckState) {
    lines.push(
      "",
      `补充核查 · ${markdown(investigationLabels[view.recheckState] || "待续查")}`,
    );
    if (view.recheckReason)
      lines.push(`补查说明：${markdown(view.recheckReason)}`);
  }
  if (view.conciseSummary && view.findings.length)
    lines.push(
      "",
      "已有证据：",
      ...view.findings.map((line) => `- ${markdown(line)}`),
    );
  else if (view.operatorSummary)
    lines.push(...view.operatorExplanation.map((line) => markdown(line)));
  const gaps = view.conciseSummary
    ? view.missingEvidence
    : view.gaps.map(readableEvidence).slice(0, 3);
  if (gaps.length)
    lines.push("", "关键缺口：", ...gaps.map((line) => `- ${markdown(line)}`));
  if (view.historical && Object.keys(view.history).length)
    lines.push(
      "",
      "### 历史调查结论（仅供参考）",
      "",
      "此结论对应旧事件版本，不能作为当前事件已完成调查或已排除风险的依据。",
      "",
      markdown(text(view.history.reason, "历史结论内容未提供")),
    );
  lines.push("", "## 事件基本信息", "");
  for (const [key, label] of Object.entries(view.basicLabels)) {
    const value = view.basics[key];
    if (value == null || value === "") continue;
    lines.push(
      `- ${label}：${markdown(key.endsWith("time") ? formatTime(value) : valueText(value))}`,
    );
  }
  lines.push("", "## 事件发生时间线", "");
  if (view.timelineTruncated)
    lines.push("部分来源时间受数量上限影响，当前时间线可能不完整。", "");
  if (view.timeline.some((item) => item.partial === true))
    lines.push(timelinePartialNotice, "");
  if (view.retainedRisk) lines.push(markdown(eventEvidenceScope(detail)), "");
  if (!view.timeline.length) lines.push("未取得可展示的告警发生时间。");
  else
    for (const item of view.timeline) {
      lines.push(
        `- ${markdown(timelineTime(item, formatTime))} · ${markdown(readableEvidence(text(item.text || item.name)))}`,
      );
      if (item.boundary) lines.push(`  范围说明：${markdown(item.boundary)}`);
    }
  if (view.otherTimeline.length)
    lines.push(
      "",
      "其他来源时间：",
      ...view.otherTimeline.map(
        (item) =>
          `- ${markdown(timelineTime(item, formatTime))} · ${markdown(item.text)}`,
      ),
    );
  lines.push(
    "",
    `## ${view.retainedRisk ? "此前确认风险的证据" : "关键证据"}`,
    "",
  );
  if (view.hasEvidenceCards) {
    if (!view.evidenceCards.length)
      lines.push(
        view.retainedRisk
          ? "此前确认风险的结论已保留，原取证明细暂不可读取。"
          : "尚未取得与当前事件版本对应的具体行为证据。",
      );
    view.evidenceCards.forEach((card, index) =>
      lines.push(evidenceMarkdown(card, index, formatTime), ""),
    );
  } else {
    for (const item of view.evidence)
      lines.push(`- ${markdown(readableEvidence(text(item.text)))}`);
    for (const packet of view.packets)
      lines.push(
        "",
        `### ${markdown(text(packet.label, "原始证据片段"))}${packet.truncated === true ? "（已截断）" : ""}`,
        "",
        `告警编号：${markdown(packet.alert_id)} · 来源：${markdown(packet.source)}`,
        "",
        code(text(packet.text, "来源未提供报文").slice(0, 16000)),
      );
  }
  if (view.gaps.length)
    lines.push(
      "",
      "## 证据缺口与待跟进",
      "",
      ...Array.from(new Set(view.gaps.map(readableEvidence).filter(Boolean)))
        .slice(0, 4)
        .map((gap) => `- ${markdown(gap)}`),
    );
  lines.push("", "## 联动调查", "");
  if (!view.correlationGroups.length) lines.push("尚无联动查询记录。");
  for (const group of view.correlationGroups) {
    if (view.correlationGroups.length > 1)
      lines.push(`### ${markdown(group.title)}`, "");
    if (group.coveredEvidenceIds?.length)
      lines.push("有价值的查询结果已列入关键证据。");
    if (!group.results.length && !group.coveredEvidenceIds?.length)
      lines.push("尚无联动查询记录。");
    lines.push(
      ...group.results.map((row) => `- ${markdown(correlationLine(row))}`),
      "",
    );
  }
  if (view.otherQueries.length)
    lines.push(
      "",
      "## 其他已执行的关联查询",
      "",
      ...view.otherQueries.map((line) => `- ${markdown(line)}`),
    );
  lines.push(
    "",
    "## 邮件与处置跟踪",
    "",
    "发信或收到回信不代表 XDR 已确认处置完成。",
  );
  const notice = detail.notice;
  if (notice) {
    lines.push(
      "",
      `邮件状态：${markdown(notice.state)}；收件人：${markdown(notice.recipient)}`,
      `发送时间：${markdown(formatTime(notice.sent_at))}`,
      `主题：${markdown(notice.subject)}`,
      "",
      markdown(notice.body),
    );
    for (const reply of records(notice.replies))
      lines.push(
        "",
        `回信：${markdown(formatTime(reply.received_at))} · ${markdown(reply.sender)} · ${markdown(reply.state)}`,
        "",
        markdown(record(reply.payload).text),
      );
  } else lines.push("", "尚无关联邮件记录。");
  for (const disposition of detail.dispositions || [])
    lines.push(
      "",
      `处置记录：${markdown(formatTime(disposition.updated_at))} · ${markdown(disposition.status)}`,
      markdown(disposition.comment),
    );
  lines.push("", "## 资料来源与完整性", "", `事件键：${markdown(event.key)}`);
  const result = record(detail.investigation?.result);
  const version = view.retainedRisk
    ? record(detail.confirmed_story)
    : view.historical
      ? view.history
      : result;
  lines.push(
    `取证版本：${markdown(valueText(version.revision ?? version.event_revision ?? "接口未提供"))}`,
  );
  if (!view.retainedRisk && detail.event.investigation?.updated_at)
    lines.push(
      `调查更新时间：${markdown(formatTime(detail.event.investigation.updated_at))}`,
    );
  if (detail.confirmed_at && view.retainedRisk)
    lines.push(`此前确证时间：${markdown(formatTime(detail.confirmed_at))}`);
  if (detail.truncated)
    lines.push("部分证据或历史记录受读取上限影响，此导出不是全量原始数据。");
  for (const warning of detail.warnings || [])
    lines.push(`- ${markdown(warning)}`);
  lines.push(
    "",
    "本文件来自当前已读取的事件详情快照。查询参数见各条证据；脱敏原始记录及当前、历史版本数据可通过“下载脱敏 JSON”查看。",
  );
  return lines.join("\n") + "\n";
}

export function eventDetailJson(
  detail: EventDetailResponse,
  timezone: string,
  exportedAt = new Date(),
) {
  // This is the authorized, server-redacted /events/detail response, never a
  // fresh tool query or a browser-local copy of unfiltered source records.
  return JSON.stringify(
    {
      exported_at: exportedAt.toISOString(),
      timezone,
      evidence_scope: eventEvidenceScope(detail),
      snapshot_scope: "selected_event_detail",
      detail,
    },
    null,
    2,
  );
}

export function saveEventDetail(
  content: string,
  detail: EventDetailResponse,
  format: "md" | "json",
  exportedAt = new Date(),
) {
  const name = (detail.event.name || detail.event.id || "事件详情")
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .slice(0, 80);
  const url = URL.createObjectURL(
    new Blob([content], {
      type:
        format === "md"
          ? "text/markdown;charset=utf-8"
          : "application/json;charset=utf-8",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `事件详情-${name}-${exportedAt.toISOString().replace(/[:.]/g, "-")}.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
