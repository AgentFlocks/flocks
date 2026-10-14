import { Link } from 'react-router-dom';
import { ArrowLeft, PanelRight, PanelRightClose } from 'lucide-react';
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
    draft:    { label: t('status.draft'),    className: 'bg-gray-100 text-gray-700' },
    active:   { label: t('status.active'),   className: 'bg-green-100 text-green-800' },
    archived: { label: t('status.archived'), className: 'bg-yellow-100 text-yellow-800' },
  };

  const status = statusConfig[workflow.status];
  const currentNodeId = latestExecution?.currentNodeId
    || latestExecution?.executionLog?.[latestExecution.executionLog.length - 1]?.node_id;
  const currentNode = workflow.workflowJson.nodes.find((node) => node.id === currentNodeId);
  const currentNodeLabel = currentNode?.description || currentNode?.id || currentNodeId;
  const currentPhase = latestExecution?.currentPhase || latestExecution?.status;
  const isRunning = latestExecution?.status === 'running';

  return (
    <div className="min-h-14 bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-shrink-0 z-10">
      {/* Back button */}
      <Link
        to="/workflows"
        className="flex items-center gap-1.5 text-gray-500 hover:text-gray-800 transition-colors text-sm font-medium"
      >
        <ArrowLeft className="w-4 h-4" />
        {t('pageTitle')}
      </Link>

      <div className="w-px h-5 bg-gray-200" />

      {/* Workflow name + status */}
      <div className="flex flex-col flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <h1 className="text-sm font-semibold text-gray-900 truncate">{displayName}</h1>
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${status.className}`}>
            {status.label}
          </span>
          {workflow.category && workflow.category !== 'default' && (
            <span className="text-xs text-gray-400 flex-shrink-0">{workflow.category}</span>
          )}
        </div>
        {isRunning && currentNodeLabel && (
          <div className="mt-1 text-xs text-emerald-700 truncate">
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
          className="flex items-center justify-center w-8 h-8 border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
          title={panelOpen ? t('detail.topBar.collapsePanel') : t('detail.topBar.expandPanel')}
        >
          {panelOpen ? (
            <PanelRightClose className="w-4 h-4" />
          ) : (
            <PanelRight className="w-4 h-4" />
          )}
        </button>
      </div>
    </div>
  );
}
