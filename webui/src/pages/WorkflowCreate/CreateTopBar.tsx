import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, ExternalLink, PanelRight, PanelRightClose } from 'lucide-react';
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
    <div className="h-14 bg-white border-b border-gray-200 flex items-center gap-3 pl-14 pr-4 flex-shrink-0 z-10 lg:pl-4">
      <Link
        to="/workflows"
        className="flex items-center gap-1.5 text-gray-500 hover:text-gray-800 transition-colors text-sm font-medium"
      >
        <ArrowLeft aria-hidden="true" className="w-4 h-4" />
        {t('pageTitle')}
      </Link>

      <div className="w-px h-5 bg-gray-200" />

      <div className="flex items-center gap-2 flex-1 min-w-0">
        <h1 className="text-sm font-semibold text-gray-900 truncate">
          {workflow ? displayName : t('create.topBar.newWorkflow')}
        </h1>
        {!workflow ? (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-600 border border-red-200 flex-shrink-0">
            {t('create.topBar.creating')}
          </span>
        ) : (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 flex-shrink-0">
            {t('create.topBar.generated')}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        {workflow && (
          <button
            onClick={() => navigate(`/workflows/${workflow.id}`)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 transition-colors"
          >
            <ExternalLink aria-hidden="true" className="w-3.5 h-3.5" />
            {t('create.topBar.viewDetail')}
          </button>
        )}
        <button
          onClick={onTogglePanel}
          className="flex items-center justify-center w-8 h-8 border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors"
          title={panelOpen ? t('detail.topBar.collapsePanel') : t('detail.topBar.expandPanel')}
        >
          {panelOpen ? (
            <PanelRightClose aria-hidden="true" className="w-4 h-4" />
          ) : (
            <PanelRight aria-hidden="true" className="w-4 h-4" />
          )}
        </button>
      </div>
    </div>
  );
}
