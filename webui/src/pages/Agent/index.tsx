import { useState, useEffect, useMemo } from 'react';
import { IconRobot, IconPlus, IconCpu, IconRefresh, IconPencil, IconTrash, IconShield, IconBolt, IconLoader2 } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useAgents } from '@/hooks/useAgents';
import { agentAPI, Agent } from '@/api/agent';
import { getAgentDisplayDescription, getAgentDisplayName } from '@/utils/agentDisplay';
import AgentSheet from './AgentSheet';

// ============================================================================
// Main Page Component
// ============================================================================

export default function AgentPage() {
  const { t, i18n } = useTranslation('agent');
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [showCreateSheet, setShowCreateSheet] = useState(false);
  const [togglingAgents, setTogglingAgents] = useState<Record<string, boolean>>({});

  const { agents, loading, error, refetch } = useAgents();
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      await Promise.all([
        agentAPI.refresh().then(() => refetch()),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 2000);
    } catch {
      // best-effort
    } finally {
      setRefreshing(false);
    }
  };

  const primaryAgents = agents.filter((a) => a.mode === 'primary');
  const subAgents = agents.filter(
    (a) => a.mode !== 'primary' && !(a.tags ?? []).includes('system')
  );

  const handleDelete = async (name: string) => {
    if (!confirm(t('confirmDelete', { name }))) return;
    try {
      await agentAPI.delete(name);
      if (editingAgent?.name === name) setEditingAgent(null);
      refetch();
    } catch (err: any) {
      alert(`${t('deleteFailed')}: ${err.message}`);
    }
  };

  const handleToggleDelegatable = async (agent: Agent, delegatable: boolean) => {
    if (togglingAgents[agent.name]) return;
    setTogglingAgents((prev) => ({ ...prev, [agent.name]: true }));
    try {
      const response = await agentAPI.setDelegatable(agent.name, delegatable);
      if (editingAgent?.name === agent.name) {
        setEditingAgent(response.data);
      }
      await refetch(false);
    } catch (err: any) {
      alert(t('error.updateFailed', { detail: err.response?.data?.detail ?? err.message }));
    } finally {
      setTogglingAgents((prev) => ({ ...prev, [agent.name]: false }));
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
            className="px-4 py-2 bg-subtle text-fg rounded-control hover:bg-hover"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader tone="ai"
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconRobot aria-hidden="true" className="w-8 h-8" />}
      />

      {/* Toolbar — mirrors the Skill page toolbar style */}
      <div className="px-4 py-2 border-b border-line flex items-center gap-2">
        <span className="text-xs text-fg-muted select-none">
          {t('totalCount', { total: primaryAgents.length + subAgents.length })}
        </span>
        <div className="ml-auto flex items-center gap-2">
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
            onClick={() => setShowCreateSheet(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors text-sm"
          >
            <IconPlus aria-hidden="true" className="w-4 h-4" />
            {t('createSubAgent')}
          </button>
        </div>
      </div>

      {/* scrollbar-gutter: stable reserves the scrollbar track width even when the
          scrollbar is absent, preventing the layout shift that occurs when filters
          toggle between many results (scrollbar visible) and few results (no bar). */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6" style={{ scrollbarGutter: 'stable' }}>
        {agents.length === 0 ? (
          <EmptyState
            icon={<IconRobot aria-hidden="true" className="w-8 h-8" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={() => setShowCreateSheet(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
              >
                <IconPlus aria-hidden="true" className="w-5 h-5" />
                {t('createSubAgent')}
              </button>
            }
          />
        ) : (
          <>
            {primaryAgents.length > 0 && (
              <AgentSection
                title={t('section.primary.title')}
                subtitle={t('section.primary.subtitle')}
                icon={<IconShield aria-hidden="true" className="w-4 h-4" />}
                agents={primaryAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
                onDelete={handleDelete}
                togglingAgents={togglingAgents}
                onToggleDelegatable={handleToggleDelegatable}
              />
            )}
            {subAgents.length > 0 && (
              <AgentSection
                title={t('section.sub.title')}
                subtitle={t('section.sub.subtitle')}
                icon={<IconBolt aria-hidden="true" className="w-4 h-4" />}
                agents={subAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
                onDelete={handleDelete}
                showSourceFilter
                paginate
                togglingAgents={togglingAgents}
                onToggleDelegatable={handleToggleDelegatable}
              />
            )}
          </>
        )}
      </div>

      {editingAgent && (
        <AgentSheet
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onSaved={() => { refetch(false); setEditingAgent(null); }}
        />
      )}

      {showCreateSheet && (
        <AgentSheet
          onClose={async () => {
            setShowCreateSheet(false);
            try { await agentAPI.refresh(); } catch { /* best-effort */ }
            refetch(false);
          }}
          onSaved={async () => {
            setShowCreateSheet(false);
            try { await agentAPI.refresh(); } catch { /* best-effort */ }
            refetch(false);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// Agent Section
// ============================================================================

// Sub-agent grid page size: 12 fills 3×4 (xl) / 4×3 (lg) / 6×2 cleanly.
const SUB_AGENT_PAGE_SIZE = 12;

type SourceFilter = 'all' | 'builtin' | 'custom';

// ---------------------------------------------------------------------------
// PaginationBar
// ---------------------------------------------------------------------------

function PaginationBar({
  total,
  page,
  totalPages,
  pageSize,
  onPageChange,
}: {
  total: number;
  page: number;
  totalPages: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}) {
  const { t } = useTranslation('agent');
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-fg-muted select-none">
      <span>{t('pagination.info', { start, end, total })}</span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="px-2 py-0.5 rounded-tag border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
        >
          ‹
        </button>
        {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPageChange(p)}
            aria-current={p === page ? 'page' : undefined}
            className={`w-6 h-5 rounded-tag text-xs font-medium transition-colors ${
              p === page
                ? 'bg-selected text-fg'
                : 'hover:bg-selected text-fg-muted'
            }`}
          >
            {p}
          </button>
        ))}
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className="px-2 py-0.5 rounded-tag border border-line hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
        >
          ›
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentSection
// ---------------------------------------------------------------------------

interface AgentSectionProps {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  agents: Agent[];
  displayLang: string;
  selectedAgent: Agent | null;
  onSelect: (agent: Agent) => void;
  onDelete: (name: string) => void;
  togglingAgents: Record<string, boolean>;
  onToggleDelegatable: (agent: Agent, delegatable: boolean) => void;
  showSourceFilter?: boolean;
  paginate?: boolean;
}

function AgentSection({
  title,
  subtitle,
  icon,
  agents,
  displayLang,
  selectedAgent,
  onSelect,
  onDelete,
  togglingAgents,
  onToggleDelegatable,
  showSourceFilter = false,
  paginate = false,
}: AgentSectionProps) {
  const { t } = useTranslation('agent');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [page, setPage] = useState(1);

  // Per-source counts for the filter chips
  const builtinCount = useMemo(() => agents.filter(a => a.native).length, [agents]);
  const customCount  = useMemo(() => agents.filter(a => !a.native).length, [agents]);

  const filtered = useMemo(
    () => showSourceFilter
      ? agents.filter((a) => {
          if (sourceFilter === 'builtin') return a.native;
          if (sourceFilter === 'custom') return !a.native;
          return true;
        })
      : agents,
    [agents, showSourceFilter, sourceFilter],
  );

  const totalPages = paginate ? Math.max(1, Math.ceil(filtered.length / SUB_AGENT_PAGE_SIZE)) : 1;

  // Clamp page when filter shrinks total pages
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  // Reset to page 1 when filter changes
  useEffect(() => { setPage(1); }, [sourceFilter]);

  const displayed = paginate
    ? filtered.slice((page - 1) * SUB_AGENT_PAGE_SIZE, page * SUB_AGENT_PAGE_SIZE)
    : filtered;

  // Filter chip definitions — same pattern as Skill page FilterChips
  const filterChips: { key: SourceFilter; label: string; count: number }[] = [
    { key: 'all',     label: t('filter.all'),     count: agents.length },
    { key: 'builtin', label: t('filter.builtin'), count: builtinCount },
    { key: 'custom',  label: t('filter.custom'),  count: customCount },
  ];

  // Grid min-height: keeps the area stable when filters reduce item count.
  // Based on SUB_AGENT_PAGE_SIZE=12 in 4-col XL layout (3 rows × ~172px + 2 gaps×12px).
  const GRID_MIN_H = paginate ? 540 : undefined;

  return (
    <div>
      {/* Section header: left accent stripe */}
      <div className="flex items-start gap-3 pl-3 border-l-2 border-line-control">
        <span className="text-fg-muted mt-0.5">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
            <span className="text-xs font-medium px-1.5 py-0.5 rounded-tag bg-subtle text-fg-muted tabular-nums">
              {agents.length}
            </span>
          </div>
          <p className="text-xs text-fg-muted mt-0.5">{subtitle}</p>
        </div>
      </div>

      {/* Source filter — segmented control, same style as Skill page */}
      {showSourceFilter && (
        <div className="mt-2.5 mb-3" role="tablist" aria-label={t('filter.aria')}>
          <div className="inline-flex items-center rounded-control border border-line bg-surface p-0.5 text-xs">
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
      )}

      {/* Grid area — min-height anchors the layout so filter switches don't
          collapse the section height and cause visual jumps. */}
      <div style={GRID_MIN_H ? { minHeight: GRID_MIN_H } : undefined}>
        {!showSourceFilter && <div className="mb-3" />}
        {displayed.length === 0 ? (
          <p className="text-xs text-fg-muted py-4">
            {t(`filter.${sourceFilter}` as any)} — {t('emptyState.title')}
          </p>
        ) : (
          <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {displayed.map((agent) => (
              <AgentCard
                key={agent.name}
                agent={agent}
                displayLang={displayLang}
                isSelected={selectedAgent?.name === agent.name}
                onClick={() => onSelect(agent)}
                onDelete={onDelete}
                toggling={!!togglingAgents[agent.name]}
                onToggleDelegatable={onToggleDelegatable}
              />
            ))}
          </div>
        )}
      </div>

      {paginate && totalPages > 1 && (
        <PaginationBar
          total={filtered.length}
          page={page}
          totalPages={totalPages}
          pageSize={SUB_AGENT_PAGE_SIZE}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}

// ============================================================================
// Agent Card
// ============================================================================

interface AgentCardProps {
  agent: Agent;
  displayLang: string;
  isSelected: boolean;
  onClick: () => void;
  onDelete: (name: string) => void;
  toggling: boolean;
  onToggleDelegatable: (agent: Agent, delegatable: boolean) => void;
}

function AgentCard({
  agent,
  displayLang,
  isSelected,
  onClick,
  onDelete,
  toggling,
  onToggleDelegatable,
}: AgentCardProps) {
  const { t } = useTranslation('agent');
  const displayName = getAgentDisplayName(agent, displayLang);
  const displayDesc = getAgentDisplayDescription(agent, displayLang);
  const showDelegatableToggle = agent.mode === 'subagent';

  return (
    <div
      className={`
 group relative bg-surface rounded-card border flex flex-col overflow-hidden
 cursor-pointer transition-all duration-150
 ${isSelected
          ? 'border-line-control ring-2 ring-focus'
          : 'border-line hover:border-line-control'
        }
 `}
      onClick={onClick}
    >
      {/* Card body */}
      <div className="flex-1 px-4 pt-3 pb-2 flex flex-col gap-2 min-w-0">
        {/* Avatar + Name row */}
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-control flex items-center justify-center shrink-0 mt-0.5 bg-subtle">
            <IconRobot aria-hidden="true" className="w-4 h-4 text-fg-muted" />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-fg truncate ">
              {displayName}
            </span>
            {/* Badges row: source badge (always shown) + delegatable */}
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              {/* Source badge: built-in vs custom — styled distinctly */}
              {agent.native
                ? (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium
 bg-info-soft text-info">
                    {t('badge.native')}
                  </span>
                )
                : (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium
 bg-data-soft text-data">
                    {t('badge.custom')}
                  </span>
                )
              }
              {agent.delegatable && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded-tag bg-subtle text-fg-muted text-xs font-medium">
                  {t('badge.delegatable')}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Description */}
        <p className="text-xs text-fg-muted line-clamp-2">
          {displayDesc || t('common:empty.noDescription')}
        </p>

        {/* Model chip */}
        {agent.model && (
          <div className="self-start inline-flex items-center gap-1 text-xs text-fg-muted">
            <IconCpu aria-hidden="true" className="w-4 h-4 shrink-0" />
            <span className="truncate max-w-[120px]">
              {agent.model.modelID}
            </span>
          </div>
        )}
      </div>

      {/* Footer — delete / enable / edit */}
      <div
        className="border-t border-line px-4 py-2 flex items-center justify-between"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Delete — disabled for built-in agents */}
        {agent.native ? (
          <button
            type="button"
            disabled
            title={t('badge.nativeDeleteDisabled')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-control text-xs font-medium
 text-fg-disabled cursor-not-allowed select-none"
          >
            <IconTrash aria-hidden="true" className="w-4 h-4" />
            {t('badge.delete')}
          </button>
        ) : (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete(agent.name); }}
            title={t('badge.delete')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-control text-xs font-medium
 text-fg-muted hover:text-danger hover:bg-danger-soft transition-colors"
          >
            <IconTrash aria-hidden="true" className="w-4 h-4" />
            {t('badge.delete')}
          </button>
        )}

        <div className="flex items-center gap-2">
          {showDelegatableToggle && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium text-fg-secondary">
                {t('form.enabled')}
              </span>
              <ToggleSwitch
                enabled={!!agent.delegatable}
                loading={toggling}
                title={agent.delegatable ? t('form.enabledTip') : t('form.disabledTip')}
                onChange={(e) => {
                  e.stopPropagation();
                  onToggleDelegatable(agent, !agent.delegatable);
                }}
              />
            </div>
          )}

          <button
            type="button"
            onClick={onClick}
            title={t('badge.edit')}
            aria-label={t('badge.edit')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-control text-xs font-medium text-fg-secondary
 hover:text-fg-secondary hover:bg-hover transition-colors"
          >
            <IconPencil aria-hidden="true" className="w-4 h-4" />
            {t('badge.edit')}
          </button>
        </div>
      </div>
    </div>
  );
}

function ToggleSwitch({ enabled, loading, title, onChange }: {
  enabled: boolean;
  loading: boolean;
  title?: string;
  onChange: (e: React.MouseEvent) => void;
}) {
  const { t } = useTranslation('common');
  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" role="switch" aria-checked={enabled} aria-label={title || t('status.enabled')}
        onClick={onChange} disabled={loading} title={title} className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={enabled} aria-disabled={loading} />
</button>
      <span className={loading ? 'text-xs text-fg-disabled' : 'text-xs text-fg-secondary'}>
        {t(loading ? 'status.loading' : enabled ? 'status.enabled' : 'status.disabled')}
      </span>
    </span>
  );
}
