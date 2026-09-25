import { Link, useNavigate } from 'react-router-dom';
import { IconArrowLeft, IconExternalLink, IconLayoutSidebarRight, IconLayoutSidebarRightCollapse } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Workflow } from '@/api/workflow';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

interface CreateTopBarProps {
  workflow: Workflow | null;
  panelOpen: boolean;
  onTogglePanel: () => void;
}

export default function CreateTopBar({ workflow, panelOpen, onTogglePanel }: CreateTopBarProps) {
  const { t, i18n } = useTranslation('workflow');
  const navigate = useNavigate();
  const displayName = workflow ? getWorkflowDisplayName(workflow, i18n?.language) : '';

  return (
    <div className="h-14 bg-surface border-b border-line flex items-center pl-14 pr-4 gap-3 flex-shrink-0 z-10 lg:pl-4">
      <Link
        to="/workflows"
        className="flex items-center gap-1.5 text-fg-muted hover:text-fg transition-colors text-sm font-medium"
      >
        <IconArrowLeft aria-hidden="true" className="w-4 h-4" />
        {t('pageTitle')}
      </Link>

      <div className="w-px h-5 bg-selected" />

      <div className="flex items-center gap-2 flex-1 min-w-0">
        <h1 className="text-title-page font-bold text-fg truncate">
          {workflow ? displayName : t('create.topBar.newWorkflow')}
        </h1>
        {!workflow ? (
          <span className="px-2 py-0.5 rounded-pill text-xs font-medium bg-danger-soft text-danger flex-shrink-0 border border-danger">
            {t('create.topBar.creating')}
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded-pill text-xs font-medium bg-success-soft text-success flex-shrink-0">
            {t('create.topBar.generated')}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        {workflow && (
          <button
            onClick={() => navigate(`/workflows/${workflow.id}`)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-action text-action-fg text-sm rounded-control hover:bg-action-hover transition-colors"
          >
            <IconExternalLink aria-hidden="true" className="w-4 h-4" />
            {t('create.topBar.viewDetail')}
          </button>
        )}
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
