import { useState, useMemo, useEffect, useId, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
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
import {
  WorkflowCapabilityState,
  WorkflowSummary,
  WorkflowTriggerStatusSummary,
  WorkflowTriggerType,
} from '@/api/workflow';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

function isBuiltin(workflow: WorkflowSummary): boolean {
  return workflow.source === 'project';
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SourceFilter = 'all' | 'builtin' | 'custom';
const PAGE_SIZE = 12;
const MAX_VISIBLE_TRIGGER_STATUSES = 2;
const STATUS_TOOLTIP_WIDTH = 224;
const STATUS_TOOLTIP_MAX_HEIGHT = 240;
const STATUS_TOOLTIP_GAP = 8;

const CAPABILITY_STATUS_STYLES: Record<WorkflowCapabilityState, { text: string; dot: string }> = {
  unconfigured: { text: 'text-gray-400', dot: 'bg-gray-300' },
  starting: { text: 'text-gray-500', dot: 'bg-gray-400' },
  running: { text: 'text-green-600', dot: 'bg-green-500' },
  stopped: { text: 'text-red-600', dot: 'bg-red-500' },
  error: { text: 'text-red-600', dot: 'bg-red-500' },
};

const TRIGGER_TYPE_LABEL_KEYS = {
  manual: 'integration.triggerType.manual',
  schedule: 'integration.triggerType.schedule',
  webhook: 'integration.triggerType.webhook',
  syslog: 'integration.triggerType.syslog',
  kafka: 'integration.triggerType.kafka',
  internal_event: 'integration.triggerType.internal_event',
  custom_webhook: 'integration.triggerType.custom_webhook',
  custom_adapter: 'integration.triggerType.custom_adapter',
  plugin: 'integration.triggerType.plugin',
} as const satisfies Record<WorkflowTriggerType, string>;

const CAPABILITY_STATUS_LABEL_KEYS = {
  unconfigured: 'integration.state.disabled',
  starting: 'integration.state.disabled',
  running: 'integration.state.enabled',
  stopped: 'integration.state.disabled',
  error: 'integration.state.disabled',
} as const satisfies Record<WorkflowCapabilityState, string>;

const CAPABILITY_DETAIL_LABEL_KEYS = {
  unconfigured: 'integration.detailState.unconfigured',
  starting: 'integration.detailState.starting',
  stopped: 'integration.detailState.stopped',
  error: 'integration.detailState.error',
} as const;

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

  const openFreshCreate = () => {
    navigate('/workflows/new', {
      state: {
        freshCreate: true,
        ts: Date.now(),
      },
    });
  };

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
        <LoadingSpinner delayMs={180} />
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
            onClick={openFreshCreate}
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
                onClick={openFreshCreate}
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
  workflows: WorkflowSummary[];
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

function WorkflowCard({ workflow }: { workflow: WorkflowSummary }) {
  const { t, i18n } = useTranslation('workflow');
  const navigate = useNavigate();
  const builtin = isBuiltin(workflow);
  const displayName = getWorkflowDisplayName(workflow, i18n?.language);

  const successRate =
    workflow.stats.callCount > 0
      ? ((workflow.stats.successCount / workflow.stats.callCount) * 100).toFixed(1)
      : '—';
  const apiStatus = workflow.integrationStatus?.api ?? { configured: false, state: 'unconfigured' as const };
  const triggerStatus = workflow.integrationStatus?.trigger ?? {
    configured: false,
    state: 'unconfigured' as const,
    count: 0,
    items: [],
  };
  const triggerItems = triggerStatus.items ?? [];
  const visibleTriggerItems = triggerItems.slice(0, MAX_VISIBLE_TRIGGER_STATUSES);
  const hiddenTriggerItems = triggerItems.slice(MAX_VISIBLE_TRIGGER_STATUSES);

  return (
    <div
      onClick={() => navigate(`/workflows/${workflow.id}`)}
      className="group relative bg-white rounded-xl border border-gray-200 flex flex-col
                 overflow-hidden cursor-pointer transition-all duration-150
                 hover:border-gray-300 hover:shadow-md"
    >
      {/* Card body */}
      <div className="flex-1 px-4 pt-3 pb-2 flex flex-col gap-2 min-w-0">
        {/* Avatar + name row */}
        <div className="flex items-start gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5 bg-gray-100">
            <WorkflowIcon className="w-4 h-4 text-gray-500" />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-gray-900 truncate leading-snug">
              {displayName}
            </span>
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              {/* Source badge */}
              {builtin ? (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                 bg-blue-50 text-blue-600">
                  {t('badge.builtin')}
                </span>
              ) : (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                 bg-teal-50 text-teal-600">
                  {t('badge.custom')}
                </span>
              )}
              {/* Node count */}
              <span className="text-[10px] text-gray-400">
                {workflow.nodeCount} {t('stats.nodes')}
              </span>
            </div>
          </div>

          <ChevronRight className="w-4 h-4 text-gray-300 shrink-0 mt-1 group-hover:text-gray-500 transition-colors" />
        </div>

        {/* Description */}
        <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">
          {workflow.description || t('noDescription')}
        </p>

        <div
          className="mt-auto pt-1 h-5 flex items-center min-w-0"
          aria-label={t('integration.aria')}
        >
          <div className="min-w-0 flex items-center gap-x-3 overflow-hidden">
            <CapabilityStatus
              label={t('integration.api')}
              state={apiStatus.state}
              statusLabel={t(CAPABILITY_STATUS_LABEL_KEYS[apiStatus.state])}
            />
            {triggerItems.length > 0 ? visibleTriggerItems.map(trigger => (
              <CapabilityStatus
                key={trigger.id}
                label={t(TRIGGER_TYPE_LABEL_KEYS[trigger.type])}
                state={trigger.state}
                statusLabel={t(CAPABILITY_STATUS_LABEL_KEYS[trigger.state])}
              />
            )) : (
              <CapabilityStatus
                label={t('integration.trigger')}
                state={triggerStatus.state}
                statusLabel={t(CAPABILITY_STATUS_LABEL_KEYS[triggerStatus.state])}
              />
            )}
          </div>
          {hiddenTriggerItems.length > 0 && (
            <OverflowStatusList items={hiddenTriggerItems} />
          )}
        </div>
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

function CapabilityStatus({
  label,
  state,
  statusLabel,
}: {
  label: string;
  state: WorkflowCapabilityState;
  statusLabel: string;
}) {
  const { t } = useTranslation('workflow');
  const detailLabel = state === 'running'
    ? null
    : t(CAPABILITY_DETAIL_LABEL_KEYS[state]);
  const accessibleLabel = detailLabel
    ? `${label}：${statusLabel}（${detailLabel}）`
    : `${label}：${statusLabel}`;
  const styles = CAPABILITY_STATUS_STYLES[state];
  return (
    <span
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px] leading-4"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${styles.dot}`} />
      <span className="font-medium text-gray-500">{label}</span>
      <span className={styles.text}>{statusLabel}</span>
    </span>
  );
}

function OverflowStatusList({ items }: { items: WorkflowTriggerStatusSummary[] }) {
  const { t } = useTranslation('workflow');
  const tooltipId = useId();
  const hideTimeoutRef = useRef<number | null>(null);
  const [position, setPosition] = useState<{
    x: number;
    y: number;
    placement: 'top' | 'bottom';
    maxHeight: number;
  } | null>(null);

  const cancelScheduledHide = useCallback(() => {
    if (hideTimeoutRef.current !== null) {
      window.clearTimeout(hideTimeoutRef.current);
      hideTimeoutRef.current = null;
    }
  }, []);

  const showTooltip = useCallback((target: HTMLElement) => {
    cancelScheduledHide();
    const rect = target.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const estimatedHeight = Math.min(STATUS_TOOLTIP_MAX_HEIGHT, 36 + items.length * 24);
    const availableAbove = Math.max(0, rect.top - STATUS_TOOLTIP_GAP * 2);
    const availableBelow = Math.max(0, viewportHeight - rect.bottom - STATUS_TOOLTIP_GAP * 2);
    const placement = availableAbove >= estimatedHeight || availableAbove >= availableBelow
      ? 'top'
      : 'bottom';
    const availableHeight = placement === 'top' ? availableAbove : availableBelow;
    const x = Math.min(
      Math.max(rect.right, STATUS_TOOLTIP_WIDTH + STATUS_TOOLTIP_GAP),
      viewportWidth - STATUS_TOOLTIP_GAP,
    );
    setPosition({
      x,
      y: placement === 'top'
        ? rect.top - STATUS_TOOLTIP_GAP
        : rect.bottom + STATUS_TOOLTIP_GAP,
      placement,
      maxHeight: Math.max(48, Math.min(STATUS_TOOLTIP_MAX_HEIGHT, availableHeight)),
    });
  }, [cancelScheduledHide, items.length]);

  const hideTooltip = useCallback(() => {
    cancelScheduledHide();
    setPosition(null);
  }, [cancelScheduledHide]);

  const scheduleHideTooltip = useCallback(() => {
    cancelScheduledHide();
    hideTimeoutRef.current = window.setTimeout(hideTooltip, 100);
  }, [cancelScheduledHide, hideTooltip]);

  useEffect(() => {
    if (!position) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hideTooltip();
    };
    document.addEventListener('pointerdown', hideTooltip);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', hideTooltip);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [hideTooltip, position]);

  useEffect(() => () => cancelScheduledHide(), [cancelScheduledHide]);

  return (
    <>
      <button
        type="button"
        aria-label={`${t('integration.moreStatuses')}：${items.length}`}
        aria-expanded={position !== null}
        aria-describedby={position ? tooltipId : undefined}
        className="ml-2 shrink-0 cursor-help text-[11px] leading-4 font-medium text-gray-400
                   hover:text-gray-600 focus-visible:outline-none focus-visible:text-gray-600"
        onMouseEnter={(event) => showTooltip(event.currentTarget)}
        onMouseLeave={scheduleHideTooltip}
        onFocus={(event) => showTooltip(event.currentTarget)}
        onBlur={hideTooltip}
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          showTooltip(event.currentTarget);
        }}
      >
        +{items.length}
      </button>
      {position && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className={`pointer-events-auto fixed z-[1000] flex w-56 max-w-[calc(100vw-1rem)] -translate-x-full flex-col
                      rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-lg
                      ${position.placement === 'top' ? '-translate-y-full' : ''}`}
          style={{ left: position.x, top: position.y, maxHeight: position.maxHeight }}
          onMouseEnter={cancelScheduledHide}
          onMouseLeave={scheduleHideTooltip}
        >
          <div className="mb-1.5 shrink-0 text-[11px] font-semibold text-gray-700">
            {t('integration.moreStatuses')}
          </div>
          <div className="min-h-0 space-y-1.5 overflow-y-auto">
            {items.map(item => (
              <div key={item.id} className="flex items-center min-w-0">
                <CapabilityStatus
                  label={t(TRIGGER_TYPE_LABEL_KEYS[item.type])}
                  state={item.state}
                  statusLabel={t(CAPABILITY_STATUS_LABEL_KEYS[item.state])}
                />
              </div>
            ))}
          </div>
          <div
            className={`absolute right-3 border-4 border-transparent
                        ${position.placement === 'top'
              ? 'top-full border-t-gray-200'
              : 'bottom-full border-b-gray-200'}`}
          />
        </div>,
        document.body,
      )}
    </>
  );
}
