import { Link } from 'react-router-dom';
import { IconArrowLeft, IconLayoutSidebarRight, IconLayoutSidebarRightCollapse } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution } from '@/api/workflow';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

interface TopBarProps {
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  panelOpen: boolean;
  onTogglePanel: () => void;
}

export default function TopBar({ workflow, latestExecution, panelOpen, onTogglePanel }: TopBarProps) {
  const { t, i18n } = useTranslation('workflow');
  const displayName = getWorkflowDisplayName(workflow, i18n?.language);

  const statusConfig = {
    draft:    { label: t('status.draft'),    className: 'bg-subtle text-fg-secondary' },
    active:   { label: t('status.active'),   className: 'bg-success-soft text-success' },
    archived: { label: t('status.archived'), className: 'bg-warning-soft text-warning' },
  };

  const status = statusConfig[workflow.status];
  const currentNodeId = latestExecution?.currentNodeId
    || latestExecution?.executionLog?.[latestExecution.executionLog.length - 1]?.node_id;
  const currentNode = workflow.workflowJson.nodes.find((node) => node.id === currentNodeId);
  const currentNodeLabel = currentNode?.description || currentNode?.id || currentNodeId;
  const currentPhase = latestExecution?.currentPhase || latestExecution?.status;
  const isRunning = latestExecution?.status === 'running';

  return (
    <div className="min-h-14 bg-surface border-b border-line pl-14 pr-4 py-2 flex items-center gap-3 flex-shrink-0 z-10 lg:pl-4">
      {/* Back button */}
      <Link
        to="/workflows"
        className="flex items-center gap-1.5 text-fg-muted hover:text-fg transition-colors text-sm font-medium"
      >
        <IconArrowLeft aria-hidden="true" className="w-4 h-4" />
        {t('pageTitle')}
      </Link>

      <div className="w-px h-5 bg-selected" />

      {/* Workflow name + status */}
      <div className="flex flex-col flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <h1 className="text-title-page font-bold text-fg truncate">{displayName}</h1>
          <span className={`px-2 py-0.5 rounded-pill text-xs font-medium flex-shrink-0 ${status.className}`}>
            {status.label}
          </span>
          {workflow.category && workflow.category !== 'default' && (
            <span className="text-xs text-fg-muted flex-shrink-0">{workflow.category}</span>
          )}
        </div>
        {isRunning && currentNodeLabel && (
          <div className="mt-1 text-xs text-success truncate">
            {t('detail.topBar.runningStage', {
              phase: t(`detail.topBar.phase.${currentPhase || 'running'}`),
              node: currentNodeLabel,
            })}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={onTogglePanel}
          className="flex items-center justify-center w-8 h-8 border border-line text-fg-secondary rounded-control hover:bg-hover transition-colors"
          title={panelOpen ? t('detail.topBar.collapsePanel') : t('detail.topBar.expandPanel')}
        >
          {panelOpen ? (
            <IconLayoutSidebarRightCollapse aria-hidden="true" className="w-4 h-4" />
          ) : (
            <IconLayoutSidebarRight aria-hidden="true" className="w-4 h-4" />
          )}
        </button>
      </div>
    </div>
  );
}
