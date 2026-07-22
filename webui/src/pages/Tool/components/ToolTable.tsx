import { useTranslation } from 'react-i18next';
import { Wrench } from 'lucide-react';
import type { Tool } from '@/api/tool';
import type { SortState, ColumnFilters } from '../types';
import { SOURCE_BADGE, CATEGORY_LABEL_KEY } from '../constants';
import SortFilterHeader from './SortFilterHeader';
import Pagination from './Pagination';
import { EnabledBadge } from './badges';

interface ToolTableProps {
  tools: Tool[];
  sort: SortState;
  filters: ColumnFilters;
  filterOptions: Record<string, string[]>;
  currentPage: number;
  totalPages: number;
  totalCount: number;
  pageSize: number;
  onSort: (f: SortState['field']) => void;
  onToggleFilter: (f: keyof ColumnFilters, v: string) => void;
  onClearFilter: (f: keyof ColumnFilters) => void;
  onPageChange: (page: number) => void;
  onSelect: (tool: Tool, initialSection?: 'info' | 'test') => void;
}

/**
 * Column grid (consistent across header and rows to keep everything aligned):
 *   accent(4px) | icon(32px) | name+desc(1fr) | source(80px) | provider(120px) | status(80px) | actions(140px)
 */
const GRID_COLS = '4px 32px minmax(0, 1fr) 80px 120px 80px 140px';

export default function ToolTable({
  tools,
  sort,
  filters,
  filterOptions,
  currentPage,
  totalPages,
  totalCount,
  pageSize,
  onSort,
  onToggleFilter,
  onClearFilter,
  onPageChange,
  onSelect,
}: ToolTableProps) {
  const { t } = useTranslation('tool');

  const getSourceLabel = (v: string) => {
    const sb = SOURCE_BADGE[v] ?? SOURCE_BADGE.custom;
    return sb.labelKey ? t(sb.labelKey) : (sb.label ?? v);
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
      {/* Header row */}
      <div
        className="grid items-center gap-3 px-4 py-2.5 bg-gray-50 border-b border-gray-200 text-[11px] font-medium text-gray-500 uppercase tracking-wide"
        style={{ gridTemplateColumns: GRID_COLS }}
      >
        <div />
        <div />
        <div>{t('table.toolName')}</div>
        <div className="text-center">
          <SortFilterHeader
            label={t('table.source')}
            field="source"
            sort={sort}
            filterValues={filterOptions.source}
            activeFilters={filters.source}
            onSort={onSort}
            onToggleFilter={onToggleFilter}
            onClearFilter={onClearFilter}
            renderLabel={getSourceLabel}
            asDiv
          />
        </div>
        <div>
          <SortFilterHeader
            label={t('table.provider')}
            field="source_name"
            sort={sort}
            filterValues={filterOptions.source_name}
            activeFilters={filters.source_name}
            onSort={onSort}
            onToggleFilter={onToggleFilter}
            onClearFilter={onClearFilter}
            asDiv
          />
        </div>
        <div className="text-center">
          <SortFilterHeader
            label={t('table.status')}
            field="enabled"
            sort={sort}
            filterValues={filterOptions.enabled}
            activeFilters={filters.enabled}
            onSort={onSort}
            onToggleFilter={onToggleFilter}
            onClearFilter={onClearFilter}
            renderLabel={(v) => (v === 'true' ? t('table.enabledLabel') : t('table.disabledLabel'))}
            asDiv
          />
        </div>
        <div className="text-right">{t('table.actions')}</div>
      </div>

      {/* Rows */}
      <div className="divide-y divide-gray-100">
        {tools.map((tool) => {
          const sb = SOURCE_BADGE[tool.source] ?? SOURCE_BADGE.custom;
          const sourceLabel = sb.labelKey ? t(sb.labelKey) : (sb.label ?? tool.source);
          const categoryLabel = t(CATEGORY_LABEL_KEY[tool.category] ?? 'category.custom');

          return (
            <div
              key={tool.name}
              className="grid items-center gap-3 px-4 py-3 hover:bg-gray-50 cursor-pointer transition-colors"
              style={{ gridTemplateColumns: GRID_COLS }}
              onClick={() => onSelect(tool)}
            >
              {/* Status accent */}
              <div className={`w-1 h-8 rounded-full ${tool.enabled ? 'bg-green-500' : 'bg-gray-300'}`} />

              {/* Icon */}
              <div className={`w-8 h-8 flex items-center justify-center rounded-lg ${tool.enabled ? 'bg-gray-100' : 'bg-gray-50'}`}>
                <Wrench className={`w-4 h-4 ${tool.enabled ? 'text-gray-700' : 'text-gray-400'}`} />
              </div>

              {/* Name + description */}
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium text-gray-900 font-mono truncate">{tool.name}</span>
                  <span className="text-[10px] text-gray-400 shrink-0">{categoryLabel}</span>
                </div>
                <p className="text-xs text-gray-500 truncate mt-0.5">{tool.description}</p>
              </div>

              {/* Source column */}
              <div className="text-center">
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${sb.className}`}>
                  {sourceLabel}
                </span>
              </div>

              {/* Provider column */}
              <div className="text-xs text-gray-600 truncate">
                {tool.source_name || 'Flocks'}
              </div>

              {/* Status column */}
              <div className="text-center">
                <EnabledBadge enabled={tool.enabled} />
              </div>

              {/* Actions column */}
              <div className="flex items-center justify-end gap-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={() => onSelect(tool, 'test')}
                  className="text-xs font-medium text-red-600 hover:text-red-800 transition-colors"
                >
                  {t('table.test')}
                </button>
                <span className="text-gray-200">|</span>
                <button
                  onClick={() => onSelect(tool, 'info')}
                  className="text-xs font-medium text-gray-500 hover:text-gray-700 transition-colors"
                >
                  {t('table.detail')}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalCount={totalCount}
        pageSize={pageSize}
        onPageChange={onPageChange}
      />
    </div>
  );
}
