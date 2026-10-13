import { ChevronLeft, ChevronRight } from "lucide-react";

export const MONITOR_PAGE_SIZE = 10;
export default function Pagination({
  label,
  page,
  total,
  onChange,
}: {
  label: string;
  page: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / MONITOR_PAGE_SIZE));
  const active = Math.min(page, pages);
  const button =
    "inline-flex items-center gap-1 rounded-lg border border-sky-100 px-2 py-1.5 text-xs text-sky-800 disabled:opacity-40 dark:border-sky-900 dark:text-sky-200";
  return (
    <nav
      aria-label={`${label}分页`}
      className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-700"
    >
      <span>
        共 {total} 条 · 每页 {MONITOR_PAGE_SIZE} 条
      </span>
      <div className="flex items-center gap-2">
        <button
          aria-label={`${label}上一页`}
          className={button}
          disabled={active <= 1}
          onClick={() => onChange(active - 1)}
        >
          <ChevronLeft size={14} />
          上一页
        </button>
        <span aria-live="polite">
          第 {active} / {pages} 页
        </span>
        <button
          aria-label={`${label}下一页`}
          className={button}
          disabled={active >= pages}
          onClick={() => onChange(active + 1)}
        >
          下一页
          <ChevronRight size={14} />
        </button>
      </div>
    </nav>
  );
}
