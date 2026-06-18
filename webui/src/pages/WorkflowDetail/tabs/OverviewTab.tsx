import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution } from '@/api/workflow';
import { Calendar, User, Tag, Activity, Clock, CheckCircle, XCircle, Layers, ChevronDown, ChevronRight, FileText } from 'lucide-react';
import RunTab from './RunTab';

interface OverviewTabProps {
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
}

function MetaRow({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-gray-100 last:border-0">
      <span className="text-gray-400 mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-gray-500 w-16 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-xs text-gray-800 font-medium flex-1 break-all">{value}</span>
    </div>
  );
}

function StatCard({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div className="rounded-md border border-gray-100 bg-gray-50/70 px-2.5 py-2">
      <div className="text-[11px] leading-4 text-gray-500">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold leading-5 tabular-nums ${color}`}>
        {value}
      </div>
    </div>
  );
}

function WorkflowFileList({ jsonPath, mdPath }: { jsonPath: string; mdPath: string }) {
  return (
    <div className="space-y-1.5 min-w-0">
      <div className="min-w-0">
        <span className="text-[11px] text-gray-400">workflow.json</span>
        <code className="block mt-0.5 rounded bg-gray-50 px-2 py-1 text-[11px] leading-4 text-gray-700 break-all">
          {jsonPath}
        </code>
      </div>
      <div className="min-w-0">
        <span className="text-[11px] text-gray-400">workflow.md</span>
        <code className="block mt-0.5 rounded bg-gray-50 px-2 py-1 text-[11px] leading-4 text-gray-700 break-all">
          {mdPath}
        </code>
      </div>
    </div>
  );
}

function CollapsibleSection({
  title,
  summary,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  summary?: ReactNode;
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="border-b border-gray-100 last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
      >
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
          {summary && (
            <div className="mt-0.5 text-xs text-gray-400 truncate">
              {summary}
            </div>
          )}
        </div>
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
        )}
      </button>
      {expanded && (
        <div className="px-4 pb-4">
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
  const [configExpanded, setConfigExpanded] = useState(true);
  const [runExpanded, setRunExpanded] = useState(true);
  const { stats } = workflow;
  const successRate =
    stats.callCount > 0 ? ((stats.successCount / stats.callCount) * 100).toFixed(1) : '0';
  const avgRuntime = `${stats.avgRuntime.toFixed(2)}s`;
  const runSummary = `${t('detail.overview.totalCalls')} ${stats.callCount} / ${t('detail.overview.successRate')} ${successRate}% / ${t('detail.overview.avgRuntime')} ${avgRuntime}`;
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
    <div className="h-full overflow-y-auto bg-white">
      <CollapsibleSection
        title={t('detail.overview.configInfo')}
        summary={t('detail.overview.nodesAndEdges', {
          nodes: workflow.workflowJson.nodes.length,
          edges: workflow.workflowJson.edges.length,
        })}
        expanded={configExpanded}
        onToggle={() => setConfigExpanded(v => !v)}
      >
        <div className="space-y-4">
          <div className="divide-y divide-gray-100">
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
              icon={<FileText className="w-3.5 h-3.5" />}
              label={t('detail.overview.workflowFiles')}
              value={(
                <WorkflowFileList
                  jsonPath={`${workflowDir}workflow.json`}
                  mdPath={`${workflowDir}workflow.md`}
                />
              )}
            />
          </div>
        </div>
      </CollapsibleSection>

      <CollapsibleSection
        title={t('detail.overview.run')}
        summary={runSummary}
        expanded={runExpanded}
        onToggle={() => setRunExpanded(v => !v)}
      >
        <div className="mb-3">
          <h4 className="mb-1.5 text-[11px] font-semibold text-gray-400">
            {t('detail.overview.runStats')}
          </h4>
          <div className="grid grid-cols-2 gap-1.5">
            <StatCard value={stats.callCount}                   label={t('detail.overview.totalCalls')} color="text-gray-900" />
            <StatCard value={`${successRate}%`}                 label={t('detail.overview.successRate')} color="text-green-600" />
            <StatCard value={avgRuntime}                        label={t('detail.overview.avgRuntime')} color="text-red-600" />
            <StatCard value={stats.errorCount}                  label={t('detail.overview.errorCount')} color="text-red-500" />
          </div>
          {stats.callCount > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">
              <CheckCircle className="h-3 w-3 text-green-500" />
              <span>{t('detail.overview.successTimes', { count: stats.successCount })}</span>
              <XCircle className="h-3 w-3 text-red-400" />
              <span>{t('detail.overview.errorTimes', { count: stats.errorCount })}</span>
            </div>
          )}
        </div>
        <RunTab
          workflow={workflow}
          latestExecution={latestExecution}
          onLatestExecutionChange={onLatestExecutionChange}
          onExecutionSettled={onExecutionSettled}
          embedded
          embeddedTabs
          hideSectionHeaders
        />
      </CollapsibleSection>
    </div>
  );
}
