import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfirmProvider } from '@/components/common/ConfirmDialog';
import WorkflowDetail from './index';

const {
  mockGetWorkflow,
  mockUpdateWorkflow,
  mockSendSessionMessage,
  originalMarkdown,
  twoHunkMarkdown,
  topOnlyMarkdown,
} = vi.hoisted(() => ({
  mockGetWorkflow: vi.fn(),
  mockUpdateWorkflow: vi.fn(),
  mockSendSessionMessage: vi.fn(),
  originalMarkdown: '# old\n\nkeep 1\nkeep 2\nkeep 3\nkeep 4\n\nlast\n',
  twoHunkMarkdown: '# new\n\nkeep 1\nkeep 2\nkeep 3\nkeep 4\n\nlast changed\n',
  topOnlyMarkdown: '# new\n\nkeep 1\nkeep 2\nkeep 3\nkeep 4\n\nlast\n',
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    get: mockGetWorkflow,
    update: mockUpdateWorkflow,
    delete: vi.fn(),
    export: vi.fn(),
  },
}));

vi.mock('@/api/session', () => ({
  sessionApi: {
    sendMessage: mockSendSessionMessage,
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'detail.flocksHelp': '让Rex帮你配置、编辑工作流',
        'detail.flocksHelpTitle': '让Rex帮你配置、编辑工作流',
        'detail.resetLayout': '重置布局',
        'detail.canvasTabs.flow': '流程图',
        'detail.canvasTabs.md': 'MD 描述',
        'detail.canvasTabs.json': 'JSON',
        'detail.editDocTitle': 'workflow.md',
        'detail.editDocModeEdit': '编辑',
        'detail.editDocModePreview': '预览',
        'detail.editDocDiffTitle': 'AI 修改差异',
        'detail.editDocDiffReviewDesc': '接受会保留当前内容；拒绝会回滚到修改前内容。',
        'detail.editDocDiffHunkTitle': '变更 {{index}}',
        'detail.editDocDiffAdded': '新增',
        'detail.editDocDiffRemoved': '删除',
        'detail.editDocDiffAccept': '接受',
        'detail.editDocDiffReject': '拒绝',
        'detail.editDocDiffAcceptHunk': '接受此段',
        'detail.editDocDiffRejectHunk': '拒绝此段',
        'detail.editDocDiffRejecting': '回滚中',
        'detail.editDocDiffAcceptHunkSuccess': '已接受此段',
        'detail.editDocDiffRejectHunkSuccess': '已拒绝此段',
        'detail.editDocDiffRejectSuccess': '已拒绝',
        'detail.editDocDiffRejectFailed': '拒绝失败',
        'detail.editDocDiffRejectHunkFailed': '拒绝此段失败',
        'detail.editDocTextareaLabel': '编辑 workflow.md',
        'detail.editDocPlaceholder': '在这里编辑 workflow.md...',
        'detail.downloadMd': '下载 MD',
        'detail.downloadMdTitle': '下载当前 workflow.md',
        'detail.generateEditDocTitle': '生成 workflow.md',
        'detail.regenerateEditDoc': '重新生成',
        'detail.editDocSave': '保存',
        'detail.editDocSaving': '保存中',
        'detail.generateWorkflow': '生成工作流',
        'detail.generateWorkflowTitle': '基于 workflow.md 生成 workflow.json',
      };
      return translations[key] ?? key;
    },
  }),
}));

vi.mock('./TopBar', () => ({
  default: ({ onTogglePanel }: { onTogglePanel: () => void }) => (
    <button type="button" onClick={onTogglePanel}>toggle panel</button>
  ),
}));

vi.mock('./FlowCanvas', () => ({
  default: () => <div data-testid="flow-canvas">flow canvas</div>,
}));

vi.mock('./RightPanel', () => ({
  default: ({
    open,
    activeTab,
    workflow,
    onWorkflowUpdated,
    onSessionChange,
    chatLaunchRequest,
  }: {
    open: boolean;
    activeTab?: string;
    workflow: ReturnType<typeof makeWorkflow>;
    onWorkflowUpdated?: (workflow: ReturnType<typeof makeWorkflow>) => void;
    onSessionChange?: (sessionId: string | null) => void;
    chatLaunchRequest?: { prompt: string; displayLabel?: string } | null;
  }) => (
    <div
      data-testid="right-panel"
      data-open={open ? 'open' : 'closed'}
      data-active-tab={activeTab}
      data-launch-label={chatLaunchRequest?.displayLabel ?? ''}
    >
      right panel
      <button
        type="button"
        onClick={() => onSessionChange?.('session-1')}
      >
        attach workflow chat session
      </button>
      <button
        type="button"
        onClick={() => onWorkflowUpdated?.({
          ...workflow,
          updatedAt: workflow.updatedAt + 1,
          markdownContent: twoHunkMarkdown,
          editMarkdownContent: twoHunkMarkdown,
        })}
      >
        simulate AI markdown update
      </button>
      <button
        type="button"
        onClick={() => onWorkflowUpdated?.({
          ...workflow,
          updatedAt: workflow.updatedAt + 1,
          markdownContent: topOnlyMarkdown,
          editMarkdownContent: topOnlyMarkdown,
        })}
      >
        simulate top-only markdown update
      </button>
    </div>
  ),
}));

vi.mock('./NodeInfoPanel', () => ({
  default: () => <div>node info</div>,
}));

