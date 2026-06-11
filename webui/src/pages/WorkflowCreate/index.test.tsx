import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowCreate from './index';

type TestInitialEntry = string | {
  pathname: string;
  search?: string;
  hash?: string;
  state?: unknown;
  key?: string;
};

const { capturedCreateRightPanelProps, mockWorkflowAPI } = vi.hoisted(() => ({
  capturedCreateRightPanelProps: [] as any[],
  mockWorkflowAPI: {
    get: vi.fn(),
    update: vi.fn(),
  },
}));

vi.mock('../WorkflowDetail/FlowCanvas', () => ({
  default: () => <div data-testid="flow-canvas">Flow canvas</div>,
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: mockWorkflowAPI,
}));

vi.mock('./CreateRightPanel', () => ({
  default: (props: any) => {
    capturedCreateRightPanelProps.push(props);
    return <div data-testid="create-right-panel">Create right panel</div>;
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        pageTitle: '工作流',
        'create.topBar.newWorkflow': '新建工作流',
        'create.topBar.creating': '创建中',
        'create.topBar.generated': '已生成',
        'create.topBar.viewDetail': '查看详情',
        'create.canvasTitle': '工作流画布',
        'create.canvasHint': '在右侧工作台中描述您的需求',
        'detail.canvasTabs.flow': '流程图',
        'detail.canvasTabs.md': '流程说明',
        'detail.canvasTabs.json': '工作流文件',
        'detail.editDocTitle': 'workflow.md',
        'detail.editDocTextareaLabel': '编辑 workflow.md',
        'detail.editDocUnsaved': '未保存',
        'detail.editDocModeEdit': '编辑',
        'detail.editDocModePreview': '预览',
        'detail.generateEditDocTitle': '生成说明',
        'detail.regenerateEditDocTitle': '重置 workflow.md',
        'detail.regenerateEditDoc': '重置文档',
        'detail.generateEditDoc': '生成说明',
        'detail.downloadMdTitle': '下载当前说明文件',
        'detail.downloadMd': '下载说明文件',
        'detail.editDocSaving': '保存中',
        'detail.editDocSave': '保存',
        'detail.generateWorkflow': '生成工作流',
        'detail.generateWorkflowTitle': '基于 workflow.md 生成 workflow.json',
        'detail.generateEditDocPrompt': '用户点击了「生成说明」。基于 {{jsonPath}} 生成 workflow.md。\n{{workflowJson}}',
        'detail.generateWorkflowPrompt': '用户点击了「生成工作流」按钮。基于 {{mdPath}} 生成 workflow.json。\n{{editDocContent}}',
        'create.chat.generateEditDocPrompt': '用户点击了「生成说明」。先生成 workflow.md。\n{{editDocContent}}',
        'create.chat.generateWorkflowPrompt': '用户点击了「生成工作流」按钮。基于当前 workflow.md 生成 workflow.json。\n{{editDocContent}}',
        'detail.editDocPlaceholder': '编辑 workflow.md',
        'detail.editDocEmpty': '暂无 workflow.md',
        'detail.editDocEmptyHint': '生成 workflow.md',
        'detail.editDocDiffTitle': 'AI 修改差异',
        'detail.editDocDiffReviewDesc': 'AI 已修改 workflow.md',
        'detail.editDocDiffAdded': '新增',
        'detail.editDocDiffRemoved': '删除',
        'detail.editDocDiffAccept': '接受',
        'detail.editDocDiffReject': '拒绝',
        'detail.editDocDiffHunkTitle': '变更 {{index}}',
        'detail.editDocDiffAcceptHunk': '接受此段',
        'detail.editDocDiffRejectHunk': '拒绝此段',
        'detail.editDocDiffRejecting': '回滚中',
        'detail.editDocDiffEmpty': '没有差异',
        'detail.dragAdjust': '拖动调整宽度',
        'detail.topBar.collapsePanel': '收起面板',
        'detail.topBar.expandPanel': '展开面板',
      };
      return (translations[key] ?? key).replace(/{{(\w+)}}/g, (_match, name: string) => (
        params?.[name] === undefined ? '' : String(params[name])
      ));
    },
    i18n: { language: 'zh-CN' },
  }),
}));

function renderWorkflowCreate(initialEntries: TestInitialEntry[] = ['/workflows/new']) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <WorkflowCreate />
    </MemoryRouter>,
  );
}

