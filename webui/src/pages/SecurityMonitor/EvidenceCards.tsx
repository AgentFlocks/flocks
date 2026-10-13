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

export function EvidenceCards({
  cards,
  formatTime,
}: {
  cards: EventEvidenceCard[];
  formatTime: (value: unknown) => string;
}) {
  return (
    <section aria-label="关键证据概述">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <ShieldCheck size={16} className="text-emerald-600" />
        关键证据
      </h3>
      <p className="mb-3 text-xs leading-6 text-slate-500">
        每条先说明查到了什么及其作用，展开详情可核对来源、查询和原始记录。
      </p>
      {cards.length ? (
        <ol className="space-y-2">
          {cards.map((card, index) => (
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
                    <div>
                      <dt className="text-slate-500">查询来源</dt>
                      <dd className="mt-1 break-words font-medium">
                        {card.source}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">行为发生时间</dt>
                      <dd className="mt-1">{formatTime(card.time)}</dd>
                    </div>
                    {(card.fields || []).map((field, position) => (
                      <div
                        key={`${field.key}-${position}`}
                        className={
                          field.key === "domain" || field.key === "command_line"
                            ? "sm:col-span-2"
                            : ""
                        }
                      >
                        <dt className="text-slate-500">{field.label}</dt>
                        <dd className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all leading-6">
                          {field.key.endsWith("time")
                            ? formatTime(field.value)
                            : valueText(field.value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
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
                    <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs leading-6">
                      {JSON.stringify(
                        {
                          evidence_id: card.evidence_id,
                          query: card.query,
                          raw: card.raw,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                </div>
              </details>
            </li>
          ))}
        </ol>
      ) : (
        <p className="rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-500 dark:bg-slate-900">
          尚未取得与当前事件版本对应的具体行为证据，查询限制与原始记录保留在技术明细。
        </p>
      )}
    </section>
  );
}
