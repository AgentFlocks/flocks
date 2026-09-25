import { pageActionLabel } from '../../pageActionLabel';
import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconArrowUp, IconArrowDown, IconFilter } from '@tabler/icons-react';
import type { SortState, SortField, ColumnFilters } from '../types';

interface SortFilterHeaderProps {
  label: string;
  field: SortField;
  sort: SortState;
  filterValues: string[];
  activeFilters: Set<string>;
  onSort: (f: SortField) => void;
  onToggleFilter: (f: keyof ColumnFilters, v: string) => void;
  onClearFilter: (f: keyof ColumnFilters) => void;
  renderLabel?: (v: string) => string;
  /** When true, renders as <div> instead of <th> (for use outside <table>) */
  asDiv?: boolean;
}

export default function SortFilterHeader({
  label,
  field,
  sort,
  filterValues,
  activeFilters,
  onSort,
  onToggleFilter,
  onClearFilter,
  renderLabel,
  asDiv = false,
}: SortFilterHeaderProps) {
  const { t } = useTranslation('tool');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const isActive = sort.field === field;
  const hasFilter = activeFilters.size > 0;

  const inner = (
    <>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onSort(field)}
          className={`flex items-center gap-0.5 hover:text-fg transition-colors ${isActive ? 'text-danger' : ''}`}
        >
          {label}
          {isActive && (sort.dir === 'asc' ? <IconArrowUp aria-hidden="true" className="w-4 h-4" /> : <IconArrowDown aria-hidden="true" className="w-4 h-4" />)}
        </button>
        <button aria-label={pageActionLabel('filter')}
          onClick={() => setOpen((v) => !v)}
          className={`ui-btn-icon inline-flex items-center justify-center p-0.5 rounded-control hover:bg-hover transition-colors ${hasFilter ? 'text-danger' : 'text-fg-muted'}`}
        >
          <IconFilter aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>

      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-surface rounded-control shadow-lg border border-line py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClearFilter(field); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-danger hover:bg-danger-soft"
            >
              {t('button.clearFilter')}
            </button>
          )}
          {filterValues.map((v) => {
            const checked = activeFilters.has(v);
            const displayLabel = renderLabel ? renderLabel(v) : v;
            return (
              <label key={v} className="flex items-center px-3 py-1.5 hover:bg-hover cursor-pointer">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleFilter(field, v)}
                  className="w-3.5 h-3.5 rounded-tag border-line-control text-danger mr-2"
                />
                <span className="text-xs text-fg-secondary">{displayLabel}</span>
              </label>
            );
          })}
        </div>
      )}
    </>
  );

  if (asDiv) {
    return (
      <div
        className="relative whitespace-nowrap inline-flex"
        ref={ref as React.RefObject<HTMLDivElement>}
      >
        {inner}
      </div>
    );
  }

  return (
    <th
      className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider relative whitespace-nowrap"
      ref={ref as React.RefObject<HTMLTableCellElement>}
    >
      {inner}
    </th>
  );
}
