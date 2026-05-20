import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Workflow as WorkflowIcon,
  Plus,
  RefreshCw,
  Sparkles,
  FolderOpen,
  Clock,
  ChevronRight,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useWorkflows } from '@/hooks/useWorkflow';
import { Workflow } from '@/api/workflow';

// ---------------------------------------------------------------------------
// Color helpers (mirrors Agent page)
// ---------------------------------------------------------------------------

const WORKFLOW_PALETTE = [
  '#3b82f6', // blue-500
  '#8b5cf6', // violet-500
  '#06b6d4', // cyan-500
  '#10b981', // emerald-500
  '#f59e0b', // amber-500
  '#ef4444', // red-500
  '#ec4899', // pink-500
  '#6366f1', // indigo-500
];

function resolveWorkflowColor(workflow: Workflow): string {
  let h = 0;
  const seed = workflow.id || workflow.name;
  for (let i = 0; i < seed.length; i++) {
    h = seed.charCodeAt(i) + ((h << 5) - h);
  }
  return WORKFLOW_PALETTE[Math.abs(h) % WORKFLOW_PALETTE.length];
}

function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function isBuiltin(workflow: Workflow): boolean {
  return workflow.source === 'project';
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SourceFilter = 'all' | 'builtin' | 'custom';
const PAGE_SIZE = 12;

// ---------------------------------------------------------------------------
// WorkflowPage
// ---------------------------------------------------------------------------

export default function WorkflowPage() {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const { workflows, loading, error, refetch } = useWorkflows();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');

  const builtinWorkflows = useMemo(() => workflows.filter(isBuiltin), [workflows]);
  const customWorkflows  = useMemo(() => workflows.filter(w => !isBuiltin(w)), [workflows]);

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      await Promise.all([
        refetch(),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 2000);
    } finally {
      setRefreshing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={() => refetch()}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  const filterChips: { key: SourceFilter; label: string; count: number }[] = [
    { key: 'all',     label: t('filter.all'),     count: workflows.length },
    { key: 'builtin', label: t('filter.builtin'), count: builtinWorkflows.length },
    { key: 'custom',  label: t('filter.custom'),  count: customWorkflows.length },
  ];

  const showBuiltin = sourceFilter !== 'custom' && builtinWorkflows.length > 0;
  const showCustom  = sourceFilter !== 'builtin' && customWorkflows.length > 0;
  const isEmpty     = !showBuiltin && !showCustom;

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<WorkflowIcon className="w-8 h-8" />}
        // Refresh / create actions intentionally moved to the toolbar below so
        // the page header stays uniform with Skill/Agent pages and the
        // segmented source filter shares a row with its primary actions.
      />

      {/* Toolbar */}
      <div className="px-4 py-2 border-b border-gray-100 flex items-center gap-3">
        {/* Source filter — same segmented-control style as Skill / Agent pages */}
        <div
          role="tablist"
          aria-label={t('filter.aria')}
          className="inline-flex items-center rounded-lg border border-gray-200 bg-white p-0.5 text-xs"
        >
          {filterChips.map((chip, idx) => {
            const active = chip.key === sourceFilter;
            return (
              <button
                key={chip.key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setSourceFilter(chip.key)}
                className={`px-2.5 py-1 rounded-md transition-colors whitespace-nowrap ${
                  active
                    ? 'bg-slate-700 text-white'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                } ${idx > 0 ? 'ml-0.5' : ''}`}
              >
                <span>{chip.label}</span>
                <span className={`ml-1.5 inline-block min-w-[1.25rem] px-1 rounded text-[10px] tabular-nums ${
                  active ? 'bg-white/15' : 'bg-gray-100 text-gray-500'
                }`}>
                  {chip.count}
                </span>
              </button>
            );
          })}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title={refreshDone ? t('common:button.refreshed') : t('common:button.refresh')}
            className={`p-1.5 rounded-lg border transition-all ${
              refreshDone
                ? 'border-green-200 text-green-600'
                : 'border-gray-200 text-gray-400 hover:bg-gray-50 hover:text-gray-600 disabled:opacity-50'
            }`}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => navigate('/workflows/new')}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
            {t('createWorkflow')}
          </button>
        </div>
      </div>

      {/* Content */}
      <div
        className="flex-1 overflow-y-auto px-4 py-4 space-y-6"
        style={{ scrollbarGutter: 'stable' }}
      >
        {isEmpty ? (
          <EmptyState
            icon={<WorkflowIcon className="w-16 h-16" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={() => navigate('/workflows/new')}
                className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Plus className="w-5 h-5" />
                {t('createWorkflow')}
              </button>
            }
          />
        ) : (
          <>
            {showCustom && (
              <WorkflowSection
                title={t('section.custom')}
                icon={<FolderOpen className="w-4 h-4" />}
                workflows={customWorkflows}
              />
            )}
            {showBuiltin && (
              <WorkflowSection
                title={t('section.builtin')}
                icon={<Sparkles className="w-4 h-4" />}
                workflows={builtinWorkflows}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkflowSection
// ---------------------------------------------------------------------------

function WorkflowSection({
  title,
  icon,
  workflows,
}: {
  title: string;
  icon: React.ReactNode;
  workflows: Workflow[];
}) {
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(workflows.length / PAGE_SIZE));

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  const displayed = workflows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    // Use a labelled <section> so the grouping is exposed as a landmark
    // region to assistive tech and to `getByRole('region', { name: title })`
    // in tests; visual styling is unchanged from the previous bare <div>.
    <section aria-label={title}>
      {/* Section header — same style as Agent page */}
      <div className="flex items-start gap-3 mb-4 pl-3 border-l-2 border-slate-300">
        <span className="text-slate-400 mt-0.5">{icon}</span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            <span className="text-[11px] font-medium px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 tabular-nums">
              {workflows.length}
            </span>
          </div>
        </div>
      </div>

      {/* Grid — min-height anchors layout to avoid jump when pagination hides rows */}
      <div style={{ minHeight: totalPages > 1 ? 540 : undefined }}>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {displayed.map(workflow => (
            <WorkflowCard key={workflow.id} workflow={workflow} />
          ))}
        </div>
      </div>

      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between text-xs text-gray-400 select-none">
          <span>
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(workflows.length, page * PAGE_SIZE)} / {workflows.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}
              className="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >‹</button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
              <button
                key={p}
                type="button"
                onClick={() => setPage(p)}
                className={`w-6 h-5 rounded text-[11px] font-medium transition-colors ${
                  p === page ? 'bg-slate-700 text-white' : 'hover:bg-gray-100 text-gray-500'
                }`}
              >{p}</button>
            ))}
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(p => p + 1)}
              className="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >›</button>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// WorkflowCard
