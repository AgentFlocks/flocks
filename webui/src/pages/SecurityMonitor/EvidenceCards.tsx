import { ChevronDown, FileSearch, ShieldCheck } from "lucide-react";
import type { EventEvidenceCard } from "@/api/securityMonitoringEvents";

const valueText = (value: unknown): string => {
  if (value === undefined || value === null || value === "")
    return "来源未提供";
  if (Array.isArray(value)) return value.map(valueText).join("、");
  return typeof value === "object"
    ? JSON.stringify(value, null, 2)
    : String(value);
};

const values = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [value];
const hasValue = (value: unknown) =>
  value !== undefined && value !== null && value !== "";
const isTimeField = (key: string) => /(?:_time|_at)$/.test(key);
const alertNameKeys = new Set(["name", "alert_name"]);
const alertIdKeys = new Set(["alert_id", "alert_ids"]);
const ipLabels: Record<string, string> = {
  src_ip: "源 IP",
  src_port: "源端口",
  dst_ip: "目的 IP",
  dst_port: "目的端口",
  x_forwarded_for: "X-Forwarded-For（请求头原值）",
};
const ipAddressKeys = new Set(["src_ip", "dst_ip", "x_forwarded_for"]);
const ipFieldKeys = new Set(Object.keys(ipLabels));
const ipValueText = (value: unknown): string =>
  Array.isArray(value)
    ? value.map(ipValueText).filter(Boolean).join("\n")
    : hasValue(value) && (typeof value !== "string" || value.trim())
      ? valueText(value)
      : "";

function cardDetails(
  card: EventEvidenceCard,
  formatTime: (value: unknown) => string,
) {
  const fields = card.fields || [];
  const names = fields
    .filter((field) => alertNameKeys.has(field.key))
    .flatMap((field) => values(field.value))
    .filter(hasValue)
    .map(valueText);
  const times = [
    card.time,
    ...fields
      .filter((field) => field.key === "alert_time")
      .flatMap((field) => values(field.value)),
  ]
    .filter(hasValue)
    .map(formatTime);
  const occurrenceTimes = [...new Set(times)];
  const hasIpAddress = fields.some(
    (field) => ipAddressKeys.has(field.key) && ipValueText(field.value),
  );
  const ipFields = hasIpAddress
    ? Object.keys(ipLabels).flatMap((key) =>
        fields
          .filter((field) => field.key === key && ipValueText(field.value))
          .map((field) => ({
            key,
            label: ipLabels[key],
            text: ipValueText(field.value),
          })),
      )
    : [];
  const otherFields = fields.flatMap((field) => {
    if (
      alertNameKeys.has(field.key) ||
      alertIdKeys.has(field.key) ||
      field.key === "alert_time" ||
      (hasIpAddress && ipFieldKeys.has(field.key))
    )
      return [];
    if (field.key !== "start_time" && field.key !== "end_time") return [field];
    const remaining = values(field.value).filter(
      (value) => !occurrenceTimes.includes(formatTime(value)),
    );
    return remaining.length ? [{ ...field, value: remaining }] : [];
  });
  return {
    name: [...new Set(names)].join("、"),
    time: occurrenceTimes.length
      ? occurrenceTimes.join("、")
      : formatTime(undefined),
    fields: otherFields,
    ipFields,
    alertId:
      card.alert_id ||
      fields.find((field) => alertIdKeys.has(field.key))?.value,
    records: card.records?.length
      ? card.records
      : [
          {
            evidence_id: card.evidence_id,
            source: card.source,
            query: card.query,
            raw: card.raw,
          },
        ],
  };
}

