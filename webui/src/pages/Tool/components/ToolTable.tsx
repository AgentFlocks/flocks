import { useTranslation } from 'react-i18next';
import { IconTool } from '@tabler/icons-react';
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
    <div className="bg-surface rounded-control border border-line overflow-x-auto flex flex-col">
      {/* Header row (min-w keeps columns intact; wrapper scrolls on narrow viewports) */}
      <div
        className="grid min-w-[640px] items-center gap-3 px-4 py-2.5 bg-subtle border-b border-line text-xs font-medium text-fg-muted uppercase tracking-wide"
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
      <div className="divide-y divide-line">
        {tools.map((tool) => {
          const sb = SOURCE_BADGE[tool.source] ?? SOURCE_BADGE.custom;
          const sourceLabel = sb.labelKey ? t(sb.labelKey) : (sb.label ?? tool.source);
          const categoryLabel = t(CATEGORY_LABEL_KEY[tool.category] ?? 'category.custom');

          return (
            <div
              key={tool.name}
              className="grid min-w-[640px] items-center gap-3 px-4 py-3 hover:bg-hover cursor-pointer transition-colors"
              style={{ gridTemplateColumns: GRID_COLS }}
              onClick={() => onSelect(tool)}
            >
              {/* Status accent */}
              <div className={`w-1 h-8 rounded-pill ${tool.enabled ? 'bg-success' : 'bg-selected'}`} />

              {/* Icon */}
              <div className={`w-8 h-8 flex items-center justify-center rounded-control ${tool.enabled ? 'bg-subtle' : 'bg-subtle'}`}>
                <IconTool aria-hidden="true" className={`w-4 h-4 ${tool.enabled ? 'text-fg-secondary' : 'text-fg-muted'}`} />
              </div>

              {/* Name + description */}
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium text-fg font-mono truncate">{tool.name}</span>
                  {/* gray-500 keeps the category tag ≥4.5:1 on white (gray-400 was 2.5:1) */}
                  <span className="text-xs text-fg-muted shrink-0">{categoryLabel}</span>
                </div>
                <p className="text-xs text-fg-muted truncate mt-0.5">{tool.description}</p>
              </div>

              {/* Source column */}
              <div className="text-center">
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium ${sb.className}`}>
                  {sourceLabel}
                </span>
              </div>

              {/* Provider column */}
              <div className="text-xs text-fg-secondary truncate">
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
                  className="text-xs font-medium text-danger hover:text-danger transition-colors"
                >
                  {t('table.test')}
                </button>
                <span className="text-fg-placeholder">|</span>
                <button
                  onClick={() => onSelect(tool, 'info')}
                  className="text-xs font-medium text-fg-muted hover:text-fg-secondary transition-colors"
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
