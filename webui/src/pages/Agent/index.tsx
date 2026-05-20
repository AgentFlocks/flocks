import { useState, useEffect, useMemo } from 'react';
import { Bot, Plus, Cpu, RefreshCw, Pencil, Trash2, Shield, Zap, ArrowDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useAgents } from '@/hooks/useAgents';
import { agentAPI, Agent } from '@/api/agent';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import AgentSheet from './AgentSheet';

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

const AGENT_PALETTE = [
  '#3b82f6', // blue-500
  '#8b5cf6', // violet-500
  '#06b6d4', // cyan-500
  '#10b981', // emerald-500
  '#f59e0b', // amber-500
  '#ef4444', // red-500
  '#ec4899', // pink-500
  '#6366f1', // indigo-500
];

function resolveAgentColor(agent: Agent): string {
  if (agent.color) return agent.color;
  let h = 0;
  for (let i = 0; i < agent.name.length; i++) {
    h = agent.name.charCodeAt(i) + ((h << 5) - h);
  }
  return AGENT_PALETTE[Math.abs(h) % AGENT_PALETTE.length];
}

/** hex → rgba string at `alpha` (0–1). Works for 3-char and 6-char hex. */
function hexAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function formatAgentName(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// ============================================================================
// Main Page Component
// ============================================================================

export default function AgentPage() {
  const { t, i18n } = useTranslation('agent');
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [showCreateSheet, setShowCreateSheet] = useState(false);

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
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Bot className="w-8 h-8" />}
      />

      {/* Toolbar — mirrors the Skill page toolbar style */}
      <div className="px-4 py-2 border-b border-gray-100 flex items-center gap-2">
        <span className="text-xs text-gray-400 select-none">
          {t('totalCount', { total: primaryAgents.length + subAgents.length })}
        </span>
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
            {primaryAgents.length > 0 && (
              <PrimaryAgentSection
                title={t('section.primary.title')}
                subtitle={t('section.primary.subtitle')}
                icon={<Shield className="w-4 h-4" />}
                agents={primaryAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
              />
            )}
            {primaryAgents.length > 0 && subAgents.length > 0 && (
              <p className="flex items-center gap-2 text-xs text-gray-400 -mt-2">
                <ArrowDown className="h-3.5 w-3.5 shrink-0 text-gray-300" aria-hidden />
                {t('section.relationHint')}
              </p>
            )}
            {subAgents.length > 0 && (
              <AgentSection
                title={t('section.sub.title')}
                subtitle={t('section.sub.subtitle')}
                icon={<Zap className="w-4 h-4" />}
                agents={subAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
                onDelete={handleDelete}
                showSourceFilter
                paginate
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
// Primary Agent Section (horizontal profile rows)
// ---------------------------------------------------------------------------

interface PrimaryAgentSectionProps {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  agents: Agent[];
  displayLang: string;
  selectedAgent: Agent | null;
  onSelect: (agent: Agent) => void;
}

function PrimaryAgentSection({
  title,
  subtitle,
  icon,
  agents,
  displayLang,
  selectedAgent,
  onSelect,
}: PrimaryAgentSectionProps) {
  return (
    <div>
      <div className="flex items-start gap-3 pl-3 border-l-2 border-slate-400">
        <span className="text-slate-500 mt-0.5">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            <span className="text-[11px] font-medium px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 tabular-nums">
              {agents.length}
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-3">
        {agents.map((agent) => (
          <PrimaryAgentRow
            key={agent.name}
            agent={agent}
            displayLang={displayLang}
            isSelected={selectedAgent?.name === agent.name}
            onClick={() => onSelect(agent)}
          />
        ))}
      </div>
    </div>
  );
}

interface PrimaryAgentRowProps {
  agent: Agent;
  displayLang: string;
  isSelected: boolean;
  onClick: () => void;
}

function PrimaryAgentRow({ agent, displayLang, isSelected, onClick }: PrimaryAgentRowProps) {
  const { t } = useTranslation('agent');
  const displayDesc = getAgentDisplayDescription(agent, displayLang);
  const color = resolveAgentColor(agent);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
      className={`
        group flex w-full cursor-pointer overflow-hidden rounded-xl border bg-white
        transition-all duration-150
        ${isSelected
          ? 'border-slate-400 shadow-md ring-2 ring-slate-200'
          : 'border-gray-200 hover:border-gray-300 hover:shadow-md'
        }
      `}
    >
      <div className="w-1 shrink-0" style={{ backgroundColor: color }} />

      <div className="flex min-w-0 flex-1 items-center gap-4 px-4 py-3.5 sm:px-5">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl"
          style={{ backgroundColor: hexAlpha(color, 0.12) }}
        >
          <Bot className="h-5 w-5" style={{ color }} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-gray-900">
              {formatAgentName(agent.name)}
            </span>
            <span className="inline-flex items-center rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
              {t('badge.primary')}
            </span>
            {agent.native ? (
              <span className="inline-flex items-center rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">
                {t('badge.native')}
              </span>
            ) : (
              <span className="inline-flex items-center rounded border border-teal-200 bg-teal-50 px-1.5 py-0.5 text-[10px] font-medium text-teal-600">
                {t('badge.custom')}
              </span>
            )}
          </div>
          <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-gray-500">
            {displayDesc || t('common:empty.noDescription')}
          </p>
          {agent.model && (
            <div
              className="mt-2 inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-[10px] text-gray-500"
              style={{ backgroundColor: hexAlpha(color, 0.08), border: `1px solid ${hexAlpha(color, 0.2)}` }}
            >
              <Cpu className="h-3 w-3 shrink-0" style={{ color }} />
              <span className="truncate">{agent.model.modelID}</span>
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-white hover:border-gray-300 group-hover:border-gray-300"
        >
          <Pencil className="h-4 w-4" />
          {t('badge.edit')}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-Agent Section (grid)
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
      <div className="flex items-start gap-3 pl-3 border-l-2 border-slate-300">
        <span className="text-slate-400 mt-0.5">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
            <span className="text-[11px] font-medium px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 tabular-nums">
              {agents.length}
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>
        </div>
      </div>

      {/* Source filter — segmented control, same style as Skill page */}
      {showSourceFilter && (
        <div className="mt-2.5 mb-3" role="tablist" aria-label={t('filter.aria')}>
          <div className="inline-flex items-center rounded-lg border border-gray-200 bg-white p-0.5 text-xs">
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
        </div>
      )}

      {/* Grid area — min-height anchors the layout so filter switches don't
          collapse the section height and cause visual jumps. */}
      <div style={GRID_MIN_H ? { minHeight: GRID_MIN_H } : undefined}>
        {!showSourceFilter && <div className="mb-3" />}
        {displayed.length === 0 ? (
          <p className="text-xs text-gray-400 py-4">
            {t(`filter.${sourceFilter}` as any)} — {t('emptyState.title')}
          </p>
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
}

function AgentCard({ agent, displayLang, isSelected, onClick, onDelete }: AgentCardProps) {
  const { t } = useTranslation('agent');
  const displayDesc = getAgentDisplayDescription(agent, displayLang);
  const color = resolveAgentColor(agent);

  return (
    <div
      className={`
        group relative bg-white rounded-xl border flex flex-col overflow-hidden
        cursor-pointer transition-all duration-150
        ${isSelected
          ? 'border-slate-400 shadow-md ring-2 ring-slate-200'
          : 'border-gray-200 hover:border-gray-300 hover:shadow-md'
        }
      `}
      onClick={onClick}
    >
      {/* Top accent bar — 3 px strip, full-width, same radius as card */}
      <div style={{ height: 3, backgroundColor: color }} />

      {/* Card body */}
      <div className="flex-1 px-4 pt-3 pb-3 flex flex-col gap-2 min-w-0">
        {/* Avatar + Name row */}
        <div className="flex items-start gap-2.5 min-w-0">
          {/* Colored avatar */}
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
            style={{ backgroundColor: hexAlpha(color, 0.12) }}
          >
            <Bot className="w-4 h-4" style={{ color }} />
          </div>

          <div className="min-w-0 flex-1">
            <span className="block text-sm font-semibold text-gray-900 truncate leading-snug">
              {formatAgentName(agent.name)}
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
          <div
            className="self-start inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] text-gray-500"
            style={{ backgroundColor: hexAlpha(color, 0.08), border: `1px solid ${hexAlpha(color, 0.2)}` }}
          >
            <Cpu className="w-3 h-3 shrink-0" style={{ color }} />
            <span className="truncate max-w-[120px]">
              {agent.model.modelID}
            </span>
          </div>
        )}
      </div>

      {/* Footer — edit + delete buttons */}
      <div
        className="border-t border-gray-100 px-3 py-1.5 flex items-center justify-between"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Delete — disabled for built-in agents */}
        {agent.native ? (
          <button
            type="button"
            disabled
            title={t('badge.nativeDeleteDisabled')}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium
                       text-gray-300 cursor-not-allowed select-none"
          >
            <Trash2 className="w-3 h-3" />
            {t('badge.delete')}
          </button>
        ) : (
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
  );
}
