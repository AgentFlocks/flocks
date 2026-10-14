import { Layers, Tag, Activity, User, Calendar, Clock, MessageSquarePlus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Workflow } from '@/api/workflow';

interface CreateOverviewTabProps {
  workflow: Workflow | null;
}

// ─────────────────────────────────────────────
// Placeholder components
// ─────────────────────────────────────────────

function PlaceholderRow({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-gray-50 last:border-0">
      <span className="text-gray-300 mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-gray-400 w-16 flex-shrink-0 pt-0.5">{label}</span>
      <div className="h-3.5 bg-gray-100 rounded flex-1 mt-0.5" />
    </div>
  );
}

function PlaceholderStatCard({ label }: { label: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-center">
      <div className="h-7 bg-gray-100 rounded mx-auto w-10 mb-1" />
      <div className="text-xs text-gray-400">{label}</div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Filled components (same as OverviewTab)
// ─────────────────────────────────────────────

function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-gray-50 last:border-0">
      <span className="text-gray-400 mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-gray-500 w-16 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-xs text-gray-800 font-medium flex-1 break-all">{value}</span>
    </div>
  );
}

function StatCard({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-center">
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-gray-500 mt-0.5">{label}</div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────

export default function CreateOverviewTab({ workflow }: CreateOverviewTabProps) {
  const { t, i18n } = useTranslation('workflow');

  if (!workflow) {
    return (
      <div className="flex flex-col gap-5 p-4 overflow-y-auto h-full">
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            {t('detail.overview.configInfo')}
          </h3>
          <div className="bg-white rounded-lg border border-gray-100 px-3">
            <PlaceholderRow label={t('detail.overview.nodeCount')} icon={<Layers className="w-3.5 h-3.5" />} />
            <PlaceholderRow label={t('detail.overview.category')} icon={<Tag className="w-3.5 h-3.5" />} />
            <PlaceholderRow label={t('detail.overview.version')} icon={<Activity className="w-3.5 h-3.5" />} />
            <PlaceholderRow label={t('detail.overview.createdBy')} icon={<User className="w-3.5 h-3.5" />} />
            <PlaceholderRow label={t('detail.overview.createdAt')} icon={<Calendar className="w-3.5 h-3.5" />} />
            <PlaceholderRow label={t('detail.overview.updatedAt')} icon={<Clock className="w-3.5 h-3.5" />} />
          </div>
        </section>

        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            {t('detail.overview.runStats')}
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <PlaceholderStatCard label={t('detail.overview.totalCalls')} />
            <PlaceholderStatCard label={t('detail.overview.successRate')} />
            <PlaceholderStatCard label={t('detail.overview.avgRuntime')} />
            <PlaceholderStatCard label={t('detail.overview.errorCount')} />
          </div>
        </section>

        <div className="flex flex-col items-center justify-center py-6 gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gray-100">
            <MessageSquarePlus className="w-5 h-5 text-gray-400" />
          </div>
          <p className="text-xs text-gray-400 text-center max-w-[180px] leading-relaxed">
            {t('create.overview.chatHint')}
          </p>
        </div>
      </div>
    );
  }

  // ── Workflow generated: show real data ──
  const { stats } = workflow;
  const successRate =
    stats.callCount > 0
      ? ((stats.successCount / stats.callCount) * 100).toFixed(1)
      : '0';

  const dateLocale = i18n.language;
  const createdAt = new Date(workflow.createdAt).toLocaleString(dateLocale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const updatedAt = new Date(workflow.updatedAt).toLocaleString(dateLocale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className="flex flex-col gap-5 p-4 overflow-y-auto h-full">
      <section>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          {t('detail.overview.configInfo')}
        </h3>
        <div className="bg-white rounded-lg border border-gray-100 px-3">
          <MetaRow
            icon={<Layers className="w-3.5 h-3.5" />}
            label={t('detail.overview.nodeCount')}
            value={t('detail.overview.nodesAndEdges', {
              nodes: workflow.workflowJson.nodes.length,
              edges: workflow.workflowJson.edges.length,
            })}
          />
          <MetaRow
            icon={<Tag className="w-3.5 h-3.5" />}
            label={t('detail.overview.category')}
            value={workflow.category}
          />
          {workflow.workflowJson.version && (
            <MetaRow
              icon={<Activity className="w-3.5 h-3.5" />}
              label={t('detail.overview.version')}
              value={workflow.workflowJson.version}
            />
          )}
          {workflow.createdBy && (
            <MetaRow
              icon={<User className="w-3.5 h-3.5" />}
              label={t('detail.overview.createdBy')}
              value={workflow.createdBy}
            />
          )}
          <MetaRow
            icon={<Calendar className="w-3.5 h-3.5" />}
            label={t('detail.overview.createdAt')}
            value={createdAt}
          />
          <MetaRow
            icon={<Clock className="w-3.5 h-3.5" />}
            label={t('detail.overview.updatedAt')}
            value={updatedAt}
          />
        </div>
      </section>

      <section>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          {t('detail.overview.runStats')}
        </h3>
        <div className="grid grid-cols-2 gap-2">
          <StatCard value={stats.callCount} label={t('detail.overview.totalCalls')} color="text-gray-900" />
          <StatCard value={`${successRate}%`} label={t('detail.overview.successRate')} color="text-green-600" />
          <StatCard
            value={`${stats.avgRuntime.toFixed(2)}s`}
            label={t('detail.overview.avgRuntime')}
            color="text-red-600"
          />
          <StatCard value={stats.errorCount} label={t('detail.overview.errorCount')} color="text-red-500" />
        </div>
      </section>

      {workflow.description && (
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            {t('create.overview.execDescription')}
          </h3>
          <div className="bg-white rounded-lg border border-gray-100 p-3 text-xs text-gray-700 leading-relaxed">
            {workflow.description}
          </div>
        </section>
      )}
    </div>
  );
}