export function EvidenceCards({
  cards,
  formatTime,
  title = "关键证据",
  description = "",
  emptyMessage = "尚未取得与当前事件版本对应的具体行为证据。",
}: {
  cards: EventEvidenceCard[];
  formatTime: (value: unknown) => string;
  title?: string;
  description?: string;
  emptyMessage?: string;
}) {
  return (
    <section aria-label={title === "关键证据" ? "关键证据概述" : title}>
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <ShieldCheck size={16} className="text-emerald-600" />
        {title}
      </h3>
      {description && (
        <p className="mb-3 text-xs leading-6 text-slate-500">{description}</p>
      )}
      {cards.length ? (
        <ol aria-label="告警证据" className="space-y-2">
          {cards.map((card, index) => {
            const detail = cardDetails(card, formatTime);
            return (
              <li
                key={card.id || index}
                className="rounded-lg border border-sky-100 bg-white dark:border-sky-900 dark:bg-slate-950"
              >
                <details className="group p-3">
                  <summary className="flex cursor-pointer list-none items-start gap-3 [&::-webkit-details-marker]:hidden">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1 break-words text-sm leading-6 text-slate-900 dark:text-slate-100">
                      {card.summary}
                    </span>
                    <span className="mt-0.5 inline-flex shrink-0 items-center gap-1 text-xs font-medium text-sky-700 dark:text-sky-300">
                      详情
                      <ChevronDown
                        size={13}
                        className="transition-transform group-open:rotate-180"
                      />
                    </span>
                  </summary>
                  <div className="ml-8 mt-4 space-y-4 border-t border-sky-100 pt-3 dark:border-sky-900">
                    <dl className="grid gap-3 text-xs sm:grid-cols-2">
                      {detail.name && (
                        <div className="sm:col-span-2">
                          <dt className="text-slate-500">告警信息</dt>
                          <dd className="mt-1 break-words font-medium">
                            {detail.name}
                          </dd>
                        </div>
                      )}
                      <div>
                        <dt className="text-slate-500">发生时间</dt>
                        <dd className="mt-1 whitespace-pre-wrap break-words">
                          {detail.time}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-slate-500">来源</dt>
                        <dd className="mt-1 break-words font-medium">
                          {valueText(card.source)}
                        </dd>
                      </div>
                    </dl>
                    {detail.ipFields.length > 0 && (
                      <section
                        aria-label="IP信息"
                        className="rounded-lg border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900"
                      >
                        <h4 className="mb-3 text-xs font-semibold">IP信息</h4>
                        <dl className="grid gap-x-5 gap-y-3 text-xs sm:grid-cols-2">
                          {detail.ipFields.map((field, position) => (
                            <div
                              key={`${field.key}-${position}`}
                              className={
                                field.key === "x_forwarded_for"
                                  ? "sm:col-span-2"
                                  : ""
                              }
                            >
                              <dt className="text-slate-500">{field.label}</dt>
                              <dd className="mt-1">
                                <pre
                                  tabIndex={0}
                                  aria-label={`${field.label}值`}
                                  className="max-h-40 select-text overflow-auto whitespace-pre-wrap break-all font-mono leading-6"
                                >
                                  {field.text}
                                </pre>
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </section>
                    )}
                    {detail.fields.length > 0 && (
                      <dl className="grid gap-3 text-xs sm:grid-cols-2">
                        {detail.fields.map((field, position) => (
                          <div
                            key={`${field.key}-${position}`}
                            className={
                              field.key === "domain" ||
                              field.key === "command_line"
                                ? "sm:col-span-2"
                                : ""
                            }
                          >
                            <dt className="text-slate-500">{field.label}</dt>
                            <dd className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all leading-6">
                              {isTimeField(field.key)
                                ? values(field.value).map(formatTime).join("、")
                                : valueText(field.value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    {card.boundary && (
                      <p className="rounded-md bg-sky-50 px-3 py-2 text-xs leading-6 text-sky-800 dark:bg-sky-950 dark:text-sky-200">
                        {card.boundary}
                      </p>
                    )}
                    {card.partial && (
                      <p className="text-xs leading-6 text-amber-700">
                        该查询仅返回部分记录；未取得的内容不能据此排除风险。
                      </p>
                    )}
                    {(card.packets || []).map((packet, position) => (
                      <div key={`${packet.field}-${position}`}>
                        <h4 className="mb-2 text-xs font-semibold">
                          {packet.label}
                        </h4>
                        {packet.truncated && (
                          <p className="mb-2 text-xs text-amber-700">
                            此报文已截断。
                          </p>
                        )}
                        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3 text-xs leading-6 text-sky-100">
                          {packet.text || "（来源明确返回空正文）"}
                        </pre>
                      </div>
                    ))}
                    <details className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
                      <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-medium">
                        <FileSearch size={13} />
                        查询参数与脱敏原始记录
                      </summary>
                      {hasValue(detail.alertId) && (
                        <p className="mt-3 break-all text-xs leading-6">
                          告警编号：{valueText(detail.alertId)}
                        </p>
                      )}
                      <ol aria-label="查询溯源记录" className="mt-3 space-y-4">
                        {detail.records.map((record, position) => (
                          <li
                            key={`${record.evidence_id || "record"}-${position}`}
                          >
                            <h4 className="text-xs font-medium">
                              查询记录 {position + 1} ·{" "}
                              {record.source || card.source}
                            </h4>
                            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs leading-6">
                              {JSON.stringify(
                                {
                                  evidence_id: record.evidence_id,
                                  query: record.query,
                                  raw: record.raw,
                                },
                                null,
                                2,
                              )}
                            </pre>
                          </li>
                        ))}
                      </ol>
                    </details>
                  </div>
                </details>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-500 dark:bg-slate-900">
          {emptyMessage}
        </p>
      )}
    </section>
  );
}