describe('WorkflowCreate page', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    capturedCreateRightPanelProps.length = 0;
    mockWorkflowAPI.get.mockRejectedValue(new Error('not restored'));
    mockWorkflowAPI.update.mockImplementation(async (_id: string, data: Record<string, unknown>) => ({
      data: {
        id: _id,
        name: _id,
        category: 'default',
        status: 'draft',
        createdAt: 0,
        updatedAt: 1,
        workflowJson: {
          start: '',
          nodes: [],
          edges: [],
        },
        markdownContent: data.markdownContent,
        editMarkdownContent: data.markdownContent,
        stats: {
          callCount: 0,
          successCount: 0,
          errorCount: 0,
          totalRuntime: 0,
          avgRuntime: 0,
          thumbsUp: 0,
          thumbsDown: 0,
        },
      },
    }));
  });

  it('starts with the blank workflow.md editor on the left', () => {
    renderWorkflowCreate();

    expect(screen.getByRole('button', { name: /流程图/ })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /流程说明/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /工作流文件/ })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '编辑 workflow.md' })).toHaveValue('');
    expect(screen.getByTestId('workflow-md-line-numbers')).toHaveTextContent('1');
    expect(screen.getByTestId('create-right-panel')).toBeInTheDocument();
  });

  it('launches the workflow.md generation task from the blank editor', async () => {
    const user = userEvent.setup();
    renderWorkflowCreate();

    await user.click(screen.getByRole('button', { name: /生成说明/ }));

    await waitFor(() => {
      const latestProps = capturedCreateRightPanelProps[capturedCreateRightPanelProps.length - 1];
      expect(latestProps.chatLaunchRequest.displayLabel).toBe('生成说明');
      expect(latestProps.chatLaunchRequest.prompt).toContain('先生成 workflow.md');
    });
  });

  it('keeps the empty flow canvas available from the flow tab', async () => {
    const user = userEvent.setup();
    renderWorkflowCreate();

    await user.click(screen.getByRole('button', { name: /流程图/ }));

    expect(screen.getByTestId('flow-canvas')).toBeInTheDocument();
    expect(screen.getByText('工作流画布')).toBeVisible();
  });

  it('launches workflow.md generation when a workflow exists without markdown', async () => {
    renderWorkflowCreate();

    act(() => {
      capturedCreateRightPanelProps[0].onWorkflowCreated({
        id: 'json_only',
        name: 'json_only',
        category: 'default',
        status: 'draft',
        createdAt: 0,
        updatedAt: 0,
        workflowJson: {
          start: 'echo',
          nodes: [{ id: 'echo', type: 'python' }],
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
      });
    });

    await waitFor(() => {
      const latestProps = capturedCreateRightPanelProps[capturedCreateRightPanelProps.length - 1];
      expect(latestProps.chatLaunchRequest.displayLabel).toBe('生成说明');
      expect(latestProps.chatLaunchRequest.prompt).toContain('生成 workflow.md');
      expect(latestProps.chatLaunchRequest.prompt).toContain('"start": "echo"');
    });
  });

  it('shows markdown diff review and edit toolbar after a workflow is created', async () => {
    const user = userEvent.setup();
    renderWorkflowCreate();

    act(() => {
      capturedCreateRightPanelProps[0].onWorkflowCreated({
        id: 'hello_world',
        name: 'hello_world',
        category: 'default',
        status: 'draft',
        createdAt: 0,
        updatedAt: 0,
        markdownContent: '# hello_world\n\n## 业务场景\n',
        workflowJson: {
          start: 'echo',
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
      });
    });

    expect(screen.getByRole('button', { name: /编辑/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /预览/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /生成工作流/ })).toBeInTheDocument();
    expect(screen.getByTestId('workflow-md-diff-review')).toBeInTheDocument();
    expect(screen.getByText('AI 修改差异')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^接受$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^拒绝$/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /生成工作流/ }));

    await waitFor(() => {
      const latestProps = capturedCreateRightPanelProps[capturedCreateRightPanelProps.length - 1];
      expect(latestProps.chatLaunchRequest.displayLabel).toBe('生成工作流');
      expect(latestProps.chatLaunchRequest.prompt).toContain('workflow.json');
      expect(latestProps.chatLaunchRequest.prompt).toContain('# hello_world');
    });
  });

  it('restores the creation draft after refreshing the create page', async () => {
    const restoredWorkflow = {
      id: 'restored_workflow',
      name: 'restored_workflow',
      category: 'default',
      status: 'draft' as const,
      createdAt: 100,
      updatedAt: 200,
      markdownContent: '# server version\n',
      workflowJson: {
        start: 'echo',
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
    mockWorkflowAPI.get.mockResolvedValue({ data: restoredWorkflow });
    window.localStorage.setItem('flocks.workflow.create.draft.v1', JSON.stringify({
      version: 1,
      workflowId: 'restored_workflow',
      chatSessionId: 'session-restored',
      creationStartedAt: 123,
      panelOpen: true,
      panelWidth: 360,
      canvasTab: 'md',
      workflowMdDraft: '# local draft\n\nunsaved line\n',
      workflowMdBase: '# local draft\n',
      workflowMdDiff: {
        before: '# local draft\n',
        after: '# local draft\n\nunsaved line\n',
      },
      editDocMode: 'edit',
      updatedAt: 456,
    }));

    renderWorkflowCreate();

    await waitFor(() => {
      expect(mockWorkflowAPI.get).toHaveBeenCalledWith('restored_workflow');
    });
    expect(screen.getByTestId('workflow-md-diff-review')).toBeInTheDocument();
    expect(screen.getByText('unsaved line')).toBeInTheDocument();

    const latestProps = capturedCreateRightPanelProps[capturedCreateRightPanelProps.length - 1];
    expect(latestProps.initialChatSessionId).toBe('session-restored');
    expect(latestProps.creationStartedAt).toBe(123);
  });

  it('starts a fresh draft when opened from the create workflow entry', () => {
    window.localStorage.setItem('flocks.workflow.create.draft.v1', JSON.stringify({
      version: 1,
      workflowId: 'stale_workflow',
      chatSessionId: 'stale-session',
      creationStartedAt: 123,
      panelOpen: true,
      panelWidth: 360,
      canvasTab: 'md',
      workflowMdDraft: '# stale workflow\n',
      workflowMdBase: '# stale workflow\n',
      editDocMode: 'edit',
      updatedAt: 456,
    }));

    renderWorkflowCreate([
      {
        pathname: '/workflows/new',
        state: { freshCreate: true },
      },
    ]);

    expect(mockWorkflowAPI.get).not.toHaveBeenCalledWith('stale_workflow');
    expect(screen.getByRole('textbox', { name: '编辑 workflow.md' })).toHaveValue('');
    expect(window.localStorage.getItem('flocks.workflow.create.draft.v1')).toBeNull();

    const latestProps = capturedCreateRightPanelProps[capturedCreateRightPanelProps.length - 1];
    expect(latestProps.initialChatSessionId).toBeNull();
  });
});
