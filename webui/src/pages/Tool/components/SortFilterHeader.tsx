import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowUp, ArrowDown, Filter } from 'lucide-react';
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
          className={`flex items-center gap-0.5 hover:text-gray-900 transition-colors ${isActive ? 'text-red-600' : ''}`}
        >
          {label}
          {isActive && (sort.dir === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)}
        </button>
        <button
          onClick={() => setOpen((v) => !v)}
          className={`p-0.5 rounded hover:bg-gray-200 transition-colors ${hasFilter ? 'text-red-600' : 'text-gray-400'}`}
        >
          <Filter className="w-3 h-3" />
        </button>
      </div>

      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-white rounded-lg shadow-lg border border-gray-200 py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClearFilter(field); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
            >
              {t('button.clearFilter')}
            </button>
          )}
          {filterValues.map((v) => {
            const checked = activeFilters.has(v);
            const displayLabel = renderLabel ? renderLabel(v) : v;
            return (
              <label key={v} className="flex items-center px-3 py-1.5 hover:bg-gray-50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleFilter(field, v)}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-red-600 focus:ring-red-500 mr-2"
                />
                <span className="text-xs text-gray-700">{displayLabel}</span>
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
      className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider relative whitespace-nowrap"
      ref={ref as React.RefObject<HTMLTableCellElement>}
    >
      {inner}
    </th>
  );
}
