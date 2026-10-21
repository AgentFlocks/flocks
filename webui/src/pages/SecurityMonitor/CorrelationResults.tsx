import { FileSearch } from "lucide-react";
import {
  correlationCount,
  correlationProduct,
  correlationStatusLabels,
  correlationWindow,
  type CorrelationGroup,
} from "./correlationDetails";

export function CorrelationResults({
  groups,
  formatTime,
}: {
  groups: CorrelationGroup[];
  formatTime: (value: unknown) => string;
}) {
  return (
    <section aria-label="联动核查" className="space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <FileSearch size={16} className="text-sky-600" />
        联动核查
      </h3>
      {!groups.length && (
        <p className="text-xs text-slate-500">尚未保存可展示的联动核查记录。</p>
      )}
      {groups.map((group) => (
        <section
          key={group.scope}
          aria-label={group.title}
          className="space-y-2"
        >
          <h4 className="text-xs font-medium text-slate-700 dark:text-slate-200">
            {group.title}
          </h4>
          <p className="text-xs leading-6 text-slate-500">
            {group.description}
          </p>
          {group.results.length ? (
            <ol aria-label={`${group.title}记录`} className="space-y-2">
              {group.results.map((row, index) => (
                <li
                  key={`${row.id}-${index}`}
                  className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="break-words text-sm font-medium">
                      {row.source || row.device || "来源设备未提供"}
                      <span className="ml-2 text-xs font-normal text-slate-500">
                        {correlationProduct(row)}
                      </span>
                    </p>
                    <span
                      className={`rounded px-2 py-1 text-xs ${row.status === "failed" || row.status === "unavailable" ? "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200" : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"}`}
                    >
                      {correlationStatusLabels[row.status] || "资料不足"}
                    </span>
                  </div>
                  {(row.summary || row.reason) && (
                    <p className="mt-2 break-words text-xs leading-6">
                      {row.summary || row.reason}
                    </p>
                  )}
                  {row.summary && row.reason && row.summary !== row.reason && (
                    <p className="mt-1 break-words text-xs leading-6 text-slate-500">
                      {row.reason}
                    </p>
                  )}
                  {row.error && (
                    <p className="mt-1 break-words text-xs leading-6 text-amber-700">
                      失败原因：{row.error}
                    </p>
                  )}
                  <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
                    <div>
                      <dt className="text-slate-500">查询主机 IP</dt>
                      <dd className="mt-1 break-all">{row.host || "未提供"}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">查询范围</dt>
                      <dd className="mt-1 break-words">
                        {correlationWindow(row, formatTime)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-slate-500">返回记录</dt>
                      <dd className="mt-1">
                        {correlationCount(row)}
                        {typeof row.total_count === "number" &&
                        Number.isInteger(row.total_count) &&
                        row.total_count >= 0
                          ? `；来源总数 ${row.total_count}`
                          : ""}
                      </dd>
                    </div>
                  </dl>
                  {row.partial && (
                    <p className="mt-2 text-xs leading-6 text-amber-700">
                      仅取得部分资料，不能按完整核查处理。
                    </p>
                  )}
                  {row.boundary && (
                    <p className="mt-2 break-words text-xs leading-6 text-slate-500">
                      {row.boundary}
                    </p>
                  )}
                  {(row.query != null || row.facts != null) && (
                    <details className="mt-2 rounded bg-slate-50 p-2 dark:bg-slate-900">
                      <summary className="cursor-pointer text-xs font-medium">
                        查询参数与脱敏返回
                      </summary>
                      <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs leading-5">
                        {JSON.stringify(
                          {
                            evidence_id: row.evidence_id,
                            query: row.query,
                            facts: row.facts,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-500 dark:bg-slate-900">
              {group.scope === "confirmed"
                ? "此前确证时未保存可展开的联动明细。"
                : "尚未保存可展示的联动核查记录。"}
            </p>
          )}
        </section>
      ))}
    </section>
  );
}
