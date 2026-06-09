import { useState, Component, type ReactNode, type ErrorInfo } from 'react';
import { Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import { useConfirm } from '@/components/common/ConfirmDialog';
import OverviewTab from './tabs/OverviewTab';
import ChatTab, { type WorkflowChatLaunchRequest } from './tabs/ChatTab';
import IntegrationTab from './tabs/IntegrationTab';

export type { WorkflowChatLaunchRequest };

// ─────────────────────────────────────────────
// Error boundary helpers
// ─────────────────────────────────────────────

function ErrorDisplay({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const { t } = useTranslation('workflow');
  return (
    <div className="p-4 text-xs text-red-600 space-y-2">
      <p className="font-semibold">{t('detail.rightPanel.renderError')}</p>
      <pre className="whitespace-pre-wrap bg-red-50 rounded p-2 overflow-auto max-h-60">
        {error.message}
        {'\n'}
        {error.stack}
      </pre>
      <button
        onClick={onRetry}
        className="text-red-600 underline"
      >
        {t('common:button.retry')}
      </button>
    </div>
  );
}

class TabErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[TabErrorBoundary]', error, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <ErrorDisplay
          error={this.state.error}
          onRetry={() => this.setState({ error: null })}
        />
      );
    }
    return this.props.children;
  }
}

// ─────────────────────────────────────────────
// RightPanel
// ─────────────────────────────────────────────

export type RightPanelTabId = 'chat' | 'overview' | 'integration';

interface RightPanelProps {
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  open: boolean;
  width?: number;
  activeTab?: RightPanelTabId;
  chatLaunchRequest?: WorkflowChatLaunchRequest | null;
  onChatLaunchRequestHandled?: (id: number) => void;
  onActiveTabChange?: (tab: RightPanelTabId) => void;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
  onWorkflowUpdated?: (updated: Workflow) => void;
  onFirstMessageSent?: () => void;
  onSessionChange?: (sessionId: string | null) => void;
  /** Currently selected node — passed to ChatTab to show reference chip in input */
  selectedNode?: WorkflowNode | null;
  onDeselectNode?: () => void;
  onDelete?: () => Promise<void>;
}

export default function RightPanel({
  workflow, latestExecution, open, width = 320,
  activeTab,
  chatLaunchRequest,
  onChatLaunchRequestHandled,
  onActiveTabChange,
  onLatestExecutionChange,
  onExecutionSettled,
  onWorkflowUpdated,
  onFirstMessageSent,
  onSessionChange,
  selectedNode, onDeselectNode,
  onDelete,
}: RightPanelProps) {
  const { t } = useTranslation('workflow');
  const confirm = useConfirm();
  const [internalActiveTab, setInternalActiveTab] = useState<RightPanelTabId>('overview');
  const [deleting, setDeleting] = useState(false);
  const currentActiveTab = activeTab ?? internalActiveTab;

  const handleTabChange = (tab: RightPanelTabId) => {
    if (activeTab === undefined) {
      setInternalActiveTab(tab);
    }
    onActiveTabChange?.(tab);
  };

  const handleDelete = async () => {
    const ok = await confirm({
      title: t('detail.rightPanel.deleteConfirmTitle'),
      description: t('detail.rightPanel.deleteConfirmDesc', { name: workflow.name }),
      confirmText: t('detail.rightPanel.deleteConfirmText'),
      variant: 'danger',
    });
    if (!ok || !onDelete) return;
    setDeleting(true);
    try {
      await onDelete();
    } finally {
      setDeleting(false);
    }
  };

  const TABS: { id: RightPanelTabId; label: string }[] = [
    { id: 'overview',     label: t('detail.rightPanel.tabOverview') },
    { id: 'chat',         label: t('detail.rightPanel.tabChat') },
    { id: 'integration',  label: t('detail.rightPanel.tabIntegration') },
  ];

  return (
    <div
      className="flex flex-col bg-white border-l border-gray-200 flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out"
      style={{ width: open ? width : 0 }}
    >
      {/* Tab bar */}
      <div className="flex border-b border-gray-100 flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`flex-1 py-3 text-xs font-medium transition-colors relative ${
              currentActiveTab === tab.id ? 'text-red-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.label}
            {currentActiveTab === tab.id && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-red-600 rounded-full" />
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
        {currentActiveTab === 'chat' && (
          <ChatTab
            workflow={workflow}
            onLatestExecutionChange={onLatestExecutionChange}
            onWorkflowUpdated={onWorkflowUpdated}
            onFirstMessageSent={onFirstMessageSent}
            onSessionChange={onSessionChange}
            launchRequest={chatLaunchRequest}
            onLaunchRequestHandled={onChatLaunchRequestHandled}
            selectedNode={selectedNode}
            onNodeRefDismiss={onDeselectNode}
          />
        )}
        {currentActiveTab === 'overview' && (
          <TabErrorBoundary>
            <OverviewTab
              workflow={workflow}
              latestExecution={latestExecution ?? null}
              onLatestExecutionChange={onLatestExecutionChange}
              onExecutionSettled={onExecutionSettled}
            />
          </TabErrorBoundary>
        )}
        {currentActiveTab === 'integration' && (
          <TabErrorBoundary>
            <IntegrationTab workflow={workflow} onWorkflowUpdated={onWorkflowUpdated} />
          </TabErrorBoundary>
        )}
      </div>

      {/* 底部删除按钮：仅概览页展示，避免编辑/集成流程中出现破坏性操作入口 */}
      {onDelete && currentActiveTab === 'overview' && (
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-100 flex-shrink-0">
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
            {deleting ? t('detail.rightPanel.deleting') : t('detail.rightPanel.deleteWorkflow')}
          </button>
        </div>
      )}
    </div>
  );
}
