import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution } from '@/api/workflow';
import { Calendar, User, Tag, Activity, Clock, CheckCircle, XCircle, Layers, ChevronDown, ChevronRight, FolderOpen } from 'lucide-react';
import { HistorySection, TestSection } from './RunTab';

interface OverviewTabProps {
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
}

function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-slate-100 last:border-0">
      <span className="text-slate-400 mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-slate-500 w-16 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-xs text-slate-800 font-medium flex-1 break-all">{value}</span>
    </div>
  );
}

function StatCard({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div className="bg-slate-50 rounded-md px-3 py-2.5">
      <div className={`text-lg font-semibold ${color}`}>{value}</div>
      <div className="text-[11px] text-slate-500 mt-0.5">{label}</div>
    </div>
  );
}

function CollapsibleSection({
  title,
  summary,
  children,
  defaultExpanded = true,
}: {
  title: string;
  summary?: ReactNode;
  children: ReactNode;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <section className="border-b border-slate-100 last:border-b-0 bg-white">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3.5 hover:bg-slate-50 transition-colors text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-red-100 focus-visible:ring-inset"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-slate-700">{title}</span>
          {!expanded && summary && (
            <span className="mt-1 block truncate text-xs font-normal text-slate-400">{summary}</span>
          )}
        </span>
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
        )}
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-2 space-y-4">
          {children}
        </div>
      )}
    </section>
  );
}

export default function OverviewTab({
  workflow,
  latestExecution = null,
  onLatestExecutionChange,
  onExecutionSettled,
}: OverviewTabProps) {
  const { t, i18n } = useTranslation('workflow');
  const { stats } = workflow;
  const successRate =
    stats.callCount > 0 ? ((stats.successCount / stats.callCount) * 100).toFixed(1) : '0';
  const workflowInfoSummary = [
    t('detail.overview.nodesAndEdges', {
      nodes: workflow.workflowJson.nodes.length,
      edges: workflow.workflowJson.edges.length,
    }),
    workflow.category,
    `${successRate}% ${t('detail.overview.successRate')}`,
  ].filter(Boolean).join(' · ');
  const workflowDir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;

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
    <div className="flex-1 min-h-0 overflow-y-auto bg-white">
      <CollapsibleSection
        title={t('detail.overview.workflowInfo')}
        summary={workflowInfoSummary}
      >
        <div className="divide-y divide-slate-100">
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
          <MetaRow
            icon={<FolderOpen className="w-3.5 h-3.5" />}
            label={t('detail.overview.fileDir')}
            value={(
              <div className="min-w-0 space-y-1.5">
                <p className="font-mono text-[11px] font-normal text-slate-600 break-all">
                  {workflowDir}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <span className="inline-flex max-w-full items-center rounded border border-slate-100 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] font-medium text-red-600">
                    workflow.md
                    {!workflow.markdownContent && (
                      <span className="ml-1 text-slate-400">{t('detail.overview.notGenerated')}</span>
                    )}
                  </span>
                  <span className="inline-flex max-w-full items-center rounded border border-slate-100 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] font-medium text-amber-600">
                    workflow.json
                  </span>
                </div>
              </div>
            )}
          />
        </div>

        <div>
          <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
            {t('detail.overview.runStats')}
          </h4>
          <div className="grid grid-cols-2 gap-2">
          <StatCard value={stats.callCount}                         label={t('detail.overview.totalCalls')} color="text-slate-900" />
          <StatCard value={`${successRate}%`}                       label={t('detail.overview.successRate')} color="text-green-600" />
          <StatCard value={`${stats.avgRuntime.toFixed(2)}s`}       label={t('detail.overview.avgRuntime')} color="text-red-600" />
          <StatCard value={stats.errorCount}                        label={t('detail.overview.errorCount')} color="text-red-500" />
          </div>
          {stats.callCount > 0 && (
            <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
              <CheckCircle className="w-3.5 h-3.5 text-green-500" />
              <span>{t('detail.overview.successTimes', { count: stats.successCount })}</span>
              <XCircle className="w-3.5 h-3.5 text-red-400 ml-2" />
              <span>{t('detail.overview.errorTimes', { count: stats.errorCount })}</span>
            </div>
          )}
        </div>
      </CollapsibleSection>

      <TestSection
        workflow={workflow}
        execution={latestExecution}
        onExecutionChange={onLatestExecutionChange}
        onExecutionSettled={onExecutionSettled}
        defaultExpanded={false}
      />
      <HistorySection
        workflowId={workflow.id}
        latestExecutionId={latestExecution?.id}
        onLatestExecutionChange={onLatestExecutionChange}
        defaultExpanded={false}
      />
    </div>
  );
}
