import { useTranslation } from 'react-i18next';
import { Workflow } from '@/api/workflow';
import { Calendar, User, Tag, Activity, Clock, CheckCircle, XCircle, Layers } from 'lucide-react';

interface OverviewTabProps {
  workflow: Workflow;
}

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

export default function OverviewTab({ workflow }: OverviewTabProps) {
  const { t, i18n } = useTranslation('workflow');
  const { stats } = workflow;
  const successRate =
    stats.callCount > 0 ? ((stats.successCount / stats.callCount) * 100).toFixed(1) : '0';

  const locale = i18n.language;
  const createdAt = new Date(workflow.createdAt).toLocaleString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const updatedAt = new Date(workflow.updatedAt).toLocaleString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className="flex flex-col gap-5 p-4 overflow-y-auto h-full">
      {/* Metadata */}
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

      {/* Run statistics */}
      <section>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
          {t('detail.overview.runStats')}
        </h3>
        <div className="grid grid-cols-2 gap-2">
          <StatCard value={stats.callCount}                         label={t('detail.overview.totalCalls')} color="text-gray-900" />
          <StatCard value={`${successRate}%`}                       label={t('detail.overview.successRate')} color="text-green-600" />
          <StatCard value={`${stats.avgRuntime.toFixed(2)}s`}       label={t('detail.overview.avgRuntime')} color="text-red-600" />
          <StatCard value={stats.errorCount}                        label={t('detail.overview.errorCount')} color="text-red-500" />
        </div>
        {stats.callCount > 0 && (
          <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
            <CheckCircle className="w-3.5 h-3.5 text-green-500" />
            <span>{t('detail.overview.successTimes', { count: stats.successCount })}</span>
            <XCircle className="w-3.5 h-3.5 text-red-400 ml-2" />
            <span>{t('detail.overview.errorTimes', { count: stats.errorCount })}</span>
          </div>
        )}
      </section>
    </div>
  );
}
