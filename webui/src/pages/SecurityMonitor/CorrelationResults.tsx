import { FileSearch } from "lucide-react";
import { correlationLine, type CorrelationGroup } from "./correlationDetails";

export function CorrelationResults({ groups }: { groups: CorrelationGroup[] }) {
  return (
    <section aria-label="联动调查" className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <FileSearch size={16} className="text-sky-600" />
        联动调查
      </h3>
      {!groups.length && (
        <p className="text-xs text-slate-500">尚无联动查询记录。</p>
      )}
      {groups.map((group) => (
        <section
          key={group.scope}
          aria-label={group.title}
          className="space-y-2"
        >
          {groups.length > 1 && (
            <h4 className="text-xs font-medium">{group.title}</h4>
          )}
          {!!group.coveredEvidenceIds?.length && (
            <p className="text-xs leading-6 text-slate-500">
              有价值的查询结果已列入关键证据。
            </p>
          )}
          {!!group.results.length && (
            <ul
              aria-label={`${group.title}记录`}
              className="list-disc space-y-1 pl-5 text-xs leading-6 text-slate-600 dark:text-slate-300"
            >
              {group.results.map((row, index) => (
                <li key={`${row.id}-${index}`} className="break-words">
                  {correlationLine(row)}
                </li>
              ))}
            </ul>
          )}
          {!group.results.length && !group.coveredEvidenceIds?.length && (
            <p className="text-xs text-slate-500">
              {group.scope === "confirmed"
                ? "此前确证时未保存联动查询记录。"
                : "尚无联动查询记录。"}
            </p>
          )}
        </section>
      ))}
    </section>
  );
}