function makeWorkflow() {
  return {
    id: 'wf-1',
    name: '测试工作流',
    category: 'default',
    status: 'draft' as const,
    createdAt: 0,
    updatedAt: 0,
    stats: {
      callCount: 0,
      successCount: 0,
      errorCount: 0,
      totalRuntime: 0,
      avgRuntime: 0,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    workflowJson: {
      start: 'node-1',
      nodes: [{ id: 'node-1', type: 'python' as const }],
      edges: [],
    },
    markdownContent: originalMarkdown,
    editMarkdownContent: originalMarkdown,
  };
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/workflows/wf-1']}>
      <ConfirmProvider>
        <Routes>
          <Route path="/workflows/:id" element={<WorkflowDetail />} />
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
}

describe('Flocks help button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWorkflow.mockResolvedValue({ data: makeWorkflow() });
    mockUpdateWorkflow.mockImplementation(async (_workflowId: string, payload: { markdownContent?: string }) => ({
      data: {
        ...makeWorkflow(),
        markdownContent: payload.markdownContent ?? '',
        editMarkdownContent: payload.markdownContent ?? '',
      },
    }));
    mockSendSessionMessage.mockResolvedValue({});
  });

  it('opens the right panel on the AI edit tab', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'toggle panel' }));

    await waitFor(() => {
      expect(screen.getByTestId('right-panel')).toHaveAttribute('data-open', 'closed');
    });

    await user.click(screen.getByRole('button', { name: '让Rex帮你配置、编辑工作流' }));

    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-open', 'open');
    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-active-tab', 'chat');
  });

  it('uses an instruction label when launching workflow regeneration from the editor', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'MD 描述' }));
    await user.click(screen.getByRole('button', { name: '生成工作流' }));

    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-active-tab', 'chat');
    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-launch-label', '生成工作流');
  });

  it('shows AI markdown diff inline above the editor and can reject it', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'simulate AI markdown update' }));

    expect(await screen.findByText('AI 修改差异')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Diff' })).not.toBeInTheDocument();
    expect(screen.getByTestId('workflow-md-diff-review')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: '编辑 workflow.md' })).not.toBeInTheDocument();
    expect(screen.getByText('keep 3')).toBeInTheDocument();

    expect(screen.getAllByRole('button', { name: '接受此段' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: '拒绝此段' })).toHaveLength(2);

    await user.click(screen.getAllByRole('button', { name: '拒绝此段' })[0]);

    await waitFor(() => {
      expect(mockUpdateWorkflow).toHaveBeenCalledWith('wf-1', {
        markdownContent: '# old\n\nkeep 1\nkeep 2\nkeep 3\nkeep 4\n\nlast changed\n',
      });
    });
    expect(screen.getByText('AI 修改差异')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '拒绝此段' })).toHaveLength(1);
  });

  it('shows synchronized line numbers while editing workflow.md', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'MD 描述' }));
    await user.click(screen.getByRole('button', { name: '编辑' }));

    const lineNumbers = within(screen.getByTestId('workflow-md-line-numbers'));
    expect(lineNumbers.getByText('1')).toBeInTheDocument();
    expect(lineNumbers.getByText('8')).toBeInTheDocument();

    const editor = screen.getByRole('textbox', { name: '编辑 workflow.md' });
    fireEvent.change(editor, { target: { value: 'first\nsecond\nthird' } });

    await waitFor(() => {
      expect(within(screen.getByTestId('workflow-md-line-numbers')).getByText('3')).toBeInTheDocument();
    });
    expect(editor).toHaveValue('first\nsecond\nthird');
  });

  it('shows unchanged markdown lines outside a single diff hunk', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'simulate top-only markdown update' }));

    expect(await screen.findByText('AI 修改差异')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '接受此段' })).toHaveLength(1);
    expect(screen.getByText('keep 3')).toBeInTheDocument();
    expect(screen.getByText('keep 4')).toBeInTheDocument();
    expect(screen.getByText('last')).toBeInTheDocument();
  });

  it('persists an accepted markdown diff review result into the workflow chat session', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'attach workflow chat session' }));
    await user.click(screen.getByRole('button', { name: 'simulate top-only markdown update' }));
    await screen.findByText('AI 修改差异');
    await user.click(screen.getByRole('button', { name: '接受' }));

    await waitFor(() => {
      expect(mockSendSessionMessage).toHaveBeenCalledWith('session-1', {
        parts: [{
          type: 'text',
          text: expect.stringContaining('decision: accepted'),
        }],
        noReply: true,
      });
    });
    const payload = mockSendSessionMessage.mock.calls[0][1].parts[0].text;
    expect(payload).toContain('proposed_change_applied: true');
    expect(payload).toContain('review_state: completed');
  });

  it('persists a rejected markdown diff review result into the workflow chat session', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'attach workflow chat session' }));
    await user.click(screen.getByRole('button', { name: 'simulate top-only markdown update' }));
    await screen.findByText('AI 修改差异');
    await user.click(screen.getByRole('button', { name: '拒绝' }));

    await waitFor(() => {
      expect(mockSendSessionMessage).toHaveBeenCalledWith('session-1', {
        parts: [{
          type: 'text',
          text: expect.stringContaining('decision: rejected'),
        }],
        noReply: true,
      });
    });
    const payload = mockSendSessionMessage.mock.calls[0][1].parts[0].text;
    expect(payload).toContain('proposed_change_applied: false');
    expect(payload).toContain('workflow.md was restored to the previous content');
  });
});
