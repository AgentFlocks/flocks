import type { HTMLAttributes, MouseEvent, ReactNode } from 'react';
import { Pencil, Trash2 } from 'lucide-react';

/**
 * One card layout for plugin resources (agents, skills): icon, title, tags
 * under the title, enabled state in the top-right corner, a two-line
 * description (full text on hover) and the actions in a fixed order —
 * delete, group, (extra), edit — as icon buttons with tooltips.
 */
/** Columns follow the space the section actually has, never a fixed count. */
export const PLUGIN_CARD_GRID_CLASS = 'grid gap-3 grid-cols-[repeat(auto-fill,minmax(min(20rem,100%),1fr))]';

export interface PluginCardProps {
  icon: ReactNode;
  title: string;
  tags?: ReactNode;
  /** Enabled state (usually a toggle badge) shown in the top-right corner. */
  status?: ReactNode;
  description?: string;
  emptyDescription: string;
  /** Extra facts under the description (model, source, warnings). */
  meta?: ReactNode;
  layout?: 'cards' | 'list';
  selected?: boolean;
  muted?: boolean;
  onOpen: () => void;
  deleteLabel: string;
  /** Omit to hide; pass with `deleteDisabledReason` for read-only items. */
  onDelete?: () => void;
  deleteDisabledReason?: string;
  groupButton?: ReactNode;
  extraActions?: ReactNode;
  editLabel: string;
  dragProps?: HTMLAttributes<HTMLElement>;
  testId?: string;
}

/** Built-in / custom tag shown under a card title; one look for every plugin page. */
export function PluginSourceTag({ builtin, label }: { builtin: boolean; label: string }) {
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${builtin ? 'bg-blue-50 text-blue-600' : 'bg-teal-50 text-teal-600'}`}>
      {label}
    </span>
  );
}

const iconButtonClass = 'inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-transparent';

export default function PluginCard({
  icon, title, tags, status, description, emptyDescription, meta, layout = 'cards', selected = false, muted = false,
  onOpen, deleteLabel, onDelete, deleteDisabledReason, groupButton, extraActions, editLabel, dragProps, testId,
}: PluginCardProps) {
  const list = layout === 'list';
  const stop = (event: MouseEvent) => event.stopPropagation();
  const showDelete = onDelete !== undefined || deleteDisabledReason !== undefined;

  return (
    <article
      {...dragProps}
      data-testid={testId}
      onClick={onOpen}
      className={`group relative flex h-full min-w-0 cursor-pointer flex-col rounded-xl border bg-white transition-all duration-150 ${list ? 'lg:flex-row lg:items-center' : ''} ${
        selected ? 'border-slate-400 shadow-md ring-2 ring-slate-200' : 'border-gray-200 hover:border-gray-300 hover:shadow-md'
      } ${muted ? 'opacity-75' : ''}`}
    >
      <div className={`flex min-w-0 flex-1 flex-col gap-2 px-4 pb-2 pt-3 ${list ? 'lg:flex-row lg:items-center lg:gap-4 lg:py-3' : ''}`}>
        <div className={`flex min-w-0 items-start gap-2.5 ${list ? 'lg:w-64 lg:shrink-0' : ''}`}>
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500">
            {icon}
          </span>
          <div className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold leading-snug text-gray-900" title={title}>{title}</span>
            {tags && <div className="mt-1 flex flex-wrap items-center gap-1">{tags}</div>}
          </div>
          {status && !list && <div className="shrink-0" onClick={stop}>{status}</div>}
        </div>
        <p className={`line-clamp-2 text-xs leading-5 text-gray-500 ${list ? 'lg:flex-1' : ''}`} title={description || undefined}>
          {description || emptyDescription}
        </p>
        {meta && <div className={`min-w-0 text-[11px] text-gray-400 ${list ? 'lg:w-48 lg:shrink-0' : ''}`}>{meta}</div>}
      </div>
      <div
        className={`mt-auto flex items-center justify-end gap-1 border-t border-gray-100 px-3 py-2 ${list ? 'lg:mt-0 lg:shrink-0 lg:border-l lg:border-t-0' : ''}`}
        onClick={stop}
      >
        {status && list && <div className="mr-auto shrink-0 lg:mr-2">{status}</div>}
        {showDelete && (
          <button
            type="button"
            disabled={!onDelete}
            onClick={() => onDelete?.()}
            title={deleteDisabledReason || deleteLabel}
            aria-label={deleteLabel}
            className={`${iconButtonClass} hover:text-red-600 enabled:hover:bg-red-50`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
        {groupButton}
        {extraActions}
        <button type="button" onClick={onOpen} title={editLabel} aria-label={editLabel} className={iconButtonClass}>
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>
    </article>
  );
}
