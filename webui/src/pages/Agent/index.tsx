import { useState, useEffect, useMemo } from 'react';
import { Bot, Plus, Cpu, RefreshCw, Pencil, Trash2, Shield, Zap, Loader2, Search } from 'lucide-react';
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

interface AgentPageProps {
  embedded?: boolean;
}

export default function AgentPage({ embedded = false }: AgentPageProps = {}) {
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
            className="px-4 py-2 bg-slate-800 text-white rounded-lg hover:bg-slate-900"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {!embedded && (
        <PageHeader
          title={t('pageTitle')}
          description={t('pageDescription')}
          icon={<Bot className="w-8 h-8" />}
        />
      )}

      {/* Toolbar — mirrors the Skill page toolbar style */}
      <div className="px-4 py-2 border-b border-gray-100 flex items-center gap-2">
        {embedded && primaryAgents[0] && (
          <PrimaryAgentToolbarItem
            agent={primaryAgents[0]}
            displayLang={i18n.language}
            onClick={() => setEditingAgent(primaryAgents[0])}
          />
        )}
        {!embedded && (
          <span className="text-xs text-gray-400 select-none">
            {t('totalCount', { total: primaryAgents.length + subAgents.length })}
          </span>
        )}
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
            onClick={() => setShowCreateSheet(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm"
          >
            <Plus className="w-4 h-4" />
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
            icon={<Bot className="w-16 h-16" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={() => setShowCreateSheet(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
              >
                <Plus className="w-5 h-5" />
                {t('createSubAgent')}
              </button>
            }
          />
        ) : (
          <>
            {!embedded && primaryAgents.length > 0 && (
              <AgentSection
                title={t('section.primary.title')}
                icon={<Shield className="w-4 h-4" />}
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
                icon={<Zap className="w-4 h-4" />}
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
          onSaved={() => { refetch(); setEditingAgent(null); }}
        />
      )}

      {showCreateSheet && (
        <AgentSheet
          onClose={async () => {
            setShowCreateSheet(false);
            try { await agentAPI.refresh(); } catch { /* best-effort */ }
            refetch();
          }}
          onSaved={async () => {
            setShowCreateSheet(false);
            try { await agentAPI.refresh(); } catch { /* best-effort */ }
            refetch();
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
    <div className="mt-3 flex items-center justify-between text-xs text-gray-400 select-none">
      <span>{t('pagination.info', { start, end, total })}</span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          ‹
        </button>
        {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPageChange(p)}
            className={`w-6 h-5 rounded text-[11px] font-medium transition-colors ${
              p === page
                ? 'bg-slate-700 text-white'
                : 'hover:bg-gray-100 text-gray-500'
            }`}
          >
            {p}
          </button>
        ))}
        <button
          type="button"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);

  // Per-source counts for the filter chips
  const builtinCount = useMemo(() => agents.filter(a => a.native).length, [agents]);
  const customCount  = useMemo(() => agents.filter(a => !a.native).length, [agents]);

  const filtered = useMemo(
    () => {
      const q = searchQuery.trim().toLowerCase();
      return agents.filter((a) => {
        if (showSourceFilter) {
          if (sourceFilter === 'builtin' && !a.native) return false;
          if (sourceFilter === 'custom' && a.native) return false;
        }
        if (!q) return true;
        const name = getAgentDisplayName(a, displayLang).toLowerCase();
        const desc = getAgentDisplayDescription(a, displayLang).toLowerCase();
        const model = a.model?.modelID?.toLowerCase() ?? '';
        return name.includes(q) || desc.includes(q) || model.includes(q);
      });
    },
    [agents, displayLang, searchQuery, showSourceFilter, sourceFilter],
  );

  const totalPages = paginate ? Math.max(1, Math.ceil(filtered.length / SUB_AGENT_PAGE_SIZE)) : 1;

  // Clamp page when filter shrinks total pages
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  // Reset to page 1 when filter changes
  useEffect(() => { setPage(1); }, [searchQuery, sourceFilter]);

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
      {showSourceFilter && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-slate-400">{icon}</span>
            <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-slate-500">
              {agents.length}
            </span>
          </div>
          <div className="inline-flex items-center rounded-lg border border-gray-200 bg-white p-0.5 text-xs" role="tablist" aria-label={t('filter.aria')}>
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
          <div className="relative ml-auto w-64">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={displayLang.toLowerCase().startsWith('zh') ? '搜索 Agent...' : 'Search agents...'}
              className="w-full rounded-lg border border-gray-200 bg-white py-1.5 pl-9 pr-3 text-sm outline-none focus:border-slate-400 focus:ring-1 focus:ring-slate-300"
            />
          </div>
        </div>
      )}

      {/* Grid area — min-height anchors the layout so filter switches don't
          collapse the section height and cause visual jumps. */}
      <div style={GRID_MIN_H ? { minHeight: GRID_MIN_H } : undefined}>
        {displayed.length === 0 ? (
          <p className="text-xs text-gray-400 py-4">
            {t(`filter.${sourceFilter}` as any)} — {t('emptyState.title')}
          </p>
        ) : !showSourceFilter ? (
          <div className="space-y-2">
            {displayed.map((agent) => (
              <PrimaryAgentRow
                key={agent.name}
                agent={agent}
                displayLang={displayLang}
                isSelected={selectedAgent?.name === agent.name}
                onClick={() => onSelect(agent)}
              />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
// Primary Agent Toolbar Entry
// ============================================================================

function PrimaryAgentToolbarItem({
  agent,
  displayLang,
  onClick,
}: Pick<AgentCardProps, 'agent' | 'displayLang' | 'onClick'>) {
  const { t } = useTranslation('agent');
  const displayName = getAgentDisplayName(agent, displayLang);
  const displayDesc = getAgentDisplayDescription(agent, displayLang);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex min-w-0 max-w-[720px] items-center gap-2 rounded-lg border border-gray-200 bg-slate-50/60 px-2.5 py-1.5 text-left transition-colors hover:border-gray-300 hover:bg-white"
      title={displayDesc || displayName}
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-white text-slate-500 ring-1 ring-gray-200">
        <Bot className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-xs font-semibold text-gray-900">{displayName}</span>
          <span className="rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-blue-600">
            {t('badge.native')}
          </span>
        </span>
        <span className="block truncate text-[11px] leading-4 text-gray-500">
          {displayDesc || t('common:empty.noDescription')}
        </span>
      </span>
      <span className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-gray-500 transition-colors group-hover:bg-white group-hover:text-slate-700">
        <Pencil className="h-3 w-3" />
        {t('badge.edit')}
      </span>
    </button>
  );
}

// ============================================================================
// Primary Agent Row
// ============================================================================

function PrimaryAgentRow({
  agent,
  displayLang,
  isSelected,
  onClick,
}: Pick<AgentCardProps, 'agent' | 'displayLang' | 'isSelected' | 'onClick'>) {
  const { t } = useTranslation('agent');
  const displayName = getAgentDisplayName(agent, displayLang);
  const displayDesc = getAgentDisplayDescription(agent, displayLang);

  return (
    <div
      className={`
        group flex cursor-pointer items-center gap-3 rounded-lg border bg-white px-3 py-2.5 transition-all
        ${isSelected
          ? 'border-slate-400 shadow-sm ring-2 ring-slate-100'
          : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/60'
        }
      `}
      onClick={onClick}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-500 ring-1 ring-slate-200">
        <Bot className="h-4 w-4" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-gray-900">{displayName}</span>
          <span className="inline-flex items-center rounded border border-blue-100 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">
            {t('badge.native')}
          </span>
          {agent.model && (
            <span className="inline-flex max-w-[220px] items-center gap-1 rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
              <Cpu className="h-3 w-3 shrink-0" />
              <span className="truncate">{agent.model.modelID}</span>
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-gray-500">
          {displayDesc || t('common:empty.noDescription')}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onClick(); }}
          title={t('badge.edit')}
          aria-label={t('badge.edit')}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-500 transition-colors hover:bg-white hover:text-slate-700"
        >
          <Pencil className="h-3 w-3" />
          {t('badge.edit')}
        </button>
      </div>
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
        group relative bg-white rounded-lg border flex flex-col overflow-hidden
        cursor-pointer transition-all duration-150
        ${isSelected
          ? 'border-slate-400 shadow-sm ring-2 ring-slate-100'
          : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/40'
        }
      `}
      onClick={onClick}
    >
      {/* Card body */}
      <div className="flex-1 px-3.5 pt-3 pb-3 flex flex-col gap-2 min-w-0">
        {/* Avatar + Name row */}
        <div className="flex items-start gap-2.5 min-w-0">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-500 ring-1 ring-slate-200">
            <Bot className="w-4 h-4" />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-gray-900 truncate leading-snug">
              {displayName}
            </span>
            {/* Badges row: source badge (always shown) + delegatable */}
            <div className="flex items-center gap-1 mt-0.5 flex-wrap">
              {/* Source badge: built-in vs custom — styled distinctly */}
              {agent.native
                ? (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                   bg-blue-50 text-blue-600 border border-blue-200">
                    {t('badge.native')}
                  </span>
                )
                : (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium
                                   bg-teal-50 text-teal-600 border border-teal-200">
                    {t('badge.custom')}
                  </span>
                )
              }
              {agent.delegatable && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-gray-200 bg-gray-50 text-gray-500 text-[10px] font-medium">
                  {t('badge.delegatable')}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Description */}
        <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">
          {displayDesc || t('common:empty.noDescription')}
        </p>

        {/* Model chip */}
        {agent.model && (
          <div className="self-start inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[10px] text-gray-500">
            <Cpu className="w-3 h-3 shrink-0" />
            <span className="truncate max-w-[120px]">
              {agent.model.modelID}
            </span>
          </div>
        )}
      </div>

      {/* Footer — delete / enable / edit */}
      <div
        className="border-t border-gray-100 px-3 py-1.5 flex items-center justify-between"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
        {!agent.native && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete(agent.name); }}
            title={t('badge.delete')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium
                       text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
          >
            <Trash2 className="w-3 h-3" />
            {t('badge.delete')}
          </button>
        )}
        </div>

        <div className="flex items-center gap-2">
          {showDelegatableToggle && (
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-medium text-gray-400">
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
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium text-gray-400
                       hover:text-slate-700 hover:bg-gray-50 transition-colors"
          >
            <Pencil className="w-3 h-3" />
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
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={onChange}
      disabled={loading}
      title={title}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent
        transition-colors duration-150 focus:outline-none disabled:cursor-wait
        ${enabled ? 'bg-slate-500' : 'bg-gray-200'}`}
    >
      {loading
        ? <Loader2 className="absolute inset-0 m-auto w-3 h-3 text-white animate-spin" />
        : (
          <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow
            transform transition-transform duration-150
            ${enabled ? 'translate-x-4' : 'translate-x-0'}`}
          />
        )
      }
    </button>
  );
}
