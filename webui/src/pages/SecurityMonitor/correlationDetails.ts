import type {
  EventCorrelationResult,
  EventDetailResponse,
} from "@/api/securityMonitoringEvents";

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const text = (value: unknown) => (typeof value === "string" ? value : "");
export const correlationStatusLabels: Record<
  EventCorrelationResult["status"],
  string
> = {
  not_queried: "未查询",
  failed: "查询失败",
  no_match: "明确无匹配",
  candidate: "发现候选",
  insufficient: "资料不足",
  legacy: "旧记录 · 资料不足",
  unknown: "资料不足",
  success: "已取得资料",
  unavailable: "设备不可用",
  skipped: "已跳过",
};
export const correlationProduct = (row: EventCorrelationResult) =>
  /sig/i.test(row.product || "")
    ? "OneSIG · 资产核查"
    : /tdp/i.test(row.product || "")
      ? "TDP · 日志核查"
      : row.product && row.product !== "unknown"
        ? row.product
        : "关联设备";
export const correlationCount = (row: EventCorrelationResult) =>
  typeof row.returned_count === "number" &&
  Number.isInteger(row.returned_count) &&
  row.returned_count >= 0
    ? `返回 ${row.returned_count} 条${/sig/i.test(row.product || "") ? "资产记录" : "记录"}`
    : "返回数未提供";
export const correlationWindow = (
  row: EventCorrelationResult,
  formatTime: (value: unknown) => string,
) => {
  if (/sig/i.test(row.product || "")) return "资产查询未提供发生时间范围";
  const start = row.window?.start,
    end = row.window?.end;
  if (start != null && end != null)
    return `${formatTime(start)} — ${formatTime(end)}`;
  if (start != null) return `起始 ${formatTime(start)}（结束时间未提供）`;
  if (end != null) return `截至 ${formatTime(end)}（起始时间未提供）`;
  return "时间范围未提供";
};

export interface CorrelationGroup {
  scope: "current" | "historical" | "confirmed";
  title: string;
  description: string;
  results: EventCorrelationResult[];
}

function legacyResults(detail: EventDetailResponse): EventCorrelationResult[] {
  return (detail.investigation?.evidence || []).flatMap((proof, index) => {
    const tool = text(proof.tool).split("__")[0];
    if (
      proof.kind !== "related" &&
      !/^(?:tdp_log_search|onesig_strategy_api_query)$/.test(tool)
    )
      return [];
    const query = record(proof.query),
      body = record(query.body);
    const product = tool.startsWith("onesig")
      ? "SIG"
      : tool.startsWith("tdp")
        ? "TDP"
        : "";
    const window = product === "SIG" ? {} : record(proof.window);
    const sqlHosts = Array.from(
      text(query.sql).matchAll(/net\.(?:src_ip|dest_ip)\s*=\s*'([^']+)'/g),
      (match) => match[1],
    );
    const host =
      text(body.search) || (new Set(sqlHosts).size === 1 ? sqlHosts[0] : "");
    // Old facts may already have lost unsupported fields. Their emptiness or
    // list length cannot establish a complete no-match result.
    return [
      {
        id: text(proof.id) || `legacy-${index}`,
        evidence_id: text(proof.id),
        device: text(proof.device),
        source: text(proof.source_name) || text(proof.device),
        product,
        status: proof.success === false ? "failed" : "legacy",
        reason:
          text(proof.error) ||
          "旧记录缺少完整返回口径，不能据此判断无匹配或核查完成。",
        host,
        window: {
          start:
            (query.time_from as string | number | undefined) ??
            (window.start as string | number | undefined),
          end:
            (query.time_to as string | number | undefined) ??
            (window.end as string | number | undefined),
        },
        partial: proof.partial === true,
        boundary: "跨设备查询仅为候选关联，需核对资产身份和时间范围。",
        query: proof.query,
        facts: proof.facts,
      } satisfies EventCorrelationResult,
    ];
  });
}

export function eventCorrelationGroups(
  detail: EventDetailResponse,
  { historical, retainedRisk }: { historical: boolean; retainedRisk: boolean },
): CorrelationGroup[] {
  const result = record(detail.investigation?.result);
  const story = record(detail.story || result.story || detail.event.story);
  const overview = record(detail.overview);
  const hasCurrent =
    Array.isArray(overview.correlation_results) ||
    Array.isArray(story.correlation_results);
  const current = (
    Array.isArray(overview.correlation_results)
      ? overview.correlation_results
      : Array.isArray(story.correlation_results)
        ? story.correlation_results
        : []
  ) as EventCorrelationResult[];
  const saved = hasCurrent && !historical ? [] : legacyResults(detail);
  const groups: CorrelationGroup[] = [];
  if (hasCurrent || !historical)
    groups.push({
      scope: "current",
      title: retainedRisk ? "当前补充核查" : "当前联动核查",
      description:
        "仅展示本事件已保存的联动查询；跨设备命中需核对资产身份，资产资料不等于威胁告警。",
      results: hasCurrent ? current : retainedRisk ? [] : saved,
    });
  if ((historical || (retainedRisk && !hasCurrent)) && saved.length)
    groups.push({
      scope: "historical",
      title: historical
        ? "历史联动记录（仅供参考）"
        : "已保存联动记录（版本待核对）",
      description:
        "以下为旧格式记录，版本与完整返回口径待核对，不能作为当前事件已完成联动核查的依据。",
      results: saved,
    });
  if (retainedRisk) {
    const confirmed = record(detail.confirmed_story);
    groups.push({
      scope: "confirmed",
      title: "此前确证时的联动记录",
      description: "以下为此前确证时保留的资料，不代表当前补充核查已完成。",
      results: Array.isArray(confirmed.correlation_results)
        ? (confirmed.correlation_results as EventCorrelationResult[])
        : [],
    });
  }
  return groups;
}
