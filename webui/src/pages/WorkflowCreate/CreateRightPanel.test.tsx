import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { Workflow } from '@/api/workflow';
import CreateRightPanel from './CreateRightPanel';

const { capturedCreateChatTabProps, capturedIntegrationTabProps } = vi.hoisted(() => ({
  capturedCreateChatTabProps: [] as any[],
  capturedIntegrationTabProps: [] as any[],
}));

vi.mock('./CreateChatTab', () => ({
  default: (props: any) => {
    capturedCreateChatTabProps.push(props);
    return <div>Workbench content</div>;
  },
}));

vi.mock('./CreateOverviewTab', () => ({
  default: () => <div>Overview content</div>,
}));

vi.mock('../WorkflowDetail/tabs/IntegrationTab', () => ({
  default: ({ workflow, onGuidePrompt }: { workflow: Workflow; onGuidePrompt?: (prompt: string, label: string) => void }) => {
    capturedIntegrationTabProps.push({ workflow, onGuidePrompt });
    return (
      <div>
        <div>Publish content for {workflow.id}</div>
        <button
          type="button"
          onClick={() => onGuidePrompt?.('publish api prompt', '发布为 API')}
        >
          发布为 API
        </button>
      </div>
    );
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'create.rightPanel.tabOverview': '详情',
        'create.rightPanel.tabChat': '工作台',
        'create.rightPanel.tabIntegration': '发布',
        'create.publish.emptyTitle': '等待生成工作流',
        'create.publish.emptyHint': '工作流生成后，可以在这里配置发布方式。',
      };
      return translations[key] ?? key;
    },
  }),
}));

const workflow: Workflow = {
  id: 'generated_workflow',
  name: 'Generated Workflow',
  category: 'default',
  status: 'draft',
  source: 'project',
  createdAt: 1,
  updatedAt: 1,
  workflowJson: {
    start: 'start',
    nodes: [],
    edges: [],
  },
  stats: {
    callCount: 0,
    successCount: 0,
    errorCount: 0,
    totalRuntime: 0,
    avgRuntime: 0,
    thumbsUp: 0,
    thumbsDown: 0,
  },
};

describe('WorkflowCreate CreateRightPanel', () => {
  beforeEach(() => {
    capturedCreateChatTabProps.length = 0;
    capturedIntegrationTabProps.length = 0;
  });

  it('opens on the workbench tab and exposes a publish tab', () => {
    render(
      <CreateRightPanel
        workflow={null}
        open
        width={420}
        onWorkflowCreated={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '详情' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '工作台' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(screen.getByText('Workbench content')).toBeInTheDocument();
  });

  it('shows a publish placeholder before the workflow is generated', async () => {
    const user = userEvent.setup();
    render(
      <CreateRightPanel
        workflow={null}
        open
        width={420}
        onWorkflowCreated={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('等待生成工作流')).toBeInTheDocument();
    expect(screen.getByText('工作流生成后，可以在这里配置发布方式。')).toBeInTheDocument();
  });

  it('reuses the workflow publish tab after generation', async () => {
    const user = userEvent.setup();
    render(
      <CreateRightPanel
        workflow={workflow}
        open
        width={420}
        onWorkflowCreated={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: '发布' }));

    expect(screen.getByText('Publish content for generated_workflow')).toBeInTheDocument();
    expect(capturedIntegrationTabProps[capturedIntegrationTabProps.length - 1].onGuidePrompt).toEqual(expect.any(Function));
  });

  it('routes publish guide prompts back into the current workbench session', async () => {
    const user = userEvent.setup();
    const onLaunchRequestHandled = vi.fn();
    render(
      <CreateRightPanel
        workflow={workflow}
        open
        width={420}
        onWorkflowCreated={vi.fn()}
        initialChatSessionId="session-existing"
        onChatLaunchRequestHandled={onLaunchRequestHandled}
      />,
    );

    await user.click(screen.getByRole('button', { name: '发布' }));
    await user.click(screen.getByRole('button', { name: '发布为 API' }));

    expect(screen.getByText('Workbench content')).toBeInTheDocument();
    const latestChatProps = capturedCreateChatTabProps[capturedCreateChatTabProps.length - 1];
    expect(latestChatProps.initialSessionId).toBe('session-existing');
    expect(latestChatProps.launchRequest).toMatchObject({
      prompt: 'publish api prompt',
      displayLabel: '发布为 API',
    });

    latestChatProps.onLaunchRequestHandled(latestChatProps.launchRequest.id);
    expect(onLaunchRequestHandled).toHaveBeenCalledWith(latestChatProps.launchRequest.id);
  });
});