// ---------------------------------------------------------------------------

function WorkflowCard({ workflow }: { workflow: Workflow }) {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const color = resolveWorkflowColor(workflow);
  const builtin = isBuiltin(workflow);

  const successRate =
    workflow.stats.callCount > 0
      ? ((workflow.stats.successCount / workflow.stats.callCount) * 100).toFixed(1)
      : '—';

  return (
    <div
      onClick={() => navigate(`/workflows/${workflow.id}`)}
      className="group relative bg-white rounded-xl border border-gray-200 flex flex-col
                 overflow-hidden cursor-pointer transition-all duration-150
                 hover:border-gray-300 hover:shadow-md"
    >
      {/* Top accent bar */}
      <div style={{ height: 3, backgroundColor: color }} />

      {/* Card body */}
      <div className="flex-1 px-4 pt-3 pb-2 flex flex-col gap-2 min-w-0">
        {/* Avatar + name row */}
        <div className="flex items-start gap-2.5">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
            style={{ backgroundColor: hexAlpha(color, 0.12) }}
          >
            <WorkflowIcon className="w-4 h-4" style={{ color }} />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-gray-900 truncate leading-snug">
              {workflow.name}
            </span>
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              {/* Source badge */}
              {builtin ? (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                 bg-blue-50 text-blue-600 border border-blue-200">
                  {t('badge.builtin')}
                </span>
              ) : (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                 bg-teal-50 text-teal-600 border border-teal-200">
                  {t('badge.custom')}
                </span>
              )}
              {/* Status badge */}
              <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-gray-200
                               bg-gray-50 text-gray-500 text-[10px] font-medium">
                {t(`status.${workflow.status}` as any) ?? workflow.status}
              </span>
              {/* Node count */}
              <span className="text-[10px] text-gray-400">
                {workflow.workflowJson.nodes.length} {t('stats.nodes')}
              </span>
            </div>
          </div>

          <ChevronRight className="w-4 h-4 text-gray-300 shrink-0 mt-1 group-hover:text-gray-500 transition-colors" />
        </div>

        {/* Description */}
        <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">
          {workflow.description || t('noDescription')}
        </p>
      </div>

      {/* Stats footer — kept from original, cleaned to white bg */}
      <div className="border-t border-gray-100 px-4 py-2.5 grid grid-cols-3 gap-2">
        <div>
          <div className="text-base font-bold text-gray-900 tabular-nums">
            {workflow.stats.callCount}
          </div>
          <div className="text-[10px] text-gray-500">{t('stats.calls')}</div>
        </div>
        <div>
          <div className="text-base font-bold tabular-nums"
               style={{ color: workflow.stats.callCount > 0 ? '#16a34a' : '#9ca3af' }}>
            {successRate}{workflow.stats.callCount > 0 ? '%' : ''}
          </div>
          <div className="text-[10px] text-gray-500">{t('stats.successRate')}</div>
        </div>
        <div>
          <div className="text-base font-bold text-gray-900 tabular-nums flex items-center gap-0.5">
            <Clock className="w-3 h-3 text-gray-400 shrink-0" />
            {workflow.stats.avgRuntime > 0 ? `${workflow.stats.avgRuntime.toFixed(1)}s` : '—'}
          </div>
          <div className="text-[10px] text-gray-500">{t('stats.avgRuntime')}</div>
        </div>
      </div>
    </div>
  );
}
