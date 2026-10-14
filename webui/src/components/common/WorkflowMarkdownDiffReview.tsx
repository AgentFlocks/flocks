import { useMemo } from 'react';
import { Check, GitCompare, Undo2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { TextDiffHunk, TextDiffLine } from '@/utils/textDiff';

interface WorkflowMarkdownDiffReviewProps {
  lines: TextDiffLine[];
  hunks: TextDiffHunk[];
  added: number;
  removed: number;
  reviewingId: string | null;
  disabled: boolean;
  onAccept: () => void;
  onReject: () => void;
  onAcceptHunk: (hunk: TextDiffHunk) => void;
  onRejectHunk: (hunk: TextDiffHunk) => void;
}

export default function WorkflowMarkdownDiffReview({
  lines,
  hunks,
  added,
  removed,
  reviewingId,
  disabled,
  onAccept,
  onReject,
  onAcceptHunk,
  onRejectHunk,
}: WorkflowMarkdownDiffReviewProps) {
  const { t } = useTranslation('workflow');
  const hunkByStart = useMemo(() => {
    const lookup = new Map<number, TextDiffHunk>();
    hunks.forEach((hunk) => {
      lookup.set(hunk.changeStartLineIndex, hunk);
    });
    return lookup;
  }, [hunks]);

  const rowClass = (line: TextDiffLine) => {
    if (line.type === 'add') return 'bg-emerald-950/40 text-emerald-50';
    if (line.type === 'remove') return 'bg-red-950/45 text-red-50';
    return 'bg-slate-950 text-slate-200';
  };
  const gutterClass = (line: TextDiffLine) => {
    if (line.type === 'add') return 'bg-emerald-950/70 text-emerald-300';
    if (line.type === 'remove') return 'bg-red-950/70 text-red-300';
    return 'bg-slate-900/70 text-slate-500';
  };
  const marker = (line: TextDiffLine) => {
    if (line.type === 'add') return '+';
    if (line.type === 'remove') return '-';
    return ' ';
  };

  return (
    <div
      data-testid="workflow-md-diff-review"
      className="flex min-h-0 flex-1 flex-col bg-slate-950 text-slate-100"
    >
      <div className="flex flex-shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2 text-xs text-slate-300">
            <GitCompare className="h-3.5 w-3.5 flex-shrink-0 text-slate-400" />
            <span className="font-medium text-slate-100">{t('detail.editDocDiffTitle')}</span>
            <span className="text-slate-500">workflow.md</span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {t('detail.editDocDiffReviewDesc')}
          </p>
        </div>

        <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-2 text-[11px] font-medium">
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">
              +{added} {t('detail.editDocDiffAdded')}
            </span>
            <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-red-300">
              -{removed} {t('detail.editDocDiffRemoved')}
            </span>
          </div>
          <button
            type="button"
            onClick={onAccept}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500 px-2.5 py-1.5 text-xs font-semibold text-emerald-950 shadow-sm transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            {t('detail.editDocDiffAccept')}
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md border border-red-400/40 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-200 shadow-sm transition-colors hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Undo2 className="h-3.5 w-3.5" />
            {reviewingId === 'reject' ? t('detail.editDocDiffRejecting') : t('detail.editDocDiffReject')}
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-slate-950">
        <div className="min-w-[720px] font-mono text-sm leading-6">
          {lines.length > 0 ? lines.map((line, index) => {
            const hunk = hunkByStart.get(index);
            const hunkIndex = hunk ? hunks.findIndex((item) => item.id === hunk.id) : -1;
            return (
              <div key={`${line.type}-${line.oldLine ?? ''}-${line.newLine ?? ''}-${index}`}>
                {hunk && (
                  <div className="flex flex-wrap items-center justify-between gap-2 border-y border-slate-800 bg-slate-900/95 px-4 py-2">
                    <div className="flex min-w-0 items-center gap-2 text-xs text-slate-300">
                      <span className="font-semibold text-slate-100">
                        {t('detail.editDocDiffHunkTitle', { index: hunkIndex + 1 })}
                      </span>
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-medium text-emerald-300">
                        +{hunk.added}
                      </span>
                      <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[11px] font-medium text-red-300">
                        -{hunk.removed}
                      </span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onAcceptHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-emerald-500/15 px-2 py-1 text-[11px] font-semibold text-emerald-200 transition-colors hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Check className="h-3 w-3" />
                        {t('detail.editDocDiffAcceptHunk')}
                      </button>
                      <button
                        type="button"
                        onClick={() => onRejectHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-red-500/15 px-2 py-1 text-[11px] font-semibold text-red-200 transition-colors hover:bg-red-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Undo2 className="h-3 w-3" />
                        {reviewingId === `reject:${hunk.id}`
                          ? t('detail.editDocDiffRejecting')
                          : t('detail.editDocDiffRejectHunk')}
                      </button>
                    </div>
                  </div>
                )}
                <div
                  className={`grid grid-cols-[56px_56px_28px_minmax(0,1fr)] border-b border-slate-900/70 ${rowClass(line)}`}
                >
                  <div className={`select-none px-2 py-0.5 text-right ${gutterClass(line)}`}>
                    {line.oldLine ?? ''}
                  </div>
                  <div className={`select-none px-2 py-0.5 text-right ${gutterClass(line)}`}>
                    {line.newLine ?? ''}
                  </div>
                  <div className={`select-none px-2 py-0.5 text-center font-semibold ${gutterClass(line)}`}>
                    {marker(line)}
                  </div>
                  <pre className="min-w-0 overflow-visible whitespace-pre-wrap break-words px-4 py-0.5 font-mono">
                    {line.text || ' '}
                  </pre>
                </div>
              </div>
            );
          }) : (
            <div className="px-4 py-8 text-center text-sm text-slate-400">
              {t('detail.editDocDiffEmpty')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
