import { useMemo } from 'react';
import { IconCheck, IconGitCompare, IconArrowBackUp } from '@tabler/icons-react';
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
    if (line.type === 'add') return 'bg-success-soft text-success';
    if (line.type === 'remove') return 'bg-danger-soft text-danger';
    return 'bg-canvas text-fg';
  };
  const gutterClass = (line: TextDiffLine) => {
    if (line.type === 'add') return 'bg-success-soft text-success';
    if (line.type === 'remove') return 'bg-danger-soft text-danger';
    return 'bg-selected text-fg-muted';
  };
  const marker = (line: TextDiffLine) => {
    if (line.type === 'add') return '+';
    if (line.type === 'remove') return '-';
    return ' ';
  };

  return (
    <div
      data-testid="workflow-md-diff-review"
      className="flex min-h-0 flex-1 flex-col bg-canvas text-fg"
    >
      <div className="flex flex-shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line-control bg-selected px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2 text-xs text-fg">
            <IconGitCompare aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
            <span className="font-medium text-fg">{t('detail.editDocDiffTitle')}</span>
            <span className="text-fg-muted">workflow.md</span>
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            {t('detail.editDocDiffReviewDesc')}
          </p>
        </div>

        <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-2 text-xs font-medium">
            <span className="rounded bg-success-soft px-1.5 py-0.5 text-success">
              +{added} {t('detail.editDocDiffAdded')}
            </span>
            <span className="rounded bg-danger-soft px-1.5 py-0.5 text-danger">
              -{removed} {t('detail.editDocDiffRemoved')}
            </span>
          </div>
          <button
            type="button"
            onClick={onAccept}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 ui-btn ui-btn-primary disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
          >
            <IconCheck aria-hidden="true" className="h-4 w-4" />
            {t('detail.editDocDiffAccept')}
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md border border-danger bg-danger-soft px-2.5 py-1.5 text-xs font-semibold text-danger shadow-sm transition-colors hover:bg-danger-soft disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
          >
            <IconArrowBackUp aria-hidden="true" className="h-4 w-4" />
            {reviewingId === 'reject' ? t('detail.editDocDiffRejecting') : t('detail.editDocDiffReject')}
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-canvas">
        <div className="min-w-[720px] font-mono text-sm leading-6">
          {lines.length > 0 ? lines.map((line, index) => {
            const hunk = hunkByStart.get(index);
            const hunkIndex = hunk ? hunks.findIndex((item) => item.id === hunk.id) : -1;
            return (
              <div key={`${line.type}-${line.oldLine ?? ''}-${line.newLine ?? ''}-${index}`}>
                {hunk && (
                  <div className="flex flex-wrap items-center justify-between gap-2 border-y border-line-control bg-selected px-4 py-2">
                    <div className="flex min-w-0 items-center gap-2 text-xs text-fg">
                      <span className="font-semibold text-fg">
                        {t('detail.editDocDiffHunkTitle', { index: hunkIndex + 1 })}
                      </span>
                      <span className="rounded bg-success-soft px-1.5 py-0.5 text-xs font-medium text-success">
                        +{hunk.added}
                      </span>
                      <span className="rounded bg-danger-soft px-1.5 py-0.5 text-xs font-medium text-danger">
                        -{hunk.removed}
                      </span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onAcceptHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-success-soft px-2 py-1 text-xs font-semibold text-success transition-colors hover:bg-success-soft disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
                      >
                        <IconCheck aria-hidden="true" className="h-4 w-4" />
                        {t('detail.editDocDiffAcceptHunk')}
                      </button>
                      <button
                        type="button"
                        onClick={() => onRejectHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-danger-soft px-2 py-1 text-xs font-semibold text-danger transition-colors hover:bg-danger-soft disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
                      >
                        <IconArrowBackUp aria-hidden="true" className="h-4 w-4" />
                        {reviewingId === `reject:${hunk.id}`
                          ? t('detail.editDocDiffRejecting')
                          : t('detail.editDocDiffRejectHunk')}
                      </button>
                    </div>
                  </div>
                )}
                <div
                  className={`grid grid-cols-[56px_56px_28px_minmax(0,1fr)] border-b border-line-control ${rowClass(line)}`}
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
            <div className="px-4 py-8 text-center text-sm text-fg-muted">
              {t('detail.editDocDiffEmpty')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
