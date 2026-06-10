import { useEffect, useState } from 'react';
import { Rocket } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Workflow } from '@/api/workflow';
import CreateChatTab, { type CreateWorkflowChatLaunchRequest } from './CreateChatTab';
import CreateOverviewTab from './CreateOverviewTab';
import IntegrationTab from '../WorkflowDetail/tabs/IntegrationTab';

type TabId = 'chat' | 'overview' | 'integration';

interface CreateRightPanelProps {
  workflow: Workflow | null;
  open: boolean;
  width?: number;
  onWorkflowCreated: (workflow: Workflow) => void;
  onWorkflowUpdated?: (workflow: Workflow) => void;
  chatLaunchRequest?: CreateWorkflowChatLaunchRequest | null;
  onChatLaunchRequestHandled?: (id: number) => void;
}

export default function CreateRightPanel({
  workflow,
  open,
  width = 320,
  onWorkflowCreated,
  onWorkflowUpdated,
  chatLaunchRequest,
  onChatLaunchRequestHandled,
}: CreateRightPanelProps) {
  const { t } = useTranslation('workflow');
  const [activeTab, setActiveTab] = useState<TabId>('chat');

  useEffect(() => {
    if (chatLaunchRequest) {
      setActiveTab('chat');
    }
  }, [chatLaunchRequest]);

  const TABS: { id: TabId; label: string }[] = [
    { id: 'overview', label: t('create.rightPanel.tabOverview') },
    { id: 'chat', label: t('create.rightPanel.tabChat') },
    { id: 'integration', label: t('create.rightPanel.tabIntegration') },
  ];

  return (
    <div
      className="flex flex-col bg-white border-l border-gray-200 flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out"
      style={{ width: open ? width : 0 }}
    >
      <div className="flex border-b border-gray-100 flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`
              flex-1 py-3 text-xs font-medium transition-colors relative
              ${activeTab === tab.id ? 'text-red-600' : 'text-gray-500 hover:text-gray-700'}
            `}
          >
            {tab.label}
            {activeTab === tab.id && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-red-600 rounded-full" />
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
        {activeTab === 'chat' && (
          <CreateChatTab
            onWorkflowCreated={onWorkflowCreated}
            launchRequest={chatLaunchRequest}
            onLaunchRequestHandled={onChatLaunchRequestHandled}
          />
        )}
        {activeTab === 'overview' && (
          <CreateOverviewTab workflow={workflow} />
        )}
        {activeTab === 'integration' && (
          workflow ? (
            <IntegrationTab
              workflow={workflow}
              onWorkflowUpdated={onWorkflowUpdated}
            />
          ) : (
            <div className="flex min-h-0 flex-1 items-center justify-center p-6">
              <div className="flex max-w-[260px] flex-col items-center gap-3 rounded-xl border border-dashed border-gray-200 bg-gray-50/70 px-5 py-6 text-center">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-red-100 bg-red-50 text-red-500">
                  <Rocket className="h-4 w-4" />
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-800">{t('create.publish.emptyTitle')}</p>
                  <p className="mt-1 text-xs leading-relaxed text-gray-500">{t('create.publish.emptyHint')}</p>
                </div>
              </div>
            </div>
          )
        )}
      </div>
    </div>
  );
}
