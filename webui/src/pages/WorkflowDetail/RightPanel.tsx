import { useState, Component, type ReactNode, type ErrorInfo } from 'react';
import { IconTrash } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import { useConfirm } from '@/components/common/ConfirmDialog';
import OverviewTab from './tabs/OverviewTab';
import ChatTab, { type WorkflowChatLaunchRequest } from './tabs/ChatTab';
import IntegrationTab from './tabs/IntegrationTab';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

export type { WorkflowChatLaunchRequest };

// ─────────────────────────────────────────────
// Error boundary helpers
// ─────────────────────────────────────────────

function ErrorDisplay({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const { t } = useTranslation('workflow');
  return (
    <div className="p-4 text-xs text-danger space-y-2">
      <p className="font-semibold">{t('detail.rightPanel.renderError')}</p>
      <pre className="whitespace-pre-wrap bg-danger-soft rounded-tag p-2 overflow-auto max-h-60">
        {error.message}
        {'\n'}
        {error.stack}
      </pre>
      <button
        onClick={onRetry}
        className="text-danger underline"
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
  /** Render as a full-size overlay of the canvas area (phone widths). */
  overlay?: boolean;
  activeTab?: RightPanelTabId;
  chatLaunchRequest?: WorkflowChatLaunchRequest | null;
  onChatLaunchRequestHandled?: (id: number) => void;
  onActiveTabChange?: (tab: RightPanelTabId) => void;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
  onWorkflowUpdated?: (updated: Workflow) => void;
  onFirstMessageSent?: () => void;
  onSessionChange?: (sessionId: string | null) => void;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
  focusExecutionId?: string;
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
  onGuidePrompt,
  focusExecutionId,
  selectedNode, onDeselectNode,
  onDelete,
  overlay = false,
}: RightPanelProps) {
  const { t, i18n } = useTranslation('workflow');
  const confirm = useConfirm();
  const workflowDisplayName = getWorkflowDisplayName(workflow, i18n?.language);
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
      description: t('detail.rightPanel.deleteConfirmDesc', { name: workflowDisplayName }),
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
      className={
        overlay
          // Phone widths: cover the canvas area instead of sharing the row.
          ? `absolute inset-0 z-40 flex min-w-0 flex-col bg-surface overflow-hidden ${open ? '' : 'hidden'}`
          : 'relative z-10 flex min-w-0 flex-col bg-surface border-l border-line flex-shrink-0 overflow-hidden transition-[width] duration-300 ease-in-out'
      }
      style={overlay ? undefined : { width: open ? width : 0 }}
    >
      {/* Tab bar */}
      <div className="flex border-b border-line flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`flex-1 py-3 text-xs font-medium transition-colors relative ${
              currentActiveTab === tab.id ? 'text-danger' : 'text-fg-muted hover:text-fg-secondary'
            }`}
          >
            {tab.label}
            {currentActiveTab === tab.id && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-danger rounded-pill" />
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
              focusExecutionId={focusExecutionId}
            />
          </TabErrorBoundary>
        )}
        {currentActiveTab === 'integration' && (
          <TabErrorBoundary>
            <IntegrationTab
              workflow={workflow}
              onWorkflowUpdated={onWorkflowUpdated}
              onGuidePrompt={onGuidePrompt}
            />
          </TabErrorBoundary>
        )}
      </div>

      {/* 底部删除按钮：仅概览页展示，避免编辑/集成流程中出现破坏性操作入口 */}
      {onDelete && currentActiveTab === 'overview' && (
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-line flex-shrink-0">
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-danger border border-danger rounded-control hover:bg-danger-soft disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
          >
            <IconTrash aria-hidden="true" className="w-4 h-4" />
            {deleting ? t('detail.rightPanel.deleting') : t('detail.rightPanel.deleteWorkflow')}
          </button>
        </div>
      )}
    </div>
  );
}
