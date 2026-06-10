import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowCreate from './index';

const { capturedCreateRightPanelProps } = vi.hoisted(() => ({
  capturedCreateRightPanelProps: [] as any[],
}));

vi.mock('../WorkflowDetail/FlowCanvas', () => ({
  default: () => <div data-testid="flow-canvas">Flow canvas</div>,
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
        'detail.canvasTabs.md': 'workflow.md',
        'detail.canvasTabs.json': 'JSON',
        'detail.editDocTitle': 'workflow.md',
        'detail.editDocTextareaLabel': '编辑 workflow.md',
        'detail.editDocUnsaved': '未保存',
        'detail.editDocModeEdit': '编辑',
        'detail.editDocModePreview': '预览',
        'detail.generateEditDocTitle': '生成 workflow.md',
        'detail.regenerateEditDoc': '重新生成',
        'detail.generateEditDoc': '生成 workflow.md',
        'detail.downloadMdTitle': '下载 workflow.md',
        'detail.downloadMd': '下载 MD',
        'detail.editDocSaving': '保存中',
        'detail.editDocSave': '保存',
        'detail.generateWorkflow': '生成工作流',
        'detail.generateWorkflowTitle': '基于 workflow.md 生成 workflow.json',
        'detail.generateWorkflowPrompt': '用户点击了「生成工作流」按钮。基于 {{mdPath}} 生成 workflow.json。\n{{editDocContent}}',
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
  }),
}));

function renderWorkflowCreate() {
  return render(
    <MemoryRouter>
      <WorkflowCreate />
    </MemoryRouter>,
  );
}

describe('WorkflowCreate page', () => {
  beforeEach(() => {
    capturedCreateRightPanelProps.length = 0;
  });

  it('starts with the blank workflow.md editor on the left', () => {
    renderWorkflowCreate();

    expect(screen.getByRole('button', { name: /流程图/ })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /workflow\.md/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /JSON/ })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '编辑 workflow.md' })).toHaveValue('');
    expect(screen.getByTestId('workflow-md-line-numbers')).toHaveTextContent('1');
    expect(screen.getByTestId('create-right-panel')).toBeInTheDocument();
  });

  it('keeps the empty flow canvas available from the flow tab', async () => {
    const user = userEvent.setup();
    renderWorkflowCreate();

    await user.click(screen.getByRole('button', { name: /流程图/ }));

    expect(screen.getByTestId('flow-canvas')).toBeInTheDocument();
    expect(screen.getByText('工作流画布')).toBeVisible();
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
});
