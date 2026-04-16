import { useState } from 'react';
import { Bot, Plus, Trash2, Cpu, Shield, Zap, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useAgents } from '@/hooks/useAgents';
import { agentAPI, Agent } from '@/api/agent';
import { getAgentDisplayDescription } from '@/utils/agentDisplay';
import AgentSheet from './AgentSheet';

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
  // Hide subagents tagged as "system" (internal/infra agents not relevant to end users).
  // All other subagents (security agents, custom agents, plugin agents) are shown.
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
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title={refreshDone ? t('common:button.refreshed') : t('common:button.refresh')}
              className={`p-2 border rounded-lg transition-all ${
                refreshDone
                  ? 'border-green-300 text-green-600 bg-green-50'
                  : 'border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50'
              }`}
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setShowCreateSheet(true)}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-600 to-slate-700 text-white rounded-lg hover:from-purple-700 hover:to-slate-800 transition-all shadow-sm"
            >
              <Plus className="w-4 h-4" />
              {t('createSubAgent')}
            </button>
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto space-y-8">
        {agents.length === 0 ? (
          <EmptyState
            icon={<Bot className="w-16 h-16" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={() => setShowCreateSheet(true)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-600 to-slate-700 text-white rounded-lg hover:from-purple-700 hover:to-slate-800 transition-all shadow-sm"
              >
                <Plus className="w-5 h-5" />
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
                theme="primary"
                agents={primaryAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
                onDelete={handleDelete}
              />
            )}
            {subAgents.length > 0 && (
              <AgentSection
                title={t('section.sub.title')}
                subtitle={t('section.sub.subtitle')}
                theme="subagent"
                agents={subAgents}
                displayLang={i18n.language}
                selectedAgent={editingAgent}
                onSelect={setEditingAgent}
                onDelete={handleDelete}
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
            // Refresh on any close (X / backdrop / cancel) so that agents
            // created by Rex in the AI-edit tab appear without an explicit
            // "Done" click on the form tab.
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

interface AgentSectionProps {
  title: string;
  subtitle: string;
  theme: 'primary' | 'subagent';
  agents: Agent[];
  displayLang: string;
  selectedAgent: Agent | null;
  onSelect: (agent: Agent) => void;
  onDelete: (name: string) => void;
}

function AgentSection({
  title,
  subtitle,
  theme,
  agents,
  displayLang,
  selectedAgent,
  onSelect,
  onDelete,
}: AgentSectionProps) {
  const isPrimary = theme === 'primary';
  const headerBg = isPrimary
    ? 'bg-gradient-to-r from-slate-50 to-sky-50/80 border-slate-200'
    : 'bg-gradient-to-r from-purple-50 to-violet-50 border-purple-200';
  const titleColor = isPrimary ? 'text-slate-800' : 'text-purple-800';
  const subtitleColor = isPrimary ? 'text-slate-600' : 'text-purple-600';
  const countBg = isPrimary ? 'bg-slate-200 text-slate-800' : 'bg-purple-100 text-purple-700';

  return (
    <div>
      <div className={`rounded-xl border px-5 py-4 mb-4 ${headerBg}`}>
        <div className="flex items-center gap-3">
          {isPrimary ? (
            <Shield className={`w-5 h-5 ${titleColor}`} />
          ) : (
            <Zap className={`w-5 h-5 ${titleColor}`} />
          )}
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h2 className={`text-lg font-semibold ${titleColor}`}>{title}</h2>
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${countBg}`}>
                {agents.length}
              </span>
            </div>
            <p className={`text-xs mt-0.5 ${subtitleColor}`}>{subtitle}</p>
          </div>
        </div>
      </div>

      <div className="grid gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {agents.map((agent) => (
          <AgentCard
            key={agent.name}
            agent={agent}
            displayLang={displayLang}
            isPrimary={isPrimary}
            isSelected={selectedAgent?.name === agent.name}
            onClick={() => onSelect(agent)}
            onDelete={onDelete}
          />
        ))}
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
  isPrimary: boolean;
  isSelected: boolean;
  onClick: () => void;
  onDelete: (name: string) => void;
}

function AgentCard({ agent, displayLang, isPrimary, isSelected, onClick, onDelete }: AgentCardProps) {
  const { t } = useTranslation('agent');
  const displayDesc = getAgentDisplayDescription(agent, displayLang);
  const borderColor = isPrimary ? '#0ea5e9' : '#8B5CF6';

  const modeBadge = isPrimary ? (
    <span className="px-1.5 py-0.5 bg-sky-100 text-sky-800 text-xs font-medium rounded-full shrink-0">
      Primary
    </span>
  ) : (
    <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-xs font-medium rounded-full shrink-0">
      Sub
    </span>
  );

  return (
    <div
      onClick={onClick}
      className={`
        relative bg-white rounded-xl border overflow-hidden cursor-pointer
        h-[180px] flex flex-col
        transition-all duration-150
        ${isSelected
          ? isPrimary
            ? 'border-sky-400 shadow-md ring-2 ring-sky-200'
            : 'border-purple-400 shadow-md ring-2 ring-purple-200'
          : 'border-gray-200 shadow-sm hover:shadow-md hover:border-gray-300'
        }
      `}
      style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
    >
      <div className={`flex-1 px-4 pt-4 min-h-0 flex flex-col gap-1.5 ${agent.native ? 'pb-4' : 'pb-2'}`}>
        <div className="flex items-start gap-1.5 flex-wrap">
          <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px]">
            {agent.name.charAt(0).toUpperCase() + agent.name.slice(1)}
          </span>
          {modeBadge}
          {agent.native && (
            <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-xs font-medium rounded-full shrink-0">
              {t('badge.native')}
            </span>
          )}
          {agent.delegatable && (
            <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 text-xs font-medium rounded-full shrink-0">
              {t('badge.delegatable')}
            </span>
          )}
        </div>

        <p className="text-xs text-gray-500 leading-relaxed line-clamp-3 flex-1">
          {displayDesc || t('common:empty.noDescription')}
        </p>

        {agent.model && (
          <div className="flex items-center gap-1 text-xs text-gray-400">
            <Cpu className="w-3 h-3 shrink-0" />
            <span className="truncate">{agent.model.providerID}/{agent.model.modelID}</span>
          </div>
        )}
      </div>

      {/* Footer actions — only shown for custom agents */}
      {!agent.native && (
        <div
          className="border-t border-gray-100 px-4 py-2 flex items-center justify-end gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => onDelete(agent.name)}
            className="flex items-center justify-center w-6 h-6 border border-red-300 text-red-500 rounded-lg hover:bg-red-50 transition-colors"
            title={t('badge.delete')}
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}
