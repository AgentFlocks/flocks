import { useState, useMemo, useEffect, useId, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import {
  IconRouteSquare2 as WorkflowIcon,
  IconPlus,
  IconRefresh,
  IconSparkles,
  IconFolderOpen,
  IconClock,
  IconChevronRight,
  IconCircleCheck,
  IconCircleX,
  IconCircleMinus,
  IconLoader2,
  type TablerIcon,
} from '@tabler/icons-react';
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

const CAPABILITY_STATUS_STYLES: Record<WorkflowCapabilityState, { text: string; background: string; icon: TablerIcon }> = {
  unconfigured: { text: 'text-fg-muted', background: 'bg-subtle', icon: IconCircleMinus },
  starting: { text: 'text-info', background: 'bg-info-soft', icon: IconLoader2 },
  running: { text: 'text-success', background: 'bg-success-soft', icon: IconCircleCheck },
  stopped: { text: 'text-fg-muted', background: 'bg-subtle', icon: IconCircleMinus },
  error: { text: 'text-danger', background: 'bg-danger-soft', icon: IconCircleX },
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
          <p className="text-danger mb-4">{error}</p>
          <button
            onClick={() => refetch()}
            className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
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
      <PageHeader tone="ai"
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<WorkflowIcon aria-hidden="true" className="w-8 h-8" />}
        // Refresh / create actions intentionally moved to the toolbar below so
        // the page header stays uniform with Skill/Agent pages and the
        // segmented source filter shares a row with its primary actions.
      />

      {/* Toolbar */}
      {/* min-w-0 + overflow-x-auto on the filter: at phone widths the three chips
          would otherwise squeeze the actions column and wrap the create button
          character by character. */}
      <div className="px-4 py-2 border-b border-line flex flex-wrap items-center gap-3 min-w-0">
        {/* Source filter — same segmented-control style as Skill / Agent pages.
            flex-auto keeps the chips on one full-width row (actions wrap below
            them on phones); the scroll container only kicks in below ~300px. */}
        <div className="min-w-0 flex-auto overflow-x-auto">
        <div
          role="tablist"
          aria-label={t('filter.aria')}
          className="inline-flex items-center rounded-control border border-line bg-surface p-0.5 text-xs"
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
                className={`px-2.5 py-1 rounded-control transition-colors whitespace-nowrap ${
                  active
                    ? 'bg-selected text-fg'
                    : 'text-fg-muted hover:text-fg-secondary hover:bg-selected'
                } ${idx > 0 ? 'ml-0.5' : ''}`}
              >
                <span>{chip.label}</span>
                <span className={`ml-1.5 inline-block min-w-[1.25rem] px-1 rounded-tag text-xs tabular-nums ${
                  active ? 'bg-surface' : 'bg-subtle text-fg-muted'
                }`}>
                  {chip.count}
                </span>
              </button>
            );
          })}
        </div>
        </div>

        <div className="ml-auto flex items-center gap-2 shrink-0">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title={refreshDone ? t('common:button.refreshed') : t('common:button.refresh')}
            className={`ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border transition-all ${
              refreshDone
                ? 'border-success text-success'
                : 'border-line text-fg-muted hover:bg-hover hover:text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled'
            }`}
          >
            <IconRefresh aria-hidden="true" className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={openFreshCreate}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors text-sm whitespace-nowrap"
          >
            <IconPlus aria-hidden="true" className="w-4 h-4" />
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
            icon={<WorkflowIcon aria-hidden="true" className="w-8 h-8" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={openFreshCreate}
                className="inline-flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
              >
                <IconPlus aria-hidden="true" className="w-5 h-5" />
                {t('createWorkflow')}
              </button>
            }
          />
        ) : (
          <>
            {showCustom && (
              <WorkflowSection
                title={t('section.custom')}
                icon={<IconFolderOpen aria-hidden="true" className="w-4 h-4" />}
                workflows={customWorkflows}
              />
            )}
            {showBuiltin && (
              <WorkflowSection
                title={t('section.builtin')}
                icon={<IconSparkles aria-hidden="true" className="w-4 h-4" />}
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
      <div className="flex items-start gap-3 mb-4 pl-3 border-l-2 border-line-control">
        <span className="text-fg-muted mt-0.5">{icon}</span>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
            <span className="text-xs font-medium px-1.5 py-0.5 rounded-tag bg-subtle text-fg-muted tabular-nums">
              {workflows.length}
            </span>
          </div>
        </div>
      </div>

      {/* Grid — min-height anchors layout to avoid jump when pagination hides rows */}
      <div style={{ minHeight: totalPages > 1 ? 540 : undefined }}>
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {displayed.map(workflow => (
            <WorkflowCard key={workflow.id} workflow={workflow} />
          ))}
        </div>
      </div>

      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between text-xs text-fg-muted select-none">
          <span>
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(workflows.length, page * PAGE_SIZE)} / {workflows.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}
              className="px-2 py-0.5 rounded-tag border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed"
            >‹</button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
              <button
                key={p}
                type="button"
                onClick={() => setPage(p)}
                aria-current={p === page ? 'page' : undefined}
                className={`w-6 h-5 rounded-tag text-xs font-medium transition-colors ${
                  p === page ? 'bg-selected text-fg' : 'hover:bg-selected text-fg-muted'
                }`}
              >{p}</button>
            ))}
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage(p => p + 1)}
              className="px-2 py-0.5 rounded-tag border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed"
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
      className="group relative bg-surface rounded-card border border-line flex flex-col
 overflow-hidden cursor-pointer transition-all duration-150
 hover:border-line-control"
    >
      {/* Card body */}
      <div className="flex-1 px-4 pt-3 pb-2 flex flex-col gap-2 min-w-0">
        {/* Avatar + name row */}
        <div className="flex items-start gap-2.5">
          <div className="w-8 h-8 rounded-control flex items-center justify-center shrink-0 mt-0.5 bg-subtle">
            <WorkflowIcon aria-hidden="true" className="w-4 h-4 text-fg-muted" />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-fg truncate ">
              {displayName}
            </span>
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              {/* Source badge */}
              {builtin ? (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium
 bg-info-soft text-info">
                  {t('badge.builtin')}
                </span>
              ) : (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium
 bg-data-soft text-data">
                  {t('badge.custom')}
                </span>
              )}
              {/* Node count */}
              <span className="text-xs text-fg-muted">
                {workflow.nodeCount} {t('stats.nodes')}
              </span>
            </div>
          </div>

          <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-placeholder shrink-0 mt-1 group-hover:text-fg-muted transition-colors" />
        </div>

        {/* Description */}
        <p className="text-xs text-fg-muted line-clamp-2">
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
      <div className="border-t border-line px-4 py-2.5 grid grid-cols-3 gap-2">
        <div>
          <div className="text-base font-bold text-fg tabular-nums">
            {workflow.stats.callCount}
          </div>
          <div className="text-xs text-fg-muted">{t('stats.calls')}</div>
        </div>
        <div>
          <div className="text-base font-bold tabular-nums"
               style={{ color: workflow.stats.callCount > 0 ? 'var(--status-success)' : 'var(--text-muted)' }}>
            {successRate}{workflow.stats.callCount > 0 ? '%' : ''}
          </div>
          <div className="text-xs text-fg-muted">{t('stats.successRate')}</div>
        </div>
        <div>
          <div className="text-base font-bold text-fg tabular-nums flex items-center gap-0.5">
            <IconClock aria-hidden="true" className="w-4 h-4 text-fg-muted shrink-0" />
            {workflow.stats.avgRuntime > 0 ? `${workflow.stats.avgRuntime.toFixed(1)}s` : '—'}
          </div>
          <div className="text-xs text-fg-muted">{t('stats.avgRuntime')}</div>
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
  const Icon = styles.icon;
  return (
    <span
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className={`inline-flex shrink-0 items-center gap-1 rounded-tag px-1.5 py-0.5 whitespace-nowrap text-xs ${styles.background}`}
    >
      <Icon aria-hidden="true" className={`h-4 w-4 ${styles.text} ${state === 'starting' ? 'animate-spin' : ''}`} />
      <span className="font-medium text-fg-muted">{label}</span>
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
        className="ml-2 shrink-0 cursor-help text-xs font-medium text-fg-muted
 hover:text-fg-secondary focus-visible:text-fg-secondary"
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
 rounded-control border border-line bg-surface px-3 py-2 shadow-lg
 ${position.placement === 'top' ? '-translate-y-full' : ''}`}
          style={{ left: position.x, top: position.y, maxHeight: position.maxHeight }}
          onMouseEnter={cancelScheduledHide}
          onMouseLeave={scheduleHideTooltip}
        >
          <div className="mb-1.5 shrink-0 text-xs font-semibold text-fg-secondary">
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
              ? 'top-full border-t-line'
              : 'bottom-full border-b-line'}`}
          />
        </div>,
        document.body,
      )}
    </>
  );
}
