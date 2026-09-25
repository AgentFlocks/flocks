import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution } from '@/api/workflow';
import { IconCalendar, IconUser, IconTag, IconActivity, IconClock, IconCircleCheck, IconCircleX, IconStack2, IconChevronDown, IconChevronRight, IconFileText } from '@tabler/icons-react';
import RunTab from './RunTab';

interface OverviewTabProps {
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
  focusExecutionId?: string;
}

function MetaRow({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-line last:border-0">
      <span className="text-fg-muted mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-fg-muted w-16 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-xs text-fg font-medium flex-1 break-all">{value}</span>
    </div>
  );
}

function StatCard({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div className="rounded-control border border-line bg-subtle px-2.5 py-2">
      <div className="text-xs text-fg-muted">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${color}`}>
        {value}
      </div>
    </div>
  );
}

function WorkflowFileList({ jsonPath, mdPath }: { jsonPath: string; mdPath: string }) {
  return (
    <div className="space-y-1.5 min-w-0">
      <div className="min-w-0">
        <span className="text-xs text-fg-muted">workflow.json</span>
        <code className="block mt-0.5 rounded-tag bg-subtle px-2 py-1 text-xs text-fg-secondary break-all">
          {jsonPath}
        </code>
      </div>
      <div className="min-w-0">
        <span className="text-xs text-fg-muted">workflow.md</span>
        <code className="block mt-0.5 rounded-tag bg-subtle px-2 py-1 text-xs text-fg-secondary break-all">
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
    <section className="border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-hover transition-colors"
      >
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg">{title}</h3>
          {summary && (
            <div className="mt-0.5 text-xs text-fg-muted truncate">
              {summary}
            </div>
          )}
        </div>
        {expanded ? (
          <IconChevronDown aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        ) : (
          <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
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
  focusExecutionId,
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
    <div className="h-full overflow-y-auto bg-surface">
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
          <div className="divide-y divide-line">
            <MetaRow
              icon={<IconStack2 aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.nodeCount')}
              value={t('detail.overview.nodesAndEdges', {
                nodes: workflow.workflowJson.nodes.length,
                edges: workflow.workflowJson.edges.length,
              })}
            />
            <MetaRow
              icon={<IconTag aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.category')}
              value={workflow.category}
            />
            {workflow.workflowJson.version && (
              <MetaRow
                icon={<IconActivity aria-hidden="true" className="w-4 h-4" />}
                label={t('detail.overview.version')}
                value={workflow.workflowJson.version}
              />
            )}
            {workflow.createdBy && (
              <MetaRow
                icon={<IconUser aria-hidden="true" className="w-4 h-4" />}
                label={t('detail.overview.createdBy')}
                value={workflow.createdBy}
              />
            )}
            <MetaRow
              icon={<IconCalendar aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.createdAt')}
              value={createdAt}
            />
            <MetaRow
              icon={<IconClock aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.updatedAt')}
              value={updatedAt}
            />
            <MetaRow
              icon={<IconFileText aria-hidden="true" className="w-4 h-4" />}
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
          <h4 className="mb-1.5 text-xs font-semibold text-fg-muted">
            {t('detail.overview.runStats')}
          </h4>
          <div className="grid grid-cols-2 gap-1.5">
            <StatCard value={stats.callCount}                   label={t('detail.overview.totalCalls')} color="text-fg" />
            <StatCard value={`${successRate}%`}                 label={t('detail.overview.successRate')} color="text-success" />
            <StatCard value={avgRuntime}                        label={t('detail.overview.avgRuntime')} color="text-danger" />
            <StatCard value={stats.errorCount}                  label={t('detail.overview.errorCount')} color="text-danger" />
          </div>
          {stats.callCount > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
              <IconCircleCheck aria-hidden="true" className="h-4 w-4 text-success" />
              <span>{t('detail.overview.successTimes', { count: stats.successCount })}</span>
              <IconCircleX aria-hidden="true" className="h-4 w-4 text-danger" />
              <span>{t('detail.overview.errorTimes', { count: stats.errorCount })}</span>
            </div>
          )}
        </div>
        <RunTab
          workflow={workflow}
          latestExecution={latestExecution}
          onLatestExecutionChange={onLatestExecutionChange}
          onExecutionSettled={onExecutionSettled}
          focusExecutionId={focusExecutionId}
          embedded
          embeddedTabs
          hideSectionHeaders
        />
      </CollapsibleSection>
    </div>
  );
}
